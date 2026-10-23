"""App-level gesture routing, settings, and one-shot commit-then-send."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
import sys
import threading
import time

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QTimer, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication

from ...gestures.app_gestures import (ACTION_LABELS, APP_LABELS, AppBinding, action_phase,
                            default_profiles, profiles_from_json,
                            profiles_to_json, validate_profiles, reserve_ring_profiles)
from ...desktop.app_shortcuts import MacAppShortcuts
from ...platforms.macos.mac_permissions import MacPermissionError
from ...diagnostics.scene_diagnostics import SceneActionError, SceneDiagnostics, new_trace_id, reason_message, exception_details
from ...gestures.gesture_settings import BoundGestureEvent, GESTURE_LABELS
from ...voice.ime.input_source_switch import foreground_pid, select_voice_input_source
from ...scenes.registry import SCENE_LABELS
from ...platforms.macos.wechat_setup import (KEYBOARD_SETTINGS_URL, MENU_TITLES,
                            KeyboardShortcutsNavigator, detect_wechat_menu)
from .shortcut_recorder import shortcut_from_event, shortcut_from_key


@dataclass(frozen=True)
class AppGestureEvent:
    source: object
    target: object
    generation: int
    created: float
    phase: str
    composing: bool
    writing: bool
    edit_requested: bool
    sentence: tuple
    capture_error: str = ""
    scene: str = ""
    exclusive: bool = False
    route: str = ""
    speech_owned: bool = False
    trace_id: str = field(default_factory=new_trace_id, compare=False)
    capture_reason: str = field(default="", compare=False)
    input_method_target: tuple | None = None
    input_focus: object = None


@dataclass(frozen=True)
class InputSourceGestureEvent:
    source: object
    generation: int
    created: float
    pid: int
    error: str = ""


@dataclass(frozen=True)
class _PolishBinding:
    # Shares the sentence-finalization gate without representing a keystroke.
    gesture: str = "swipe-down"
    enabled: bool = True


class AppGestureController(QObject):
    changed = Signal()
    noticeChanged = Signal()
    recordingChanged = Signal()
    shortcutCaptured = Signal(str)
    setupChanged = Signal()
    _setupResult = Signal(str, int, object)
    _inputSourceResult = Signal(str)

    def __init__(self, owner, *, backend=None):
        super().__init__(owner)
        self.owner = owner
        self.backend = backend or MacAppShortcuts()
        self._diagnostics = SceneDiagnostics(owner._diagnostic_log.path,
                                            run_id=owner._diagnostic_run_id, writer=owner._diagnostic_log)
        self._diagnostics.start_session()
        self._generation = 0
        self._error = ""
        self._notice = ""
        self._recording = False
        self._recording_error = ""
        self._pending = None
        self._last_dispatch = None
        from ..desktop.browser_action_queue import BrowserActionQueue
        self._browser_actions = BrowserActionQueue(self)
        self._last_browser_submit = None
        self._wechat_info = {"detected": False, "message": "选择微信后自动识别菜单语言", "version": ""}
        self._menu_generation = 0
        self._navigation_generation = 0
        self._navigation_cancel = threading.Event()
        self._setup_closed = False
        self._settings_status = ""
        self._setupResult.connect(self._apply_setup_result)
        self._inputSourceResult.connect(self._input_source_result)
        self._source_busy = False
        self._source_last = -float("inf")
        # Keep explicitly saved legacy settings; a fresh install has no implicit
        # Codex/WorkBuddy/WeChat shortcuts. New UI mappings are opt-in per bundle.
        self._legacy_configured = owner._settings.contains("gestures/appProfiles")
        try:
            self._profiles = profiles_from_json(owner._settings.value(
                "gestures/appProfiles", profiles_to_json(default_profiles())))
            reserved = reserve_ring_profiles(self._profiles, frozenset())
            if reserved != self._profiles:
                self._profiles = reserved
                owner._settings.setValue("gestures/appProfiles", profiles_to_json(reserved))
                self._error = "旧发送绑定已更新；输入框内上滑统一为 Enter"
            validate_profiles(self._profiles, owner._gesture_bindings, frozenset())
        except (ValueError, TypeError, KeyError):
            self._profiles = default_profiles()
            self._error = "应用手势设置无效，已恢复默认"
        available = self._unused_source_gestures()
        saved = owner._settings.value("gestures/inputSourceGesture", None)
        if sys.platform != "darwin":
            self._source_gesture = ""
        elif saved is None:
            # Standalone selection is opt-in; tap handles automatic activation.
            self._source_gesture = ""
        else:
            self._source_gesture = str(saved) if str(saved) in available else ""
        owner._settings.setValue("gestures/inputSourceGesture", self._source_gesture)
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self.dismissNotice)
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.timeout.connect(lambda: self.cancel_pending("等待定稿超时，请定稿后重新触发手势"))
        from ..desktop.application_mapping_controller import ApplicationMappingController
        self._catalog = ApplicationMappingController(self)

    def _native_diagnostic(self):
        try:
            return dict(getattr(self.backend, "last_diagnostic", {}) or {})
        except Exception:
            return {}

    def _trace(self, trace_id, stage, reason, *, target=None, **facts):
        try:
            if target is not None:
                facts.update(app=target.bundle, pid=target.pid, scene=target.scene,
                             input_context=target.input_context, blocked=target.blocked)
            self._diagnostics.record(trace_id, stage, reason, **facts)
        except Exception:
            pass  # Diagnostics must not change gesture routing or execution.

    def _capture_trace(self, trace_id, name, target, error=None):
        info = dict(getattr(target, "diagnostic", {}) or self._native_diagnostic())
        reason = (error.reason if isinstance(error, SceneActionError) else
                  info.get("reason") or ("capture_failed" if error else "captured" if target else "target_unavailable"))
        if error and not isinstance(error, SceneActionError) and reason in {"capture_started", "ready", "recognized", "target_verified", "shortcut_posted"}:
            reason = "permission_denied" if isinstance(error, MacPermissionError) else "capture_failed"
        self._trace(trace_id, "capture", reason, target=target,
                    gesture=name, native=info, app=info.get("app", ""),
                    **(exception_details(error) if error else {}))
        return reason

    def _stop_trace(self, event, reason):
        source = event.source.event if isinstance(event.source, BoundGestureEvent) else event.source
        self._trace(event.trace_id, "outcome", reason, target=event.target,
                    gesture=str(getattr(source, "name", "")),
                    age_ms=round((time.monotonic() - event.created) * 1000, 2),
                    event_generation=event.generation, current_generation=self._generation,
                    phase=event.phase, current_phase=self.phase_state()["phase"],
                    composing=event.composing, writing=event.writing, edit_requested=event.edit_requested)

    def _override_stop_reason(self, event):
        if self._recording:
            return "shortcut_recording"
        if event.generation != self._generation:
            return "settings_changed"
        if time.monotonic() - event.created > 1.0:
            return "event_expired"
        if event.sentence != self.sentence():
            # Source activation can deliver its UI notification after the
            # worker captured the old idle identity. Accept only the exact
            # new identity already proved by this event's live IMK response;
            # dispatch still rechecks that proof without switching sources.
            focus = event.input_focus
            inline = self.owner._inline_input
            if not (focus is not None and focus.status == "nontext" and focus.target
                    and tuple(focus.target[:3]) == self.sentence()
                    and focus.target[3] == str(inline._view.get("application", ""))):
                return "sentence_changed"
        return ""

    @Property(QObject, constant=True)
    def catalog(self):
        return self._catalog

    @Property(bool, constant=True)
    def supported(self):
        return sys.platform == "darwin"

    @Property("QVariantList", constant=True)
    def apps(self):
        return [{"value": key, "label": value} for key, value in APP_LABELS.items()]

    @Property("QVariantList", notify=changed)
    def gestures(self):
        return [{"value": "", "label": "未绑定"}] + [
            {"value": key, "label": value} for key, value in GESTURE_LABELS.items()
            if key in GESTURE_LABELS]

    @Property("QVariantMap", notify=changed)
    def profiles(self):
        return {app: [{"action": action, "label": ACTION_LABELS[action], **vars(binding)}
                       for action, binding in actions.items() if action != "send"]
                for app, actions in self._profiles.items()}

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property("QVariantMap", notify=setupChanged)
    def wechatInfo(self):
        return self._wechat_info

    @Property(str, notify=setupChanged)
    def systemSettingsStatus(self):
        return self._settings_status

    @Property(str, notify=noticeChanged)
    def notice(self):
        return self._notice

    @Property(bool, notify=recordingChanged)
    def recording(self):
        return self._recording

    @recording.setter
    def recording(self, value):
        app = QCoreApplication.instance()
        if app is not None and bool(value) != self._recording:
            if value:
                app.installEventFilter(self)
            else:
                app.removeEventFilter(self)
        self._recording = bool(value)
        self._recording_error = ""
        self.recordingChanged.emit()

    @Property(str, notify=recordingChanged)
    def recordingError(self):
        return self._recording_error

    def eventFilter(self, watched, event):
        if event.type() not in {QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease} or not self._recording:
            return False
        field = QGuiApplication.focusObject()
        if field is None or not field.property("shortcutRecorder") or not field.property("visible"):
            return False
        event.accept()
        if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
            if event.key() == Qt.Key_Escape:
                field.setProperty("focus", False)
                self.recording = False
            else:
                try:
                    value = shortcut_from_event(event)
                except ValueError as exc:
                    self._recording_error = str(exc)
                    self.recordingChanged.emit()
                else:
                    if value:
                        self.shortcutCaptured.emit(value)
        return True

    def notify(self, text, *, duration=2200):
        self._notice = str(text)
        self.noticeChanged.emit()
        self._notice_timer.start(duration)

    @Slot()
    def dismissNotice(self):
        self._notice = ""
        self.noticeChanged.emit()

    def _save(self):
        self._generation += 1
        self.cancel_pending("手势设置已变化，请重新触发手势")
        self.owner._settings.setValue("gestures/appProfiles", profiles_to_json(self._profiles))
        self._error = ""
        self.changed.emit()

    def validate_voice(self, bindings):
        if any(name in {"swipe-up", "swipe-down"}
               for pair in bindings.as_dict().values() for name in pair):
            raise ValueError("输入状态的上下滑固定用于发送和润色")
        validate_profiles(self._profiles, bindings, frozenset())
        self._validate_source_conflict(self._profiles, bindings)

    def _unused_source_gestures(self):
        used = {g for pair in self.owner._gesture_bindings.as_dict().values() for g in pair}
        used.update({"swipe-up", "swipe-down"})
        return [g for g in GESTURE_LABELS if g not in used]

    def _validate_source_conflict(self, profiles, voice):
        gesture = self._source_gesture
        if gesture and any(gesture in pair for pair in voice.as_dict().values()):
            raise ValueError("该手势用于切换语音输入法，请先修改或关闭输入法切换手势")

    @Property(str, notify=changed)
    def inputSourceGesture(self):
        return self._source_gesture

    @Property(str, notify=changed)
    def inputSourceHint(self):
        return (GESTURE_LABELS[self._source_gesture] + " 切入 Mythlink"
                if self._source_gesture else "从菜单栏选择 Mythlink")

    @Property("QVariantList", notify=changed)
    def inputSourceOptions(self):
        return [{"value": "", "label": "未绑定（默认关闭）"}] + [
            {"value": g, "label": GESTURE_LABELS[g]} for g in self._unused_source_gestures()]

    @Slot(str, result=bool)
    def setInputSourceGesture(self, gesture):
        if gesture and gesture not in self._unused_source_gestures():
            self._error = "该手势已分配给听写或应用操作，请选择未占用的手势"
            self.changed.emit()
            return False
        self._source_gesture = gesture
        self.owner._settings.setValue("gestures/inputSourceGesture", gesture)
        self._generation += 1
        self._error = ""
        self.changed.emit()
        self.owner.gestureSettingsChanged.emit()
        return True

    def handle_input_source(self, event):
        if (self._recording or self._source_busy or self._setup_closed
                or event.generation != self._generation
                or time.monotonic() - event.created > 1.0):
            return
        if self.owner._inline_input.installing:
            self.notify("输入法正在更新，请稍后重试")
            return
        if event.error:
            self.notify(event.error)
            return
        # Explicit input-source selection must not depend on cached sentence
        # state. TIS selection is idempotent when our IME is already selected;
        # real activation/deactivation callbacks own any session transition.
        if time.monotonic() - self._source_last < 0.6:
            return
        self._source_busy = True
        self._source_last = time.monotonic()
        connection = self.owner._disconnect_event
        def work():
            error = ""
            try:
                if (self._setup_closed or connection.is_set()
                        or connection is not self.owner._disconnect_event
                        or event.generation != self._generation):
                    error = "连接或设置已变化，请重新触发手势"
                    return
                select_voice_input_source(event.pid)
            except Exception as exc:
                error = str(exc)
            finally:
                if not self._setup_closed:
                    self._inputSourceResult.emit(error)
        threading.Thread(target=work, name="SelectVoiceInputSource", daemon=True).start()

    @Slot(str)
    def _input_source_result(self, error):
        self._source_busy = False
        if self._setup_closed:
            return
        self.notify(error or "已切换到 Mythlink")
        self.owner._event_log("INPUT_SOURCE_GESTURE", result="failed" if error else "selected", reason=error)

    @Slot(str, str, str, str, bool, result=bool)
    def setBinding(self, app, action, gesture, shortcut, enabled):
        try:
            if app not in self._profiles or action not in self._profiles[app]:
                raise ValueError("应用操作不存在")
            if action == "send":
                raise ValueError("输入模式上滑固定为 Enter，适用于所有应用")
            binding = AppBinding(gesture, shortcut, enabled if gesture else False)
            profiles = {key: dict(value) for key, value in self._profiles.items()}
            profiles[app][action] = binding
            validate_profiles(profiles, self.owner._gesture_bindings, frozenset())
            self._validate_source_conflict(profiles, self.owner._gesture_bindings)
        except (ValueError, TypeError) as exc:
            self._error = str(exc)
            self.changed.emit()
            return False
        self._profiles = profiles
        self._legacy_configured = True
        self._save()
        return True

    @Slot(str)
    def resetApp(self, app):
        if app not in self._profiles:
            return
        defaults = default_profiles()[app]
        self._profiles = {**self._profiles, app: {
            action: AppBinding(b.gesture, b.shortcut, b.enabled and b.gesture not in self.owner._gesture_bindings.confirm + self.owner._gesture_bindings.end
                              and b.gesture != self._source_gesture)
            for action, b in defaults.items()}}
        self._legacy_configured = True
        self._save()

    @Slot(int, int, result=str)
    def shortcutFromKey(self, key, modifiers):
        try:
            return shortcut_from_key(key, modifiers)
        except ValueError as exc:
            self._recording_error = str(exc)
            self.recordingChanged.emit()
            return ""

    @Slot(str, str, result=str)
    def wechatMenuTitle(self, action, language):
        if action not in {"previous", "next"}:
            return ""
        if language == "auto":
            return str(self._wechat_info.get(action, ""))
        pair = MENU_TITLES.get(language)
        return pair[action == "next"] if pair else ""

    @Slot(str, str)
    def copyWeChatMenu(self, action, language):
        title = self.wechatMenuTitle(action, language)
        if not title:
            self.notify("请先重新识别微信菜单，或手动选择菜单语言")
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(title)
            self.notify("菜单名称已复制")

    @Slot()
    def refreshWeChatMenu(self):
        if not self.supported or self._setup_closed:
            return
        self._menu_generation += 1
        generation = self._menu_generation
        self._wechat_info = {**self._wechat_info, "message": "正在识别微信版本和菜单语言…"}
        self.setupChanged.emit()
        def work():
            try:
                result = detect_wechat_menu()
            except Exception as exc:
                result = {"detected": False, "message": "无法读取微信菜单：" + str(exc), "version": ""}
            if not self._setup_closed:
                self._setupResult.emit("menu", generation, result)
        threading.Thread(target=work, name="WeChatMenuSetup", daemon=True).start()

    @Slot(str, int, object)
    def _apply_setup_result(self, kind, generation, result):
        if self._setup_closed:
            return
        if kind == "menu" and generation == self._menu_generation:
            self._wechat_info = result
            self.owner._event_log("WECHAT_SETUP", operation="detect_menu",
                                  detected=bool(result.get("detected")),
                                  version=result.get("version", ""), language=result.get("language", ""))
            if result.get("detected"):
                signature = json.dumps([result["previous"], result["next"]], ensure_ascii=False)
                previous = self.owner._settings.value("gestures/wechatMenuTitles", "")
                if previous and previous != signature:
                    self._wechat_info["message"] = "微信菜单语言已变化，请同步更新系统 App 快捷键中的菜单标题"
                self.owner._settings.setValue("gestures/wechatMenuTitles", signature)
        elif kind == "settings" and generation == self._navigation_generation:
            self.owner._event_log("WECHAT_SETUP", operation="open_app_shortcuts", result=result)
            self._settings_status = {
                "ready": "已打开 App 快捷键。点击「＋」，应用选择微信，再添加下面两个菜单及对应按键。",
                "cancelled": "已停止定位。需要时可再次打开 App 快捷键。",
            }.get(result, "请在键盘设置中点击「键盘快捷键」，再在左侧选择「App 快捷键」。无需开启「功能键」开关。")
        else:
            return
        self.setupChanged.emit()

    @Slot()
    def openSystemShortcuts(self):
        if not self.supported:
            return
        self._navigation_cancel.set()
        self._navigation_cancel = cancel = threading.Event()
        self._navigation_generation += 1
        generation = self._navigation_generation
        opened = QDesktopServices.openUrl(QUrl(KEYBOARD_SETTINGS_URL))
        self._settings_status = ("正在打开 App 快捷键…" if opened
                                 else "未能打开系统设置，请进入「系统设置 → 键盘 → 键盘快捷键 → App 快捷键」。")
        self.setupChanged.emit()
        if not opened:
            return
        def navigate():
            try:
                result = KeyboardShortcutsNavigator().run(cancel)
            except Exception:
                result = "manual"
            if not self._setup_closed:
                self._setupResult.emit("settings", generation, result)
        threading.Thread(target=navigate, name="AppShortcutSettings", daemon=True).start()

    def sentence(self):
        inline = self.owner._inline_input
        return (inline._epoch, inline._client_id, inline._utterance_id)

    def foreground_identity(self):
        """Read application identity without AX focus or editor contents."""
        if sys.platform != "darwin":
            return None
        from ...platforms.macos.mac_workspace import frontmost_application
        from ...desktop.host_application import application_identity
        try:
            app = frontmost_application()
            return application_identity(app, os.getpid()) if app is not None else None
        except Exception:
            return None

    def foreground_has_binding(self, name):
        # No mappings anywhere needs no workspace read. Otherwise, only the
        # foreground app's regular and enabled-scene bindings can claim input.
        if not self._catalog.uses(name) and not self._catalog.uses_scene(name):
            return False
        front = self.foreground_identity()
        if front is None:
            return True  # An unreadable foreground is not proof of no conflict.
        return (self._catalog.uses(name, bundle=front[0])
                or self._catalog.uses_scene(name, bundle=front[0]))

    def phase_state(self):
        owner = self.owner
        inline = getattr(owner, "_inline_input", None)
        view = inline._view if inline is not None and inline.enabled else {}
        phase = str(view.get("phase", "idle"))
        if getattr(owner, "_utterance_active", False) and phase in {"idle", "dictated", "edited", "undone", "interrupted", "error"}:
            phase = "listening"
        if owner._inline_requests and phase != "polishing":
            phase = "editing"
        if inline is not None and inline._action_pending:
            phase = "applying"
        writing = bool(view.get("awaiting_readback") or (inline and inline._reply_stage == "final")
                       or (inline and inline._action_pending) or view.get("handoff_state") == "pending"
                       or getattr(owner, "_undo_running", False))
        return {"phase": phase, "composing": bool(view.get("has_composition")),
                "writing": writing, "edit_requested": bool(view.get("edit_requested")) and phase not in {"edited", "dictated", "undone", "idle"}}

    def envelope(self, event, *, audio_busy=False, start_focus=None, input_only=False):
        """Only gestures sharing an input binding need focus-based arbitration."""
        source = event.event if isinstance(event, BoundGestureEvent) else event
        name = str(getattr(source, "name", ""))
        bindings = event.bindings if isinstance(event, BoundGestureEvent) else self.owner._gesture_bindings
        input_priority = name in self.input_gestures(bindings)
        created, generation = time.monotonic(), self._generation
        if (name and name == self._source_gesture
                and not self.owner._global_gesture_bindings.action_for(name)
                and not self.foreground_has_binding(name)):
            # Preserve the explicit input-source startup entry without requiring AX.
            try:
                pid, error = foreground_pid(), ""
            except Exception as exc:
                pid, error = 0, str(exc)
            return InputSourceGestureEvent(event, generation, created, pid, error)
        state, sentence = self.phase_state(), self.sentence()
        busy = bool(audio_busy or self.owner._ring_gestures.speech_busy())
        trace_id = new_trace_id()
        self._trace(trace_id, "received", "received", gesture=name, route="focus", **state)
        target, error, capture_error = None, None, ""
        input_method_target = None
        try:
            inline = self.owner._inline_input
            # WeChat can retain a valid IMK text client while AX returns
            # NoValue permanently. Ask that client live; a retained sentence
            # or ready flag alone must never override application focus.
            if input_priority and not busy:
                if start_focus is None:
                    start_focus = inline.capture_start_focus()
                if start_focus is None:
                    raise SceneActionError("input_method_not_ready")
                if start_focus is not None:
                    self._trace(trace_id, "input_focus", start_focus.reason or start_focus.status,
                                gesture=name, elapsed_ms=start_focus.elapsed_ms)
                    if start_focus.status == "text":
                        input_method_target = start_focus.target
                    elif start_focus.status == "unavailable":
                        raise SceneActionError(start_focus.reason)
                    elif input_only:
                        raise SceneActionError("input_method_no_focus")
            if busy and input_priority and name in {"swipe-up", "swipe-down"}:
                input_method_target = inline.capture_gesture_target()
                if input_method_target is None:
                    raise SceneActionError("input_method_not_ready")
            # Cancelling/converting a live sentence needs its session identity,
            # not an AX lookup that could delay cancellation after focus loss.
            if input_method_target is not None:
                if name == "swipe-up":
                    # Enter still needs a real native window/PID target. IMK
                    # proves its text client; AX only pins the window here.
                    target = self.backend.capture(plain_enter=True, focus_sensitive=False)
                    if (target is None or (target.bundle, target.pid) !=
                            (input_method_target[3], input_method_target[4])):
                        target, input_method_target = None, None
            elif not busy or name in {"swipe-up", "swipe-down"}:
                front = None
                scene_capture = False
                if self._catalog.uses_scene(name):
                    front = self.foreground_identity()
                    if front is None:
                        raise SceneActionError("target_unavailable")
                    scene_capture = self._catalog.uses_scene(name, bundle=front[0])
                target = self.backend.capture(menu_action=True,
                    **({"scene": True} if scene_capture else {}),
                    focus_sensitive=False)
                if target is not None and front is not None and (target.bundle, target.pid) != front:
                    target = None
                    raise SceneActionError("foreground_changed")
                if (target is not None and start_focus is not None and start_focus.target
                        and (target.bundle, target.pid) != (start_focus.target[3], start_focus.target[4])):
                    target = None
                    raise SceneActionError("foreground_changed")
        except Exception as exc:
            error, capture_error = exc, f"{type(exc).__name__}: {exc}"
            input_method_target = None
        if input_method_target is not None:
            capture_reason = "input_method_focus"
            self._trace(trace_id, "capture", capture_reason, gesture=name,
                        app=input_method_target[3], pid=input_method_target[4])
        else:
            capture_reason = self._capture_trace(trace_id, name, target, error)
        context = target.input_context if target is not None and not target.blocked else "unknown"
        if start_focus is not None and start_focus.status == "nontext":
            # AX is still useful for the outside app/scene action, but its
            # cached text classification cannot override a fresh IMK rejection.
            context = "nontext"
        available = target is not None and not target.blocked
        route = ("blocked" if error is not None else
                 "input" if input_method_target is not None or busy else
                 "outside" if available and (not input_priority or context == "nontext") else "blocked")
        scene = (target.scene if route == "outside" and target.scene in
                 self._catalog.configured_scenes().get(target.bundle, []) else "")
        self._trace(trace_id, "route", "input_method_focus" if input_method_target is not None else "speech_busy" if busy else context if input_priority else "no_input_conflict",
                    target=target, gesture=name, owner=route, active_scene=scene)
        return AppGestureEvent(BoundGestureEvent(source, bindings),
            target, generation, created, sentence=sentence, capture_error=capture_error,
            capture_reason=capture_reason, trace_id=trace_id, route=route,
            speech_owned=busy, scene=scene, exclusive=route != "input",
            input_method_target=input_method_target, input_focus=start_focus, **state)

    def input_gestures(self, bindings=None):
        bindings = bindings or self.owner._gesture_bindings
        return {"swipe-up", "swipe-down"} | {g for pair in bindings.as_dict().values() for g in pair if g} | (
            {self._source_gesture} if self._source_gesture else set())

    def scene_envelope(self, event, *, trace_id=None):
        # Compatibility entry: only shared input bindings need focus arbitration.
        captured = self.envelope(event)
        return captured if isinstance(captured, AppGestureEvent) and captured.exclusive else None

    def outside_binding(self, event):
        target = event.target
        if target is None:
            return None
        source = event.source.event if isinstance(event.source, BoundGestureEvent) else event.source
        name = str(getattr(source, "name", ""))
        catalog = self._catalog
        if event.scene:
            action = "scene:" + name
            binding = catalog.for_scene(target.bundle, event.scene).get(action)
            if binding is not None:
                return action, binding
        action = "menu:" + name
        binding = catalog.for_target(target.bundle).get(action)
        if binding is not None:
            return action, binding
        # Only applications explicitly present in the catalog own app gestures.
        # Retained legacy profiles must not silently override global bindings.
        return None

    def input_event_valid(self, event):
        if self._override_stop_reason(event):
            self._stop_trace(event, self._override_stop_reason(event))
            return False
        # A live sentence keeps its cancellation route even if focus is lost.
        if event.speech_owned and self.owner._ring_gestures.speech_busy():
            return True
        if event.input_method_target is not None:
            valid = self.owner._inline_input.same_gesture_target(event.input_method_target)
            if not valid:
                self._stop_trace(event, "input_method_focus_changed")
            return valid
        self._stop_trace(event, "text_focus_required")
        return False

    def handle_override(self, event):
        """Consumed overrides never fall through, even if stale or unassigned."""
        reason = self._override_stop_reason(event)
        if reason:
            self._stop_trace(event, reason)
            return
        if event.capture_error:
            reason = event.capture_reason or "capture_failed"
            self._stop_trace(event, reason)
            self.notify(reason_message(reason))
            return
        target = event.target
        if target is not None and target.blocked:
            reason = target.diagnostic.get("reason", "blocked_window")
            self._stop_trace(event, reason)
            self.notify(reason_message(reason))
            return
        if target is None or self.owner._ring_gestures.speech_busy():
            self._stop_trace(event, "target_unavailable" if target is None else "speech_busy")
            return
        if action_phase("next", phase=event.phase, composing=event.composing,
                        writing=event.writing, edit_requested=event.edit_requested) != "dispatch":
            self._stop_trace(event, "phase_blocked")
            return
        source = event.source.event if isinstance(event.source, BoundGestureEvent) else event.source
        name = str(getattr(source, "name", ""))
        if target.focus_sensitive and target.input_context != "nontext":
            self._stop_trace(event, "text_focus" if target.input_context == "text" else "unknown_focus")
            return
        selected = self.outside_binding(event)
        if selected is not None:
            action, binding = selected
            self._dispatch(action, binding, target, trace_id=event.trace_id, input_focus=event.input_focus)
        else:
            self._stop_trace(event, "binding_missing")

    def handle_scene(self, event):
        """A claimed scene gesture never falls through to a global/voice action."""
        source = event.source.event if isinstance(event.source, BoundGestureEvent) else event.source
        action = "scene:" + str(getattr(source, "name", ""))
        target = event.target
        reason = ("target_unavailable" if target is None else
                  "blocked_window" if target.blocked else
                  "scene_changed" if not event.scene or target.scene != event.scene else
                  "text_focus" if target.focus_sensitive and target.input_context == "text" else
                  "unknown_focus" if target.focus_sensitive and target.input_context != "nontext" else
                  self._override_stop_reason(event) or
                  ("speech_busy" if self.owner._ring_gestures.speech_busy() else ""))
        if not reason and action_phase("next", phase=event.phase, composing=event.composing,
                writing=event.writing, edit_requested=event.edit_requested) != "dispatch":
            reason = "phase_blocked"
        if reason:
            self._stop_trace(event, reason)
            return
        binding = self._catalog.for_scene(target.bundle, event.scene).get(action)
        if binding is not None:
            self._dispatch(action, binding, target, trace_id=event.trace_id, input_focus=event.input_focus)
        else:
            self._stop_trace(event, "binding_missing")

    def is_menu_mapping_event(self, event) -> bool:
        """Only explicit menu mappings may use the operation-mode app route."""
        if not isinstance(event, AppGestureEvent):
            return False
        if event.exclusive or event.scene:
            return True  # handle_scene revalidates settings, speech and native context.
        source = event.source.event if isinstance(event.source, BoundGestureEvent) else event.source
        name = str(getattr(source, "name", ""))
        if not self._catalog.uses(name):
            return False
        if event.capture_error:
            return True  # Let handle() report the failed capture without dispatching.
        target = event.target
        return bool(target is not None and target.menu_action
                    and "menu:" + name in self._catalog.for_target(target.bundle))

    def handle(self, event: AppGestureEvent) -> bool:
        if event.exclusive:
            self.handle_override(event)
            return True
        source = event.source.event if isinstance(event.source, BoundGestureEvent) else event.source
        name = str(getattr(source, "name", ""))
        target = event.target
        if event.capture_error:
            self.notify("应用手势无法读取前台应用，请查看运行日志")
            self.owner._event_log("APP_GESTURE", gesture=name, result="blocked",
                                  reason=event.capture_reason or "capture_failed", trace=event.trace_id)
            self._stop_trace(event, event.capture_reason or "capture_failed")
            return True
        if target is None and event.input_method_target is None:
            self._stop_trace(event, event.capture_reason or "target_unavailable")
            return False
        actions = ({"send": AppBinding("swipe-up", "Return")} if name == "swipe-up"
                   else {"polish": _PolishBinding()} if name == "swipe-down"
                   else {})
        action = next((key for key, b in actions.items() if b.enabled and b.gesture == name), None)
        if action is None:
            self._stop_trace(event, "binding_missing")
            return False
        if self._recording:
            self._stop_trace(event, "shortcut_recording")
            return True
        if event.generation != self._generation or time.monotonic() - event.created > 1.0:
            self._stop_trace(event, "settings_changed" if event.generation != self._generation else "event_expired")
            self.notify("操作已过期，请重新触发手势")
            return True
        if target is not None and target.blocked:
            self._stop_trace(event, target.diagnostic.get("reason", "blocked_window"))
            reason = target.diagnostic.get("reason", "")
            self.notify((reason_message(reason) if reason else "当前有弹窗或菜单，请关闭后在目标应用中重试")
                        if target.menu_action else "请回到主聊天输入区域操作")
            self.owner._event_log("APP_GESTURE", app=target.bundle, gesture=name,
                                  result="blocked", reason="blocked_window_or_control")
            return True
        phase_action = "next" if action.startswith("menu:") else "send" if action == "polish" else action
        original = action_phase(phase_action, phase=event.phase, composing=event.composing,
                                writing=event.writing, edit_requested=event.edit_requested)
        current = action_phase(phase_action, **self.phase_state())
        if original not in {"dispatch", "finish_then_send"}:
            self._stop_trace(event, "phase_blocked")
            self.notify(original)
            return True
        if current not in {"dispatch", "finish_then_send"}:
            self._stop_trace(event, "phase_blocked")
            self.notify(current)
            return True
        just_committed = (original == "finish_then_send" and current == "dispatch"
                          and event.sentence == self.sentence()
                          and self.owner._inline_input._view.get("phase") == "dictated"
                          and bool(str(self.owner._inline_input._view.get("raw", "")).strip())
                          and not self.owner._inline_input.error)
        if (original != current and not just_committed) or (original == "finish_then_send" and event.sentence != self.sentence()):
            self._stop_trace(event, "sentence_changed")
            self.notify("本句状态已变化，请重新触发手势")
            return True
        if self._pending is not None:
            self._stop_trace(event, "pending_commit")
            if action not in {"send", "polish"}:
                self.notify("正在等待本句定稿，请稍后再切换对话")
            return True
        if current == "finish_then_send":
            inline = self.owner._inline_input
            if not inline.enabled or not inline.ready or not inline._utterance_id:
                self.notify("听写输入框尚未就绪，请稍后重新触发手势")
                return True
            try:
                same_target = (inline.same_gesture_target(event.input_method_target)
                               if event.input_method_target is not None else
                               self.backend.same_target(target, require_focus=True))
            except Exception:
                same_target = False
            bundle = event.input_method_target[3] if event.input_method_target is not None else target.bundle
            if inline._view.get("application") != bundle or not same_target:
                self.notify("输入目标已变化，请在原输入框定稿后重试")
                return True
            self._pending = (event, actions[action])
            self._pending_timer.start(12000)
            self.owner._finish_utterance_event.set()
            inline.finish()
            self.notify("正在定稿，完成后润色；左滑可取消" if action == "polish" else
                        "正在定稿，完成后发送；左滑可取消", duration=12000)
            return True
        if action == "polish":
            self._dispatch_polish(event, restore_raw_on_undo=original != "finish_then_send")
            return True
        self._dispatch(action, actions[action], target, trace_id=event.trace_id,
                       input_method_target=event.input_method_target)
        return True

    def _dispatch_polish(self, event, *, restore_raw_on_undo):
        inline, target = self.owner._inline_input, event.target
        if (event.sentence != self.sentence() or not inline.ready or not inline._utterance_id
                or inline._view.get("phase") != "dictated" or not str(inline._view.get("raw", "")).strip()
                or inline.error or inline._view.get("edit_requested")):
            self.notify("当前没有可润色的本句听写")
            return
        if inline._view.get("polished"):
            self.notify("本句已润色")
            return
        try:
            same_target = (inline.same_gesture_target(event.input_method_target)
                           if event.input_method_target is not None else
                           self.backend.same_target(target, require_focus=True))
        except Exception:
            same_target = False
        bundle = event.input_method_target[3] if event.input_method_target is not None else target.bundle
        if inline._view.get("application") != bundle or not same_target:
            self.notify("输入目标已变化，未执行润色")
            return
        if inline.polish(restore_raw_on_undo=restore_raw_on_undo):
            self.dismissNotice()
            self.owner._event_log("APP_GESTURE", app=bundle, action="polish", result="requested")

    def _dispatch(self, action, binding, target, *, trace_id="", input_method_target=None, input_focus=None):
        trace_id = trace_id or new_trace_id()
        signature = (target.bundle, target.pid, action, target.scene, target.page_key,
                     target.document_key, binding.shortcut)
        now = time.monotonic()
        if self._last_dispatch and self._last_dispatch[0] == signature and now - self._last_dispatch[1] < 0.35:
            self._trace(trace_id, "outcome", "duplicate_suppressed", target=target, action=action)
            return
        self._trace(trace_id, "dispatch", "dispatch_started", target=target, action=action, shortcut=binding.shortcut)
        try:
            if input_method_target is not None:
                inline = self.owner._inline_input
                valid = (inline.same_gesture_target(input_method_target) if len(input_method_target) == 6
                         else inline.capture_gesture_target() == input_method_target)
                if not valid:
                    raise RuntimeError("输入目标已变化，未发送快捷键")
            if action == "send" and not target.plain_enter and target.input_context != "text":
                raise RuntimeError("请先点入消息输入框，再上滑发送")
            from ...browser_bridge.protocol import media_action
            direct = media_action(binding.action_id) if target.browser_target and action.startswith("scene:") else ""
            if (action.startswith(("scene:", "menu:", "global:"))
                    and (target.browser_target or target.pid == os.getpid())):
                # Our Qt windows must keep servicing IMK/AX while the worker
                # validates them. The existing serial queue also pins settings
                # and connection state until immediately before dispatch.
                self._dispatch_browser(action, binding, target, direct, trace_id, signature, now, input_focus)
                return
            if input_focus is not None and not self.owner._inline_input.same_input_focus(input_focus):
                raise SceneActionError("input_method_focus_changed")
            if direct:
                self.backend.browser_action(target, direct)
            else:
                self.backend.post(target, binding.shortcut, require_focus=action == "send")
            self._trace(trace_id, "outcome", "browser_action_applied" if direct else "shortcut_posted",
                        target=target, action=action, shortcut=binding.shortcut,
                        delivery="acknowledged" if direct else "posted_unverified", native=self._native_diagnostic())
            self._last_dispatch = (signature, now)
            if not action.startswith("scene:"):
                self._retire_sentence(target, sent=action == "send")
            self.owner._event_log("APP_GESTURE", app=target.profile, action=action,
                                  shortcut=binding.shortcut, result="posted")
            self.dismissNotice()
        except Exception as exc:
            native = getattr(exc, "diagnostic", {}) or self._native_diagnostic()
            reason = ("permission_denied" if isinstance(exc, MacPermissionError) else
                      getattr(exc, "reason", "") or native.get("reason") or "native_exception")
            # A successful capture/validation is not the cause of a later exception.
            if reason in {"ready", "captured", "recognized", "target_verified", "shortcut_posted", "dispatch_started"}:
                reason = "native_exception"
            self._trace(trace_id, "outcome", reason, target=target, action=action,
                        shortcut=binding.shortcut, native=native, **exception_details(exc))
            permissions = getattr(self.owner._inline_input, "permissions", None)
            if permissions is not None:
                permissions.report_error(exc)
            self.notify(reason_message("delivery_unknown" if native.get("delivery") == "unknown" else reason)
                        if action.startswith(("menu:", "scene:", "global:")) else str(exc))
            self.owner._event_log("APP_GESTURE", app=target.profile, action=action,
                                  result="blocked", reason=reason, trace=trace_id, error_type=type(exc).__name__)

    def _dispatch_browser(self, action, binding, target, direct, trace_id, signature, created, input_focus=None):
        if (self._last_browser_submit and self._last_browser_submit[0] == signature
                and created - self._last_browser_submit[1] < .35):
            self._trace(trace_id, "outcome", "duplicate_suppressed", target=target, action=action)
            return
        generation, connection = self._generation, self.owner._disconnect_event

        def valid():
            if (self._setup_closed or generation != self._generation
                    or connection is not self.owner._disconnect_event or connection.is_set()):
                return "gesture_state_changed"
            if time.monotonic() - created > 1.0:
                return "browser_command_expired"
            if self.owner._ring_gestures.speech_busy():
                return "speech_busy"
            return ""

        def work():
            # Both DOM actions and user shortcuts keep native foreground,
            # window, page, player and input-focus validation. Only IO moves
            # off the UI thread; capture thread-local diagnostics here too.
            self._trace(trace_id, "execute", "dispatch_started", target=target, action=action)
            try:
                if input_focus is not None and not self.owner._inline_input.same_input_focus(input_focus):
                    raise SceneActionError("input_method_focus_changed")
                if direct:
                    self.backend.browser_action(target, direct)
                else:
                    self.backend.post(target, binding.shortcut, require_focus=False)
                return self._native_diagnostic(), None
            except Exception as exc:
                return getattr(exc, "diagnostic", {}) or self._native_diagnostic(), exc

        def complete(result, queue_error):
            native, error = result if result is not None else ({}, queue_error)
            if error is None:
                self._trace(trace_id, "outcome", "browser_action_applied" if direct else "shortcut_posted",
                            target=target, action=action, shortcut=binding.shortcut,
                            delivery="acknowledged" if direct else "posted_unverified", native=native)
                self._last_dispatch = (signature, created)
                self.owner._event_log("APP_GESTURE", app=target.profile, action=action,
                                      shortcut=binding.shortcut, result="posted")
                if not valid():
                    if not action.startswith("scene:"):
                        self._retire_sentence(target)
                    self.dismissNotice()
                return
            reason = ("permission_denied" if isinstance(error, MacPermissionError) else
                      getattr(error, "reason", "") or native.get("reason") or "native_exception")
            if reason in {"ready", "captured", "recognized", "target_verified", "shortcut_posted", "dispatch_started"}:
                reason = "native_exception"
            self._trace(trace_id, "outcome", reason, target=target, action=action,
                        shortcut=binding.shortcut, native=native, **exception_details(error))
            self.owner._event_log("APP_GESTURE", app=target.profile, action=action,
                                  result="blocked", reason=reason, trace=trace_id, error_type=type(error).__name__)
            if not valid():
                permissions = getattr(self.owner._inline_input, "permissions", None)
                if permissions is not None:
                    permissions.report_error(error)
                self.notify(reason_message("delivery_unknown" if native.get("delivery") == "unknown" else reason))

        if self._browser_actions.submit(work, valid, complete):
            self._last_browser_submit = (signature, created)
        else:
            self._trace(trace_id, "outcome", "browser_command_expired", target=target, action=action)

    def _retire_sentence(self, target, *, sent=False):
        inline = self.owner._inline_input
        if inline.enabled and inline._view.get("application") == target.bundle:
            inline.release_completed_shortcut(sent=sent)

    def settled(self, phase, text):
        if self._pending is None:
            return
        event, binding = self._pending
        if event.sentence != self.sentence() or phase != "dictated" or not str(text).strip():
            self.cancel_pending("本句没有成功定稿，已取消后续操作")
            return
        if (self._generation != event.generation or self.owner._disconnect_event.is_set()
                or not self.owner._connected or not self.owner._runtime_active):
            self.cancel_pending("连接或设置已变化，已取消后续操作")
            return
        if action_phase("send", **self.phase_state()) != "dispatch":
            self.cancel_pending("文字尚未完成应用，请完成后重新触发手势")
            return
        self._pending = None
        self._pending_timer.stop()
        if binding.gesture == "swipe-down":
            self._dispatch_polish(event, restore_raw_on_undo=False)
        else:
            self._dispatch("send", binding, event.target, trace_id=event.trace_id,
                           input_method_target=event.input_method_target)

    def state_changed(self):
        if self._pending is None:
            return
        event, _ = self._pending
        inline = self.owner._inline_input
        if (event.sentence != self.sentence() or not inline.ready
                or inline._view.get("phase") in {"editing", "edited", "polishing", "undone", "interrupted", "error"}
                or inline._view.get("edit_requested")):
            self.cancel_pending("本句已取消、转编辑或切换输入框，已取消后续操作")

    def cancel_pending(self, reason=""):
        if self._pending is not None:
            self._pending = None
            self._pending_timer.stop()
            if reason:
                self.notify(reason)
            else:
                self.dismissNotice()

    def close(self):
        self._browser_actions.close()
        self._catalog.close()
        app = QCoreApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.recording = False
        self._setup_closed = True
        self._navigation_cancel.set()
        self.cancel_pending()
        self._notice_timer.stop()
