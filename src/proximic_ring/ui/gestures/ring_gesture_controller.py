"""Focus-based gesture ownership, ahead of the audio endpoint and shortcuts."""
from __future__ import annotations

from dataclasses import dataclass, replace
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, Qt, Signal, Slot

from ...gestures.app_gestures import QUIET_PHASES
from ...gestures.gesture_settings import (GESTURE_LABELS, GLOBAL_ACTION_LABELS, GLOBAL_BINDINGS_KEY,
                                VOICE_GESTURE_GROUP)
from ...scenes.registry import SCENE_LABELS
from ...diagnostics.scene_diagnostics import reason_message
from ..desktop.text_focus_controller import TextFocusController
from ..desktop.page_scroll_controller import PageScrollController
from ..desktop.window_selector_controller import WindowSelectorController
from .app_gesture_controller import InputSourceGestureEvent


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


class RingGestureController(QObject):
    changed = Signal()
    showRequested = Signal(str, str)
    hideRequested = Signal()
    sceneHudRequested = Signal(str, object)
    _sceneHudReady = Signal(int, float, object, object)
    _requested = Signal(object, object)
    _sceneRequested = Signal(object, object)
    _globalTargetReady = Signal(object, object, bool, object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
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
        if connection is not self.owner._disconnect_event or connection.is_set():
            return False
        name = str(getattr(event, "name", ""))
        proximity = getattr(self.owner, "_proximity", None)
        if proximity is not None and proximity.filter_gesture(name, connection):
            return False
        if self.session_blocked():
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
            self._selector.enqueue(canonical.get(global_action, name), connection, busy)
            return False
        with self._lock:
            if self._transition is not None or self._scroll.applying.is_set():
                return False
            generation = self._generation
        service, bindings = self.owner._app_gestures, self.owner._gesture_bindings
        endpoint = name in bindings.confirm + bindings.end
        if endpoint and agent is not None and (agent.owns_input_focus()
                or agent.recommendations.owns_input_focus()):
            # This is our known Qt editor. A conflicting app/global assignment
            # must not send its configured voice key through AXSystemDialog.
            return True
        if endpoint and busy:
            return True
        start_focus = None
        input_only = (endpoint and name in bindings.confirm and not global_action
                      and not service.foreground_has_binding(name))
        if input_only:
            # No app conflict still needs a real first-start client. Keep this
            # independent of AX and of the legacy focus-window recovery helper.
            created = time.monotonic()
            inline = self.owner._inline_input
            start_focus = inline.capture_start_focus()
            if start_focus is not None and start_focus.status == "text":
                inline.pin_start_focus(start_focus.target if start_focus else None, created)
                return True
        routed = service.envelope(event, audio_busy=audio_busy,
                                  start_focus=start_focus, input_only=input_only)
        if isinstance(routed, InputSourceGestureEvent):
            self._sceneRequested.emit(ModeGestureEvent(routed, generation), connection)
            return False
        if routed.route == "input" and endpoint:
            if not busy and name in bindings.confirm:
                # Carry the live proof across the audio/UI queue. BEGIN must
                # still target this client and selection, even if Qt learns
                # about an IMK activation after this worker has returned.
                focus = routed.input_method_target
                self.owner._inline_input.pin_start_focus(focus if focus and len(focus) == 6 else None,
                                                        routed.created)
            return True
        if (routed.route == "outside" and name in bindings.confirm
                and service.outside_binding(routed) is None and not global_action):
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
        if event.capture_error and not service._override_stop_reason(event):
            reason = event.capture_reason or "capture_failed"
            service._stop_trace(event, reason)
            service.notify(reason_message(reason) if reason.startswith("input_method_")
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
            return
        custom = self.owner._global_gesture_bindings.custom_for(source.name)
        if custom is not None:
            from ...gestures.app_gestures import AppBinding
            service._dispatch('global:' + source.name, AppBinding(source.name, custom.shortcut),
                              event.target, trace_id=event.trace_id, input_focus=event.input_focus)
            return
        canonical = {"show_menu": "index-pinch", "window_selector": "clench",
                     "scroll_up": "swipe-up", "scroll_down": "swipe-down",
                     "workflow_recommendations": "workflow-recommendations", "agent": "agent"}
        stamp = (self._selector.capture_stamp() if action == "window_selector" else
                 self._scroll.capture_stamp() if action in {"scroll_up", "scroll_down"} else None)
        request = MenuRequest(canonical[action], self._generation, event.created, False,
                              stamp=stamp, target=event.target, app_generation=event.generation,
                              transition=self._transition, trace_id=event.trace_id,
                              input_focus=event.input_focus)
        self._apply(request, connection)

    def envelope(self, event):
        # Capture before the native target lookup, which may take time. A
        # queued input action cannot leak across a mode change and back.
        with self._lock:
            generation = self._generation
        return ModeGestureEvent(self.owner._app_gestures.envelope(event), generation)

    def input_owner_changed(self):
        """Discard queued actions when stroke input takes/releases the gestures."""
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
            generation, created, connection = self._generation, time.monotonic(), self.owner._disconnect_event
            self._pending_hud = (generation, created, connection)
            self._hud_timer.start()
            def work():
                try:
                    focus = self.owner._inline_input.capture_start_focus()
                    target = (self.owner._app_gestures.backend.capture(menu_action=True, scene=True,
                              focus_sensitive=False) if focus and focus.status == "nontext" else None)
                    if (target is not None and focus.target
                            and (target.bundle, target.pid) != (focus.target[3], focus.target[4])):
                        target = None
                except Exception:
                    target, focus = None, None
                try:
                    self._sceneHudReady.emit(generation, created, connection, (target, focus))
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
        if request is None or request != self._pending_hud:
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

    @Slot(int, float, object, object)
    def _show_scene_hud(self, generation, created, connection, target):
        if not self._take_scene_hud((generation, created, connection)):
            return
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
            self.showRequested.emit("operation", "暂时无法确认输入焦点")
            return
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
        if request.target.focus_sensitive and request.target.input_context != "nontext":
            return "input_focus"
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
                valid = backend.same_target(request.target, require_focus=request.target.focus_sensitive)
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
            self._global_trace(request, "global_action_started", native=diagnostic)
            with self._lock:
                self._mode = "operation"  # Internal hint/scroll context, never a user-selected mode.
            if request.name == "index-pinch":
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
        self._cancel_scene_hud()
        self._fields.close()
        self._scroll.close()
        self._selector.close()
