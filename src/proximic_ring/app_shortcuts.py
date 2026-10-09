"""Small macOS keyboard boundary. No activation, text reads, clipboard or clicks."""
from __future__ import annotations

from dataclasses import dataclass, field
import sys
import hashlib
import time

from .app_gestures import normalize_shortcut, profile_for_application
from .mac_permissions import require_post_event_access
from .scenes.models import PRESENTATION
from .scenes.capabilities import BROWSERS, application_scene_profiles, installed_scene_profiles, installed_application_category
from .browser_accessibility import BrowserAccessibility, read_structure
from .scenes.recognition.engine import detect_scene
from .scenes.models import SceneResult
from .scenes.recognition.focus import inspect_focus
from .scene_diagnostics import new_trace_id, SceneActionError, safe_data


def verify_native_api() -> None:
    """Validate the installed bridges without reading the desktop or posting keys."""
    import AppKit
    import ApplicationServices
    import Quartz

    for module, names in (
        (AppKit, ("NSWorkspace",)),
        (ApplicationServices, ("AXUIElementCreateApplication", "AXUIElementCopyAttributeValue",
                               "AXUIElementSetMessagingTimeout", "AXUIElementSetAttributeValue",
                               "AXUIElementCopyMultipleAttributeValues", "AXValueGetType", "AXValueGetValue")),
        (Quartz, ("CGPreflightPostEventAccess", "CGEventCreateKeyboardEvent",
                  "CGEventGetFlags", "CGEventSetFlags", "CGEventSetIntegerValueField", "CGEventPostToPid")),
    ):
        for name in names:
            if not callable(getattr(module, name, None)):
                raise RuntimeError(f"macOS 应用手势接口不可用：{module.__name__}.{name}")


@dataclass(frozen=True)
class ShortcutTarget:
    bundle: str
    pid: int
    profile: str
    window: object = field(default=None, repr=False)
    focus: object = field(default=None, repr=False)
    role: str = ""
    blocked: bool = False
    remote_id: str = ""
    plain_enter: bool = False
    menu_action: bool = False
    scene: str = ""
    scene_checked: bool = False
    input_context: str = "unknown"
    document_key: str = ""
    page_key: str = ""
    web_area: object = field(default=None, repr=False)
    player: object = field(default=None, repr=False)
    trace_id: str = field(default="", compare=False)
    diagnostic: dict = field(default_factory=dict, compare=False, repr=False)


