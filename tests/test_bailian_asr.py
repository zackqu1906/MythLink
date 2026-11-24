import json
import queue
import threading

import numpy as np
import pytest

from proximic_ring.asr.backends.bailian import MESSAGE_MODEL, STREAMING_MODEL
from proximic_ring.asr.factory import ASRBackendSettings, create_streaming_asr_backend
from proximic_ring.audio.pcm import encode_pcm16le

websocket = pytest.importorskip("websocket")


class DuplexSocket:
    """Script server events independently of the audio sender."""
    def __init__(self):
        self.incoming = queue.Queue()
        self.commands = []
        self.audio = bytearray()
        self.closed = threading.Event()

    def send(self, message):
        command = json.loads(message)
        self.commands.append(command)
        if command["header"]["action"] == "run-task":
            self.event("task-started")
        else:
            self.event("task-finished")

    def event(self, name, **payload):
        header = {"task_id": self.commands[0]["header"]["task_id"], "event": name}
        self.incoming.put(json.dumps({"header": header, "payload": payload}))

    def sentence(self, index, text, final):
        self.event("result-generated", output={"sentence": {
            "sentence_id": index, "text": text, "sentence_end": final,
        }})

    def send_binary(self, data):
        self.audio.extend(data)

    def recv(self):
        return self.incoming.get(timeout=2)

    def shutdown(self):
        self.closed.set()

    def abort(self):
        self.incoming.put("")


def make_backend(model=STREAMING_MODEL, language="auto", **options):
    return create_streaming_asr_backend("bailian", ASRBackendSettings(
        model=model, language=language, options={"api_key": "test-only-key", **options},
    ))


@pytest.mark.parametrize("model, language", [(STREAMING_MODEL, "auto"), (MESSAGE_MODEL, "yue")])
def test_duplex_revises_and_joins_sentences_and_sends_tail_only_once(monkeypatch, model, language):
    ws = DuplexSocket()
    def connect(url, **kwargs):
        assert url == "wss://example.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        assert kwargs["header"] == {"Authorization": "Bearer test-only-key"}
        return ws
    monkeypatch.setattr(websocket, "create_connection", connect)
    backend = make_backend(model, language, api_host="https://example.cn-beijing.maas.aliyuncs.com/api/v1")
    partials = queue.Queue()
    backend.set_partial_callback(lambda text, ready, duration: partials.put(text))
    backend.start()
    audio = np.linspace(-0.5, 0.5, 3300, dtype=np.float32)
    assert backend.feed(audio) is None
    assert bytes(ws.audio) == encode_pcm16le(audio[:3200])
    ws.sentence(0, "天气", False)
    assert partials.get(timeout=1) == "天气"
    ws.sentence(0, "今天天气好。", True)
    ws.sentence(1, "出去走", False)
    ws.sentence(1, "出去走走。", True)
    assert backend.finish(audio) == "今天天气好。出去走走"
    assert partials.get(timeout=1) == "今天天气好。"
    assert partials.get(timeout=1) == "今天天气好。出去走"
    assert bytes(ws.audio) == encode_pcm16le(audio)
    run, finish = ws.commands
    assert run["header"]["streaming"] == "duplex"
    assert run["payload"]["model"] == model
    params = run["payload"]["parameters"]
    assert params == {"sample_rate": 16000, "format": "pcm", "vad_model": "near_meeting_16k", **(
        {"language_hints": ["zh"], "keep_dialect": True, "intermediate_result_enabled": True}
        if model == MESSAGE_MODEL else {})}
    assert finish == {"header": dict(run["header"], action="finish-task"), "payload": {"input": {}}}
    assert ws.closed.is_set()


def test_task_failure_or_unconfirmed_partial_never_commits(monkeypatch):
    sockets = [DuplexSocket(), DuplexSocket()]
    monkeypatch.setattr(websocket, "create_connection", lambda *a, **k: sockets.pop(0))
    backend = make_backend()
    ws = sockets[0]
    backend.start()
    ws.sentence(0, "draft", False)
    with pytest.raises(RuntimeError, match="完整结果"):
        backend.finish(np.empty(0))
    ws = sockets[0]
    backend.start()
    ws.incoming.put(json.dumps({"header": {
        "task_id": ws.commands[0]["header"]["task_id"], "event": "task-failed",
        "error_code": "InvalidApiKey", "error_message": "unsafe test-only-key",
    }}))
    assert ws.closed.wait(1)
    with pytest.raises(RuntimeError, match="InvalidApiKey") as failure:
        backend.finish(np.empty(0))
    assert "test-only-key" not in str(failure.value)


def test_cancelled_connection_cannot_join_next_session(monkeypatch):
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    old, new = DuplexSocket(), DuplexSocket()
    def connect(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(2)
            return old
        return new
    monkeypatch.setattr(websocket, "create_connection", connect)
    backend = make_backend()
    backend.set_cancel_event(cancelled)
    errors = []
    def start_old():
        with pytest.raises(RuntimeError, match="取消") as failure:
            backend.start()
        errors.append(failure)
    starter = threading.Thread(target=start_old)
    starter.start()
    assert entered.wait(1)
    cancelled.set()
    starter.join(1)
    assert errors
    backend.set_cancel_event(threading.Event())
    backend.start()
    release.set()
    assert old.closed.wait(1) and not old.commands
    new.sentence(0, "新会话。", True)
    assert backend.finish(np.empty(0)) == "新会话"


def test_credentials_cannot_be_sent_to_an_unrelated_host(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API Key"):
        make_backend(api_key="")
    for url in ("http://dashscope.aliyuncs.com", "https://dashscope.aliyuncs.com.example.org",
                "wss://dashscope.aliyuncs.com@example.org"):
        with pytest.raises(ValueError, match="API Host"):
            make_backend(api_host=url)
