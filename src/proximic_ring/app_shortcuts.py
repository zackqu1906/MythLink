"""Small macOS keyboard boundary. No activation, text reads, clipboard or clicks."""
from __future__ import annotations

from dataclasses import dataclass, field
import sys

from .app_gestures import KEY_CODES, normalize_shortcut, profile_for_application
from .mac_permissions import require_post_event_access
from .gesture_scenes import PRESENTATION
from .scene_capabilities import application_scene_profiles, installed_scene_profiles, BROWSERS, VIDEO
from .activity_scenes import activity_context
from .browser_media import BrowserMedia, browser_media_context


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
    website: str = ""
    page_key: str = ""
    web_area: object = field(default=None, repr=False)
    player: object = field(default=None, repr=False)


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
        scene_profiles = application_scene_profiles(bundle) if scene else {}
        if scene:
            try:
                scene_profiles = installed_scene_profiles(bundle, str(app.bundleURL().path()))
            except AttributeError:
                pass
            if not scene_profiles and not menu_action:
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
        def native_attr(node, name):
            if node is None:
                return None
            if name == "AXValueSettable":
                error, editable = AX.AXUIElementIsAttributeSettable(node, "AXValue", None)
                return bool(editable) if error == 0 else None
            error, value = AX.AXUIElementCopyAttributeValue(node, name, None)
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
            return ShortcutTarget(bundle, pid, profile, window, focus, role,
                                  plain_enter=True)
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
        browser = browser_media_context(window, focus, attr) if scene and bundle.casefold() in BROWSERS else BrowserMedia()
        active_scene, input_context = ((browser.scene, browser.input_context) if browser.scene else
            activity_context(bundle, scene_profiles, window, focus, attr, screen_frames=screen_frames, application_name=str(app.localizedName() or "")) if scene else ("", "unknown"))
        if scene and bundle.casefold() in BROWSERS and active_scene == VIDEO and not browser.scene:
            active_scene = ""  # Browser video must be tied to the current page/player.
        # Explicit application shortcuts don't require a chat input field.
        # Preserve the stricter focus guard for legacy chat navigation, while
        # genuine dialogs/sheets and open menus still block all app shortcuts.
        scene_dialog = scene and active_scene == PRESENTATION and input_context == "nontext"
        chat_focus_blocked = (role in {"AXComboBox", "AXSearchField"}
                              or any(word in description for word in ("terminal", "终端", "search", "搜索")))
        blocked = (window is None or bool(attr(window, "AXModal")) or bool(attr(window, "AXSheets"))
                   or bool(attr(window, "AXMinimized"))
                   or (subrole in {"AXDialog", "AXSystemDialog"} and not scene_dialog)
                   or role in {"AXMenu", "AXMenuItem"}
                   or (not menu_action and chat_focus_blocked))
        if scene or menu_action:
            latest = frontmost_application()
            if latest is None or (str(latest.bundleIdentifier() or ""), int(latest.processIdentifier())) != (bundle, pid):
                return None
            if (native_attr(element, "AXFocusedWindow") != reported_window
                    or native_attr(element, "AXFocusedUIElement") != focus):
                return None  # Focus changed during the read; do not mix windows.
        return ShortcutTarget(bundle, pid, profile or bundle, window, focus, role, blocked,
                              menu_action=menu_action, scene=active_scene, scene_checked=scene,
                              input_context=input_context, website=browser.website, page_key=browser.page_key,
                              web_area=browser.web_area, player=browser.player)

    def same_target(self, target: ShortcutTarget, *, require_focus: bool = False) -> bool:
        options = dict(plain_enter=target.plain_enter, menu_action=target.menu_action)
        if target.scene_checked:
            options["scene"] = True
        current = self.capture(**options)
        if current is None or (current.bundle, current.pid) != (target.bundle, target.pid) or current.blocked:
            return False
        if target.window is not None and current.window != target.window:
            return False
        if target.page_key or current.page_key:
            if (current.page_key != target.page_key or current.website != target.website
                    or current.web_area != target.web_area or current.player != target.player):
                return False
        if target.scene_checked and (current.scene != target.scene
                                     or (target.scene and current.input_context != target.input_context)):
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
            raise RuntimeError("目标窗口已变化，请在目标应用中重新操作")
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
