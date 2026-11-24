"""Duplex Qwen-Audio ASR over Bailian's DashScope inference WebSocket."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
import uuid

import numpy as np

from ...audio.pcm import encode_pcm16le
from ..factory import ASRBackendSettings
from ..punctuation import trim_short_utterance_end

STREAMING_MODEL = "qwen-audio-3.1-asr-flash-streaming"
MESSAGE_MODEL = "qwen-audio-3.1-asr-flash-message"
DEFAULT_HOST = "dashscope.aliyuncs.com"
_PACKET_BYTES = 3200  # 100 ms of 16 kHz mono PCM16.


def _inference_url(address: str) -> str:
    address = address.strip() or DEFAULT_HOST
    url = urlsplit(address if "://" in address else "https://" + address)
    host = url.hostname or ""
    if (url.scheme not in {"https", "wss"} or url.netloc.lower() != host
            or not re.fullmatch(r"dashscope(?:-intl)?\.aliyuncs\.com|"
                                r"[a-z0-9-]+\.(?:cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com", host)):
        raise ValueError("请填写百炼控制台的北京或新加坡 API Host")
    return f"wss://{host}/api-ws/v1/inference"


@dataclass
class _Session:
    cancelled: threading.Event
    partial: Callable[[str, float, float], None] | None
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: threading.Event = field(default_factory=threading.Event)
    finishing: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    stopped: threading.Event = field(default_factory=threading.Event)
    socket: Any = None
    error: str = ""
    text: str = ""
    pending_pcm: bytearray = field(default_factory=bytearray)
    ready_at: float = field(default_factory=time.perf_counter)
    sent_bytes: int = 0

    def command(self, action: str, **payload) -> None:
        self.socket.send(json.dumps({
            "header": {"action": action, "task_id": self.task_id, "streaming": "duplex"},
            "payload": {"input": {}, **payload},
        }))

    def check(self) -> None:
        if self.cancelled.is_set() or self.stopped.is_set():
            raise RuntimeError("已取消百炼语音识别")
        if self.error:
            raise RuntimeError(self.error)

    def wait(self, event: threading.Event, description: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while not event.wait(0.03):
            self.check()
            if time.monotonic() >= deadline:
                raise RuntimeError(f"百炼语音识别{description}超时")
        self.check()


def _join_sentences(parts: dict[int, str]) -> str:
    text = ""
    for index in sorted(parts):
        part = parts[index]
        if re.search(r"[a-zA-Z0-9.?!,:]$", text) and re.match(r"[a-zA-Z0-9]", part):
            text += " "
        text += part
    return text


class BailianStreamingASR:
    backend_name = "bailian"

    def __init__(self, settings: ASRBackendSettings):
        self.model_name = settings.model or STREAMING_MODEL
        if self.model_name not in {STREAMING_MODEL, MESSAGE_MODEL}:
            raise ValueError("请选择受支持的百炼实时语音识别模型")
        self._key = (settings.options.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")).strip()
        if not self._key or "\r" in self._key or "\n" in self._key:
            raise ValueError("请在高级设置中填写百炼 API Key")
        self._url = _inference_url(settings.options.get("api_host") or os.getenv("DASHSCOPE_BASE_URL", ""))
        self._parameters: dict[str, object] = {"format": "pcm", "sample_rate": 16000, "vad_model": "near_meeting_16k"}
        language = settings.language
        if language and language != "auto":
            self._parameters["language_hints"] = ["zh" if language == "yue" else language]
        if language == "yue":
            self._parameters["keep_dialect"] = True
        if self.model_name == MESSAGE_MODEL:
            self._parameters["intermediate_result_enabled"] = True
        self._cancel = threading.Event()
        self._partial = None
        self._session: _Session | None = None
        self._reported = ""

    def set_cancel_event(self, event: threading.Event) -> None:
        self._cancel = event

    def set_partial_callback(self, callback: Callable[[str, float, float], None]) -> None:
        self._partial = callback

    def mark_chunk_ready(self, ready_at: float) -> None:
        if self._session:
            self._session.ready_at = ready_at

    def start(self) -> None:
        self.abort()
        session = self._session = _Session(self._cancel, self._partial)
        self._reported = ""
        threading.Thread(target=self._receive, args=(session,), daemon=True, name="BailianASR").start()
        try:
            session.wait(session.started, "启动", 10.0)
        except Exception:
            self.abort()
            raise

    def _receive(self, session: _Session) -> None:
        # This thread owns only its originating session, including its callback.
        # A cancelled slow handshake can never attach to the next utterance.
        sentences: dict[int, str] = {}
        pending_sentences: set[int] = set()
        try:
            # Keep offline and local-ASR installations independent of this extra.
            from websocket import WebSocketTimeoutException, create_connection
            session.check()
            session.socket = create_connection(self._url, header={"Authorization": f"Bearer {self._key}"},
                                               timeout=10, redirect_limit=0)
            session.check()
            session.command("run-task", task_group="audio", task="asr", function="recognition",
                            model=self.model_name, parameters=self._parameters)
            while not session.stopped.is_set():
                try:
                    raw = session.socket.recv()
                except WebSocketTimeoutException:
                    # Silence can outlast a socket read deadline. start/finish
                    # enforce their own deadlines while the sender stays live.
                    continue
                if not raw:
                    raise ConnectionError("WebSocket closed")
                response = json.loads(raw)
                header = response["header"]
                if header["task_id"] != session.task_id:
                    continue
                event = header["event"]
                if event == "task-started":
                    session.started.set()
                elif event == "task-failed":
                    code = header.get("error_code", "")
                    safe_code = code if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", code) else "Unknown"
                    session.error = f"百炼语音识别失败（{safe_code}），请检查模型、密钥和业务空间"
                    break
                elif event == "result-generated":
                    sentence = response["payload"]["output"]["sentence"]
                    if sentence.get("heartbeat") or "sentence_id" not in sentence:
                        continue
                    index = sentence["sentence_id"]
                    sentences[index] = sentence["text"]
                    if sentence.get("sentence_end") or not sentence["text"]:
                        pending_sentences.discard(index)
                    else:
                        pending_sentences.add(index)
                    text = _join_sentences(sentences)
                    if text != session.text:
                        session.text = text
                        if session.partial and not session.cancelled.is_set() and not session.stopped.is_set():
                            session.partial(text, session.ready_at, session.sent_bytes / 32_000)
                elif event == "task-finished":
                    if not session.finishing.is_set() or pending_sentences:
                        session.error = "百炼语音识别在返回完整结果前结束"
                    break
        except ImportError:
            session.error = "百炼语音识别需要 websocket-client，请安装 asr-bailian 依赖"
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            reason = f"HTTP {status}" if isinstance(status, int) else type(exc).__name__
            session.error = f"百炼语音识别连接失败（{reason}）"
        finally:
            try:
                if session.socket is not None:
                    session.socket.shutdown()
            finally:
                session.done.set()
                session.started.set()  # Also release a failed handshake.

    def _active(self) -> _Session:
        if self._session is None:
            raise RuntimeError("百炼语音识别尚未启动")
        self._session.check()
        return self._session

    def _send(self, session: _Session, data: bytes) -> None:
        session.check()
        session.sent_bytes += len(data)
        session.socket.send_binary(data)

    def feed(self, audio_16k: np.ndarray) -> str | None:
        session = self._active()
        session.pending_pcm.extend(encode_pcm16le(audio_16k))
        while len(session.pending_pcm) >= _PACKET_BYTES:
            self._send(session, bytes(session.pending_pcm[:_PACKET_BYTES]))
            del session.pending_pcm[:_PACKET_BYTES]
        text = session.text
        if not session.partial and text != self._reported:
            self._reported = text
            return text
        return None

    def finish(self, final_audio_16k: np.ndarray) -> str:
        del final_audio_16k  # The controller passes the full recording, already fed.
        try:
            session = self._active()
            if session.pending_pcm:
                self._send(session, bytes(session.pending_pcm))
            session.finishing.set()
            session.command("finish-task")
            session.wait(session.done, "等待最终结果", 12.0)
            return trim_short_utterance_end(session.text)
        finally:
            self.abort()

    def abort(self) -> None:
        session, self._session = self._session, None
        if session:
            session.stopped.set()
            if session.socket is not None and not session.done.is_set():
                try:
                    session.socket.abort()  # Wake recv; the receiver closes its own socket.
                except (AttributeError, OSError):
                    pass  # Already closed by the receiver.


def create_streaming_backend(settings: ASRBackendSettings) -> BailianStreamingASR:
    return BailianStreamingASR(settings)
