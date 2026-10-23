import threading
import time
import json
import logging

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.text_processing import LLMSettings, TextProcessingResult
from proximic_ring.ui.voice.hotword_controller import HotwordController
from test_app_gestures import route
from test_inline_state import component, receive


def pump_until(condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert condition()


class Extractor:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.failure = False

    def extract_hotwords(self, before, instruction, after, settings):
        self.calls.append((before, instruction, after))
        self.started.set()
        assert self.release.wait(3)
        if self.failure:
            raise RuntimeError("model offline")
        return ["张玮"]


@pytest.fixture
def service(tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    extractor = Extractor()
    controller = HotwordController(tmp_path / "words.json", processor=extractor)
    yield controller, extractor
    controller.close()
    extractor.release.set()
    if controller._thread is not None:
        controller._thread.join(1)
    app.processEvents()


KEY = ("epoch", "client", "utterance", 2)


def prepare(c, key=KEY):
    c.prepare(key, "是张玮，王字旁的玮", "联系张伟", LLMSettings(enabled=True))
    c.expect_result(key, "联系张玮")


def test_learning_waits_for_native_confirmation_and_undo_removes_it(service):
    c, model = service
    prepare(c)
    assert model.calls == [] and c.store.snapshot() == []
    c.settle(KEY, "edited", "联系张玮。原有后缀")
    assert model.started.wait(1)
    c.settle(KEY, "edited", "联系张玮。原有后缀")
    model.release.set()
    pump_until(lambda: c.store.snapshot() == ["张玮"])
    assert len(model.calls) == 1 and model.calls[0] == ("联系张伟", "是张玮，王字旁的玮", "联系张玮")
    c.settle((*KEY[:3], 3), "dictated", "是张玮，王字旁的玮")
    assert c.store.snapshot() == []


def test_edit_saves_word_and_enabled_state_together_or_keeps_both(service, monkeypatch):
    c, _ = service
    c.add("张伟")
    entry_id = c.entries[0]["id"]
    original = c.store.path.read_bytes()

    def unavailable(*args):
        raise OSError("保存失败")

    with monkeypatch.context() as patch:
        patch.setattr("proximic_ring.voice.hotwords.os.replace", unavailable)
        assert not c.edit(entry_id, "张玮", False)
    assert c.entries[0]["word"] == "张伟" and c.entries[0]["enabled"]
    assert c.store.path.read_bytes() == original and c.message == "保存失败"
    assert c.edit(entry_id, "张玮", False)
    assert c.entries[0]["word"] == "张玮" and not c.entries[0]["enabled"]


@pytest.mark.parametrize("action", ["undo", "disable", "close"])
def test_late_extraction_cannot_readd_after_cancel(service, action):
    c, model = service
    prepare(c)
    c.settle(KEY, "edited", "联系张玮")
    assert model.started.wait(1)
    if action == "undo":
        c.settle((*KEY[:3], 3), "dictated", "原指令")
    elif action == "disable":
        c.autoLearn = False
    else:
        c.close()
    model.release.set()
    time.sleep(0.03)
    QCoreApplication.processEvents()
    assert not c.store.snapshot()


def test_unapplied_error_and_wrong_revision_never_learn(service):
    c, model = service
    prepare(c)
    c.settle((*KEY[:3], 99), "edited", "联系张玮")
    c.settle(KEY, "edited", "另一段文字")
    c.settle(KEY, "edited", "联系张玮", "回读失败")
    c.expect_result(KEY, "联系张玮", "编辑失败")
    c.settle(KEY, "dictated", "原指令")
    c.settle(KEY, "edited", "联系张玮")
    assert model.calls == [] and not c.store.snapshot()


def test_historical_source_undo_only_retracts_that_edit(service):
    c, model = service
    model.release.set()
    for key in [KEY, (*KEY[:2], "next-utterance", 2)]:
        prepare(c, key)
        c.settle(key, "edited", "联系张玮")
    pump_until(lambda: c.entries and c.entries[0]["corrections"] == 2)
    c.settle((*KEY[:3], 1), "dictated", "原指令", historical=True)
    assert c.entries[0]["corrections"] == 1
    c.store.add("张玮")
    c.settle((*KEY[:2], "next-utterance", 3), "undone", "")
    assert c.store.snapshot() == ["张玮"]


def test_model_failure_is_reported_without_touching_edit(service):
    c, model = service
    model.failure = True
    model.release.set()
    prepare(c)
    c.settle(KEY, "edited", "联系张玮")
    pump_until(lambda: "不受影响" in c.message)
    assert not c.entries


def test_queued_result_after_manual_deletion_respects_tombstone(service):
    c, model = service
    c.add("张玮")
    prepare(c)
    c.settle(KEY, "edited", "联系张玮")
    assert model.started.wait(1)
    c.remove(c.entries[0]["id"])
    notifications = []
    c.learned.connect(lambda *args: notifications.append(args))
    model.release.set()
    pump_until(lambda: c._edits[KEY[:3]].before == "")
    assert not c.entries and not notifications


def test_native_settlement_carries_original_undo_identity(component):
    inline, _ = component
    inline._utterance_id = "current"
    received = []
    inline.transactionSettled.connect(lambda *args: received.append(args))
    receive(inline, "settled", phase="dictated", revision=8, source_utterance_id="previous", text="restored")
    assert received == [(("epoch-one", "client-one", "previous", 8), "dictated", "restored", "", True)]


def test_undo_still_retracts_after_job_metadata_was_evicted(service):
    c, model = service
    model.release.set()
    prepare(c)
    c.settle(KEY, "edited", "联系张玮")
    pump_until(lambda: c.store.snapshot() == ["张玮"])
    c._edits.clear()
    c.settle((*KEY[:3], 1), "dictated", "原指令", historical=True)
    assert c.store.snapshot() == []


def test_older_settlement_cannot_cancel_new_edit(service):
    c, model = service
    model.release.set()
    prepare(c)
    c.settle((*KEY[:3], 1), "dictated", "old")
    c.settle(KEY, "edited", "联系张玮")
    pump_until(lambda: c.store.snapshot() == ["张玮"])


def test_reapplying_a_reverted_edit_can_learn_again(service):
    c, model = service
    model.release.set()
    prepare(c)
    c.settle(KEY, "edited", "联系张玮")
    pump_until(lambda: c.store.snapshot() == ["张玮"])
    c.settle(KEY, "undone", "")
    assert not c.store.snapshot()
    prepare(c)
    c.settle(KEY, "edited", "联系张玮")
    pump_until(lambda: c.store.snapshot() == ["张玮"])


def test_app_routes_edit_result_ack_and_undo_to_learning(route, monkeypatch):
    c, _, inline, _, sent, messages, _ = route
    c._asr_backend = "volcengine"
    c._latest_asr_session_id = 12
    model = Extractor()
    model.release.set()
    c.hotwords._processor = model
    submitted = []
    monkeypatch.setattr(c._text_processing_worker, "submit", submitted.append)
    inline._view.update(phase="editing", revision=2)
    key = (inline._epoch, inline._client_id, inline._utterance_id, 2)
    c._submit_inline_edit(key, "王字旁的玮", "联系张伟", inline.target_reference())
    request = submitted[0]
    c._apply_text_processed(TextProcessingResult(request.request_id, request.session_id, "edit", "王字旁的玮",
                                                "联系张玮", 0.1, True))
    assert messages[-1][0] == "edit_result" and not model.calls
    receive(inline, "settled", phase="edited", revision=2, text="联系张玮")
    pump_until(lambda: c.hotwords.store.snapshot() == ["张玮"])
    receive(inline, "settled", phase="dictated", revision=3, text="王字旁的玮")
    assert c.hotwords.store.snapshot() == [] and not sent


@pytest.mark.parametrize("before,instruction,after,word", [
    ("进行哔哩哔哩。飞书。", "鼠标的鼠。", "进行哔哩哔哩。飞鼠。", "飞鼠"),
    ("进行哔哩哔哩。飞鼠。miss 绘制。", "myth 绘制。", "进行哔哩哔哩。飞鼠。myth 绘制。", "myth"),
])
def test_replayed_ellipsis_results_save_notify_and_retract(route, monkeypatch, caplog,
                                                        before, instruction, after, word):
    """Two previously missed utterances using the word-only response format."""
    from proximic_ring.text_processing import OpenAICompatibleTextProcessor
    from proximic_ring.voice.hotwords import HotwordStore
    c, _, inline, _, sent, messages, _ = route
    caplog.set_level(logging.INFO)
    c._asr_backend = "volcengine"
    c._latest_asr_session_id = 12
    settings = LLMSettings(enabled=True, provider="volcengine", model="fixture",
                           base_url="https://example.invalid", api_key_env="TEST_UNUSED_API_KEY")
    monkeypatch.setattr(c, "_voice_llm_settings", lambda *args: settings)
    c.hotwords._processor = OpenAICompatibleTextProcessor()
    monkeypatch.setattr(c.hotwords._processor, "_request_chat", lambda *a, **kw: json.dumps([word]))
    submitted, notices = [], []
    monkeypatch.setattr(c._text_processing_worker, "submit", submitted.append)
    c.hotwords.learned.connect(lambda *args: notices.append(args))
    inline._view.update(phase="editing", revision=2)
    key = (inline._epoch, inline._client_id, inline._utterance_id, 2)
    c._submit_inline_edit(key, instruction, before, inline.target_reference())
    request = submitted[0]
    c._apply_text_processed(TextProcessingResult(request.request_id, request.session_id, "edit",
                                                instruction, after, .1, True))
    assert not c.hotwords.entries
    receive(inline, "state", phase="edited", revision=2, error="", ready=True, awaiting_readback=False)
    receive(inline, "settled", phase="edited", revision=2, text=after)
    pump_until(lambda: bool(notices))
    assert c.hotwords.store.snapshot() == [word]
    assert HotwordStore(c.hotwords.store.path).snapshot() == [word]
    assert notices == [(key, [word])]
    assert any(message[0] == "hotwords_learned" for message in messages)
    stages = [json.loads(r.getMessage().split("] ", 1)[1])["stage"]
              for r in caplog.records if "[HOTWORD_LEARNING]" in r.getMessage()]
    assert all(stage in stages for stage in ("prepared", "queued", "extract_started", "extract_finished", "saved"))
    receive(inline, "settled", phase="dictated", revision=3, text=instruction)
    assert c.hotwords.store.snapshot() == [] and not sent


def test_empty_extraction_has_explicit_diagnostic_and_library_message(service, monkeypatch, caplog):
    c, model = service
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(model, "extract_hotwords", lambda *args: [])
    prepare(c)
    c.settle(KEY, "edited", "联系张玮")
    pump_until(lambda: "未提取到" in c.message)
    assert not c.entries
    assert any('"stage": "no_candidates"' in r.getMessage() for r in caplog.records)


def test_notifications_only_after_new_words_are_saved(service):
    c, model = service
    model.release.set()
    notices = []
    c.learned.connect(lambda *args: notices.append(args))
    prepare(c)
    c.settle(KEY, "edited", "联系张玮")
    pump_until(lambda: bool(notices))
    assert notices == [(KEY, ["张玮"])]
    assert "张玮" in c.message
    next_key = (*KEY[:2], "next", 2)
    prepare(c, next_key)
    c.settle(next_key, "edited", "联系张玮")
    pump_until(lambda: c.entries[0]["corrections"] == 2)
    assert len(notices) == 1


def test_failed_save_does_not_announce_success(service, monkeypatch):
    import proximic_ring.voice.hotwords as module
    c, model = service
    model.release.set()
    notices = []
    c.learned.connect(lambda *args: notices.append(args))
    monkeypatch.setattr(module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    prepare(c)
    c.settle(KEY, "edited", "联系张玮")
    pump_until(lambda: "disk full" in c.message)
    assert not notices and not c.entries


def test_native_notice_is_bound_to_current_confirmed_edit(component):
    inline, _ = component
    inline._utterance_id = "one"
    inline._view.update(phase="edited", revision=2, error="", awaiting_readback=False)
    key = (inline._epoch, inline._client_id, "one", 2)
    state = inline._view.copy()
    for invalid in [("old", *key[1:]), (key[0], "old", *key[2:]), (*key[:2], "old", 2), (*key[:3], 1)]:
        inline.notify_hotwords_learned(invalid, ["张玮"])
    assert not any(m["type"] == "hotwords_learned" for m in inline._bridge.messages)
    inline.notify_hotwords_learned(key, ["张玮"])
    assert inline._bridge.messages[-1]["type"] == "hotwords_learned"
    assert inline._bridge.messages[-1]["words"] == ["张玮"]
    assert inline._view == state
    before = len(inline._bridge.messages)
    for phase in ["listening", "polishing", "dictated", "undone", "interrupted"]:
        inline._view["phase"] = phase
        inline.notify_hotwords_learned(key, ["张玮"])
    assert len(inline._bridge.messages) == before
