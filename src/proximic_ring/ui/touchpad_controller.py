"""GUI lifecycle for the existing Ring session; high-rate events never enter Qt."""
from __future__ import annotations

import math
import os
import sys
import time
from types import SimpleNamespace

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot, QTimer

from ..touchpad_mouse import TouchpadMouseOutput
from ..touchpad_strokes import TouchpadStrokeOutput
from ..stroke_input import StrokeDictionary, SYMBOLS, NAMES


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
        self._seconds = int(self._number("seconds", 90, 30, 600))
        if self._seconds not in (30, 90, 180, 300, 600): self._seconds = 90
        self._clicks = owner._settings.value("touchpad/clicks", True, type=bool)
        self._invert_y = owner._settings.value("touchpad/invertY", False, type=bool)
        self._warm = False
        self._mode = str(owner._settings.value("touchpad/inputMode", "pointer"))
        if self._mode not in {"pointer", "stroke"}: self._mode = "pointer"
        self._dictionary = None
        self._code = ""
        self._candidates = []
        self._selected = 0
        self._page = 0
        self._last_stroke = ""
        self._commit_message = ""
        self._stroke_target = None
        self._pending_stroke_commit = ""
        self._local_stroke_input = False
        self._stroke_context = ""
        self._predictor = None
        self._prediction_mode = False
        owner._inline_input.strokeCommitted.connect(self._stroke_committed)
        self.sourceReady.connect(self.attach, Qt.QueuedConnection)
        self._event.connect(self._receive, Qt.QueuedConnection)
        owner.connectedChanged.connect(self._connection_changed)
        owner.busyChanged.connect(self.changed)
        owner.recognitionEnabledChanged.connect(self.changed)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.changed)

    def _number(self, key, default, low, high):
        try:
            value = float(self.owner._settings.value("touchpad/" + key, default))
            return max(low, min(high, value)) if math.isfinite(value) else default
        except (ValueError, TypeError):
            return default

    @Property(bool, constant=True)
    def supported(self): return sys.platform in {"darwin", "win32"}

    @Property(bool, notify=changed)
    def available(self):
        return (self.supported and not self._closed and self.owner.connected
                and not self.owner.busy and not self.owner.recognitionEnabled
                and self._source is not None
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
        label = "笔画输入" if self._mode == "stroke" else "触摸板"
        return {"starting": "正在开启" + label, "running": label + "已开启",
                "stopping": "正在停止" + label, "error": label + "未能运行"}.get(self._state, label + "已关闭")

    @Property(str, notify=changed)
    def message(self): return self._message

    @Property(str, notify=changed)
    def inputMode(self): return self._mode

    @Property(str, notify=changed)
    def strokeCode(self): return " ".join(SYMBOLS[x] for x in self._code)

    @Property(list, notify=changed)
    def strokeCandidates(self): return self._candidates[self._page * 5:self._page * 5 + 5]

    @Property(bool, notify=changed)
    def predictionMode(self): return self._prediction_mode

    @Slot(str)
    def setStrokeContext(self, text):
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

    @Property(str, notify=changed)
    def commitMessage(self): return self._commit_message

    @Slot(bool)
    def setLocalStrokeInput(self, enabled):
        enabled = bool(enabled)
        if enabled == self._local_stroke_input:
            return
        self._clear_external_composition()
        self._local_stroke_input = enabled
        self._stroke_context = ""
        self._stroke_target = None
        self._code = ""
        self._refresh_candidates()

    @Property(int, notify=changed)
    def remaining(self):
        run = self._run
        return max(0, math.ceil(run.deadline - time.monotonic())) if run and run.deadline else 0

    @Property(float, notify=changed)
    def gain(self): return self._gain

    @gain.setter
    def gain(self, value):
        if not self._run and math.isfinite(value) and .25 <= value <= 3.:
            self._gain = round(value, 2)
            self.owner._settings.setValue("touchpad/gain", self._gain)
            self.changed.emit()

    @Property(int, notify=changed)
    def seconds(self): return self._seconds

    @seconds.setter
    def seconds(self, value):
        if not self._run and value in (30, 90, 180, 300, 600):
            self._seconds = value
            self.owner._settings.setValue("touchpad/seconds", value)
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
            self.start()

    @Slot(str)
    def setInputMode(self, mode):
        if mode not in {"pointer", "stroke"} or mode == self._mode or self.busy:
            return
        self._mode = mode
        self._prediction_mode = False
        self.owner._settings.setValue("touchpad/inputMode", mode)
        self._code = ""
        self._candidates = []
        self._selected = 0
        self._page = 0
        self._commit_message = ""
        self._clear_external_composition()
        self._stroke_target = None
        self._pending_stroke_commit = ""
        run = self._run
        if run is not None and self._state == "running":
            old = run.output
            old.stop()
            run.mode = mode
            run.firmware_taps = mode == "stroke"
            run.output = self._new_output(run, "mode_ready")
            run.output.start()
            self._message = "正在切换到" + ("笔画输入" if mode == "stroke" else "鼠标控制") + "…"
            self._watch(run, run.source.set_touchpad_gestures(
                (lambda event: self._post(run, "gesture", event)) if mode == "stroke" else None
            ), "gesture_update")
        self.changed.emit()

    @Slot(str)
    def addStroke(self, category):
        if category not in SYMBOLS or self._mode != "stroke": return
        self._append_stroke(category, NAMES[category])

    @Slot()
    def undoStroke(self):
        if self._code:
            self._code = self._code[:-1]
            self._refresh_candidates()

    @Slot()
    def clearStrokes(self):
        self._code = ""
        self._refresh_candidates()

    @Slot()
    def nextCandidates(self):
        if self.moreCandidates:
            self._page += 1
            self._selected = 0
            self._sync_external_composition()
            self.changed.emit()

    @Slot()
    def previousCandidates(self):
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
        if self._pending_stroke_commit:
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
                self._refresh_candidates()
            else:
                self._commit_message = "请将光标放入目标文本框后轻触确认；候选字已保留。"
                self.changed.emit()

    def _commit_character(self, char):
        # Keep the composition until the target accepts a direct insertion.
        if self._local_stroke_input:
            self.localCharacterCommitted.emit(char)
            return True
        if sys.platform == "win32":
            adapter = self.owner._desktop_target_adapter()
            target = self._stroke_target
            if target is None:
                try:
                    target = adapter.capture_reference()
                except Exception:
                    return False
            elif not adapter.is_foreground(target):
                # A click in our own candidate UI is intentional; another
                # external foreground application is not.
                import ctypes
                user32 = ctypes.windll.user32
                foreground = int(user32.GetForegroundWindow() or 0)
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
                if pid.value != os.getpid():
                    raise RuntimeError("输入目标已切换，请重新把光标放入文本框")
            adapter.inject(target, char)
            return True
        elif sys.platform == "darwin":
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
            self._commit_message = "已输入「" + char + "」"
            self._refresh_candidates()
        else:
            self._commit_message = "输入失败：" + (error or "目标文本框未接受文字")
            self.changed.emit()

    def _clear_external_composition(self):
        if sys.platform == "darwin" and not self._local_stroke_input:
            self.owner._inline_input.stroke_clear()

    def _sync_external_composition(self):
        if self._mode != "stroke" or self._local_stroke_input:
            return
        if sys.platform == "darwin":
            if (self._code and not self.owner._inline_input.stroke_update(
                    self.strokeCode, self.strokeCandidates, self._selected)):
                self._commit_message = "请先在目标文本框选中 ProxiMic 输入法，再用 Ring 书写。"
            elif not self._code:
                self.owner._inline_input.stroke_clear()
        elif sys.platform == "win32" and self._code:
            import ctypes
            user32 = ctypes.windll.user32
            foreground = int(user32.GetForegroundWindow() or 0)
            if not foreground:
                return
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
            if pid.value == os.getpid():
                return
            try:
                target = self.owner._desktop_target_adapter().capture_reference()
            except Exception:
                return
            if (self._stroke_target is not None and
                    (target.window_handle, target.control_handle, target.accessibility_id) !=
                    (self._stroke_target.window_handle, self._stroke_target.control_handle,
                     self._stroke_target.accessibility_id)):
                self._code = ""
                self._candidates = []
                self._selected = self._page = 0
            self._stroke_target = target

    def _stroke_dictionary(self):
        if self._dictionary is None:
            self._dictionary = StrokeDictionary(recognizer='dtw')
        return self._dictionary

    def _refresh_candidates(self):
        try:
            self._prediction_mode = False
            if self._code:
                self._candidates = self._stroke_dictionary().candidates(self._code, limit=30)
            elif self._local_stroke_input and self._stroke_context:
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
        self.changed.emit()

    def _append_stroke(self, category, shape):
        if len(self._code) >= 12: self._code = ""
        self._code += category
        self._last_stroke = shape
        self._commit_message = ""
        self._refresh_candidates()

    def _new_output(self, run, ready_kind):
        output = None
        def ended(reason, error):
            self._post(run, "output_end", (output, reason, error))
        if run.mode == "stroke":
            output = TouchpadStrokeOutput(
                connection=self._connection, on_ready=lambda: self._post(run, ready_kind, output),
                on_end=ended, on_stroke=lambda points: self._post(run, "stroke", points),
                on_tap=lambda: self._post(run, "tap", None),
                on_trace=lambda points, finished: self._post(run, "trace", (output, points, finished)))
        else:
            output = self.output_factory(
                connection=self._connection, gain=self._gain, clicks=self._clicks, invert_y=self._invert_y,
                on_ready=lambda: self._post(run, ready_kind, output), on_end=ended)
        return output

    @Slot()
    def start(self):
        if not self.available or self._run is not None:
            return
        run = SimpleNamespace(source=self._source, output=None, deadline=None, reason="", sdk_started=False,
                              last_stats=None, last_stats_log=0., last_reset_log=0, mode=self._mode,
                              firmware_taps=self._mode == "stroke")
        self._run = run
        self.owner._event_log("TOUCHPAD_START", _live_message="", duration_s=self._seconds,
                              input_mode=self._mode, gain=self._gain, clicks=self._clicks, invert_y=self._invert_y,
                              audio_source=getattr(self.owner, "_audio_source", None),
                              ring_mic_enabled=getattr(self._source, "audio_enabled", None))
        self._state, self._warm = "starting", False
        self._message = "正在准备" + ("笔画输入" if self._mode == "stroke" else "鼠标控制") + "…"
        run.output = self._new_output(run, "ready")
        self.changed.emit()
        run.output.start()

    def _post(self, run, kind, value):
        if kind in {"stroke", "tap", "gesture"}:
            value = (time.monotonic(), value)
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
        run = self._run
        if run is None or self._state == "stopping": return
        run.reason = reason or ("已停止笔画输入" if run.mode == "stroke" else "已停止鼠标控制")
        self.strokeTraceChanged.emit([], False)
        self._clear_external_composition()
        self._stroke_target = None
        run.output.stop()  # Synchronous gate; never wait for Quartz or MNN here.
        self._state, self._message = "stopping", "正在停止" + ("笔画输入" if run.mode == "stroke" else "鼠标控制") + "…"
        self._timer.stop()
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
        self.strokeTraceChanged.emit([], False)
        self._clear_external_composition()
        self._stroke_target = None
        self._pending_stroke_commit = ""
        run.output.stop()
        self._run = None
        self._timer.stop()
        error = error or getattr(run, "output_error", None)
        self._state = "error" if error else "idle"
        if isinstance(error, PermissionError):
            self._message = ("请在系统设置的「隐私与安全性 → 辅助功能」中允许 MythLink 控制电脑，然后重新开启。"
                             if sys.platform == "darwin" else "无法控制系统鼠标，请检查 Windows 输入权限后重试。")
        else:
            self._message = str(error) if error else run.reason or "已自动停止，可再次开启。"
        stats = run.last_stats
        self.owner._event_log(
            "TOUCHPAD_STOP", _live_message="", reason=run.reason or "stream_ended",
            error=repr(error) if error else "", packets=getattr(stats, "packets", None),
            tokens=getattr(stats, "tokens", None), resets=getattr(stats, "resets", None),
            invalid_packets=getattr(stats, "invalid_packets", None),
            duplicate_packets=getattr(stats, "duplicate_packets", None),
            stale_packets=getattr(stats, "stale_packets", None),
            queue_overflows=getattr(stats, "queue_overflows", None),
            sequence_gaps=getattr(stats, "sequence_gaps", None),
            missing_packets=getattr(stats, "missing_packets", None),
            arrival_gaps=getattr(stats, "arrival_gaps", None),
            idle_resets=getattr(stats, "idle_resets", None),
            reboots=getattr(stats, "reboots", None))
        self.changed.emit()

    @Slot(object, str, object)
    def _receive(self, run, kind, value):
        if run is not self._run: return
        event_age_ms = 0.
        if kind in {"stroke", "tap", "gesture"} and isinstance(value, tuple) and len(value) == 2:
            posted_at, value = value
            event_age_ms = (time.monotonic() - posted_at) * 1000
        if kind == "ready" and self._state == "starting" and value is run.output:
            try:
                run.sdk_started = True
                def ended(error):
                    run.output.stop()
                    self._post(run, "ended", error)
                self._watch(run, run.source.start_touchpad(
                    on_event=lambda event: run.output.submit(event),
                    on_gesture=(lambda event: self._post(run, "gesture", event)) if run.mode == "stroke" else None,
                    on_stats=lambda stats: self._post(run, "stats", stats),
                    on_stopped=ended,
                    duration_s=self._seconds), "started")
            except Exception as exc:
                self._finish(run, exc)
        elif kind == "started":
            if value:
                self._finish(run, value)
            elif self._state == "starting":
                paused = getattr(run.source, "_stream_paused", None)
                self.owner._event_log(
                    "TOUCHPAD_RUNNING", _live_message="",
                    ring_mic_paused=paused.is_set() if paused is not None else None,
                    gesture_channel_active=getattr(run.source, "gestures_active", None),
                    **self._stream_channels(run.source))
                run.deadline = time.monotonic() + self._seconds
                run.output.activate(self._seconds)
                self._state, self._message = "running", "准备中，稍后即可" + ("书写笔画。" if run.mode == "stroke" else "移动指针。按 Esc 随时停止。")
                self._timer.start()
                self.changed.emit()
        elif kind == "mode_ready" and value is run.output and self._state == "running":
            remaining = (run.deadline or 0) - time.monotonic()
            if remaining <= 0:
                self.stop("已自动停止")
                return
            run.output.activate(remaining)
            self._message = "在桌面书写笔画，轻触可选择高亮候选。" if run.mode == "stroke" else "用 Ring 移动指针，按 Esc 随时停止。"
            self.changed.emit()
        elif kind == "trace" and self._state == "running" and run.mode == "stroke":
            output, points, finished = value
            if output is run.output:
                # Nested Python tuples become opaque PyObjects in QML. Send
                # numeric QVariantLists so Canvas can read each coordinate.
                self.strokeTraceChanged.emit([[float(x), float(y)] for x, y in points], finished)
        elif kind == "stroke" and self._state == "running" and run.mode == "stroke":
            try:
                recognition_start = time.monotonic()
                result = self._stroke_dictionary().recognize(value)
                if result is not None:
                    self._append_stroke(result["category"], result["shape"])
                    self.owner._event_log("STROKE_RECOGNIZED", _live_message="", points=len(value),
                        category=result["category"], shape=result["shape"], distance=result["distance"],
                        recognizer=result.get("recognizer", "unknown"),
                        template_profile=result.get("template_profile", "none"),
                        category_alternatives=result.get("category_alternatives", []),
                        ui_queue_ms=round(event_age_ms, 1),
                        recognition_and_candidates_ms=round((time.monotonic()-recognition_start)*1000, 1))
            except Exception as exc:
                self._commit_message = "笔画识别失败：" + str(exc)
                self.changed.emit()
        elif kind == "tap" and self._state == "running" and run.mode == "stroke":
            if run.firmware_taps:
                return  # Firmware TRIGGER class 5 owns confirmation in gesture mode.
            self.selectCandidate(self._selected)
            self.owner._event_log("STROKE_TAP", _live_message="", ui_queue_ms=round(event_age_ms, 1))
        elif kind == "gesture" and self._state == "running" and run.mode == "stroke":
            if getattr(value, "kind", None) != "trigger" or getattr(value, "protocol_version", None) != 2:
                return
            action = {4: "next", 3: "previous", 1: "undo", 2: "clear", 5: "confirm"}.get(getattr(value, "class_id", None))
            if action == "next": self._move_candidate(1)
            elif action == "previous": self._move_candidate(-1)
            elif action == "undo":
                if self._code:
                    self.undoStroke()
                elif self._local_stroke_input:
                    self.localBackspaceRequested.emit()
            elif action == "clear": self.clearStrokes()
            elif action == "confirm": self.selectCandidate(self._selected)
            if action:
                self.owner._event_log("STROKE_GESTURE", _live_message="", action=action,
                                      code=self._code, selected=self._selected, page=self._page,
                                      ui_queue_ms=round(event_age_ms, 1))
        elif kind == "gesture_update":
            run.firmware_taps = run.mode == "stroke" and value is None
            if value is not None and self._state == "running":
                if run.mode == "pointer":
                    self.stop("固件手势通道未能关闭：" + str(value))
                else:
                    self._message = "笔画可用，但滑动手势未能启动：" + str(value)
                    self.changed.emit()
        elif kind == "stats" and self._state == "running":
            run.last_stats = value
            now = time.monotonic()
            resets = getattr(value, "resets", 0)
            if resets != run.last_reset_log or now - run.last_stats_log >= 5.:
                self.owner._event_log(
                    "TOUCHPAD_STATS", _live_message="", packets=getattr(value, "packets", None),
                    tokens=getattr(value, "tokens", None), warmup_frames=value.warmup_frames,
                    inference_batch_ms=round(getattr(value, "inference_batch_ms", 0.), 1),
                    packet_queue_age_ms=round(getattr(value, "packet_queue_age_ms", 0.), 1),
                    contact_probability=round(getattr(value, "contact_probability", 0.), 3),
                    resets=resets, invalid_packets=getattr(value, "invalid_packets", None),
                    duplicate_packets=getattr(value, "duplicate_packets", None),
                    stale_packets=getattr(value, "stale_packets", None),
                    queue_overflows=getattr(value, "queue_overflows", None),
                    sequence_gaps=getattr(value, "sequence_gaps", None),
                    missing_packets=getattr(value, "missing_packets", None),
                    arrival_gaps=getattr(value, "arrival_gaps", None),
                    idle_resets=getattr(value, "idle_resets", None),
                    reboots=getattr(value, "reboots", None),
                    **self._stream_channels(run.source),
                    last_data_age_s=getattr(value, "last_data_age_s", None))
                run.last_stats_log, run.last_reset_log = now, resets
            warm = value.warmup_frames >= 200
            if warm != self._warm:
                self._warm = warm
                self._message = ("在桌面书写笔画，轻触可选择高亮候选。" if run.mode == "stroke" else "用 Ring 移动指针，按 Esc 随时停止。") if warm else "信号恢复中，正在重新准备…"
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

    @staticmethod
    def _stream_channels(source):
        endpoint = getattr(source, "_touchpad_endpoint", None)
        session = endpoint[1] if endpoint else None
        return {"device_mic_active": getattr(session, "mic_active", None),
                "device_swipe_active": getattr(session, "swipe_active", None),
                "device_touchpad_active": getattr(session, "touchpad_active", None)}

    def close(self):
        self._closed = True
        self.stop("鼠标控制已关闭")
