"""Touchpad double-click stroke sessions, bound to one focused editor.

Activation uses Tap's input-method preparation without starting idle voice.
"""
from __future__ import annotations

import time
import uuid

from PySide6.QtCore import QObject, QTimer

from ..stroke_display import TRAIL_STYLE


class StrokeSessionController(QObject):
    def __init__(self, touchpad):
        super().__init__(touchpad)
        self.tp, self.owner = touchpad, touchpad.owner
        self.inline = self.owner._inline_input
        self.phase = "idle"
        self.local = False
        self._generation = 0
        self._connection = None
        self._origin = None
        self._initial_context = ""
        self._token = ""
        self._deadline = 0.
        self._output_started = False
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._poll)
        # _accept already runs on Qt. Preserve lifecycle order so an old idle
        # state cannot arrive after the next successful readiness handshake.
        self.inline.strokeEvent.connect(self._native_event)
        self.inline.settled.connect(lambda *_: QTimer.singleShot(0, self._poll))
        self.inline.interrupted.connect(lambda: QTimer.singleShot(0, self._poll))
        touchpad.strokeTraceChanged.connect(self._trace)
        touchpad.changed.connect(self._output_changed)

    @property
    def engaged(self):
        return self.phase != "idle"

    @property
    def _source(self):
        preparation = self.inline._input_preparation
        if preparation and preparation[0] == self._token:
            return self.inline._source_activation
        return None

    def _set_phase(self, phase):
        previous, self.phase = self.phase, phase
        self.owner._event_log("STROKE_SESSION", _live_message="", phase=phase,
                              previous=previous, local=self.local,
                              application=self._origin[0] if self._origin else "")

    def fail(self, error):
        self.cancel("笔画输入启动失败：" + str(error))

    @property
    def blocks_voice(self):
        return self.phase in {"source", "native", "starting", "running"}

    def consume(self, event, connection):
        name = getattr(event, "name", "")
        if self.engaged and name in {"tap", "swipe-left", "swipe-right", "swipe-up", "swipe-down"}:
            return self.phase != "running"
        return False

    def request(self, target=None):
        self._toggle(self.owner._disconnect_event, target)

    def _toggle(self, connection, event):
        if (connection is not self.owner._disconnect_event or connection.is_set()
                or self.tp._closed or self.owner._quitting):
            return
        if self.engaged or (self.tp.active and self.tp.inputMode == "stroke"):
            self.cancel("已回到鼠标模式")
            return
        ring = self.owner._ring_gestures
        if (not self.tp.active or not self.tp.available or self.tp.busy
                or self.owner._proximity.gestures_blocked or ring.windowSelector.blocked.is_set()
                or ring.textFields.picker.active.is_set() or ring.textFields.applying.is_set()):
            return
        self._generation += 1
        self._connection = connection
        self._token = uuid.uuid4().hex
        self._origin = ((event["bundle"], event["pid"]) if event else self.inline._foreground_identity())
        self.local = self.tp._local_stroke_focus
        if not self.local and self.inline._foreground_identity() != self._origin:
            self.cancel("已切换应用，笔画唤起已取消")
            return
        self._timer.start()
        if self.tp._speech_busy():
            self._finish_voice()
        else:
            self._select_source()

    def _finish_voice(self):
        if not self.owner._inline_enabled():
            self.cancel("请先结束当前语音，再唤起笔画输入")
            return
        self._set_phase("voice")
        self._deadline = time.monotonic() + 15
        self.owner._cancel_inline_requests("切换到笔画输入，按听写结束")
        self.inline.finish_for_stroke()

    def _select_source(self):
        if self.local:
            self._begin_output()
            return
        self._set_phase("source")
        self._deadline = 0  # Tap's shared preparation owns its bounded timeout.
        self.tp._sync_voice_gate()
        self.tp.changed.emit()
        try:
            self.inline.release_completed_shortcut()
            generation = self._generation
            self.inline.prepare_input_method(
                self._token, self._origin,
                lambda message: self._input_ready(generation, message),
                lambda error: self._preparation_failed(generation, error))
        except Exception as exc:
            self.fail(exc)

    def _preparation_failed(self, generation, error):
        if generation == self._generation and self.phase == "source":
            self.cancel(error)

    def _input_ready(self, generation, message):
        if generation != self._generation or self.phase != "source":
            return
        self.inline.stroke_target = (message["epoch"], message["client_id"], self._token)
        self._deadline = time.monotonic() + 3
        self._set_phase("native")
        if not self.inline.send_stroke("stroke_begin", gesture_label=self.tp.strokeGestureLabel):
            self.cancel("输入法连接已断开")

    def _native_event(self, message):
        if not self.engaged or self.local:
            return
        kind = message.get("type")
        if kind != "connected" and message.get("epoch") != self.inline._epoch:
            return
        if kind in {"connected", "disconnected"}:
            target = self.inline.stroke_target
            if target and (kind == "disconnected" or message.get("epoch") != target[0]):
                self.cancel("输入法连接已改变，笔画输入已退出")
            return
        if kind == "stroke_ready" and self.phase == "native":
            if (message.get("stroke_session") != self._token or not self.inline.stroke_target
                    or message.get("client_id") != self.inline.stroke_target[1]):
                return
            if not message.get("success") or message.get("panel_visible") is False:
                self.cancel(str(message.get("error") or "输入法未能进入笔画模式"))
                return
            self._initial_context = str(message.get("context") or "")[-4:]
            if self.allow_input():
                self._begin_output()
        elif kind == "stroke_cleared" and message.get("stroke_session") == self._token:
            self.cancel("笔画输入已退出")
        elif kind == "stroke_ink_cleared" and message.get("stroke_session") == self._token:
            self.tp._code = ""
            self.tp._stroke_context = ""
            self.tp.strokeTraceChanged.emit([], False)
            self.tp._refresh_candidates()
        elif kind == "state" and self.phase in {"native", "starting", "running"}:
            target = self.inline.stroke_target
            if target and (message.get("client_id") != target[1] or not message.get("ready")):
                self.cancel("已离开当前输入框，笔画输入已退出")

    def _begin_output(self):
        self._set_phase("starting")
        self._output_started = False
        self.tp.setInputMode("stroke")
        if self.tp.inputMode != "stroke":
            self.cancel("当前输入尚未结束")
            return
        self.tp._local_stroke_input = self.local
        if not self.local:
            self.tp._stroke_context = self._initial_context
        self.tp._sync_voice_gate()
        self._output_started = True
        self._output_changed()

    def _output_changed(self):
        if (self.phase == "starting" and self._output_started and self.tp.state == "running"
                and self.tp._run and self.tp._run.output_ready):
            self._set_phase("running")
            self._deadline = 0
            self.tp._refresh_candidates()
        elif self._output_started and self.phase in {"starting", "running"} and self.tp.state in {"error", "stopping"}:
            self.cancel(self.tp.message, stop_output=False)

    def _poll(self):
        if not self.engaged:
            return
        if (self._connection is not self.owner._disconnect_event or self._connection.is_set()
                or self.owner._proximity.gestures_blocked):
            self.cancel("笔画输入已退出")
            return
        if self._deadline and time.monotonic() >= self._deadline:
            self.cancel("笔画输入准备超时，请更新输入法组件后重试")
            return
        if self.phase == "voice" and not self.tp._speech_busy():
            if self.inline._view.get("phase") == "error":
                self.cancel(self.inline.error or "语音未能完成，暂未进入笔画")
                return
            self._select_source()
        if self.local:
            if not self.tp._local_stroke_focus:
                self.cancel("已离开试写框，笔画输入已退出")
            return
        if self.phase == "source":
            preparation = self.inline._input_preparation
            if not preparation or preparation[0] != self._token:
                self.cancel("输入法准备已取消")
            return  # Tap's preparation handles helper focus, readiness and reconnects.
        if self.phase in {"native", "starting", "running"} and self.allow_input():
            self.inline._bridge.send({"type": "ping"})

    def allow_input(self):
        """Native activation owns the editor, exactly as it does for Tap voice."""
        if not self.engaged or self.local:
            return True
        generation = self._generation
        foreground = self.inline._foreground_identity()
        if generation != self._generation:
            return False
        target = self.inline.stroke_target
        if (not target or foreground != self._origin or target[:2] != (self.inline._epoch, self.inline._client_id)
                or not self.inline.ready):
            self.cancel("已离开当前输入框，笔画输入已退出")
            return False
        return True

    def _trace(self, points, finished):
        if self.phase == "running" and not self.local:
            self.inline.send_stroke("stroke_trace", points=points, finished=finished, style=TRAIL_STYLE)

    def cancel(self, reason="", *, stop_output=True):
        previous = self.phase
        self._generation += 1
        self.phase = "idle"
        self._timer.stop()
        if previous != "idle" or reason:
            self.owner._event_log("STROKE_SESSION", _live_message=reason,
                                  phase="idle", previous=previous, reason=reason)
        self.inline.cancel_input_preparation(self._token)
        self.inline.send_stroke("stroke_end")
        self.inline.stroke_target = None
        self.inline._stroke_request = None
        self.inline._stroke_timer.stop()
        self._initial_context = ""
        self.tp._code = self.tp._pending_stroke_commit = ""
        if not self.local:
            self.tp._stroke_context = ""
        self.tp._prediction_mode = False
        self.tp._candidates = []
        self.tp.strokeTraceChanged.emit([], False)
        if stop_output and self.tp._run:
            if self.tp.available and not self.owner._proximity.gestures_blocked:
                self.tp.restore_pointer()
            else:
                self.tp.stop(reason)
        self.tp._sync_voice_gate()
        if reason:
            self.tp._commit_message = reason
            self.tp._message = reason
        self.tp.changed.emit()

    def close(self):
        self.cancel(stop_output=False)
