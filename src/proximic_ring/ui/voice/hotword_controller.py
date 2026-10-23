"""Personal vocabulary UI and non-blocking learning from confirmed edits."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import queue
import threading
import time

from PySide6.QtCore import QObject, Property, Signal, Slot

from ...voice.hotwords import HotwordStore
from ...text_processing.llm import OpenAICompatibleTextProcessor
from ...text_processing.model import LLMSettings


@dataclass
class _Edit:
    key: tuple
    before: str
    instruction: str
    settings: LLMSettings
    after: str = ""
    applied: bool = False
    cancelled: threading.Event = field(default_factory=threading.Event)

    @property
    def source(self):
        return json.dumps(self.key, ensure_ascii=False, separators=(",", ":"))


class HotwordController(QObject):
    changed = Signal()
    messageChanged = Signal()
    learned = Signal(object, object)
    _completed = Signal(object, object, str)

    def __init__(self, path: Path, parent=None, *, processor=None):
        super().__init__(parent)
        self.store = HotwordStore(path)
        self._message = self.store.load_error
        self._processor = processor or OpenAICompatibleTextProcessor()
        self._edits: OrderedDict[tuple, _Edit] = OrderedDict()
        self._queue = queue.Queue(maxsize=16)
        self._thread = None
        self._closed = threading.Event()
        self._completed.connect(self._finish)

    @Property("QVariantList", notify=changed)
    def entries(self):
        active = set(self.store.snapshot())
        return [dict(id=e["id"], word=e["word"], enabled=e["enabled"], pinned=e["pinned"],
                     source="手动添加" if e["manual"] else "自动学习",
                     corrections=len(e["evidence"]), selected=e["word"] in active)
                for e in self.store.entries()]

    @Property(str, notify=changed)
    def summary(self):
        entries = self.store.entries()
        enabled = sum(bool(e["enabled"]) for e in entries)
        return f"{len(entries)} 个热词 · 下一句使用 {len(self.store.snapshot())} 个 · {enabled - len(self.store.snapshot())} 个超出预算"

    @Property(str, notify=messageChanged)
    def message(self):
        return self._message

    def _show(self, text):
        self._message = text
        self.messageChanged.emit()

    @staticmethod
    def _diagnose(stage, key, **fields):
        try:
            logging.getLogger(__name__).info("[HOTWORD_LEARNING] %s", json.dumps(
                dict(stage=stage, key=list(key), **fields), ensure_ascii=False))
        except Exception:
            pass

    @Slot()
    def clearMessage(self):
        self._show("")

    def _mutate(self, action, message=""):
        try:
            changed = action()
        except (OSError, ValueError) as exc:
            self._show(str(exc))
            return False
        if changed:
            self.changed.emit()
        self._show(message)
        return True

    @Property(bool, notify=changed)
    def autoLearn(self):
        return self.store.auto_learn

    @autoLearn.setter
    def autoLearn(self, enabled):
        if not self._mutate(lambda: self.store.set_auto_learn(enabled)):
            return
        if not enabled:
            for edit in self._edits.values():
                edit.cancelled.set()

    @Slot(str, result=bool)
    def add(self, word):
        return self._mutate(lambda: self.store.add(word), "已添加，将从下一句开始参与识别。")

    @Slot(str, str, result=bool)
    def rename(self, entry_id, word):
        return self._mutate(lambda: self.store.update(entry_id, word=word))

    @Slot(str, str, bool, result=bool)
    def edit(self, entry_id, word, enabled):
        return self._mutate(lambda: self.store.update(entry_id, word=word, enabled=enabled))

    @Slot(str, bool)
    def setEnabled(self, entry_id, enabled):
        self._mutate(lambda: self.store.update(entry_id, enabled=enabled))

    @Slot(str, bool)
    def setPinned(self, entry_id, pinned):
        self._mutate(lambda: self.store.update(entry_id, pinned=pinned))

    @Slot(str, str, result=bool)
    def move(self, entry_id, before_id):
        return self._mutate(lambda: self.store.move(entry_id, before_id), "顺序已保存，从下一句开始生效。")

    @Slot("QVariantList", result="QVariantList")
    def previewSelection(self, entry_ids):
        from ...voice.hotwords import select_hotwords
        entries = {e["id"]: e for e in self.store.entries()}
        return select_hotwords(entries[k]["word"] for k in entry_ids if k in entries and entries[k]["enabled"])

    @Slot(str)
    def remove(self, entry_id):
        self._mutate(lambda: self.store.remove(entry_id), "已删除，此词不会再次被自动收录；仍可手动添加。")

    def prepare(self, key, instruction, before, settings):
        if self._closed.is_set() or self.store.load_error or not self.autoLearn or not before or not instruction:
            self._diagnose("prepare_skipped", key, closed=self._closed.is_set(),
                           load_error=bool(self.store.load_error), auto_learn=self.autoLearn,
                           before_chars=len(before), instruction_chars=len(instruction))
            return
        key = tuple(key)
        root = key[:3]
        old = self._edits.get(root)
        if old is not None:
            if old.key == key and not old.cancelled.is_set():
                return
            old.cancelled.set()
        self._edits[root] = _Edit(key, before, instruction, settings)
        self._edits.move_to_end(root)
        self._diagnose("prepared", key, before_chars=len(before), instruction_chars=len(instruction))
        # Native undo history is bounded too; never retain whole documents indefinitely.
        while len(self._edits) > 256:
            _, expired = self._edits.popitem(last=False)
            expired.cancelled.set()

    def expect_result(self, key, text, error=""):
        edit = self._edits.get(tuple(key)[:3])
        if edit is None or edit.key != tuple(key) or edit.applied:
            return
        if error or not text or edit.before == text:
            edit.cancelled.set()
            self._diagnose("result_skipped", key, reason="edit_error" if error else "unchanged_or_empty")
            return
        edit.after = text
        self._diagnose("result_ready", key, after_chars=len(text))

    @Slot(object, str, str, str, bool)
    def settle(self, key, phase, text, error="", historical=False):
        key = tuple(key)
        edit = self._edits.get(key[:3])
        if edit is not None and not historical and key[-1] < edit.key[-1]:
            return
        if phase in {"dictated", "undone"}:
            self._mutate(lambda: self.store.retract_transaction(key[:3]))
            if edit is not None:
                self._diagnose("retracted", key, phase=phase)
                edit.cancelled.set()
                # Keep an applied identity for duplicate undo, but release text.
                edit.before = edit.instruction = edit.after = ""
            return
        if edit is None:
            if phase == "edited":
                self._diagnose("settlement_skipped", key, reason="missing_prepared_edit")
            return
        if phase != "edited" or error or edit.applied or edit.key != key:
            self._diagnose("settlement_skipped", key, phase=phase, error=bool(error),
                           already_applied=edit.applied, matching_revision=edit.key == key)
            return
        # Native IME appends the untouched suffix after the model's edited prefix.
        if not edit.after or not text.startswith(edit.after) or edit.cancelled.is_set():
            self._diagnose("settlement_skipped", key, reason="cancelled_or_text_mismatch",
                           cancelled=edit.cancelled.is_set(), expected_chars=len(edit.after), actual_chars=len(text))
            return
        edit.applied = True
        self._enqueue(edit)

    def _enqueue(self, edit):
        if self._closed.is_set() or not self.autoLearn or edit.cancelled.is_set():
            return
        try:
            self._queue.put_nowait(edit)
        except queue.Full:
            edit.cancelled.set()
            self._show("后台提词繁忙，本次未学习热词；编辑已正常完成。")
            self._diagnose("queue_full", edit.key)
            return
        self._diagnose("queued", edit.key)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="HotwordLearning", daemon=True)
            self._thread.start()

    def _run(self):
        while not self._closed.is_set():
            try:
                edit = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if edit.cancelled.is_set():
                continue
            words, error = [], ""
            started = time.monotonic()
            self._diagnose("extract_started", edit.key)
            try:
                words = self._processor.extract_hotwords(edit.before, edit.instruction, edit.after, edit.settings)
            except Exception as exc:
                error = f"本次未学习热词：{type(exc).__name__}；编辑结果不受影响。"
                self._diagnose("extract_failed", edit.key, error_type=type(exc).__name__)
            self._diagnose("extract_finished", edit.key, candidates=len(words),
                           elapsed_ms=round((time.monotonic() - started) * 1000), error=bool(error))
            if not self._closed.is_set() and not edit.cancelled.is_set():
                self._completed.emit(edit, words, error)

    @Slot(object, object, str)
    def _finish(self, edit, words, error):
        if (self._closed.is_set() or not self.autoLearn or edit.cancelled.is_set()
                or self._edits.get(edit.key[:3]) is not edit):
            self._diagnose("result_discarded", edit.key, reason="closed_disabled_cancelled_or_superseded")
            return
        if error:
            self._show(error)
        elif words:
            before = {e["id"] for e in self.store.entries()}
            if self._mutate(lambda: self.store.learn(edit.source, words)):
                added = [e["word"] for e in self.store.entries() if e["id"] not in before]
                if added:
                    self._show("已收录热词：" + "、".join(added))
                    self.learned.emit(edit.key, added)
                self._diagnose("saved", edit.key, added=added,
                               reason="new_words" if added else "already_present_or_blocked")
            else:
                self._diagnose("save_failed", edit.key)
        else:
            self._show("本次未提取到可收录的纠正词。")
            self._diagnose("no_candidates", edit.key)
        edit.before = edit.instruction = edit.after = ""

    def discard_pending(self):
        for edit in self._edits.values():
            if not edit.applied:
                edit.cancelled.set()
                edit.before = edit.instruction = edit.after = ""

    def close(self):
        self._closed.set()
        for edit in self._edits.values():
            edit.cancelled.set()
