"""One background match request, bound to the native handwriting session."""
from queue import Empty, SimpleQueue
import threading
import uuid

from PySide6.QtCore import QObject, QTimer

from ...voice.hotwords import STROKE_CONTEXT_CHARS


class HandwritingCorrection(QObject):
    def __init__(self, touchpad):
        super().__init__(touchpad)
        self.tp = touchpad
        self.inline = touchpad.owner._inline_input
        self.state = {}
        self.job = None
        self.thread = None
        self.results = SimpleQueue()
        self.key_requests = set()
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._poll)
        self.inline.strokeEvent.connect(self._receive)

    def reset(self):
        self.state = {}
        self.job = None
        self.key_requests.clear()
        self.tp.changed.emit()

    def request(self):
        if not self.tp.handwritingCorrectionActive:
            return
        if self.tp._local_stroke_input:
            self.tp._commit_message = '请在目标文本框中使用修正'
        elif self.tp._code or self.tp._pending_stroke_commit:
            self.tp._commit_message = '请先确认正在书写的字'
        elif not self.state.get('busy'):
            self.inline.send_stroke('stroke_correction_request', request_id=uuid.uuid4().hex)
        self.tp.changed.emit()

    def undo(self):
        if (self.tp.handwritingCorrectionCanUndo
                and not self.tp._local_stroke_input and self.tp.strokeSession.phase == 'running'
                and self.tp.strokeSession.allow_input()):
            self.inline.send_stroke('stroke_correction_undo')

    def _receive(self, message):
        target = self.inline.stroke_target
        if (not target or tuple(message.get(k) for k in ('epoch', 'client_id', 'stroke_session')) != target
                or self.tp._closed):
            return
        kind = message.get('type')
        if kind == 'stroke_correction_key':
            request = message.get('request_id')
            if not request or request in self.key_requests or not self.state.get('busy'):
                return
            self.key_requests.add(request)
            error = ''
            try:
                origin = self.tp.strokeSession._origin
                if (not origin or origin[0] != message.get('application')
                        or not self.tp.strokeSession.allow_input() or self.inline._foreground_identity() != origin):
                    raise RuntimeError('输入目标已切换，未发送快捷键')
                from ...platforms.macos.wechat_native_keys import send_wechat_key, send_input_method_key
                args = (message.get('command'), message.get('count'), message.get('event_tag'))
                if origin[0] == 'com.tencent.xinWeChat':
                    send_wechat_key(*args, expected_pid=origin[1])
                else:
                    send_input_method_key(origin[0], *args, expected_pid=origin[1])
            except Exception as exc:
                error = str(exc)
            if target == self.inline.stroke_target:
                self.inline.send_stroke('stroke_correction_key_result', request_id=request, error=error)
        elif kind == 'stroke_correction_state':
            self.state = message
            context = message.get('context')
            if self.tp.windowsInputPrototype and isinstance(context, str):
                self.tp._stroke_context = context[-STROKE_CONTEXT_CHARS:]
                self.tp._refresh_candidates()
            if message.get('message'):
                self.tp._commit_message = message['message']
            self.tp.changed.emit()
        elif kind == 'stroke_correction_requested':
            request = message.get('request_id')
            if not request or not self.tp.handwritingCorrectionActive:
                return
            if self.thread and self.thread.is_alive():
                self.inline.send_stroke('stroke_correction_result', request_id=request,
                                        error='上次修正仍在处理，请稍后重试')
                return
            try:
                settings = self.tp.owner._voice_llm_settings()
                processor = self.tp.owner._text_processing_worker._processor
                original, word = message['original'], message['handwriting']
            except Exception as exc:
                self.inline.send_stroke('stroke_correction_result', request_id=request, error=str(exc))
                return
            job = self.job = (target, request)
            results = self.results
            def run():
                try:
                    fragment = processor.locate_handwriting_correction(original, word, settings)
                    results.put((job, fragment, ''))
                except Exception as exc:
                    results.put((job, '', str(exc)))
            self.thread = threading.Thread(target=run, daemon=True, name='handwriting-correction')
            self.thread.start()
            self.timer.start()

    def _poll(self):
        while True:
            try:
                job, fragment, error = self.results.get_nowait()
            except Empty:
                break
            if (job == self.job and job[0] == self.inline.stroke_target
                    and self.tp.handwritingCorrectionActive and not self.tp._closed):
                self.job = None
                self.inline.send_stroke('stroke_correction_result', request_id=job[1],
                                        original_text=fragment, error=error)
        if self.thread is None or not self.thread.is_alive():
            self.timer.stop()

    def close(self):
        self.job = None
        self.timer.stop()
