"""Global Ring modes, ahead of both the audio endpoint and app shortcuts."""
from __future__ import annotations

from dataclasses import dataclass, replace
import threading
import time

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot

from ..app_gestures import QUIET_PHASES
from ..gesture_settings import (GESTURE_LABELS, GLOBAL_ACTION_LABELS, GLOBAL_BINDINGS_KEY,
                                VOICE_GESTURE_GROUP)
from ..scene_capabilities import SCENE_LABELS
from .text_focus_controller import TextFocusController
from .page_scroll_controller import PageScrollController
from .window_selector_controller import WindowSelectorController


@dataclass(frozen=True)
class ModeGestureEvent:
    source: object
    generation: int


@dataclass(frozen=True)
class MenuRequest:
    name: str
    generation: int
    created: float
    busy: bool
    transition: object = None
    stamp: object = None
    selection: object = None


class RingGestureController(QObject):
    changed = Signal()
    showRequested = Signal(str, str)
    hideRequested = Signal()
    sceneHudRequested = Signal(str, object)
    _sceneHudReady = Signal(int, float, object, object)
    _requested = Signal(object, object)
    _sceneRequested = Signal(object, object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._lock = threading.Lock()
        self._mode = "input"
        self._generation = 0
        self._session_was_blocked = False
        self._transition = None
        self._selector = WindowSelectorController(self)
        self._fields = TextFocusController(self)
        self._scroll = PageScrollController(self)
        self._scroll.notice.connect(lambda message: self.showRequested.emit(self.mode, message))
        self._fields.notice.connect(lambda message: self.showRequested.emit(self.mode, message))
        self._selector.notice.connect(lambda message: self.showRequested.emit(self.mode, message))
        self._selector.changed.connect(self._fields.sync)
        self.changed.connect(self._fields.sync)
        # Always queue: firmware callbacks must never manipulate Qt windows.
        self._requested.connect(self._apply, Qt.QueuedConnection)
        self._sceneRequested.connect(owner._apply_gesture, Qt.QueuedConnection)
        self._sceneHudReady.connect(self._show_scene_hud, Qt.QueuedConnection)
        owner.connectedChanged.connect(self._connection_changed)

    @Property(str, notify=changed)
    def mode(self):
        with self._lock:
            return self._mode

    @Property(str, notify=changed)
    def modeLabel(self):
        return "输入模式" if self.mode == "input" else "操作模式"

    @Property(QObject, constant=True)
    def textFields(self):
        return self._fields

    @Property(QObject, constant=True)
    def windowSelector(self):
        return self._selector

    @Property("QVariantMap", notify=changed)
    def globalBindings(self):
        return self.owner._global_gesture_bindings.as_dict()

    @Property("QVariantList", constant=True)
    def globalActions(self):
        return [dict(value=key, label=label) for key, label in GLOBAL_ACTION_LABELS.items()]

    @Slot(str, result="QVariantList")
    def globalGestureOptions(self, action):
        used = {g for key, g in self.globalBindings.items() if key != action}
        used.update(self.owner._app_gestures.catalog.voice_group())
        used.add(self.owner._app_gestures.inputSourceGesture)
        return [dict(value=g, label=label) for g, label in GESTURE_LABELS.items() if g not in used]

    def _global_gesture_editable(self, gesture):
        return (gesture in GESTURE_LABELS
                and gesture not in self.owner._app_gestures.catalog.voice_group()
                and gesture != self.owner._app_gestures.inputSourceGesture)

    @Slot(str, result="QVariantList")
    def globalActionOptions(self, gesture):
        if not self._global_gesture_editable(gesture):
            return []
        bindings = self.owner._global_gesture_bindings
        previous_action = bindings.action_for(gesture)
        options = []
        for action, label in GLOBAL_ACTION_LABELS.items():
            previous_gesture = bindings.as_dict()[action]
            if previous_gesture == gesture:
                effect = "此手势当前用于「" + label + "」。"
            elif previous_action:
                effect = (f"保存后，{GESTURE_LABELS[gesture]}用于「{label}」，"
                          f"{GESTURE_LABELS[previous_gesture]}用于「{GLOBAL_ACTION_LABELS[previous_action]}」。")
            else:
                effect = (f"保存后，{GESTURE_LABELS[gesture]}用于「{label}」，"
                          f"释放{GESTURE_LABELS[previous_gesture]}的全局占用。")
            options.append(dict(id=action, label=label, path="当前绑定：" + GESTURE_LABELS[previous_gesture],
                                shortcut="", available=True, effect=effect))
        return options

    @Slot(str, str, result=bool)
    def setGlobalGestureAction(self, gesture, action):
        """Gesture-first editor: move a function, or exchange two assignments atomically."""
        if action not in GLOBAL_ACTION_LABELS or not self._global_gesture_editable(gesture):
            return False
        current = self.owner._global_gesture_bindings
        previous_action = current.action_for(gesture)
        updates = {action: gesture}
        if previous_action and previous_action != action:
            updates[previous_action] = current.as_dict()[action]
        return self._save_global_bindings(replace(current, **updates))

    @Slot(str, str, result=bool)
    def setGlobalBinding(self, action, gesture):
        if action not in GLOBAL_ACTION_LABELS or gesture not in {row["value"] for row in self.globalGestureOptions(action)}:
            return False
        return self._save_global_bindings(replace(self.owner._global_gesture_bindings, **{action: gesture}))

    def _save_global_bindings(self, bindings):
        if self.owner._global_gesture_bindings == bindings:
            return True
        if self.speech_busy() or self._fields.applying.is_set() or self._scroll.applying.is_set():
            self.owner._app_gestures.notify("请先结束当前操作，再修改全局手势")
            return False
        self.owner._settings.setValue(GLOBAL_BINDINGS_KEY, bindings.to_json())
        with self._lock:
            self.owner._global_gesture_bindings = bindings
            self._generation += 1
            self._transition = None
        self._fields.picker.stop()
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
        self._fields.sync()

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

    def filter(self, event, audio_busy: bool, connection) -> bool:
        """Worker-thread gate. True preserves the existing voice/app route."""
        if connection is not self.owner._disconnect_event or connection.is_set():
            return False
        name = str(getattr(event, "name", ""))
        global_action = self.owner._global_gesture_bindings.action_for(name)
        canonical = {"show_menu": "index-pinch", "switch_mode": "middle-pinch", "window_selector": "clench"}
        proximity = getattr(self.owner, "_proximity", None)
        if proximity is not None and proximity.filter_gesture(name, connection):
            return False
        busy = bool(audio_busy or self.speech_busy())
        if self._selector.blocked.is_set():
            self._selector.enqueue(canonical.get(global_action, name if name in VOICE_GESTURE_GROUP else ""), connection, busy)
            return False
        picker = self._fields.picker
        with self._lock:
            # Check foreground ownership even while an old field picker is
            # waiting for its next focus poll; Tap must not escape into voice
            # immediately after switching to an overridden application.
            scene_allowed = not global_action and self._transition is None
            scene_generation = self._generation
        if scene_allowed:
            scene_event = self.owner._app_gestures.scene_envelope(event)
            if scene_event is not None:
                if busy:
                    scene_event = replace(scene_event, phase="listening")
                self._sceneRequested.emit(ModeGestureEvent(scene_event, scene_generation), connection)
                return False  # Consumed before tap can reach the audio endpoint.
        selection_gestures = {"swipe-up", "swipe-down", "swipe-left", "swipe-right", "tap"}
        stamp = (self._fields.capture_stamp() if self.mode == "input" and
                 (name == "swipe-down" or (picker.active.is_set() and name in selection_gestures))
                 and not busy else None)
        if self.mode == "operation" and name in {"swipe-up", "swipe-down"} and not busy:
            stamp = self._scroll.capture_stamp()
        if global_action == "window_selector" and not busy:
            stamp = self._selector.capture_stamp()
        with self._lock:
            if self._scroll.pending.is_set() and global_action != "show_menu":
                if self._scroll.applying.is_set() or name in {"swipe-up", "swipe-down"}:
                    return False
            if self._fields.pending.is_set() and global_action != "show_menu":
                # A mode switch may cancel discovery. During the short actual
                # focus write, suppress gestures until its result is known.
                if global_action not in {"switch_mode", "window_selector"} or self._fields.applying.is_set():
                    return False
            if self._transition is not None and global_action != "show_menu":
                # In particular, a tap must not start audio while Qt is
                # deciding the preceding mode change.
                return False
            ticket = None
            if self._mode == "input":
                if picker.active.is_set() or (name == "swipe-down" and not busy):
                    ticket = picker.claim(name, stamp, voice_busy=busy)
                    if ticket is not None:
                        if ticket["action"] == "enter":
                            self._generation += 1  # Invalidate old queued app actions.
                        request = MenuRequest(name, self._generation, time.monotonic(), busy,
                                              stamp=stamp, selection=ticket)
                        self._requested.emit(request, connection)
                        return ticket["action"] == "voice"
                    if not global_action:
                        return False
            if not global_action and name != "swipe-down":
                if self._mode == "input":
                    return True
                if name != "swipe-up":
                    # Explicit app-menu mappings work in either mode. Keep
                    # voice/source gestures and legacy profiles input-only.
                    return not busy and self.owner._app_gestures.catalog.uses(name)
            transition = object() if global_action in {"switch_mode", "window_selector"} and not busy else None
            if transition is not None:
                self._transition = transition
            request = MenuRequest(canonical.get(global_action, name), self._generation, time.monotonic(), busy, transition, stamp)
        self._requested.emit(request, connection)
        return False

    def envelope(self, event):
        # Capture before the native target lookup, which may take time. A
        # queued input action cannot leak across a mode change and back.
        with self._lock:
            generation = self._generation
        return ModeGestureEvent(self.owner._app_gestures.envelope(event), generation)

    def accepts(self, event):
        with self._lock:
            if (isinstance(event, ModeGestureEvent) and getattr(event.source, "exclusive", False)
                    and (self._fields.pending.is_set() or self._scroll.pending.is_set())):
                self._trace_rejected(event)
                return False
            mode_allowed = self._mode == "input" or (
                self._mode == "operation" and isinstance(event, ModeGestureEvent)
                and not self._scroll.applying.is_set() and not self._fields.pending.is_set()
                and self.owner._app_gestures.is_menu_mapping_event(event.source)
            )
            accepted = (not self.session_blocked() and mode_allowed and self._transition is None
                    and not self._selector.blocked.is_set()
                    and not self._fields.picker.active.is_set()
                    and (not isinstance(event, ModeGestureEvent)
                         or event.generation == self._generation))
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
            field_pending=self._fields.pending.is_set(), scroll_pending=self._scroll.pending.is_set(),
            scroll_applying=self._scroll.applying.is_set(), picker=self._fields.picker.active.is_set())

    @Slot()
    def showMenu(self):
        if self.session_blocked():
            return
        self._fields.refresh()
        if self.owner._app_gestures.catalog.apps and not self.speech_busy():
            generation, created, connection = self._generation, time.monotonic(), self.owner._disconnect_event
            def work():
                try:
                    target = self.owner._app_gestures.backend.capture(menu_action=True, scene=True)
                except Exception:
                    target = None
                try:
                    self._sceneHudReady.emit(generation, created, connection, target)
                except RuntimeError:
                    pass
            threading.Thread(target=work, name="GestureSceneHints", daemon=True).start()
            return
        self.showRequested.emit(self.mode, "")

    @Slot(int, float, object, object)
    def _show_scene_hud(self, generation, created, connection, target):
        if (generation != self._generation or time.monotonic() - created > 1.0 or self.session_blocked()
                or connection is not self.owner._disconnect_event or connection.is_set()):
            return
        catalog = self.owner._app_gestures.catalog
        catalog.observe_scene(target)
        if (target is not None and not target.blocked
                and not self.speech_busy() and not self._selector.blocked.is_set() and not self._fields.picker.active.is_set()):
            scene = target.scene if target.input_context == "nontext" and target.scene in catalog.configured_scenes().get(target.bundle, []) else ""
            disabled = bool(scene or catalog.voice_overridden(target.bundle))
            items = catalog.scene_bindings_for_target(target, scene) if scene else catalog._bindings_for(target.bundle)
            if disabled or items:
                label = catalog._apps.get(target.bundle, {}).get("label", "应用")
                scene_label = SCENE_LABELS.get(scene, "常规")
                actions = {g: (b["label"], "scene" if scene else "application")
                           for g, b in items.items() if catalog._can_bind_regular(g)}
                actions.update({g: (action, "global") for g, action in catalog.globalOccupancy.items()})
                if disabled:
                    actions.update({g: ("未绑定", "unbound") for g in catalog.voice_group() if g not in actions})
                self.sceneHudRequested.emit(self.mode, [dict(key=g, gesture=GESTURE_LABELS[g], action=actions[g][0],
                    scope=actions[g][1], application=label, scene=scene, sceneLabel=scene_label, voiceDisabled=disabled)
                    for g in GESTURE_LABELS if g in actions])
                return
        self.showRequested.emit(self.mode, "")

    @Slot(object, object)
    def _apply(self, request, connection):
        selection_handled = False
        try:
            owner = self.owner
            if (self.session_blocked() or connection is not owner._disconnect_event or connection.is_set()
                    or not owner._runtime_active or not owner._connected
                    or time.monotonic() - request.created > 1.0):
                return
            if request.name == "index-pinch":
                self.showMenu()
                self._log(request.name, "show")
                return
            with self._lock:
                if request.generation != self._generation:
                    return
            if request.selection and request.selection["action"] == "voice":
                self._fields.picker.handle(request.selection)
                selection_handled = True
                return
            # Keep the recognition-time busy flag even if finalization finished
            # before Qt delivered the gesture. Never queue it for later.
            if request.busy or self.speech_busy():
                self.showRequested.emit(self.mode, "请先结束本句")
                self._log(request.name, "blocked", "speech_busy")
                return
            if request.selection:
                self._fields.picker.handle(request.selection)
                selection_handled = True
                self._log(request.name, "select_input_field")
                return
            if request.name == "middle-pinch":
                with self._lock:
                    self._mode = "operation" if self._mode == "input" else "input"
                    self._generation += 1
                owner._app_gestures.cancel_pending()
                self.changed.emit()
                self.showMenu()
                self._log(request.name, "switch")
                return
            if request.name == "swipe-down" and self.mode == "input":
                return
            if self.mode == "operation" and request.name in {"swipe-up", "swipe-down"}:
                self._scroll.start(request, connection)
                return
            if request.name == "clench":
                with self._lock:
                    self._generation += 1  # Old application actions cannot cross the overview.
                    current = replace(request, generation=self._generation)
                self._selector.start(current, connection)
        finally:
            if request.selection and not selection_handled:
                self._fields.picker.discard(request.selection)
            with self._lock:
                if request.transition is not None and self._transition is request.transition:
                    self._transition = None

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
        self._fields.sync()

    def close(self):
        self._fields.close()
        self._scroll.close()
        self._selector.close()