class LocalMacAppShortcuts:
    def __init__(self):
        self._browser_accessibility = BrowserAccessibility()

    def capture(self, *, plain_enter: bool = False, menu_action: bool = False,
                scene: bool = False, scene_observation: bool = False) -> ShortcutTarget | None:
        started = time.perf_counter()
        info = {"trace_id": new_trace_id(), "reason": "capture_started", "ax_errors": {}}
        self.last_diagnostic = info
        def finish(reason):
            info.update(reason=reason, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
            return None
        if sys.platform != "darwin":
            return finish("unsupported_platform")
        from .mac_workspace import frontmost_application
        app = frontmost_application()
        if app is None:
            return finish("no_foreground_application")
        bundle, pid = str(app.bundleIdentifier() or ""), int(app.processIdentifier())
        info.update(app=bundle, pid=pid)
        scene_profiles = application_scene_profiles(bundle) if scene else {}
        application_category = ""
        if scene:
            try:
                application_path = str(app.bundleURL().path())
                scene_profiles = installed_scene_profiles(bundle, application_path)
                application_category = installed_application_category(bundle, application_path)
            except AttributeError:
                pass
            if not scene_profiles and not menu_action:
                return finish("no_scene_capability")
        profile = profile_for_application(bundle, str(app.localizedName() or ""))
        if not profile and not plain_enter and not menu_action and not scene:
            return finish("application_not_configured")
        import ApplicationServices as AX
        element = AX.AXUIElementCreateApplication(pid)
        # Never fetch AXValue, selection or document text. Scene capture may
        # inspect a bounded set of presentation control metadata below.
        try:
            AX.AXUIElementSetMessagingTimeout(element, 0.08)
        except (AttributeError, TypeError):
            pass
        info["capabilities"] = list(scene_profiles)
        browser_scene = scene and bundle.casefold() in BROWSERS
        if browser_scene:
            info['browser_accessibility'] = self._browser_accessibility.prepare(app, element, AX)
        context_read_failed = False
        def record_error(name, error):
            if error and len(info['ax_errors']) < 24:
                key = name + ':' + str(error)
                info['ax_errors'][key] = info['ax_errors'].get(key, 0) + 1

        def native_attr(node, name):
            nonlocal context_read_failed
            if node is None:
                return None
            info["last_attribute"] = name
            if name == "AXValueSettable":
                error, editable = AX.AXUIElementIsAttributeSettable(node, "AXValue", None)
                info["ax_reads"] = info.get("ax_reads", 0) + 1
                record_error(name, error)
                return bool(editable) if error == 0 else None
            error, value = AX.AXUIElementCopyAttributeValue(node, name, None)
            if (node is element and name in {"AXFocusedWindow", "AXFocusedUIElement"}
                    and error not in (0, -25212, -25205)):  # NoValue / AttributeUnsupported
                context_read_failed = True
            info["ax_reads"] = info.get("ax_reads", 0) + 1
            record_error(name, error)
            if error == 0 and value is not None and name in {"AXPosition", "AXSize"}:
                value_type = AX.kAXValueCGPointType if name == "AXPosition" else AX.kAXValueCGSizeType
                valid, pair = AX.AXValueGetValue(value, value_type, None)
                return tuple(pair) if valid else None
            return value if error == 0 else None
        # AX attributes are cross-process calls. Reuse reads within this one
        # snapshot only, so slower computers have the same detection budget.
        cache = {}
        def attr(node, name):
            key = (id(node), name)
            if (browser_scene and node is not None and node is not element
                    and name in {'AXRole', 'AXHidden'} and key not in cache):
                values = read_structure(AX, node)
                if values is not None:
                    info['ax_batches'] = info.get('ax_batches', 0) + 1
                    info['ax_reads'] = info.get('ax_reads', 0) + 1
                    for attribute, (error, value) in values.items():
                        record_error(attribute, error)
                        cache.setdefault((id(node), attribute), (node, value))
            if key not in cache:
                cache[key] = (node, native_attr(node, name))
            return cache[key][1]
        window = attr(element, "AXFocusedWindow")
        reported_window = window
        focus = attr(element, "AXFocusedUIElement")
        role = str(attr(focus, "AXRole") or "")
        # Entering a show (especially on another display) may leave the app's
        # focused-window attribute on its editor. Prefer the actual focused
        # control's owning window; never scan background windows for a show.
        focus_window = focus if role == "AXWindow" else attr(focus, "AXWindow")
        if (focus_window is not None and attr(focus_window, "AXRole") == "AXWindow"
                and attr(focus, "AXFocused") is not False):
            window = focus_window
        if plain_enter:
            # Enter follows the focused control's own semantics, including web
            # editors, search fields and address bars. No app/role whitelist.
            finish("ready")
            return ShortcutTarget(bundle, pid, profile, window, focus, role,
                                  plain_enter=True, trace_id=info["trace_id"], diagnostic=safe_data(info))
        subrole = str(attr(window, "AXSubrole") or "")
        # Do not turn a chat shortcut into a dialog confirmation or terminal input.
        description = str(attr(focus, "AXDescription") or "").casefold()
        screen_frames = []
        if (PRESENTATION in scene_profiles
                and attr(window, "AXFullScreen") is not True):
            try:
                import AppKit
                import Quartz
                for screen in AppKit.NSScreen.screens():
                    bounds = Quartz.CGDisplayBounds(int(screen.deviceDescription()["NSScreenNumber"]))
                    screen_frames.append((bounds.origin.x, bounds.origin.y, bounds.size.width, bounds.size.height))
            except (AttributeError, KeyError, TypeError, ValueError):
                pass  # No display evidence means no geometric fallback.
        recognized = (detect_scene(bundle, scene_profiles, window, focus, attr,
            screen_frames=screen_frames, application_name=str(app.localizedName() or ""),
            application_category=application_category, observation=scene_observation)
            if scene else SceneResult())
        active_scene, input_context = recognized.scene, recognized.input_context
        if (browser_scene and not active_scene and info['browser_accessibility'].get('warming')
                and recognized.diagnostic.get('reason') in {'no_focused_webpage', 'no_web_player', 'page_url_unavailable'}):
            recognized.diagnostic['reason'] = 'browser_accessibility_initializing'
        info.update(recognition=recognized.diagnostic, scene=active_scene, input_context=input_context,
                    focus_role=role, window_subrole=subrole,
                    focus_window_override=window != reported_window)
        # Application commands (e.g. New/Open) can create their first window.
        # A window is optional for explicit mappings, not for legacy chat keys.
        # Preserve the stricter focus guard for legacy chat navigation, while
        # genuine dialogs/sheets and open menus still block all app shortcuts.
        scene_dialog = scene and active_scene == PRESENTATION and input_context == "nontext"
        chat_focus_blocked = (role in {"AXComboBox", "AXSearchField"}
                              or any(word in description for word in ("terminal", "终端", "search", "搜索")))
        blocked = ((window is None and not menu_action) or bool(attr(window, "AXModal")) or bool(attr(window, "AXSheets"))
                   or bool(attr(window, "AXMinimized"))
                   or (subrole in {"AXDialog", "AXSystemDialog"} and not scene_dialog)
                   or role in {"AXMenu", "AXMenuItem"}
                   or (not menu_action and chat_focus_blocked))
        document = attr(window, "AXDocument") if scene else None
        document_title = (attr(window, "AXTitle") if scene and not document
                          and bundle.casefold() == "com.apple.preview" else None)
        if scene or menu_action:
            focus_deadline = time.monotonic() + .18
            def focus_attr(node, key):
                if time.monotonic() >= focus_deadline:
                    raise TimeoutError()
                return attr(node, key)
            try:
                if not blocked:
                    focus_snapshot = inspect_focus(window, focus, focus_attr, window_shortcut=menu_action)
                    info["focus_policy"] = ("application_shortcut" if menu_action and window is None else
                                            "window_shortcut" if menu_action else "focused_control")
                    info["disabled_containers"] = list(focus_snapshot.disabled_containers)
                    info["unfocused_containers"] = list(focus_snapshot.unfocused_containers)
                    blocked = focus_snapshot.blocked
                    info["focus_reason"] = focus_snapshot.reason
            except TimeoutError:
                return finish("focus_validation_timeout")
            latest = frontmost_application()
            if latest is None or (str(latest.bundleIdentifier() or ""), int(latest.processIdentifier())) != (bundle, pid):
                return finish("foreground_changed")
            if (native_attr(element, "AXFocusedWindow") != reported_window
                    or native_attr(element, "AXFocusedUIElement") != focus):
                return finish("focus_changed")  # Do not mix windows in a snapshot.
            if menu_action and window is None and context_read_failed:
                return finish("target_unavailable")  # A failed AX read is not an absent window.
            if role != "AXWindow" and native_attr(focus, "AXWindow") != focus_window:
                return finish("window_changed")
            if scene and native_attr(window, "AXDocument") != document:
                return finish("document_changed")
            if document_title is not None and native_attr(window, "AXTitle") != document_title:
                return finish("document_changed")
            if recognized.web_area is not None:
                for key in ("AXURL", "AXDocument"):
                    cached = cache.get((id(recognized.web_area), key))
                    if cached is not None and native_attr(recognized.web_area, key) != cached[1]:
                        return finish("page_changed")
                if scene_observation and recognized.diagnostic.get('inferred_page'):
                    from .scenes.recognition.browser import _window_page
                    page_deadline = time.monotonic() + .15
                    def page_attr(node, key):
                        if time.monotonic() >= page_deadline:
                            raise TimeoutError()
                        return native_attr(node, key)
                    try:
                        if _window_page(window, page_attr, include_web=True) != recognized.web_area:
                            return finish('page_changed')
                    except TimeoutError:
                        return finish('page_changed')
        document_key = hashlib.sha256(str(document or document_title).encode()).hexdigest() if document or document_title else ""
        info["blocked"] = blocked
        # Reuse only already-read values; diagnostics never spend additional AX
        # calls or steal time from the recognition budget.
        flags = {key: cache.get((id(window), key), (None, None))[1]
                 for key in ("AXRole", "AXModal", "AXSheets", "AXMinimized", "AXFullScreen")}
        info["window_flags"] = {key: bool(value) if key == "AXSheets" else value for key, value in flags.items()}
        blocked_reason = ("window_missing" if window is None and not menu_action else
            "modal_window" if flags["AXModal"] else "sheet_open" if flags["AXSheets"] else
            "minimized_window" if flags["AXMinimized"] else
            "dialog_window" if subrole in {"AXDialog", "AXSystemDialog"} and not scene_dialog else
            "menu_or_sheet_focus" if role in {"AXMenu", "AXMenuItem"} else
            info.get("focus_reason", "blocked_window"))
        finish(blocked_reason if blocked else "ready" if menu_action and window is None else
               recognized.diagnostic.get("reason", "ready"))
        return ShortcutTarget(bundle, pid, profile or bundle, window, focus, role, blocked,
                              menu_action=menu_action, scene=active_scene, scene_checked=scene,
                              input_context=input_context, document_key=document_key, page_key=recognized.page_key,
                              web_area=recognized.web_area, player=recognized.player,
                              trace_id=info["trace_id"], diagnostic=safe_data(info))

    def same_target(self, target: ShortcutTarget, *, require_focus: bool = False) -> bool:
        options = dict(plain_enter=target.plain_enter, menu_action=target.menu_action)
        if target.scene_checked:
            options["scene"] = True
        current = self.capture(**options)
        def outcome(reason, valid=False):
            self.last_diagnostic = safe_data(dict(trace_id=target.trace_id, reason=reason,
                expected_app=target.bundle, expected_pid=target.pid, expected_scene=target.scene,
                observed_app=current.bundle if current else "", observed_pid=current.pid if current else 0,
                observed_scene=current.scene if current else "",
                observed=getattr(current, "diagnostic", {}) if current else getattr(self, "last_diagnostic", {})))
            return valid
        if current is None:
            return outcome("target_unavailable")
        if (current.bundle, current.pid) != (target.bundle, target.pid):
            return outcome("foreground_changed")
        if current.blocked:
            return outcome(current.diagnostic.get("reason", "blocked_window"))
        if (target.window is not None or target.menu_action) and current.window != target.window:
            return outcome("window_changed")
        if target.page_key or current.page_key:
            if (current.page_key != target.page_key
                    or current.web_area != target.web_area or current.player != target.player):
                return outcome("page_changed")
        if target.scene_checked and current.document_key != target.document_key:
            return outcome("document_changed")
        if target.scene_checked and (current.scene != target.scene
                                     or (target.scene and current.input_context != target.input_context)):
            return outcome("scene_changed")
        if (require_focus or (target.scene_checked and target.scene)) and target.focus is not None and current.focus != target.focus:
            return outcome("focus_changed")
        return outcome("target_verified", True)

    def post(self, target: ShortcutTarget, shortcut: str, *, require_focus: bool = False) -> None:
        import Quartz
        parts = normalize_shortcut(shortcut).split("+")
        if target.plain_enter and parts != ["Return"]:
            raise RuntimeError("全局上滑仅允许 Enter")
        from .mac_shortcut_events import prepare_chord, post_chord
        events, releases, modifiers = prepare_chord(Quartz, shortcut)
        # Allocation does not authorize delivery; validate the target immediately
        # before the first modifier event, keeping all events pinned to its PID.
        if not self.same_target(target, require_focus=require_focus):
            details = getattr(self, "last_diagnostic", {})
            raise SceneActionError(details.get("reason", "target_unavailable"), details)
        require_post_event_access()
        validation = getattr(self, "last_diagnostic", {})
        self.last_diagnostic = dict(trace_id=target.trace_id, reason="dispatch_started",
            delivery="unknown", events_posted=0, events_planned=len(events),
            event_sequence="balanced_modifiers_v1", modifier_keys=modifiers, validation=validation)
        self.last_diagnostic["event_flags"] = [int(Quartz.CGEventGetFlags(event))
                                                for _, _, event in events]
        post_chord(Quartz, target.pid, events, releases, self.last_diagnostic)
        self.last_diagnostic.update(reason="shortcut_posted", delivery="posted_unverified")


class MacAppShortcuts:
    @property
    def last_diagnostic(self):
        from .native_access import native_access
        return native_access().last_diagnostic

    def capture(self, *, plain_enter=False, menu_action=False, scene=False):
        if sys.platform != "darwin":
            return None
        from .native_access import native_access
        options = dict(plain_enter=plain_enter, menu_action=menu_action)
        if scene:
            options["scene"] = True
        value = native_access().call("capture", **options)
        return ShortcutTarget(**value) if value is not None else None

    def same_target(self, target, *, require_focus=False):
        from .native_access import native_access
        return native_access().call("same_target", target=target.remote_id, require_focus=require_focus)

    def post(self, target, shortcut, *, require_focus=False):
        from .native_access import native_access
        channel = native_access()
        channel.call("shortcut", target=target.remote_id,
                     shortcut=normalize_shortcut(shortcut), require_focus=require_focus)
        return channel.last_diagnostic
