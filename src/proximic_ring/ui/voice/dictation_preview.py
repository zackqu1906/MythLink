"""Speculative dictation suggestions. Only a native write receipt marks one applied."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import json
import threading
from time import perf_counter

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from ...text_processing.context_preparation import resolve_model_context
from ...text_processing.llm import OpenAICompatibleTextProcessor


@dataclass
class PreviewRequest:
    token: str
    epoch: str
    utterance: str
    revision: int
    raw: str
    interaction: str
    preflight_block: str = ""
    started: float = field(default_factory=perf_counter)
    cancelled: threading.Event = field(default_factory=threading.Event)
    invalidated_at: float = 0.
    shown: bool = False
    accepting: bool = False
    future: object = None
    outcome: object = None


class DictationPreviewController(QObject):
    _result = Signal(object, object)
    _accept = Signal(object, object)
    _dismiss = Signal(object, str)
    _recordError = Signal(str)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.inline = owner._inline_input
        self.current = None
        self._awaiting_write = {}
        self._inflight = {}
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="DictationPreview")
        self._history = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PreviewHistory")
        self._processor = OpenAICompatibleTextProcessor()
        self._closed = False
        self._result.connect(self._deliver, Qt.QueuedConnection)
        self._accept.connect(self._apply, Qt.QueuedConnection)
        self._dismiss.connect(self.dismiss, Qt.QueuedConnection)
        self._recordError.connect(owner._append_log, Qt.QueuedConnection)
        self.inline.dictationPreviewEvent.connect(self._event)
        self.inline.actionRequested.connect(self._action_requested)
        self.inline.changed.connect(self._connection_changed)

    def _record(self, job, **fields):
        if self._closed or not job.interaction:
            return
        def write():
            try:
                self.owner._modification_dataset.record_dictation_preview(job.interaction, job.token, fields)
            except Exception as exc:
                self._recordError.emit(f"听写润色记录保存失败：{exc}")
        self._history.submit(write)

    def _action_requested(self, action):
        if action != "accept_dictation_preview":
            self.dismiss(reason="next_sentence" if action == "begin" else action)

    def _connection_changed(self):
        job = self.current
        if job and job.epoch != self.inline._epoch:
            self.dismiss(job, "disconnected")

    def _send(self, kind, job, **fields):
        if self.inline._bridge and self.inline._epoch == job.epoch:
            return self.inline._bridge.send(dict(type=kind, token=job.token, **fields))
        return False

    def dismiss(self, job=None, reason="mode_changed"):
        job = job or self.current
        if job is None or job is not self.current:
            return
        self.current = None
        job.invalidated_at = job.invalidated_at or perf_counter()
        job.cancelled.set()
        output_blocked = reason in {"unchanged", "empty", "multiple_lines", "structured_output", "preview_unavailable"}
        expired_before_use = (not job.shown or job.accepting) and reason in {"user_input", "next_sentence", "target_changed"}
        fields = dict(reason=reason, rule_blocked=bool(job.preflight_block) or output_blocked or expired_before_use
                      or reason == "write_unconfirmed", eligible_ms=round((job.invalidated_at - job.started) * 1000))
        if job.future is not None and job.future.cancel():
            self._inflight.pop(job.token, None)
            fields["generation"] = "not_started"
        self._record(job, **fields)
        self._send("dictation_preview_dismiss", job)

    def _event(self, message):
        token = message.get("token")
        if not token or self._closed:
            return
        job = self.current
        if message["type"] == "dictation_preview_state":
            reason = message.get("reason", "")
            if reason in {"applied", "write_unconfirmed"}:
                writing = self._awaiting_write.pop(token, None)
                if writing:
                    self._record(writing, applied=reason == "applied", reason=reason, rule_blocked=reason == "write_unconfirmed", detail=message.get("detail", ""))
            if job is not None and job.token == token:
                if message.get("visible") is True:
                    job.shown = True
                    self._record(job, shown=True, shown_after_ms=round((perf_counter()-job.started)*1000))
                elif reason == "applying":
                    self.current = None
                else:
                    self.dismiss(job, reason or "target_changed")
            return

        if not message.get("raw") or (job and job.token == token):
            return
        identity = (message["epoch"], message["client_id"], message["utterance_id"])
        current_sentence = identity == (self.inline._epoch, self.inline._client_id, self.inline._utterance_id)
        capture = self.owner._voice_capture or {}
        if capture.get("key") != identity:
            capture = {}
        session_id = self.inline._session_id if current_sentence else self.inline._utterance_sessions.get(identity[2], 0)
        interaction = capture.get("interaction_id") or self.owner._modification_dataset.interaction_id_for_session(session_id)
        job = PreviewRequest(token, message["epoch"], message["utterance_id"], message["revision"],
                             message["raw"], interaction, message.get("blocked", ""))
        settings = replace(self.owner._llm_settings(), enabled=True)
        if not current_sentence or not self.inline.automatic_polish or not self.owner._desktop_output or self.owner._quitting:
            self._record(job, generation="not_started", raw=job.raw, shown=False, accept_requested=False,
                         applied=False, rule_blocked=True, reason="next_sentence" if not current_sentence else "mode_changed",
                         model=settings.model)
            self._send("dictation_preview_dismiss", job)
            return
        self.dismiss(reason="next_sentence")
        self._record(job, generation="pending", raw=job.raw, shown=False,
                     accept_requested=False, applied=False, rule_blocked=bool(job.preflight_block), preflight_block=job.preflight_block, reason=job.preflight_block, output=None, model=settings.model)
        self.current = job
        self._inflight[job.token] = job
        job.future = self._pool.submit(self._generate, job, settings, message.get("before_cursor", ""), capture.get("model_context"))

    def _generate(self, job, settings, prefix, captured):
        model_start = None
        try:
            context = resolve_model_context(captured, cancelled=job.cancelled.is_set) if captured else {}
            if job.cancelled.is_set():
                result = dict(generation="not_started")
            else:
                model_start = perf_counter()
                candidates = self.owner._hotwords.store.polish_candidates(job.raw)
                output = self._processor.suggest_dictation(settings, job.raw, prefix, context, candidates)
                result = dict(generation="completed", output=output)
        except Exception as exc:
            result = dict(generation="failed" if model_start or not job.cancelled.is_set() else "not_started",
                          generation_error=str(exc))
        finished = perf_counter()
        if model_start is not None:
            result["model_ms"] = round((finished-model_start)*1000)
        result["elapsed_ms"] = round((finished-job.started)*1000)
        job.outcome = (result, finished)
        self._result.emit(job, job.outcome)

    def _deliver(self, job, outcome):
        if self._closed:
            return
        self._inflight.pop(job.token, None)
        result, finished = outcome
        current = job is self.current and not job.cancelled.is_set()
        result["in_time"] = (not job.invalidated_at or finished <= job.invalidated_at) if result["generation"] == "completed" else None
        self._record(job, **result)
        if not current:
            return
        text = result.get("output")
        if job.preflight_block:
            self.dismiss(job, job.preflight_block)
        elif result["generation"] != "completed":
            self.dismiss(job, "generation_failed")
        elif not text or text == job.raw or len(text.splitlines()) != 1:
            reason = "empty" if not text else "unchanged" if text == job.raw else "multiple_lines"
            self.dismiss(job, reason)
        elif self._structured_output(text):
            self.dismiss(job, "structured_output")
        elif not self._send("dictation_preview_result", job, text=text):
            self.dismiss(job, "disconnected")

    @staticmethod
    def _structured_output(text):
        if text.startswith(("```", "{", "[")):
            try:
                return text.startswith("```") or isinstance(json.loads(text), (list, dict))
            except ValueError:
                return False
        return False

    def consume(self, gesture, connection, *, audio_busy):
        """The Ring worker claims the physical down swipe only while the preview is visible."""
        job = self.current
        if job is None or audio_busy or job.cancelled.is_set():
            return False
        if gesture == "swipe-down" and job.shown:
            if not job.accepting:
                job.accepting = True
                failure = self.inline.resume_gesture_failure()
                self._accept.emit(job, (connection, failure))
            return True
        return False

    def next_sentence(self):
        job = self.current
        if job and not job.cancelled.is_set():
            job.invalidated_at = perf_counter()
            job.cancelled.set()
            self._dismiss.emit(job, "next_sentence")

    def _write_timeout(self, token):
        job = self._awaiting_write.pop(token, None)
        if job:
            self._record(job, applied=None, reason="no_write_receipt")
            self.dismiss(job, "no_write_receipt")

    def _apply(self, job, outcome):
        if self._closed:
            return
        connection, failure = outcome
        self._record(job, accept_requested=True)
        try:
            if (not failure and job is self.current and not job.cancelled.is_set() and job.shown
                    and self.inline.automatic_polish and connection is self.owner._disconnect_event
                    and not connection.is_set() and job.epoch == self.inline._epoch
                    and job.utterance == self.inline._utterance_id
                    and job.revision == self.inline._view.get("revision")
                    and self.inline._view.get("raw") == job.raw):
                if self.inline.accept_dictation_preview(job.token, job.revision):
                    self._awaiting_write[job.token] = job
                    QTimer.singleShot(12000, lambda: self._write_timeout(job.token))
                    return
            if job is self.current:
                self.dismiss(job, "target_changed")
        finally:
            self.inline.finish_resumed_gesture()

    def close(self):
        if self._closed:
            return
        self.dismiss(reason="closed")
        for token in list(self._awaiting_write):
            self._write_timeout(token)
        for job in list(self._inflight.values()):
            if job.outcome is not None:
                self._deliver(job, job.outcome)
            else:
                self._record(job, generation="closed_without_result")
        self._inflight.clear()
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._history.shutdown(wait=False)
        self._processor.close()
