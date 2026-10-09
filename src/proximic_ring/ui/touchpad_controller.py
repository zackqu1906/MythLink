"""GUI lifecycle for the existing Ring session; high-rate events never enter Qt."""
from __future__ import annotations

import math
import sys
import time
from types import SimpleNamespace

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication

from ..touchpad_mouse import TouchpadMouseOutput
from ..touchpad_clicks import TouchpadClicks
from ..touchpad_diagnostics import TouchpadDiagnostics
from ..touchpad_strokes import TouchpadStrokeOutput
from ..stroke_input import StrokeDictionary, SYMBOLS, NAMES
from ..stroke_display import TRAIL_STYLE, normalized_trace, standard_stroke
from ..mac_permissions import MacPermissionError


class TouchpadController(QObject):
    changed = Signal()
    localCharacterCommitted = Signal(str)
    localBackspaceRequested = Signal()
    strokeTraceChanged = Signal(list, bool)
    sourceReady = Signal(object, object)
    _event = Signal(object, str, object)

    def __init__(self, owner, *, output_factory=TouchpadMouseOutput):
        super().__init__(owner)
        self.owner, self.output_factory = owner, output_factory
        self._source = self._connection = self._run = None
        self._closed = False
        self._state = "idle"
        self._message = "开启后，用 Ring 移动系统指针并轻触点击。"
        self._gain = self._number("gain", 1., .25, 3.)
        owner._settings.remove("touchpad/seconds")
        self._clicks = owner._settings.value("touchpad/clicks", True, type=bool)
        self._invert_y = owner._settings.value("touchpad/invertY", False, type=bool)
        self._warm = False
        self.stroke_output_factory = TouchpadStrokeOutput
        self._mode = "pointer"
        for key in ("touchpad/inputMode", "touchpad/strokeGesture", "touchpad/strokeEnabled"):
            owner._settings.remove(key)
        self._dictionary = self._predictor = None
        self._code = self._last_stroke = self._commit_message = ""
        self._candidates = []
        self._selected = self._page = 0
        self._pending_stroke_commit = ""
        self._local_stroke_input = self._prediction_mode = False
        self._stroke_context = ""
        self._stroke_gate = self._voice_was_enabled = False
        self._standard_trace = False
        self._local_stroke_focus = False
        self._visibility_epoch = 0
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._visibility_changed)
        from .stroke_session_controller import StrokeSessionController
        self.strokeSession = StrokeSessionController(self)
        owner._inline_input.strokeCommitted.connect(self._stroke_committed)
        owner._inline_input.strokeInvalidated.connect(self._invalidate_strokes)
        self.sourceReady.connect(self.attach, Qt.QueuedConnection)
        self._event.connect(self._receive, Qt.QueuedConnection)
        owner.connectedChanged.connect(self._connection_changed)
        owner.busyChanged.connect(self.changed)
        owner.gestureSettingsChanged.connect(self.changed)

    def _number(self, key, default, low, high):
        try:
            value = float(self.owner._settings.value("touchpad/" + key, default))
            return max(low, min(high, value)) if math.isfinite(value) else default
        except (ValueError, TypeError):
            return default

    def _visibility_changed(self, state):
        self._visibility_epoch += 1
        if self._run is not None:
            self.owner._event_log('TOUCHPAD_VISIBILITY', _live_message='',
                                  foreground=state == Qt.ApplicationActive, mode=self._mode)

    @Property(bool, constant=True)
    def supported(self): return sys.platform == "darwin"

    @Property(bool, notify=changed)
    def available(self):
        return (self.supported and not self._closed and self.owner.connected
                and not self.owner.busy and self._source is not None
                and self._connection is self.owner._disconnect_event
                and not self._connection.is_set())

    @Property(bool, notify=changed)
    def active(self): return self._state in {"starting", "running"}

    @Property(bool, notify=changed)
    def busy(self): return self._state in {"starting", "stopping"}

    @Property(str, notify=changed)
    def state(self): return self._state

    @Property(str, notify=changed)
    def title(self):
        return {"starting": "正在开启触摸板", "running": "触摸板已开启",
                "stopping": "正在停止触摸板", "error": "触摸板未能运行"}.get(self._state, "触摸板已关闭")

    @Property(str, notify=changed)
    def message(self): return self._message

    @Property(str, notify=changed)
    def inputMode(self): return self._mode

    @Property(str, constant=True)
    def strokeGestureLabel(self): return "Touchpad 双击"

    @Property(int, constant=True)
    def doubleClickIntervalMs(self): return round(TouchpadClicks.interval * 1000)

    @Property(bool, notify=changed)
    def strokePreparing(self):
        return self.strokeSession.engaged and self.strokeSession.phase != "running"

    @Slot(bool)
    def setLocalStrokeFocus(self, focused):
        self._local_stroke_focus = bool(focused)
        if not focused and self.strokeSession.engaged and self.strokeSession.local:
            self.strokeSession.cancel("已离开试写框，笔画输入已退出")

    @Property(str, notify=changed)
    def strokeCode(self): return " ".join(SYMBOLS[x] for x in self._code)

    @Property(list, notify=changed)
    def strokeCandidates(self): return self._candidates[self._page * 5:self._page * 5 + 5]

    @Property(bool, notify=changed)
    def predictionMode(self): return self._prediction_mode

    @Slot(str)
    def setStrokeContext(self, text):
        if not self._local_stroke_input:
            return
        text = text[-4:]
        if text != self._stroke_context:
            self._stroke_context = text
            if self._local_stroke_input and not self._code:
                self._refresh_candidates()

    @Property(int, notify=changed)
    def candidatePage(self): return self._page

    @Property(bool, notify=changed)
    def moreCandidates(self): return (self._page + 1) * 5 < len(self._candidates)

    @Property(int, notify=changed)
    def selectedCandidate(self): return self._selected

    @Property(str, notify=changed)
    def lastStroke(self): return self._last_stroke

    @Property("QVariantMap", constant=True)
    def strokeTrailStyle(self): return dict(TRAIL_STYLE)

    @Property(str, notify=changed)
    def commitMessage(self): return self._commit_message

    @Slot(bool)
    def setLocalStrokeInput(self, enabled):
        enabled = bool(enabled)
        if self.strokeSession.engaged and not self.strokeSession.local:
            return  # Opening the trial page must not redirect an external session.
        if enabled == self._local_stroke_input:
            return
        if self.strokeSession.engaged and self.strokeSession.local and not enabled:
            self.strokeSession.cancel("已离开试写框，笔画输入已退出")
        self._clear_external_composition()
        self._local_stroke_input = enabled
        self._stroke_context = ""
        self._code = ""
        self._pending_stroke_commit = ""
        self._refresh_candidates()

    @Property(bool, notify=changed)
    def blocksVoice(self): return self._stroke_gate

    def _speech_busy(self):
        return self.owner._ring_gestures.speech_busy()

    def _sync_voice_gate(self):
        blocked = (self.strokeSession.blocks_voice or (self._mode == "stroke" and
                   (self.active or self.busy or bool(self._code) or bool(self._pending_stroke_commit))))
        if blocked == self._stroke_gate:
            return
        self._stroke_gate = blocked
        self.owner._inline_input.stroke_mode = blocked
        if blocked:
            self._voice_was_enabled = (self.owner._recognition_event.is_set()
                                       or self.owner._interaction_recognition_suspended)
            self.owner._recognition_event.clear()
        else:
            if (self.owner.recognitionEnabled
                    and not self._closed and not self.owner._quitting
                    and self.owner.connected and not self.owner._disconnect_event.is_set()
                    and not self.owner._proximity.gestures_blocked
                    and not self.owner._interaction_recognition_suspended):
                self.owner._recognition_event.set()
            self._voice_was_enabled = False
        self.owner._ring_gestures.input_owner_changed()

    @Slot()
    def _invalidate_strokes(self):
        if self.strokeSession.phase == "running" and not self.strokeSession.local:
            self.strokeSession.cancel("输入框已改变，笔画输入已退出")
        self._code = self._pending_stroke_commit = ""
        if not self._local_stroke_input:
            self._stroke_context = ""
        self._candidates = []
        self._selected = self._page = 0
        self._prediction_mode = False
        self.strokeTraceChanged.emit([], False)
        self._sync_voice_gate()
        self.changed.emit()

    def consume_gesture(self, event, connection):
        # Called on the BLE worker before voice/app mappings; never manipulate Qt here.
        if connection is not self.owner._disconnect_event or connection.is_set():
            return False
        if (self._run and self._run.switch_pending is self._run.output
                and getattr(event, "name", "") in {"tap", "swipe-left", "swipe-right", "swipe-up", "swipe-down"}):
            return True
        if self.strokeSession.consume(event, connection):
            return True
        run = self._run
        if (not self._stroke_gate or run is None or run.mode != "stroke"
                or connection is not self._connection or connection.is_set()):
            return False
        if getattr(event, "name", "") not in {"swipe-left", "swipe-right", "swipe-up", "swipe-down", "tap"}:
            return False
        if self._state == "running":
            self._post(run, "gesture", (run.output, event))
        return True

    @Property(float, notify=changed)
    def gain(self): return self._gain

    @gain.setter
    def gain(self, value):
        if not self._run and math.isfinite(value) and .25 <= value <= 3.:
            self._gain = round(value, 2)
            self.owner._settings.setValue("touchpad/gain", self._gain)
            self.changed.emit()

    @Property(bool, notify=changed)
    def clicks(self): return self._clicks

    @clicks.setter
    def clicks(self, value):
        if not self._run:
            self._clicks = bool(value)
            self.owner._settings.setValue("touchpad/clicks", self._clicks)
            self.changed.emit()

    @Property(bool, notify=changed)
    def invertY(self): return self._invert_y

    @invertY.setter
    def invertY(self, value):
        if not self._run:
            self._invert_y = bool(value)
            self.owner._settings.setValue("touchpad/invertY", self._invert_y)
            self.changed.emit()

    @Slot(object, object)
    def attach(self, source, connection):
        if self._closed or connection is not self.owner._disconnect_event or connection.is_set():
            return
        self._source, self._connection = source, connection
        self.changed.emit()

    def _connection_changed(self):
        if not self.owner.connected:
            self.stop("设备已断开")
            self._source = self._connection = None
        self.changed.emit()

    @Slot()
    def toggle(self):
        if self.active:
            self.stop()
        elif not self.busy:
            self.setInputMode("pointer")
            self.start()

    @Slot(str)
    def setInputMode(self, mode):
        if mode not in {"pointer", "stroke"} or mode == self._mode or self.busy:
            return
        if self._pending_stroke_commit or (mode == "stroke" and self._speech_busy()):
            self._message = "请先完成或取消当前输入／改写，再切换模式。"
            self._commit_message = self._message
            self.changed.emit()
            return
        self._clear_external_composition()
        self._mode = mode
        self._code = self._pending_stroke_commit = self._commit_message = ""
        self._candidates = []
        self._selected = self._page = 0
        self._prediction_mode = False
        self.strokeTraceChanged.emit([], False)
        run = self._run
        if run is not None and self._state == "running":
            run.output.stop()
            run.output_ready = False
            run.mode = mode
            run.output = self._new_output(run, "mode_ready")
            self._sync_voice_gate()
            run.output.start()
        else:
            self._sync_voice_gate()
        self._message = ("用 Ring 书写笔画，轻触确认候选字。" if mode == "stroke"
                         else "用 Ring 移动指针，点击「停止触摸板」结束。")
        self.changed.emit()

    def restore_pointer(self):
        if self._mode != "pointer":
            self.setInputMode("pointer")
        elif self._run and self._state == "running":
            # Cancel a pending native focus click before resuming movement.
            run = self._run
            run.output.stop()
            run.output_ready = False
            run.output = self._new_output(run, "mode_ready")
            run.output.start()

    @Slot(str)
    def addStroke(self, category):
        if category not in SYMBOLS or self._pending_stroke_commit: return
        if self._mode != "stroke" and (self.active or not self._local_stroke_input): return
        if self._speech_busy():
            self._commit_message = "请先完成或取消当前语音输入／改写，再输入笔画。"
            self.changed.emit()
            return
        self._append_stroke(category, NAMES[category])

    @Slot()
    def undoStroke(self):
        if self._pending_stroke_commit: return
        if self._code:
            self._code = self._code[:-1]
            self.strokeTraceChanged.emit([], False)
            self._refresh_candidates()

    @Slot()
    def clearStrokes(self):
        if self._pending_stroke_commit: return
        self._code = ""
        self.strokeTraceChanged.emit([], False)
        self._refresh_candidates()

    @Slot()
    def nextCandidates(self):
        if self._pending_stroke_commit: return
        if self.moreCandidates:
            self._page += 1
            self._selected = 0
            self._sync_external_composition()
            self.changed.emit()

    @Slot()
    def previousCandidates(self):
        if self._pending_stroke_commit: return
        if self._page:
            self._page -= 1
            self._selected = 0
            self._sync_external_composition()
            self.changed.emit()

    def _move_candidate(self, direction):
        if not self._candidates:
            return
        absolute = self._page * 5 + self._selected + direction
        absolute = max(0, min(len(self._candidates) - 1, absolute))
        self._page, self._selected = divmod(absolute, 5)
        self._sync_external_composition()
        self.changed.emit()

    @Slot(int)
    def selectCandidate(self, index):
        if not self.strokeSession.allow_input():
            return
        self._select_candidate(index)

    def _select_candidate(self, index):
        if self._pending_stroke_commit:
            return
        if self._speech_busy():
            self._commit_message = "请先完成或取消当前语音输入／改写，再选择候选字。"
            self.changed.emit()
            return
        absolute = self._page * 5 + index
        if 0 <= index < 5 and absolute < len(self._candidates):
            char = self._candidates[absolute]
            try:
                inserted = self._commit_character(char)
            except Exception as exc:
                self._commit_message = "输入失败：" + str(exc)
                self.changed.emit()
                return
            if inserted is None:
                self._commit_message = "正在输入「" + char + "」…"
                self.changed.emit()
            elif inserted:
                self._commit_message = "已输入「" + char + "」"
                self._code = ""
                self.strokeTraceChanged.emit([], False)
                self._refresh_candidates()
            else:
                self._commit_message = "请将光标放入目标文本框后轻触确认；候选字已保留。"
                self.changed.emit()

    def _commit_character(self, char):
        # Keep the composition until the target accepts a direct insertion.
        if self._local_stroke_input:
            self.localCharacterCommitted.emit(char)
            return True
        if sys.platform == "darwin":
            if self.owner._inline_input.stroke_commit(char):
                self._pending_stroke_commit = char
                return None
            return False
        return False

    @Slot(str, bool, str)
    def _stroke_committed(self, char, success, error):
        if char != self._pending_stroke_commit:
            return
        self._pending_stroke_commit = ""
        if success:
            self._code = ""
            context = self.owner._inline_input.stroke_context
            self._stroke_context = (context if context is not None else self._stroke_context + char)[-4:]
            self.strokeTraceChanged.emit([], False)
            self._commit_message = "已输入「" + char + "」"
            self._refresh_candidates()
        else:
            self._commit_message = "输入失败：" + (error or "目标文本框未接受文字")
            self.changed.emit()

    def _clear_external_composition(self):
        if (sys.platform == "darwin" and not self._local_stroke_input
                and (self._code or self._pending_stroke_commit or self._prediction_mode)):
            self.owner._inline_input.stroke_clear()

    def _sync_external_composition(self):
        if self._mode != "stroke" or self._local_stroke_input:
            return
        if sys.platform == "darwin":
            if self._code or self._prediction_mode:
                if not self.owner._inline_input.stroke_update(
                        self.strokeCode, self.strokeCandidates, self._selected, prediction=self._prediction_mode):
                    self._commit_message = "请先在目标文本框选中 ProxiMic 输入法，再用 Ring 书写。"
            else:
                self.owner._inline_input.stroke_clear()

    def _stroke_dictionary(self):
        if self._dictionary is None:
            self._dictionary = StrokeDictionary(recognizer='dtw')
        return self._dictionary

    def _refresh_candidates(self):
        try:
            self._prediction_mode = False
            if self._code:
                self._candidates = self._stroke_dictionary().candidates(self._code, limit=30)
            elif self._stroke_context and (self._local_stroke_input or self.strokeSession.phase == "running"):
                if self._predictor is None:
                    from ..stroke_context import StrokeContext
                    self._predictor = StrokeContext()
                self._candidates = self._predictor.suggestions(self._stroke_context)
                self._prediction_mode = bool(self._candidates)
            else:
                self._candidates = []
        except Exception as exc:
            self._candidates = []
            self._commit_message = "笔画字库加载失败：" + str(exc)
        self._selected = 0
        self._page = 0
        self._sync_external_composition()
        self._sync_voice_gate()
        self.changed.emit()

    def _append_stroke(self, category, shape):
        if len(self._code) >= 12: self._code = ""
        self._code += category
        self._last_stroke = shape
        self._commit_message = ""
        self._refresh_candidates()
        self._standard_trace = True
        self.strokeTraceChanged.emit(standard_stroke(category), True)

    def _new_output(self, run, ready_kind):
        output = None
        def ended(reason, error):
            self._post(run, "output_end", (output, reason, error))
        connection = self._connection
        def gesture_detected(name):
            if run is self._run and output is run.output:
                run.diagnostics.note('resolved_' + name)
                self.owner._gesture_trigger.submit(name, connection)
        common = dict(connection=self._connection,
                      on_ready=lambda: self._post(run, ready_kind, output), on_end=ended,
                      on_gesture=gesture_detected, on_diagnostic=run.diagnostics.note)
        pointer_after_step = run.last_move_step
        def double_click(target=None):
            if run is not self._run or output is not run.output:
                return
            run.switch_pending = output
            self._post(run, "double_click", (output, target, time.monotonic()))
        if run.mode == "stroke":
            output = self.stroke_output_factory(**common,
                on_stroke=lambda points: self._post(run, "stroke", (output, points)),
                on_tap=lambda: None,  # Firmware tap owns confirmation; SDK click only separates ink.
                on_double_click=double_click,
                on_trace=lambda points, finished: self._post(run, "trace", (output, (points, finished))))
        else:
            output = self.output_factory(**common, gain=self._gain, clicks=self._clicks,
                                         invert_y=self._invert_y, on_double_click=double_click)
            output.pointer_after_step = pointer_after_step
        return output

    @Slot()
    def start(self):
        if not self.available or self._run is not None:
            return
        if self._mode == "stroke" and self._speech_busy():
            self._message = "请先完成或取消当前语音输入／改写，再开启笔画输入。"
            self._commit_message = self._message
            self.changed.emit()
            return
        run = SimpleNamespace(source=self._source, output=None, reason="", sdk_started=False,
                              output_ready=False, switch_pending=None, mode=self._mode,
                              last_move_step=0,
                              diagnostics=TouchpadDiagnostics())
        self._run = run
        self._state, self._warm = "starting", False
        self._message = "正在准备笔画输入…" if run.mode == "stroke" else "正在准备鼠标控制…"
        run.output = self._new_output(run, "ready")
        self._sync_voice_gate()
        self.changed.emit()
        run.output.start()

    def _post(self, run, kind, value):
        try:
            self._event.emit(run, kind, value)
        except RuntimeError:
            pass  # QObject already destroyed during application shutdown.

    def _watch(self, run, future, kind):
        def done(result):
            try:
                result.result()
                error = None
            except BaseException as exc:
                error = exc
            self._post(run, kind, error)
        future.add_done_callback(done)

    @Slot()
    def stop(self, reason=""):
        if self.strokeSession.engaged:
            self.strokeSession.cancel(reason, stop_output=False)
        run = self._run
        if run is None or self._state == "stopping": return
        run.reason = reason or ("已停止笔画输入" if run.mode == "stroke" else "已停止鼠标控制")
        self._clear_external_composition()
        self._code = self._pending_stroke_commit = ""
        self._candidates = []
        self.strokeTraceChanged.emit([], False)
        run.output.stop()  # Synchronous gate; never wait for Quartz or MNN here.
        self._state, self._message = "stopping", "正在停止鼠标控制…"
        self.changed.emit()
        if not run.sdk_started:
            self._finish(run, None)
            return
        try:
            self._watch(run, run.source.stop_touchpad(), "stopped")
        except Exception as exc:
            self._finish(run, exc)

    def _finish(self, run, error):
        if run is not self._run: return
        run.output.stop()
        self._run = None
        self._mode = "pointer"
        error = error or getattr(run, "output_error", None)
        self._state = "error" if error else "idle"
        if isinstance(error, (PermissionError, MacPermissionError)):
            if isinstance(error, MacPermissionError) and error.state.accessibility is True:
                detail = "辅助功能已开启，但鼠标事件发送权限尚未生效。"
            else:
                detail = "当前鼠标控制进程的辅助功能权限尚未生效。"
            self._message = detail + self.owner._inline_input.permissions.instructions
        else:
            self._message = str(error) if error else run.reason or "已停止，可再次开启。"
        self._clear_external_composition()
        self._invalidate_strokes()

    @Slot(object, str, object)
    def _receive(self, run, kind, value):
        if run is not self._run: return
        if kind == "ready" and self._state == "starting" and value is run.output:
            try:
                run.sdk_started = True
                def ended(error):
                    run.output.stop()
                    self._post(run, "ended", error)
                def observed(event):
                    if run is not self._run:
                        return
                    run.diagnostics.observe(event)
                    if event.kind == 'move':
                        run.last_move_step = event.step
                    elif event.kind == 'contact' and event.state == 'reset' and event.step == 0:
                        run.last_move_step = 0
                        run.output.pointer_after_step = 0
                    if run.mode == 'stroke' or event.kind != 'move':
                        run.output.submit(event)
                def pointer_moved(event):
                    if run is self._run and run.mode == 'pointer':
                        output = run.output
                        if event.step > output.pointer_after_step:
                            run.diagnostics.note('pointer_frame')
                            output.submit(event)
                def stats(value):
                    self._post(run, 'stats', (time.monotonic(), value, run.diagnostics.snapshot()))
                self._watch(run, run.source.start_touchpad(
                    on_event=observed,
                    on_pointer_move=pointer_moved,
                    on_stats=stats,
                    on_stopped=ended,
                    duration_s=None), "started")
            except Exception as exc:
                self._finish(run, exc)
        elif kind == "started":
            if value:
                self._finish(run, value)
            elif self._state == "starting":
                run.output.pointer_after_step = run.last_move_step
                run.output.activate()
                run.output_ready = True
                self._state = "running"
                self._message = "准备中，稍后即可书写笔画。" if run.mode == "stroke" else "准备中，稍后即可移动指针。点击「停止触摸板」结束。"
                self.changed.emit()
        elif kind == "mode_ready" and value is run.output and self._state == "running":
            run.output.pointer_after_step = run.last_move_step
            run.output.activate()
            run.output_ready = True
            self.changed.emit()
        elif kind == "double_click" and self._state == "running":
            output, target, created = value
            if output is not run.output: return
            self.owner._event_log("TOUCHPAD_DOUBLE_CLICK", _live_message="", mode=run.mode,
                                  age_ms=round((time.monotonic() - created) * 1000))
            try:
                if time.monotonic() - created <= 1:
                    self.strokeSession.request(target)
            except Exception as exc:
                self.strokeSession.fail(exc)
            finally:
                if run.switch_pending is output:
                    run.switch_pending = None
                if not self.strokeSession.engaged and output is run.output:
                    output.pointer_after_step = run.last_move_step
                    output.activate()
        elif kind in {"stroke", "trace", "gesture"} and self._state == "running" and run.mode == "stroke":
            output, payload = value
            if output is not run.output:
                return
            if kind == "gesture" and (run.switch_pending is output or not output.active):
                return
            if kind == "trace":
                points, finished = payload
                # The recognizer below publishes the canonical shape at lift.
                # Its display lifetime belongs to the views, not sensor cleanup.
                if points and not finished:
                    self._standard_trace = False
                    self.strokeTraceChanged.emit(normalized_trace(points), False)
                elif not points and not self._standard_trace:
                    self.strokeTraceChanged.emit([], False)
            elif kind == "stroke" and not self._pending_stroke_commit:
                try:
                    result = self._stroke_dictionary().recognize(payload)
                    if result is not None:
                        run.diagnostics.note('stroke_recognized')
                        action = lambda: self._append_stroke(result["category"], result["shape"])
                        if self.strokeSession.allow_input(): action()
                    else:
                        run.diagnostics.note('stroke_unrecognizable')
                        self._standard_trace = False
                        self.strokeTraceChanged.emit([], False)
                except Exception as exc:
                    run.diagnostics.note('stroke_recognition_error')
                    self._commit_message = "笔画识别失败：" + str(exc)
                    self.changed.emit()
            elif kind == 'stroke':
                run.diagnostics.note('stroke_waiting_commit')
            elif kind == "gesture" and not self._pending_stroke_commit:
                name = getattr(payload, "name", "")
                if name == "swipe-right": self._move_candidate(1)
                elif name == "swipe-left": self._move_candidate(-1)
                elif name == "swipe-up": self.undoStroke()
                elif name == "swipe-down": self.clearStrokes()
                elif name == "tap": self.selectCandidate(self._selected)
        elif kind == "stats" and self._state == "running":
            captured_at, value, counts = value
            app = QGuiApplication.instance()
            foreground = app.applicationState() == Qt.ApplicationActive if app is not None else None
            report = run.diagnostics.report(value, counts, captured_at, foreground=foreground,
                                             visibility_epoch=self._visibility_epoch, now=time.monotonic())
            if report is not None:
                # Keep reason counts numeric; the generic logger truncates long string fields.
                reasons = {'count_' + key: count for key, count in report.pop('events').items()}
                self.owner._event_log('TOUCHPAD_STATS', _live_message='', mode=run.mode, **report, **reasons)
            warm = value.warmup_frames >= 200
            if warm != self._warm:
                self._warm = warm
                self._message = ("用 Ring 书写笔画，轻触确认候选字。" if run.mode == "stroke" else "用 Ring 移动指针，点击「停止触摸板」结束。") if warm else "信号恢复中，正在重新准备…"
                self.changed.emit()
        elif kind in {"ended", "stopped"}:
            self._finish(run, value)
        elif kind == "output_end" and self._state != "stopping":
            output, reason, error = value
            if output is not run.output: return
            if not run.sdk_started:
                run.reason = reason
                self._finish(run, error)
            else:
                run.output_error = error
                self.stop(reason or str(error or "鼠标控制已停止"))

    def close(self):
        self._closed = True
        self.strokeSession.close()
        self.stop("鼠标控制已关闭")
        self._clear_external_composition()
        self._invalidate_strokes()
