"""Focus-based gesture ownership, ahead of the audio endpoint and shortcuts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import threading
import time
import sys

from PySide6.QtCore import QObject, Property, QTimer, Qt, Signal, Slot

from ...gestures.app_gestures import QUIET_PHASES
from ...gestures.gesture_settings import (GESTURE_LABELS, GLOBAL_ACTION_LABELS, GLOBAL_BINDINGS_KEY,
                                VOICE_GESTURE_GROUP, ApplicationChoice, GLOBAL_APPLICATIONS_KEY,
                                MAX_RECENT_APPLICATIONS)
from ...scenes.registry import SCENE_LABELS
from ...desktop.host_application import HOST_BUNDLE
from ...diagnostics.scene_diagnostics import reason_message
from ..desktop.text_focus_controller import TextFocusController
from ..desktop.page_scroll_controller import PageScrollController
from ..desktop.window_selector_controller import WindowSelectorController
from ..desktop.application_launcher import ApplicationLauncher
from ...screenshots.saved_capture import SavedCaptureController
from .app_gesture_controller import InputSourceGestureEvent
from ...gestures.history import observe_gesture
from ...ring_devices import matches_tap_gesture


@dataclass(frozen=True)
class ModeGestureEvent:
    source: object
    generation: int
    transition: object = None


@dataclass(frozen=True)
class MenuRequest:
    name: str
    generation: int
    created: float
    busy: bool
    transition: object = None
    stamp: object = None
    selection: object = None
    target: object = None
    app_generation: int = -1
    trace_id: str = ""
    input_focus: object = None
    application: object = None


class RingGestureController(QObject):
    changed = Signal()
    showRequested = Signal(str, str)
    mainWindowRequested = Signal()
    hideRequested = Signal()
    sceneHudRequested = Signal(str, object)
    _sceneHudReady = Signal(object, object)
    _requested = Signal(object, object)
    _sceneRequested = Signal(object, object)
    _globalTargetReady = Signal(object, object, bool, object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._recent_applications = self._load_recent_applications()
        self._lock = threading.Lock()
        self._mode = "input"
        self._generation = 0
        self._session_was_blocked = False
        self._transition = None
        self._pending_global = None
        self._closed = False
        self._pending_hud = None
        self._hud_timer = QTimer(self)
        self._hud_timer.setSingleShot(True)
        self._hud_timer.setTimerType(Qt.PreciseTimer)
        self._hud_timer.setInterval(1000)
        self._hud_timer.timeout.connect(self._scene_hud_fallback)
        # A newer notice or an explicit hide supersedes optional scene lookup.
        self.showRequested.connect(self._cancel_scene_hud)
        self.sceneHudRequested.connect(self._cancel_scene_hud)
        self.hideRequested.connect(self._cancel_scene_hud)
        self.changed.connect(self._cancel_scene_hud)
        self._selector = WindowSelectorController(self)
        self._application_launcher = ApplicationLauncher(self)
        self._screenshot = SavedCaptureController(owner, self)
        self._screenshot.savedNotice.connect(lambda message: owner._app_gestures.notify(message, duration=3500))
        # Retained compatibility API; field discovery/selection is retired.
        self._fields = TextFocusController(self, enabled=False)
        self._scroll = PageScrollController(self)
        self._scroll.notice.connect(lambda message: self.showRequested.emit(self.mode, message))
        self._selector.notice.connect(lambda message: self.showRequested.emit(self.mode, message))
        # Always queue: firmware callbacks must never manipulate Qt windows.
        self._requested.connect(self._apply, Qt.QueuedConnection)
        self._sceneRequested.connect(self._deliver_gesture, Qt.QueuedConnection)
        self._sceneHudReady.connect(self._show_scene_hud, Qt.QueuedConnection)
        self._globalTargetReady.connect(self._apply_global_target, Qt.QueuedConnection)
        owner.connectedChanged.connect(self._connection_changed)
        from ..shared.clipboard_suggestion import ClipboardSuggestionController
        self._clipboard_suggestion = ClipboardSuggestionController(owner, self)

    def _load_recent_applications(self):
        raw = self.owner._settings.value(GLOBAL_APPLICATIONS_KEY, None)
        try:
            # Migrate existing launch bindings once, without changing assignments.
            records = (json.loads(str(raw)) if raw is not None else [
                dict(bundle=item.bundle, application_name=item.application_name)
                for item in reversed(self.owner._global_gesture_bindings.applications)])
            if not isinstance(records, list):
                raise ValueError("打开应用记录格式无效")
            choices = {}
            for record in records:
                choice = ApplicationChoice(**record)
                choices.setdefault(choice.bundle, choice)
            recent = tuple(choices.values())[:MAX_RECENT_APPLICATIONS]
        except (TypeError, ValueError):
            return ()  # A damaged history never resets the actual gesture bindings.
        if raw is None:
            self.owner._settings.setValue(GLOBAL_APPLICATIONS_KEY,
                json.dumps([asdict(item) for item in recent], ensure_ascii=False))
        return recent

    def _remember_application(self, application):
        choice = ApplicationChoice(application.bundle, application.application_name)
        recent = (choice, *(item for item in self._recent_applications if item.bundle != choice.bundle))[:MAX_RECENT_APPLICATIONS]
        if recent == self._recent_applications:
            return
        self.owner._settings.setValue(GLOBAL_APPLICATIONS_KEY,
            json.dumps([asdict(item) for item in recent], ensure_ascii=False))
        self._recent_applications = recent
        self.changed.emit()

    @Property(str, notify=changed)
    def mode(self):
        with self._lock:
            return self._mode

    @Property(str, notify=changed)
    def modeLabel(self):
        return "输入中" if self.mode == "input" else "应用手势"

    @Property(QObject, constant=True)
    def textFields(self):
        return self._fields

    @Property(QObject, constant=True)
    def windowSelector(self):
        return self._selector

    @Property("QVariantMap", notify=changed)
    def globalBindings(self):
        return self.owner._global_gesture_bindings.assignments()

    @Property("QVariantMap", notify=changed)
    def globalActionLabels(self):
        return self.owner._global_gesture_bindings.labels()

    @Property("QVariantList", constant=True)
    def globalActions(self):
        return [dict(value=key, label=label) for key, label in GLOBAL_ACTION_LABELS.items()]

    @Property(QObject, constant=True)
    def screenshot(self):
        return self._screenshot

    @Slot(str, result="QVariantList")
    def globalGestureOptions(self, action):
        used = {g for key, g in self.globalBindings.items() if key != action}
        return [dict(value="", label="无")] + [dict(value=g, label=label) for g, label in GESTURE_LABELS.items() if g not in used]

    def _global_gesture_editable(self, gesture):
        return gesture in GESTURE_LABELS

    @Slot(str, result="QVariantList")
    def globalActionOptions(self, gesture):
        if not self._global_gesture_editable(gesture):
            return []
        bindings = self.owner._global_gesture_bindings
        assignments, labels = bindings.assignments(), bindings.labels()
        previous_action = bindings.action_for(gesture)
        options = [dict(id="", label="无", path="解除此手势的全局绑定", shortcut="", available=True,
                        effect="此手势不执行全局功能，可在应用中配置动作。")]
        if sys.platform in {"darwin", "win32"}:
            options.append(dict(id="open_application", label="打开应用", path="选择要打开的本机应用", shortcut="",
                                available=True, effect="触发后打开所选应用；已打开时切换到该应用。"))
            options.extend(dict(id="application-choice:" + item.bundle, label=item.label,
                                path="最近选择的应用 · 全局手势共用", shortcut="", applicationBundle=item.bundle,
                                available=True, effect=f"选择后，{GESTURE_LABELS[gesture]}用于「{item.label}」。")
                           for item in self._recent_applications)
        action_order = [item.action_id for item in reversed(bindings.custom_shortcuts)] + list(GLOBAL_ACTION_LABELS)
        for action in action_order:
            label = labels[action]
            previous_gesture = assignments[action]
            if previous_gesture == gesture:
                effect = "此手势当前用于「" + label + "」。"
            elif previous_action and previous_gesture:
                effect = (f"选择后，{GESTURE_LABELS[gesture]}用于「{label}」，"
                          f"{GESTURE_LABELS[previous_gesture]}用于「{labels[previous_action]}」。")
            elif previous_gesture:
                effect = (f"选择后，{GESTURE_LABELS[gesture]}用于「{label}」，"
                          f"释放{GESTURE_LABELS[previous_gesture]}的全局占用。")
            else:
                effect = f"选择后，{GESTURE_LABELS[gesture]}用于「{label}」。"
            custom = bindings.custom_for(previous_gesture) if action.startswith('custom:') else None
            options.append(dict(id=action, label=label, path="当前绑定：" + GESTURE_LABELS.get(previous_gesture, "无"),
                                shortcut=custom.shortcut if custom else "", custom=custom is not None,
                                available=True, effect=effect))
        return options

    @Slot(str, result="QVariantMap")
    def globalBindingAction(self, gesture):
        bindings = self.owner._global_gesture_bindings
        application = bindings.application_for(gesture)
        if application is not None:
            # The saved binding stays visible even if its app ages out of history.
            return dict(id=application.action_id, label=application.label, path="打开应用", shortcut="",
                        applicationBundle=application.bundle, available=True,
                        effect="此手势当前用于「" + application.label + "」。")
        action = bindings.action_for(gesture)
        return next((item for item in self.globalActionOptions(gesture) if action and item["id"] == action), {})

    @Slot(str, str, result=bool)
    def setGlobalGestureAction(self, gesture, action):
        """Gesture-first editor: move a function, or exchange two assignments atomically."""
        try:
            updated = self.owner._global_gesture_bindings.with_action(gesture, action)
        except ValueError as exc:
            self.owner._app_gestures.notify(str(exc))
            return False
        return self._save_global_bindings(updated)

    @Slot(str, str, str, result=bool)
    def setGlobalCustomBinding(self, gesture, label, shortcut):
        try:
            updated = self.owner._global_gesture_bindings.with_custom(gesture, label, shortcut)
        except (TypeError, ValueError) as exc:
            self.owner._app_gestures.notify(str(exc))
            return False
        return self._save_global_bindings(updated)

    @Slot(str, str, result=bool)
    def setGlobalApplicationBinding(self, gesture, bundle):
        candidate = next((app for app in self.owner.appGestures.catalog.candidates if app["value"] == bundle), None)
        if candidate is None:
            recent = next((item for item in self._recent_applications if item.bundle == bundle), None)
            if recent is not None:
                candidate = dict(value=recent.bundle, label=recent.application_name)
        if candidate is None or sys.platform not in {"darwin", "win32"}:
            self.owner._app_gestures.notify("未找到此应用，请刷新应用列表后重试")
            return False
        try:
            updated = self.owner._global_gesture_bindings.with_application(gesture, bundle, candidate["label"])
        except (TypeError, ValueError) as exc:
            self.owner._app_gestures.notify(str(exc))
            return False
        if not self._save_global_bindings(updated):
            return False
        if bundle.startswith("win.") and candidate.get("path"):
            self.owner._settings.setValue("gestures/windowsLaunch/" + bundle, candidate["path"])
        # History is shared independently of gesture transactions and survives undo.
        self._remember_application(updated.application_for(gesture))
        return True

    @Slot(str, str, result=bool)
    def setGlobalBinding(self, action, gesture):
        if action not in GLOBAL_ACTION_LABELS or gesture not in {row["value"] for row in self.globalGestureOptions(action)}:
            return False
        if not gesture:
            previous = self.owner._global_gesture_bindings.as_dict()[action]
            return self.setGlobalGestureAction(previous, '') if previous else True
        return self.setGlobalGestureAction(gesture, action)

    def _save_global_bindings(self, bindings):
        if self.owner._global_gesture_bindings == bindings:
            return True
        if self.speech_busy() or self._scroll.applying.is_set():
            self.owner._app_gestures.notify("请先结束当前操作，再修改全局手势")
            return False
        self.owner._settings.setValue(GLOBAL_BINDINGS_KEY, bindings.to_json())
        with self._lock:
            self.owner._global_gesture_bindings = bindings
            self._generation += 1
            self._transition = None
        self._selector.cancel()
        self.hideRequested.emit()
        service = self.owner._app_gestures
        service._generation += 1
        service.cancel_pending()
        self.changed.emit()
        service.changed.emit()
        service.catalog.changed.emit()
        self.owner.gestureSettingsChanged.emit()
        return True

    def session_blocked(self):
        proximity = getattr(self.owner, "_proximity", None)
        return proximity is not None and proximity.gestures_blocked

    def allow_immediate_end_tap(self):
        owner = self.owner
        agent = owner._agent.sessions.active
        if (self.session_blocked() or self._selector.blocked.is_set() or self._screenshot.active
                or self._clipboard_suggestion._offer is not None or owner._voice_practice.active
                or owner._touchpad.blocksVoice or owner._touchpad.strokeSession.engaged
                or agent.workspaceActive or agent.captureActive or agent.history_recommendations.visible
                or agent.recommendations.owns_input_focus()):
            return False
        service = owner._app_gestures
        return not (owner._global_gesture_bindings.action_for("double-tap")
                    or service.inputSourceGesture == "double-tap"
                    or service.foreground_has_binding("double-tap"))

    def lock_state_changed(self):
        blocked = self.session_blocked()
        if blocked == self._session_was_blocked:
            return
        self._session_was_blocked = blocked
        if blocked:
            with self._lock:
                self._generation += 1
                self._transition = None
            self.hideRequested.emit()
            self._selector.cancel()
            self._screenshot.cancel()
            self.owner._app_gestures.cancel_pending()

    def speech_busy(self):
        owner = self.owner
        state = owner._app_gestures.phase_state()
        # Retained dictated/edited text is not a busy sentence. Preparation,
        # composition, final writeback and actual edit jobs are busy.
        return bool(
            state["phase"] not in QUIET_PHASES
            or state["composing"] or state["writing"] or state["edit_requested"]
            or owner._pending_inline_audio_start or owner._utterance_active
            or owner._app_gestures._pending is not None
            or (not owner._inline_enabled() and (
                owner._interaction_state in {"listening", "processing"}
                or owner._pending_text_requests or owner._pending_mode_routes
                or owner._pending_dictation_result is not None
            ))
        )

    def filter(self, event, audio_busy: bool, connection, *, skip_stroke=False) -> bool:
        """Worker gate: only accepted voice start/end events reach the audio endpoint."""
        identity = getattr(event, "record_id", "")
        try:
            front = self.owner._app_gestures.foreground_identity()
            observe_gesture(self.owner, identity, gesture=getattr(event, "name", ""),
                foreground_application=front[0] if front else "", application=front[0] if front else "",
                voice_state="listening" if audio_busy else self.owner._app_gestures.phase_state()["phase"],
                status="observed")
        except Exception:
            observe_gesture(self.owner, identity, gesture=getattr(event, "name", ""), status="observed")
        if connection is not self.owner._disconnect_event or connection.is_set():
            observe_gesture(self.owner, identity, status="not_triggered")
            return False
        name = str(getattr(event, "name", ""))
        proximity = getattr(self.owner, "_proximity", None)
        if proximity is not None and proximity.filter_gesture(name, connection):
            return False
        if self.session_blocked():
            observe_gesture(self.owner, identity, status="not_triggered")
            return False
        preview = getattr(self.owner, "_dictation_preview", None)
        if preview is not None and preview.consume(name, connection, audio_busy=audio_busy):
            return False
        if self._clipboard_suggestion.consume(event, connection):
            return False
        if self._screenshot.consume(event, connection):
            return False
        agent = getattr(self.owner, '_agent', None)
        if agent is not None and agent.filter_gesture(event, connection):
            return False
        touchpad = getattr(self.owner, "_touchpad", None)
        if not skip_stroke and touchpad is not None and touchpad.consume_gesture(event, connection):
            return False
        busy = bool(audio_busy or self.speech_busy())
        global_action = self.owner._global_gesture_bindings.action_for(name)
        canonical = {"show_menu": "index-pinch", "window_selector": "clench"}
        if self._selector.blocked.is_set():
            selector_name = canonical.get(global_action, name)
            if (name != "tap" and global_action != "window_selector"
                    and matches_tap_gesture(name, self.owner._device_family)):
                selector_name = "tap"
            self._selector.enqueue(selector_name, connection, busy)
            return False
        with self._lock:
            if self._transition is not None or self._scroll.applying.is_set():
                observe_gesture(self.owner, identity, status="not_triggered")
                return False
            generation = self._generation
        service, bindings = self.owner._app_gestures, self.owner._gesture_bindings
        endpoint = name in bindings.confirm + bindings.end
        if endpoint and service.windows_pointer_owns_gesture(name, bindings) and not busy:
            if not global_action and not service.foreground_has_binding(name):
                return False
            endpoint = False  # An application/global action can still own the gesture in pointer mode.
        if (name in bindings.switch_mode
                and getattr(self.owner, "_automatic_session_enabled", lambda: False)()):
            endpoint = True
        if endpoint and agent is not None and (agent.owns_input_focus()
                or agent.recommendations.owns_input_focus()):
            # This is our known Qt editor. A conflicting app/global assignment
            # must not send its configured voice key through AXSystemDialog.
            return True
        if endpoint and busy:
            return True
        start_focus = None
        end_only = endpoint and name not in bindings.confirm
        input_only = (endpoint and not global_action
                      and not service.foreground_has_binding(name))
        if input_only:
            if end_only:
                observe_gesture(self.owner, identity, status="not_triggered")
                return False  # There is no active sentence to finish.
            inline = self.owner._inline_input
            start_focus = inline.capture_start_focus(require_editable=True)
            confirmed = time.monotonic()
            if start_focus is not None and start_focus.status == "text" and len(start_focus.target or ()) == 6:
                observe_gesture(self.owner, identity, application=start_focus.target[3], focus="text")
                inline.pin_start_focus(start_focus.target, confirmed)
                if preview is not None:
                    preview.next_sentence()
                return True
        routed = service.envelope(event, audio_busy=audio_busy,
                                  start_focus=start_focus, input_only=input_only, end_only=end_only)
        if isinstance(routed, InputSourceGestureEvent):
            self._sceneRequested.emit(ModeGestureEvent(routed, generation), connection)
            return False
        if routed.route == "ignored":
            return False
        if routed.route == "input" and endpoint:
            if end_only or len(routed.input_method_target or ()) != 6:
                return False
            self.owner._inline_input.pin_start_focus(routed.input_method_target, routed.created)
            if preview is not None:
                preview.next_sentence()
            return True
        transition = None
        if (routed.route == "outside" and global_action == "window_selector"
                and service.outside_binding(routed) is None):
            with self._lock:
                transition = self._transition = object()
        self._sceneRequested.emit(ModeGestureEvent(routed, generation, transition), connection)
        return False

    @Slot(object, object)
    def _deliver_gesture(self, event, connection):
        try:
            self.owner._apply_gesture(event, connection)
        finally:
            with self._lock:
                pending = self._pending_global
                if (event.transition is not None and self._transition is event.transition
                        and (pending is None or pending.transition is not event.transition)):
                    self._transition = None

    def handle_outside(self, event, connection):
        """App/scene binding wins; only an absent binding may use a global default."""
        service = self.owner._app_gestures
        if event.route == "ignored":
            return
        if event.capture_error and not service._override_stop_reason(event):
            reason = event.capture_reason or "capture_failed"
            service._stop_trace(event, reason)
            service.notify(reason_message(reason) if reason.startswith("input_method_") or reason == "windows_tsf_not_selected"
                           or reason == "foreground_or_connection_changed"
                           else "应用手势无法读取前台应用，请查看运行日志")
            return
        if service._override_stop_reason(event) or self.speech_busy():
            service._stop_trace(event, "gesture_state_changed")
            return
        if event.route != "outside":
            target = event.target
            diagnostic = target.diagnostic if target is not None else {}
            reason = (diagnostic.get("reason", "blocked_window") if target is not None and target.blocked
                      else diagnostic.get("focus_reason") or "unknown_focus")
            service._stop_trace(event, reason)
            service.notify("当前有弹窗或菜单，请关闭后在目标应用中重试" if reason == "blocked_window"
                           else reason_message(reason))
            return
        if service.outside_binding(event) is not None:
            service.handle_override(event)
            return
        source = event.source.event
        action = self.owner._global_gesture_bindings.action_for(source.name)
        if not action:
            service._stop_trace(event, "binding_missing")
            target = event.target
            if (target is not None and source.name in GESTURE_LABELS
                    and service.catalog.is_unconfigured(target.bundle)
                    and service.foreground_identity() == (target.bundle, target.pid)):
                service.notify("当前应用未配置手势")
            return
        if action == 'clipboard_paste':
            from ...gestures.app_gestures import AppBinding
            # Let the destination application consume the original clipboard
            # MIME data, including images. Never replace it with text or a path.
            shortcut = 'Cmd+V' if sys.platform == 'darwin' else 'Ctrl+V'
            service._dispatch('global:clipboard_paste', AppBinding(source.name, shortcut),
                              event.target, trace_id=event.trace_id, input_focus=event.input_focus)
            return
        custom = self.owner._global_gesture_bindings.custom_for(source.name)
        if custom is not None:
            from ...gestures.app_gestures import AppBinding
            service._dispatch('global:' + source.name, AppBinding(source.name, custom.shortcut, system_wide=True),
                              event.target, trace_id=event.trace_id, input_focus=event.input_focus)
            return
        canonical = {"show_menu": "index-pinch", "window_selector": "clench",
                     "screen_capture": "screen-capture",
                     "scroll_up": "swipe-up", "scroll_down": "swipe-down",
                     "workflow_recommendations": "workflow-recommendations", "agent": "agent"}
        application = self.owner._global_gesture_bindings.application_for(source.name)
        stamp = (self._selector.capture_stamp() if action == "window_selector" else
                 self._scroll.capture_stamp() if action in {"scroll_up", "scroll_down"} else None)
        request = MenuRequest("open_application" if application else canonical[action], self._generation, event.created, False,
                              stamp=stamp, target=event.target, app_generation=event.generation,
                              transition=self._transition, trace_id=event.trace_id,
                              input_focus=event.input_focus, application=application)
        self._apply(request, connection)

    def envelope(self, event):
        # Capture before the native target lookup, which may take time. A
        # queued input action cannot leak across a mode change and back.
        with self._lock:
            generation = self._generation
        return ModeGestureEvent(self.owner._app_gestures.envelope(event), generation)

    def input_owner_changed(self):
        """Discard queued actions when stroke input or capture owns the gestures."""
        with self._lock:
            self._generation += 1
            self._transition = None
        self._selector.cancel()
        self.owner._app_gestures.cancel_pending()
        self.hideRequested.emit()

    def accepts(self, event):
        with self._lock:
            source = event.source if isinstance(event, ModeGestureEvent) else event
            transition_ok = self._transition is None or (
                isinstance(event, ModeGestureEvent) and event.transition is self._transition)
            accepted = (not self.session_blocked() and transition_ok
                    and not self._selector.blocked.is_set()
                    and not self._scroll.applying.is_set()
                    and not (self._scroll.pending.is_set() and getattr(source, "route", "") == "outside")
                    and (not isinstance(event, ModeGestureEvent) or event.generation == self._generation))
            if not accepted:
                self._trace_rejected(event)
            return accepted

    def _trace_rejected(self, event):
        source = event.source if isinstance(event, ModeGestureEvent) else event
        if not getattr(source, "trace_id", ""):
            return
        # Diagnostic metadata only. No additional target capture or routing.
        self.owner._app_gestures._trace(source.trace_id, "outcome", "ring_route_blocked",
            target=source.target, mode=self._mode, session_blocked=self.session_blocked(),
            event_generation=getattr(event, "generation", None), current_generation=self._generation,
            transition=self._transition is not None, selector=self._selector.blocked.is_set(),
            scroll_pending=self._scroll.pending.is_set(), scroll_applying=self._scroll.applying.is_set())

    @Slot()
    def showMenu(self):
        self._cancel_scene_hud()
        if self.session_blocked():
            return
        if not self.speech_busy():
            request = (self._generation, time.monotonic(), self.owner._disconnect_event)
            self._pending_hud = request
            self._hud_timer.start()
            def work():
                try:
                    focus = self.owner._inline_input.capture_start_focus(allow_activation=False)
                    target = (self.owner._app_gestures.backend.capture(menu_action=True, scene=True,
                              focus_sensitive=False) if focus and focus.status == "nontext" else None)
                    if (target is not None and focus.target
                            and not self.owner._app_gestures._matches_focus(target, (focus.target[3], focus.target[4]))):
                        target = None
                except Exception:
                    target, focus = None, None
                try:
                    self._sceneHudReady.emit(request, (target, focus))
                except RuntimeError:
                    pass
            threading.Thread(target=work, name="GestureSceneHints", daemon=True).start()
            return
        self.showRequested.emit("input", "")

    @Slot()
    def _cancel_scene_hud(self):
        self._hud_timer.stop()
        self._pending_hud = None

    def _take_scene_hud(self, request):
        if request is None or request is not self._pending_hud:
            return False
        self._cancel_scene_hud()
        generation, _, connection = request
        # Hints can also be opened from the tray while the Ring is disconnected.
        # A later connection change still invalidates this request's generation.
        return (generation == self._generation and not self.session_blocked()
                and connection is self.owner._disconnect_event)

    @Slot()
    def _scene_hud_fallback(self):
        if self._take_scene_hud(self._pending_hud):
            self.owner._event_log("RING_GESTURE", action="hint_fallback", reason="scene_lookup_timeout")
            self.showRequested.emit(self.mode, "")

    @Slot(object, object)
    def _show_scene_hud(self, request, target):
        if not self._take_scene_hud(request):
            return
        _, created, _ = request
        if time.monotonic() - created > 1.0:
            self.owner._event_log("RING_GESTURE", action="hint_fallback", reason="scene_lookup_timeout")
            self.showRequested.emit(self.mode, "")
            return
        target, focus = target
        catalog = self.owner._app_gestures.catalog
        catalog.observe_scene(target)
        if self.speech_busy() or (focus is not None and focus.status == "text"):
            self.showRequested.emit("input", "")
            return
        if target is None or target.blocked or focus is None or focus.status != "nontext":
            self.showRequested.emit("operation", "" if sys.platform == "win32" else "暂时无法确认输入焦点")
            return
        self._show_outside_hints(target)

    def _show_outside_hints(self, target):
        catalog = self.owner._app_gestures.catalog
        scene = target.scene if target.scene in catalog.configured_scenes().get(target.bundle, []) else ""
        items = dict(catalog._bindings_for(target.bundle))
        if scene:
            items.update(catalog.scene_bindings_for_target(target, scene))
        actions = {g: (action, "global") for g, action in catalog.globalOccupancy.items()}
        actions.update({g: (b["label"], "scene" if scene else "application") for g, b in items.items()})
        label = catalog._apps.get(target.bundle, {}).get("label", "")
        self.sceneHudRequested.emit("operation", [dict(key=g, gesture=GESTURE_LABELS[g], action=actions[g][0],
            scope=actions[g][1], application=label, scene=scene, sceneLabel=SCENE_LABELS.get(scene, ""))
            for g in GESTURE_LABELS if g in actions])

    def _global_stop_reason(self, request, connection):
        owner = self.owner
        if self._closed:
            return "controller_closed"
        if request.selection is not None or self.session_blocked():
            return "session_blocked"
        if connection is not owner._disconnect_event or connection.is_set():
            return "connection_changed"
        if not owner._runtime_active or not owner._connected:
            return "runtime_inactive"
        if time.monotonic() - request.created > 1.0:
            return "event_expired"
        with self._lock:
            if request.generation != self._generation:
                return "gesture_generation_changed"
        if request.busy or self.speech_busy():
            return "speech_busy"
        if request.app_generation != owner._app_gestures._generation:
            return "settings_changed"
        if request.target is None:
            return "target_unavailable"
        if request.target.blocked:
            return "blocked_window"
        return ""

    def _global_trace(self, request, reason, **fields):
        self.owner._app_gestures._trace(request.trace_id, "outcome", reason,
            target=request.target, action=request.name, **fields)

    def _release_global(self, request):
        with self._lock:
            if self._pending_global is request:
                self._pending_global = None
            if request.transition is not None and self._transition is request.transition:
                self._transition = None

    @Slot(object, object)
    def _apply(self, request, connection):
        reason = self._global_stop_reason(request, connection)
        if reason or self._pending_global is not None:
            self._global_trace(request, reason or "global_validation_pending")
            self._release_global(request)
            return
        self._pending_global = request
        backend = self.owner._app_gestures.backend
        # AX queries to our own windows need the Qt event loop to answer them.
        # Blocking it on the worker pipe makes same_target time out on Mythlink.
        def verify():
            try:
                valid = backend.same_target(request.target)
                diagnostic = dict(getattr(backend, "last_diagnostic", {}) or {})
                if valid and request.input_focus is not None:
                    valid = self.owner._inline_input.same_input_focus(request.input_focus)
                    if not valid:
                        diagnostic = {"reason": "input_method_focus_changed"}
            except Exception as exc:
                valid = False
                diagnostic = dict(getattr(exc, "diagnostic", {}) or {})
                diagnostic.setdefault("reason", "target_validation_failed")
                diagnostic["error_type"] = type(exc).__name__
            try:
                self._globalTargetReady.emit(request, connection, bool(valid), diagnostic)
            except RuntimeError:
                pass  # The UI was destroyed while the native read was in flight.
        threading.Thread(target=verify, name="RingGlobalTarget", daemon=True).start()

    @Slot(object, object, bool, object)
    def _apply_global_target(self, request, connection, valid, diagnostic):
        if self._pending_global is not request:
            return
        try:
            reason = self._global_stop_reason(request, connection)
            if reason or not valid:
                self._global_trace(request, reason or diagnostic.get("reason") or "target_changed",
                                   native=diagnostic)
                return
            if request.application is not None:
                if request.application.bundle == HOST_BUNDLE:
                    self.mainWindowRequested.emit()
                    self._application_opened(request, True, "")
                elif request.application.bundle.startswith("win.") and sys.platform == "win32":
                    from ...desktop.windows_applications import open_application
                    bundle = request.application.bundle
                    try:
                        open_application(bundle, self.owner._settings.value("gestures/windowsLaunch/" + bundle, ""))
                    except (OSError, ValueError) as exc:
                        self._application_opened(request, False, str(exc))
                    else:
                        self._application_opened(request, True, "")
                else:
                    self._application_launcher.open(request.application, lambda success, detail:
                        self._application_opened(request, success, detail))
                return
            self._global_trace(request, "global_action_started", native=diagnostic)
            with self._lock:
                self._mode = "operation"  # Internal hint/scroll context, never a user-selected mode.
            if request.name == "index-pinch":
                # The shared pinch already resolved and revalidated its target.
                # Re-probing here can lose the editor while the profile settles.
                if sys.platform == "win32" and request.input_focus is not None:
                    self.owner._app_gestures.catalog.observe_scene(request.target)
                    self._show_outside_hints(request.target)
                else:
                    self.showMenu()
                self._log(request.name, "show")
                return
            if request.name == "workflow-recommendations":
                self.owner.agent.openRecommendations('gesture')
                self._log(request.name, 'show')
                return
            if request.name == "agent":
                self.owner.agent.toggleFocus()
                self._log(request.name, 'show')
                return
            if request.name == "screen-capture":
                self.hideRequested.emit()
                self._screenshot.start()
                self._log(request.name, 'show')
                return
            if sys.platform == "win32" and request.name in {"clench", "swipe-up", "swipe-down"}:
                shortcut = {"clench": "Win+Tab", "swipe-up": "PageUp", "swipe-down": "PageDown"}[request.name]
                try:
                    self.owner._app_gestures.backend.post(request.target, shortcut, system_wide=True)
                except (RuntimeError, ValueError, OSError) as exc:
                    self.owner._app_gestures.notify(str(exc))
                return
            if request.name in {"swipe-up", "swipe-down"}:
                self._scroll.start(request, connection)
                return
            if request.name == "clench":
                with self._lock:
                    self._generation += 1  # Old application actions cannot cross the overview.
                    current = replace(request, generation=self._generation)
                self._selector.start(current, connection)
        finally:
            self._release_global(request)

    def _application_opened(self, request, success, detail):
        if self._closed:
            return
        self._global_trace(request, "global_action_started" if success else "application_open_failed",
                           application_bundle=request.application.bundle, error=detail)
        if not success:
            self.owner._app_gestures.notify(f"无法打开「{request.application.application_name}」，请确认应用仍已安装，或重新选择应用")

    def _log(self, gesture, action, reason=""):
        self.owner._event_log("RING_GESTURE", gesture=gesture, action=action,
                              mode=self.mode, reason=reason)

    @Slot()
    def _connection_changed(self):
        # Disconnect/reconnect invalidates pending mode commands and app events
        # but preserves the chosen global mode for this application run.
        with self._lock:
            self._generation += 1
            self._transition = None
        if not self.owner._connected:
            self.hideRequested.emit()
            self._selector.cancel()
        else:
            self._selector.prepare()

    def close(self):
        self._closed = True
        self._clipboard_suggestion.close()
        self._screenshot.close()
        self._application_launcher.close()
        self._cancel_scene_hud()
        self._fields.close()
        self._scroll.close()
        self._selector.close()
