"""Persistent personal vocabulary, independent of optional interaction history."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unicodedata
import uuid


# Seed's bidirectional endpoint documents 100 tokens. Without its tokenizer,
# Use UTF-8 bytes as a conservative proxy, not an exact Seed token count.
HOTWORD_BUDGET = 90
MAX_WORD_CHARS = 40


def normalize_word(value: str) -> str:
    word = unicodedata.normalize("NFKC", str(value)).strip()
    if (not word or len(word) > MAX_WORD_CHARS or "\n" in word or "\r" in word
            or any(unicodedata.category(c).startswith("C") for c in word)):
        raise ValueError("请输入 1～40 个字符的词语，不要包含换行或控制字符")
    return word


def select_hotwords(words, budget: int = HOTWORD_BUDGET) -> list[str]:
    selected, seen, used = [], set(), 0
    for value in words:
        if not isinstance(value, str):
            continue
        try:
            word = normalize_word(value)
        except ValueError:
            continue
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        cost = len(word.encode("utf-8")) + 2
        if used + cost > budget:
            break  # The enabled prefix is authoritative; never jump over a word.
        selected.append(word)
        used += cost
    return selected


class HotwordStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.load_error = ""
        self._state = {"version": 2, "auto_learn": True, "entries": [], "blocked": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (value.get("version") not in (1, 2) or not isinstance(value.get("entries"), list)
                    or not isinstance(value.get("blocked"), list)):
                raise ValueError("无效的热词库格式")
            for entry in value["entries"]:
                normalize_word(entry["word"])
                if (not isinstance(entry["evidence"], dict) or not isinstance(entry["id"], str)
                        or not all(isinstance(entry[k], bool) for k in ("manual", "enabled", "pinned"))
                        or not all(isinstance(t, (int, float)) and math.isfinite(t)
                                   for t in [entry["updated_at"], *entry["evidence"].values()])
                        or not all(isinstance(k, str) for k in entry["evidence"])):
                    raise ValueError("无效的热词记录")
            if (not isinstance(value.get("auto_learn", True), bool)
                    or not all(isinstance(k, str) for k in value["blocked"])
                    or len({e["word"].casefold() for e in value["entries"]}) != len(value["entries"])
                    or len({e["id"] for e in value["entries"]}) != len(value["entries"])):
                raise ValueError("无效的热词库记录")
            if value["version"] == 1:
                value["entries"].sort(key=self._priority, reverse=True)
                value["version"] = 2
            self._state = value
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # Do not overwrite an unreadable personal dictionary on next edit.
            self.load_error = f"热词库读取失败，原文件已保留：{exc}"
        self._refresh_snapshot()

    def _refresh_snapshot(self):
        ordered = self._state["entries"]
        self._selected = tuple(select_hotwords(e["word"] for e in ordered if e["enabled"]))
        self._transactions = set()
        for entry in ordered:
            for source in entry["evidence"]:
                try:
                    key = json.loads(source)
                    if isinstance(key, list) and len(key) == 4 and all(isinstance(k, str) for k in key[:3]):
                        self._transactions.add(tuple(key[:3]))
                except (TypeError, ValueError):
                    continue

    def _change(self, mutate):
        with self._lock:
            if self.load_error:
                raise ValueError(self.load_error)
            updated = deepcopy(self._state)
            mutate(updated)
            if updated == self._state:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix=".hotwords-", delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(updated, output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self._state = updated
            self._refresh_snapshot()
            return True

    @property
    def auto_learn(self) -> bool:
        with self._lock:
            return bool(self._state.get("auto_learn", True))

    def set_auto_learn(self, enabled: bool):
        return self._change(lambda state: state.update(auto_learn=bool(enabled)))

    def entries(self) -> list[dict]:
        with self._lock:
            return deepcopy(self._state["entries"])

    @staticmethod
    def _priority(entry):
        evidence = entry["evidence"]
        # Only used to migrate the order displayed by the version 1 library.
        recent = max([entry["updated_at"], *evidence.values()])
        return (bool(entry["pinned"]), recent + min(len(evidence), 10) * 3600,
                bool(entry["manual"]), entry["word"])

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._selected)

    def add(self, word: str):
        word = normalize_word(word)
        def mutate(state):
            key = word.casefold()
            state["blocked"] = [item for item in state["blocked"] if item != key]
            entry = next((e for e in state["entries"] if e["word"].casefold() == key), None)
            if entry:
                entry.update(word=word, manual=True, enabled=True, updated_at=time.time())
            else:
                state["entries"].insert(0, self._entry(word, manual=True))
        return self._change(mutate)

    @staticmethod
    def _entry(word, *, manual=False):
        return dict(id=uuid.uuid4().hex, word=word, manual=manual, enabled=True,
                    pinned=False, updated_at=time.time(), evidence={})

    def update(self, entry_id: str, *, word=None, enabled=None, pinned=None):
        if word is not None:
            word = normalize_word(word)
        def mutate(state):
            entry = next((e for e in state["entries"] if e["id"] == entry_id), None)
            if entry is None:
                raise ValueError("热词已不存在，请刷新后重试")
            if word is not None and word != entry["word"]:
                if any(e["id"] != entry_id and e["word"].casefold() == word.casefold() for e in state["entries"]):
                    raise ValueError("这个热词已经在词库中")
                state["blocked"] = list(set(state["blocked"]) | {entry["word"].casefold()})
                state["blocked"] = [k for k in state["blocked"] if k != word.casefold()]
                entry.update(word=word, manual=True, evidence={}, updated_at=time.time())
            if enabled is not None:
                entry["enabled"] = bool(enabled)
            if pinned is not None:
                entry["pinned"] = bool(pinned)
                if pinned:  # Compatibility for older UI callers; order alone wins.
                    state["entries"].remove(entry)
                    state["entries"].insert(0, entry)
        return self._change(mutate)

    def move(self, entry_id: str, before_id: str = ""):
        """Move one stable identity, preserving words added during a UI drag."""
        def mutate(state):
            entries = state["entries"]
            entry = next((e for e in entries if e["id"] == entry_id), None)
            if entry is None or (before_id and not any(e["id"] == before_id for e in entries)):
                raise ValueError("词库已更新，请重新拖动")
            if entry_id == before_id:
                return
            entries.remove(entry)
            index = next((i for i, e in enumerate(entries) if e["id"] == before_id), len(entries))
            entries.insert(index, entry)
        return self._change(mutate)

    def remove(self, entry_id: str):
        def mutate(state):
            for entry in state["entries"]:
                if entry["id"] == entry_id:
                    state["blocked"] = list(set(state["blocked"]) | {entry["word"].casefold()})
            state["entries"] = [e for e in state["entries"] if e["id"] != entry_id]
        return self._change(mutate)

    def learn(self, source: str, words: list[str]):
        def mutate(state):
            added = []
            for word in words:
                word = normalize_word(word)
                key = word.casefold()
                if key in state["blocked"]:
                    continue
                entry = next((e for e in state["entries"] if e["word"].casefold() == key), None)
                if entry is None:
                    entry = self._entry(word)
                    state["entries"].append(entry)
                    added.append(entry)
                # Repeated delivery must not inflate priority or re-enable a word.
                entry["evidence"].setdefault(source, time.time())
            state["entries"] = added + [e for e in state["entries"] if e not in added]
        return self._change(mutate)

    def retract(self, source: str):
        def mutate(state):
            for entry in state["entries"]:
                entry["evidence"].pop(source, None)
            state["entries"] = [e for e in state["entries"] if e["manual"] or e["evidence"]]
        return self._change(mutate)

    def retract_transaction(self, identity: tuple):
        """History undo can outlive the in-memory extraction job/document."""
        with self._lock:
            if identity not in self._transactions:
                return False
        def matches(source):
            try:
                key = json.loads(source)
                return isinstance(key, list) and tuple(key[:3]) == identity
            except (TypeError, ValueError):
                return False
        def mutate(state):
            for entry in state["entries"]:
                entry["evidence"] = {k: v for k, v in entry["evidence"].items() if not matches(k)}
            state["entries"] = [e for e in state["entries"] if e["manual"] or e["evidence"]]
        return self._change(mutate)


def validated_hotwords(response, before: str, after: str) -> list[str]:
    """Validate returned words; the model judges correction intent from context."""
    if not isinstance(response, list) or len(response) > 8:
        raise ValueError("热词提取必须返回至多 8 个词语组成的 JSON 数组")
    words, rejected = [], []
    for item in response:
        if not isinstance(item, str):
            rejected.append("invalid_type")
            continue
        try:
            word = normalize_word(item)
        except ValueError:
            rejected.append("invalid_word")
            continue
        if len(word) < 2:
            rejected.append("isolated_character")
            continue
        if after.count(word) <= before.count(word):
            rejected.append("not_added_to_result")
            continue
        if (not any(c.isalpha() for c in word)
                or re.fullmatch(r"[零〇一二两三四五六七八九十百千万亿点年月日号时分秒周星期]+", word)):
            rejected.append("numeric_or_calendar_word")
            continue
        words.append(word)
    try:
        logging.getLogger(__name__).info("[HOTWORD_EXTRACTION] %s", json.dumps({
            "candidates": len(response), "accepted": list(dict.fromkeys(words)),
            "rejected": rejected, "reason": "model_empty" if not response else "validated",
        }, ensure_ascii=False))
    except Exception:
        pass
    return list(dict.fromkeys(words))
