"""Touchpad two-finger double-click stroke sessions, bound to one focused editor.

Activation uses Tap's input-method preparation without starting idle voice.
"""
from __future__ import annotations

import time
import uuid
import math

from PySide6.QtCore import QObject, Property, QRect, QTimer, Signal, Slot

from ...touchpad.stroke.stroke_display import TRAIL_STYLE
from ...voice.hotwords import STROKE_CONTEXT_CHARS


def stroke_popup_geometry(caret, area, correction_gutter=0):
    left, top, _, bottom = caret
    margin, gap = 8, 6
    # The native window must contain the menu's overflow too: a 60px left
    # slider, shadows, and the raised punctuation palette (including its
    # opening animation). Keep these in sync with strokePopup in Main.qml.
    horizontal_gutters, vertical_gutters = 76 + correction_gutter, 24
    side = min(max(280, min(420, round(area.height() * .28))),
               area.width() - 2 * margin - horizontal_gutters,
               math.floor((area.height() - 2 * margin - vertical_gutters) / 1.4))
    if side <= 0:
        return QRect()
    width = side + horizontal_gutters
    height = side + math.ceil(side * .4) + vertical_gutters
    min_x, min_y = area.x() + margin, area.y() + margin
    max_x = area.x() + area.width() - margin - width
    max_y = area.y() + area.height() - margin - height
    below, above = math.ceil(bottom + gap), math.floor(top - gap - height)
    if below <= max_y:
        y = below
    elif above >= min_y:
        y = above
    else:
        y = above if top - min_y > max_y + height - bottom else below
    return QRect(max(min_x, min(max_x, round(left) - 68)), max(min_y, min(max_y, y)), width, height)


