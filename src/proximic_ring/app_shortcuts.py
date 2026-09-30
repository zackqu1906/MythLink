"""Small macOS keyboard boundary. No activation, text reads, clipboard or clicks."""
from __future__ import annotations

from dataclasses import dataclass, field
import sys

from .app_gestures import KEY_CODES, normalize_shortcut, profile_for_application
from .mac_permissions import require_post_event_access
from .gesture_scenes import presentation_profile, installed_presentation_profile, presentation_context


def verify_native_api() -> None:
    """Validate the installed bridges without reading the desktop or posting keys."""
    import AppKit
    import ApplicationServices
    import Quartz

    for module, names in (
        (AppKit, ("NSWorkspace",)),
        (ApplicationServices, ("AXUIElementCreateApplication", "AXUIElementCopyAttributeValue",
                               "AXUIElementSetMessagingTimeout")),
        (Quartz, ("CGPreflightPostEventAccess", "CGEventCreateKeyboardEvent",
                  "CGEventSetFlags", "CGEventSetIntegerValueField", "CGEventPostToPid")),
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


class LocalMacAppShortcuts:
    def capture(self, *, plain_enter: bool = False, menu_action: bool = False,
                scene: bool = False) -> ShortcutTarget | None:
        if sys.platform != "darwin":
            return None
        from .mac_workspace import frontmost_application
        app = frontmost_application()
        if app is None:
            return None
        bundle, pid = str(app.bundleIdentifier() or ""), int(app.processIdentifier())
        scene_profile = presentation_profile(bundle) if scene else ""
        if scene and not scene_profile:
            try:
                scene_profile = installed_presentation_profile(bundle, str(app.bundleURL().path()))
            except AttributeError:
                pass
            if not scene_profile:
                return None
        profile = profile_for_application(bundle, str(app.localizedName() or ""))
        if not profile and not plain_enter and not menu_action and not scene:
            return None
        import ApplicationServices as AX
        element = AX.AXUIElementCreateApplication(pid)
        # Never fetch AXValue, selection or document text. Scene capture may
        # inspect a bounded set of presentation control metadata below.
        try:
            AX.AXUIElementSetMessagingTimeout(element, 0.08)
        except (AttributeError, TypeError):
            pass
        def attr(node, name):
            if node is None:
                return None
            error, value = AX.AXUIElementCopyAttributeValue(node, name, None)
            if error == 0 and value is not None and name in {"AXPosition", "AXSize"}:
                value_type = AX.kAXValueCGPointType if name == "AXPosition" else AX.kAXValueCGSizeType
                valid, pair = AX.AXValueGetValue(value, value_type, None)
                return tuple(pair) if valid else None
            return value if error == 0 else None
        window = attr(element, "AXFocusedWindow")
        focus = attr(element, "AXFocusedUIElement")
        role = str(attr(focus, "AXRole") or "")
        if plain_enter:
            # Enter follows the focused control's own semantics, including web
            # editors, search fields and address bars. No app/role whitelist.
            return ShortcutTarget(bundle, pid, profile, window, focus, role,
                                  plain_enter=True)
        subrole = str(attr(window, "AXSubrole") or "")
        # Do not turn a chat shortcut into a dialog confirmation or terminal input.
        description = str(attr(focus, "AXDescription") or "").casefold()
        active_scene, input_context = presentation_context(bundle, window, focus, attr, profile=scene_profile) if scene else ("", "unknown")
        # WPS's verified slide surface uses AXDialog. Only a positive scene
        # capture may exempt it; ordinary shortcuts and all other dialogs stay blocked.
        scene_dialog = scene and scene_profile == "wps" and active_scene and input_context == "nontext"
        blocked = (bool(attr(window, "AXModal")) or (subrole in {"AXDialog", "AXSystemDialog"} and not scene_dialog)
                   or role in {"AXMenu", "AXMenuItem", "AXComboBox", "AXSearchField"}
                   or any(word in description for word in ("terminal", "终端", "search", "搜索")))
        if scene:
            latest = frontmost_application()
            if latest is None or (str(latest.bundleIdentifier() or ""), int(latest.processIdentifier())) != (bundle, pid):
                return None
        return ShortcutTarget(bundle, pid, profile or bundle, window, focus, role, blocked,
                              menu_action=menu_action, scene=active_scene, scene_checked=scene,
                              input_context=input_context)

    def same_target(self, target: ShortcutTarget, *, require_focus: bool = False) -> bool:
        options = dict(plain_enter=target.plain_enter, menu_action=target.menu_action)
        if target.scene_checked:
            options["scene"] = True
        current = self.capture(**options)
        if current is None or (current.bundle, current.pid) != (target.bundle, target.pid) or current.blocked:
            return False
        if target.window is not None and current.window != target.window:
            return False
        if target.scene_checked and (not target.scene or current.scene != target.scene
                                     or current.input_context != "nontext"):
            return False
        if require_focus and target.focus is not None and current.focus != target.focus:
            return False
        return True

    def post(self, target: ShortcutTarget, shortcut: str, *, require_focus: bool = False) -> None:
        import Quartz
        parts = normalize_shortcut(shortcut).split("+")
        if target.plain_enter and parts != ["Return"]:
            raise RuntimeError("全局上滑仅允许 Enter")
        if not self.same_target(target, require_focus=require_focus):
            raise RuntimeError("目标窗口已变化，请在目标对话中重新操作")
        require_post_event_access()
        masks = {"Cmd": Quartz.kCGEventFlagMaskCommand, "Ctrl": Quartz.kCGEventFlagMaskControl,
                 "Alt": Quartz.kCGEventFlagMaskAlternate, "Shift": Quartz.kCGEventFlagMaskShift}
        if "Fn" in parts[:-1]:
            masks["Fn"] = Quartz.kCGEventFlagMaskSecondaryFn
        flags = sum(masks[part] for part in parts[:-1])
        # Precreate the down/up pair, so an allocation failure cannot leave a key held.
        events = [Quartz.CGEventCreateKeyboardEvent(None, KEY_CODES[parts[-1]], down)
                  for down in (True, False)]
        if any(event is None for event in events):
            raise RuntimeError("无法创建应用快捷键")
        for event in events:
            Quartz.CGEventSetFlags(event, flags)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventSourceUserData, 0x50524F584147)
        for event in events:
            Quartz.CGEventPostToPid(target.pid, event)


class MacAppShortcuts:
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
        native_access().call("shortcut", target=target.remote_id,
                             shortcut=normalize_shortcut(shortcut), require_focus=require_focus)
