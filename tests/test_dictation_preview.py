import io
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

from proximic_ring.text_processing import LLMSettings, OpenAICompatibleTextProcessor
from proximic_ring.ui.voice.dictation_preview import DictationPreviewController
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector


class Inline(QObject):
    changed = Signal()
    actionRequested = Signal(str)
    dictationPreviewEvent = Signal(object)
    automatic_polish = True

    def __init__(self):
        super().__init__()
        self._epoch, self._client_id, self._utterance_id = "epoch", "field", "first"
        self._session_id = 24
        self._view = dict(raw="我带", revision=4)
        self.sent = []
        self._bridge = SimpleNamespace(send=lambda msg: self.sent.append(msg) or True)

    def accept_dictation_preview(self, token, revision):
        self.actionRequested.emit("accept_dictation_preview")
        return self._bridge.send(dict(type="accept_dictation_preview", token=token, revision=revision))

    def resume_gesture_failure(self):
        return None

    def finish_resumed_gesture(self):
        pass


class Host(QObject):
    _quitting = False
    _desktop_output = True
    _gesture_bindings = SimpleNamespace(confirm=["tap"])

    def _append_log(self, msg):
        raise AssertionError(msg)

    def _llm_settings(self):
        return LLMSettings(enabled=True, provider="volcengine", model="test-deepseek", api_key="test")

    def __init__(self, root):
        super().__init__()
        self._inline_input = Inline()
        self._disconnect_event = threading.Event()
        self._modification_dataset = ModificationDatasetCollector(root, "test")
        interaction = self._modification_dataset.begin_session(24)
        self._voice_capture = dict(key=("epoch", "field", "first"), interaction_id=interaction,
                                   model_context={"scene": "对话", "context": "谁能带野餐垫？"})


def wait_for(condition):
    deadline = time.monotonic() + 3
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        QCoreApplication.processEvents()
        time.sleep(.005)


@pytest.fixture
def preview(tmp_path):
    host = Host(tmp_path)
    preview = DictationPreviewController(host)
    yield host, preview
    preview.close()


def submit(host, **extra):
    host._inline_input.dictationPreviewEvent.emit(dict(type="dictation_preview_requested", token="t",
        epoch="epoch", client_id="field", utterance_id="first", raw="我带", revision=4, before_cursor="前文", **extra))


def data(host):
    store = host._modification_dataset
    return store._interaction_data(host._voice_capture["interaction_id"]).get("dictation_preview", {})


def native_state(host, visible, reason=""):
    host._inline_input.dictationPreviewEvent.emit(dict(type="dictation_preview_state", token="t",
                                                      visible=visible, reason=reason, epoch="epoch"))


def test_tap_discards_late_output_but_records_generation_and_deadline(preview):
    host, controller = preview
    started, release = threading.Event(), threading.Event()
    def generate(settings, text, before, context):
        assert before == "前文" and context["context"] == "谁能带野餐垫？"
        started.set()
        assert release.wait(2)
        return "我带野餐垫。"
    controller._processor.suggest_dictation = generate
    submit(host)
    assert started.wait(1)
    assert not controller.consume("tap", host._disconnect_event, audio_busy=False)
    controller.next_sentence()  # The worker confirmed tap will start dictation.
    release.set()
    wait_for(lambda: data(host).get("output") == "我带野餐垫。")
    row = data(host)
    assert row["reason"] == "next_sentence" and row["in_time"] is False and row["model_ms"] >= 0
    assert not row["shown"] and not row["applied"]
    assert not any(m["type"] == "dictation_preview_result" for m in host._inline_input.sent)
    detail = host._modification_dataset.load_details(host._voice_capture["interaction_id"])
    text = next(s for s in detail["sections"] if s["key"] == "dictation_preview")["items"]
    assert "未在有效期内生成" in text[0]["text"] and text[1]["text"] == "我带野餐垫。"


@pytest.mark.parametrize("output,reason", [("我带","unchanged"), ("","empty"), ("甲\n乙","multiple_lines"),
                                          ('["甲","乙"]',"structured_output")])
def test_filtered_results_keep_the_actual_model_output(preview, output, reason):
    host, controller = preview
    controller._processor.suggest_dictation = lambda *_: output
    submit(host)
    wait_for(lambda: data(host).get("reason") == reason)
    row = data(host)
    assert row["output"] == output and row["rule_blocked"] and not row["shown"] and not row["applied"]


def test_punctuation_only_accept_requires_native_write_receipt(preview):
    host, controller = preview
    controller._processor.suggest_dictation = lambda *_: "我带。"
    submit(host)
    wait_for(lambda: any(m["type"] == "dictation_preview_result" for m in host._inline_input.sent))
    assert not controller.consume("swipe-down", host._disconnect_event, audio_busy=False)
    native_state(host, True)
    for _ in range(2):
        assert controller.consume("swipe-down", host._disconnect_event, audio_busy=False)
    wait_for(lambda: data(host).get("accept_requested"))
    assert sum(m["type"] == "accept_dictation_preview" for m in host._inline_input.sent) == 1
    assert data(host)["shown"] and not data(host)["applied"]
    native_state(host, False, "applying")
    native_state(host, False, "applied")
    wait_for(lambda: data(host).get("applied"))
    assert data(host)["reason"] == "applied"


def test_no_verified_range_keeps_generation_but_blocks_preview(preview):
    host, controller = preview
    controller._processor.suggest_dictation = lambda *_: "我带野餐垫。"
    submit(host, blocked="range_unverified")
    wait_for(lambda: controller.current is None and data(host).get("generation") == "completed")
    assert data(host)["reason"] == "range_unverified" and data(host)["rule_blocked"] and not data(host)["shown"]
    assert data(host)["output"] == "我带野餐垫。"


def test_ark_request_uses_exact_single_answer_prompt_and_disabled_thinking():
    calls = []
    def transport(req, **_):
        calls.append(json.loads(req.data))
        body = dict(status="completed", output=[dict(type="message", content=[dict(type="output_text", text="我带野餐垫。")])])
        return io.BytesIO(json.dumps(body).encode())
    settings = LLMSettings(enabled=True, provider="volcengine", model="test-deepseek", api_key="test")
    out = OpenAICompatibleTextProcessor(urlopen=transport).suggest_dictation(settings, "我带", "前文", {"scene":"对话","context":"野餐垫"})
    assert out == "我带野餐垫。" and calls[0]["thinking"] == {"type":"disabled"} and "reasoning" not in calls[0]
    messages = calls[0]["input"]
    assert messages[0]["content"][0]["text"] == (Path(__file__).resolve().parents[1]/"tools/data/asr_recovery_fragment_joint_v2.txt").read_text()
    assert json.loads(messages[1]["content"][0]["text"]) == {"scene":"对话","context":"野餐垫","before_cursor":"前文","asr_final":"我带"}