class StrokeSessionController(QObject):
    caretChanged = Signal()
    gestures = {"tap", "double-tap", "index-pinch", "double-index-pinch",
                "swipe-left", "swipe-right", "swipe-up", "swipe-down"}

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
        self.word_completions = False
        self.native_correction = False
        self._token = ""
        self._deadline = 0.
        self._output_started = False
        self._restore_qos = None
        self._caret = []
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

    @Property(list, notify=caretChanged)
    def caret(self):
        return self._caret

    def _set_caret(self, caret):
        if self._caret != caret:
            self._caret = caret
            self.caretChanged.emit()

    def _screen_caret(self, caret):
        from ..shared.windows_caret import screen_caret
        return screen_caret(caret)

    @Slot(list, result=QRect)
    @Slot(list, int, result=QRect)
    def popupGeometry(self, caret, correction_gutter=0):
        target = self._screen_caret(caret)
        return stroke_popup_geometry(*target, correction_gutter) if target else QRect()

    @Slot(list, result=QRect)
    def strokeSequenceBounds(self, caret):
        target = self._screen_caret(caret)
        if not target:
            return QRect()
        return target[1].adjusted(4, 4, -4, -4)

    @Slot(list, int, int, result=QRect)
    def strokeSequenceGeometry(self, caret, width, height):
        target = self._screen_caret(caret)
        if not target:
            return QRect()
        (_, top, right, _), area = target
        area = area.adjusted(4, 4, -4, -4)
        width, height = min(width, area.width()), min(height, area.height())
        # Prefer the caret's right edge; move only this surface inward when
        # needed to show the whole sequence at a screen edge.
        x = max(area.left(), min(math.ceil(right + 4), area.right() + 1 - width))
        y = max(area.top(), min(math.floor(top), area.bottom() + 1 - height))
        return QRect(x, y, width, height)

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
        if self.engaged and name in self.gestures:
            return self.phase != "running"
        return False

    def request(self, target=None):
        self._toggle(self.owner._disconnect_event, target)

    def request_local(self):
        self._toggle(self.owner._disconnect_event, None, local=True)

    def _toggle(self, connection, event, *, local=False):
        if (not self.tp.strokeSupported or connection is not self.owner._disconnect_event or connection.is_set()
                or self.tp._closed or self.owner._quitting):
            return
        if self.engaged or (self.tp.active and self.tp.inputMode == "stroke"):
            self.cancel("已回到鼠标模式")
            return
        ring = self.owner._ring_gestures
        if (not self.tp.active or not self.tp.available or self.tp.busy
                or self.owner._proximity.gestures_blocked or ring.windowSelector.blocked.is_set()):
            return
        self._generation += 1
        self._connection = connection
        self._token = uuid.uuid4().hex
        if local:
            self.local = True
            self._origin = None
        elif self.tp.externalStrokeSupported:
            self.local = self.tp._local_stroke_focus
            if self.tp.windowsInputPrototype:
                self.local = bool(event and event.get("local") and self.tp._local_input_since is not None)
            self._origin = None
            if not self.local:
                self._origin = ((event["bundle"], event["pid"]) if event and "pid" in event
                                else self.inline._foreground_identity())
        else:
            self.local = self.tp._local_stroke_focus
            if not self.local:
                self.cancel('Windows 请在程序内试写框输入笔画')
                return
            self._origin = None
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
        options = {}
        if self.tp.handwritingCorrectionSupported:
            options['correction_enabled'] = self.tp.handwritingCorrectionEnabled
        if not self.inline.send_stroke("stroke_begin", gesture_label=self.tp.strokeGestureLabel,
                                       tap_hint=self.tp.strokeTapHint, **options):
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
            self.word_completions = message.get("word_completions") is True
            self.native_correction = message.get("handwriting_correction") is True
            if self.tp.windowsInputPrototype and self.native_correction:
                self.inline.send_stroke("stroke_correction_configure", enabled=self.tp.handwritingCorrectionEnabled)
            self._initial_context = str(message.get("context") or "")[-STROKE_CONTEXT_CHARS:]
            self._set_caret(message.get("caret") or [])
            if self.allow_input():
                self._begin_output()
        elif kind == "stroke_cleared" and message.get("stroke_session") == self._token:
            self.cancel("笔画输入已退出")
        elif kind == "stroke_delete_result" and self.phase == "running":
            target = (message.get("epoch"), message.get("client_id"), message.get("stroke_session"))
            if self.inline.stroke_target == target:
                self.tp._editor_delete_result(message)
        elif kind == "stroke_ink_cleared" and message.get("stroke_session") == self._token:
            self.tp._code = ""
            self.tp._clear_stroke_evidence()
            self.tp._stroke_context = ""
            self.tp.strokeTraceChanged.emit([], False)
            self.tp._refresh_candidates()
        elif kind == "state" and self.phase in {"native", "starting", "running"}:
            target = self.inline.stroke_target
            if target and (message.get("client_id") != target[1] or not message.get("ready")):
                self.cancel("已离开当前输入框，笔画输入已退出")
            elif "caret" in message and self.tp.windowsInputPrototype:
                caret = message["caret"]
                # Editors can temporarily return TS_E_NOLAYOUT after a write.
                # The same writable context is still active; retain its last
                # valid anchor until layout catches up. cancel() clears it on
                # real focus loss or session retirement.
                if len(caret) == 4 and any(caret):
                    self._set_caret(caret)

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
            if self.tp.windowsInputPrototype and not self.local:
                from ...voice.ime.windows_bridge import begin_interactive_stroke
                try:
                    self._restore_qos = begin_interactive_stroke()
                except (AttributeError, OSError) as error:
                    self.owner._event_log("STROKE_WINDOWS_QOS", _live_message="", error=str(error))
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
        if self.phase == "running" and not self.local and not self.tp.windowsInputPrototype:
            self.inline.send_stroke("stroke_trace", points=points, finished=finished, style=TRAIL_STYLE)

    def cancel(self, reason="", *, stop_output=True):
        previous = self.phase
        self._generation += 1
        self.phase = "idle"
        self.native_correction = False
        self._set_caret([])
        self._timer.stop()
        restore_qos, self._restore_qos = self._restore_qos, None
        if restore_qos is not None:
            try:
                restore_qos()
            except OSError as error:
                self.owner._event_log("STROKE_WINDOWS_QOS", _live_message="", error=str(error))
        if previous != "idle" or reason:
            self.owner._event_log("STROKE_SESSION", _live_message=reason,
                                  phase="idle", previous=previous, reason=reason)
        self.inline.cancel_input_preparation(self._token)
        self.inline.send_stroke("stroke_end")
        self.inline.stroke_target = None
        self.tp._handwriting_correction.reset()
        self.inline._stroke_request = None
        self.inline._stroke_timer.stop()
        self._initial_context = ""
        self.word_completions = False
        self.tp._code = self.tp._pending_stroke_commit = ""
        self.tp._clear_stroke_evidence()
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
