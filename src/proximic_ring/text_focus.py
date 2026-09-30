"""Bounded, metadata-only Accessibility discovery and pinned focus changes."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import math
import time
import uuid


TEXT_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}
# These apps need lazy AX content enabled. The user approved this runtime
# initialization for Codex and WeChat; do not change other apps' AX settings.
ENHANCED_UI_BUNDLES = {"com.openai.codex", "com.tencent.xinWeChat", "com.tencent.WeChat"}
BROWSER_BUNDLES = {"com.apple.Safari", "com.apple.SafariTechnologyPreview", "com.google.Chrome",
                   "com.google.Chrome.canary", "org.chromium.Chromium", "com.microsoft.edgemac",
                   "company.thebrowser.Browser", "com.brave.Browser", "org.mozilla.firefox",
                   "org.mozilla.firefoxdeveloperedition", "com.operasoftware.Opera", "com.kagi.kagimacOS"}
MESSAGES = {
    "presentation": "场景模式中，暂停自动选择输入框",
    "voice_overridden": "此应用已覆盖语音手势，暂停选择输入框",
    "checking": "正在查找输入框",
    "no_fields": "当前窗口未提供文本框",
    "permission": "需要辅助功能权限",
    "no_window": "当前没有可用窗口",
    "own_app": "请切到目标应用",
    "unavailable": "暂时无法读取输入框",
    "limited": "输入框读取未完成，请重试",
    "stale": "窗口或焦点已变化，请重试",
    "not_focusable": "未能切换到目标输入框，请重试",
    "unsupported": "当前系统不支持切换输入框",
    "no_geometry": "应用未提供输入框位置",
}


def foreground_stamp():
    from .mac_workspace import frontmost_application
    app = frontmost_application()
    if app is None:
        return {"pid": 0, "window": 0}
    return window_stamp(int(app.processIdentifier()))


def window_stamp(pid):
    import Quartz
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, 0) or []
    window = next((int(w[Quartz.kCGWindowNumber]) for w in windows
                   if w.get(Quartz.kCGWindowOwnerPID) == pid and w.get(Quartz.kCGWindowLayer) == 0), 0)
    return {"pid": pid, "window": window}


class FocusError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(MESSAGES[status])


class MacAX:
    """All AX calls run in the restartable worker, with short IPC timeouts."""
    def __init__(self):
        import ApplicationServices as AX
        import CoreFoundation as CF
        self.ax, self.cf = AX, CF
        self._enhanced_apps = OrderedDict()
        self.clock = time.monotonic

    def front(self):
        # The shared reader refreshes both worker and gesture-thread captures.
        return foreground_stamp()

    def application(self, pid):
        app = self.ax.AXUIElementCreateApplication(pid)
        self.ax.AXUIElementSetMessagingTimeout(app, 0.06)
        return app

    @staticmethod
    def _application_identity(pid):
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return "", (pid, None)
        launched = app.launchDate()
        return str(app.bundleIdentifier() or ""), (pid, float(launched.timeIntervalSince1970()) if launched else None)

    def prepare_application(self, pid, app):
        bundle, identity = self._application_identity(pid)
        if bundle not in ENHANCED_UI_BUNDLES:
            return True
        if identity not in self._enhanced_apps:
            ready_at = self.clock()
            # One request per process lifetime, never toggle on each poll and
            # never disable another accessibility client's existing support.
            if self.attr(app, "AXEnhancedUserInterface") is False and self.settable(app, "AXEnhancedUserInterface"):
                self.ax.AXUIElementSetAttributeValue(app, "AXEnhancedUserInterface", True)
                # AppKit can return kAXErrorNotImplemented although this write
                # took effect (verified in Codex). Readback + tree discovery,
                # rather than the setter's return code, determines success.
                self.attr(app, "AXEnhancedUserInterface")
                ready_at = self.clock() + 2.2
            self._enhanced_apps[identity] = ready_at
            while len(self._enhanced_apps) > 64:
                self._enhanced_apps.popitem(last=False)
        return self.clock() >= self._enhanced_apps[identity]

    def bundle(self, pid):
        return self._application_identity(pid)[0]

    def _check(self, error):
        if error in (0, self.ax.kAXErrorAttributeUnsupported, self.ax.kAXErrorNoValue):
            return
        raise FocusError("unavailable")

    def attr(self, node, name):
        if node is None:
            return None
        error, value = self.ax.AXUIElementCopyAttributeValue(node, name, None)
        self._check(error)
        return value if error == 0 else None

    def metadata(self, node):
        names = ("AXRole", "AXSubrole", "AXHidden", "AXEnabled", "AXIsEditable")
        error, values = self.ax.AXUIElementCopyMultipleAttributeValues(node, names, 0, None)
        self._check(error)
        if error or values is None:
            raise FocusError("unavailable")
        def plain(value):
            if (value is not None and self.cf.CFGetTypeID(value) == self.ax.AXValueGetTypeID()
                    and self.ax.AXValueGetType(value) == self.ax.kAXValueAXErrorType):
                ok, code = self.ax.AXValueGetValue(value, self.ax.kAXValueAXErrorType, None)
                if ok:
                    self._check(code)
                return None
            return value
        return dict(zip(names, (plain(value) for value in values)))

    def children(self, node, limit):
        for name in ("AXVisibleChildren", "AXChildren"):
            error, count = self.ax.AXUIElementGetAttributeValueCount(node, name, None)
            self._check(error)
            if error:
                continue
            if count > limit:
                raise FocusError("limited")
            if not count:
                # Several AppKit/WebKit containers advertise this optional
                # attribute but return an empty array despite real children.
                if name == "AXVisibleChildren":
                    continue
                return []
            error, values = self.ax.AXUIElementCopyAttributeValues(node, name, 0, count, None)
            self._check(error)
            if error:
                raise FocusError("unavailable")
            return list(values or [])
        return []

    def settable(self, node, name):
        error, value = self.ax.AXUIElementIsAttributeSettable(node, name, None)
        self._check(error)
        return error == 0 and bool(value)

    def rect(self, node):
        position, size = self.attr(node, "AXPosition"), self.attr(node, "AXSize")
        if position is None or size is None:
            return None
        ok_p, p = self.ax.AXValueGetValue(position, self.ax.kAXValueCGPointType, None)
        ok_s, s = self.ax.AXValueGetValue(size, self.ax.kAXValueCGSizeType, None)
        return (p.x, p.y, s.width, s.height) if ok_p and ok_s else None

    def focus(self, node):
        error = self.ax.AXUIElementSetAttributeValue(node, "AXFocused", True)
        if error != 0:
            raise FocusError("not_focusable")

    def confirmed_focus(self, app, target, previous):
        # WebKit updates browser chrome and the web process asynchronously.
        # Observe completion briefly; never resend the focus mutation.
        deadline = self.clock() + 0.25
        while True:
            focused = self.attr(app, "AXFocusedUIElement")
            if focused == target or focused not in (None, previous) or self.clock() >= deadline:
                return focused
            time.sleep(.015)

    def hit_test(self, point):
        system = self.ax.AXUIElementCreateSystemWide()
        self.ax.AXUIElementSetMessagingTimeout(system, 0.06)
        error, node = self.ax.AXUIElementCopyElementAtPosition(system, *point, None)
        self._check(error)
        return node if error == 0 else None

    def click_focus(self, scope, point, *, expected_focus):
        """One balanced click, only after the session validates the hit target."""
        import AppKit
        import Quartz
        from .mac_permissions import require_post_event_access
        from .page_scroll import MacScrollAX
        require_post_event_access()
        # Reuse the scroll path's verified AX-window matching and native local
        # coordinates. scope.stamp may identify an address suggestion popup.
        router = MacScrollAX()
        window = router.window_number(scope)
        set_window_location = router._window_location_setter()
        pid = scope.stamp["pid"]
        info = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionIncludingWindow, window) or []
        bounds = next((w[Quartz.kCGWindowBounds] for w in info
                       if w.get(Quartz.kCGWindowNumber) == window
                       and w.get(Quartz.kCGWindowOwnerPID) == pid), None)
        if bounds is None:
            raise FocusError("stale")
        local = (point[0] - bounds["X"], point[1] - bounds["Y"])
        if not (0 <= local[0] < bounds["Width"] and 0 <= local[1] < bounds["Height"]):
            raise FocusError("stale")
        events = []
        for kind in (AppKit.NSEventTypeLeftMouseDown, AppKit.NSEventTypeLeftMouseUp):
            # PostToPid skips WindowServer's window annotation. A plain CG
            # mouse event has windowNumber=0 and AppKit discards the click.
            carrier = AppKit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                kind, (0, 0), 0, time.monotonic(), window, None, 0, 1, 0)
            event = Quartz.CGEventCreateCopy(carrier.CGEvent()) if carrier is not None else None
            if event is None:
                raise FocusError("not_focusable")
            Quartz.CGEventSetLocation(event, point)
            set_window_location(event, local)
            Quartz.CGEventSetFlags(event, 0)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventWindowUnderMousePointer, window)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent, window)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventSourceUserData, 0x50524F584147)
            events.append(event)
        # Allocate both events before posting either. Recheck after route
        # discovery so a foreground/focus change cannot redirect the click.
        if (self.front() != scope.stamp
                or self.attr(scope.app, "AXFocusedUIElement") != expected_focus
                or router.window_number(scope) != window):
            raise FocusError("stale")
        for event in events:
            Quartz.CGEventPostToPid(pid, event)


@dataclass
class Scope:
    token: str
    stamp: dict
    app: object
    window: object
    root: object
    last: object = None


@dataclass
class Plan:
    scope: Scope
    target: object
    original_focus: object
    count: int
    index: int
    expires: float
    partial: bool = False
    selection: tuple = ()


def intersection(a, b):
    if a is None:
        return b
    if b is None:
        return a
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return left, top, max(0, right - left), max(0, bottom - top)


class TextFocusSession:
    MAX_NODES = 1200
    MAX_DEPTH = 48
    BUDGET_SECONDS = 0.85

    def __init__(self, ax=None, *, clock=time.monotonic):
        self.ax = ax or MacAX()
        self.clock = clock
        self.scopes = OrderedDict()
        self.plans = OrderedDict()
        self.deadline = 0.0
        self.scan_incomplete = False
        self.selection = None

    def _check_budget(self):
        if self.clock() >= self.deadline:
            raise FocusError("limited")

    def _ancestry(self, node, root):
        nodes = []
        for _ in range(self.MAX_DEPTH):
            self._check_budget()
            if node is None or node in nodes:
                return []
            nodes.append(node)
            if node == root:
                return nodes
            node = self.ax.attr(node, "AXParent")
        return []

    def _editable(self, node, meta=None):
        self._check_budget()
        meta = meta or self.ax.metadata(node)
        return (meta.get("AXRole") in TEXT_ROLES and meta.get("AXEnabled") is not False
                and not meta.get("AXHidden") and meta.get("AXIsEditable") is not False
                and self.ax.settable(node, "AXFocused")
                and (bool(meta.get("AXIsEditable")) or self.ax.settable(node, "AXValue")))

    def _capture(self, ignored_pid=0, expected=None):
        self._check_budget()
        stamp = self.ax.front()
        if expected is not None and stamp != expected:
            raise FocusError("stale")
        if not stamp["pid"]:
            raise FocusError("no_window")
        if stamp["pid"] == ignored_pid:
            raise FocusError("own_app")
        app = self.ax.application(stamp["pid"])
        if not self.ax.prepare_application(stamp["pid"], app):
            raise FocusError("checking")
        window = self.ax.attr(app, "AXFocusedWindow")
        if window is None or self.ax.attr(window, "AXMinimized"):
            raise FocusError("no_window")
        focus = self.ax.attr(app, "AXFocusedUIElement")
        root = window
        ancestors = self._ancestry(focus, window) if focus is not None else []
        for node in ancestors[:-1]:
            meta = self.ax.metadata(node)
            if meta.get("AXRole") in {"AXMenu", "AXMenuItem"}:
                raise FocusError("no_window")
            if (meta.get("AXRole") in {"AXSheet", "AXDialog", "AXPopover"}
                    or meta.get("AXSubrole") in {"AXDialog", "AXSystemDialog"}):
                root = node
                break
        # A sheet can be exposed before its text control acquires focus.
        sheets = self.ax.attr(window, "AXSheets") or []
        if root == window and sheets:
            root = sheets[-1]
        for key, scope in list(self.scopes.items()):
            # WindowServer may put an address suggestion panel ahead of the
            # browser window. That changes the recognition stamp, not the AX
            # window we entered. Keep its logical identity and remembered field.
            if scope.stamp["pid"] == stamp["pid"] and scope.window == window and scope.root == root:
                if scope.stamp != stamp:
                    scope = replace(scope, stamp=stamp)
                    self.scopes[key] = scope  # Existing plans keep their original stamp.
                self.scopes.move_to_end(key)
                break
        else:
            scope = Scope(uuid.uuid4().hex, stamp, app, window, root)
            self.scopes[scope.token] = scope
            while len(self.scopes) > 32:
                self.scopes.popitem(last=False)
        if focus is not None and self._ancestry(focus, root) and self._editable(focus):
            scope.last = focus
        return scope, focus

    def _fields(self, scope):
        self.scan_incomplete = False
        clip = self.ax.rect(scope.root) or self.ax.rect(scope.window)
        stack, seen, fields = [(scope.root, 0, clip)], set(), []
        while stack:
            self._check_budget()
            node, depth, clip = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            if len(seen) > self.MAX_NODES or depth > self.MAX_DEPTH:
                raise FocusError("limited")
            try:
                meta = self.ax.metadata(node)
            except FocusError as exc:
                if exc.status != "unavailable" or node == scope.root:
                    raise
                self.scan_incomplete = True
                continue
            if meta.get("AXHidden") or meta.get("AXEnabled") is False:
                continue
            # No other window/tab may enter the current scope through an AX link.
            if node != scope.root and meta.get("AXRole") in {"AXWindow", "AXSheet", "AXDialog", "AXPopover"}:
                continue
            role = meta.get("AXRole")
            try:
                if role == "AXScrollArea" or role in TEXT_ROLES:
                    clip = intersection(clip, self.ax.rect(node))
                    if clip is not None and (clip[2] <= 0 or clip[3] <= 0):
                        continue
                if self._editable(node, meta):
                    fields.append(node)
                    continue
                children = self.ax.children(node, self.MAX_NODES - len(seen))
            except FocusError as exc:
                # Safari exposes AXUnknown nodes whose child count is nonzero
                # but whose child-read returns kAXErrorFailure. One unreadable
                # branch must not hide independently verified input fields.
                if exc.status != "unavailable":
                    raise
                self.scan_incomplete = True
                continue
            stack.extend((child, depth + 1, clip) for child in reversed(children))
        return fields

    def is_address_bar(self, node, scope, ancestors=None):
        bundle = self.ax.bundle(scope.stamp["pid"])
        if bundle not in BROWSER_BUNDLES:
            return False
        ancestors = ancestors or self._ancestry(node, scope.root)
        roles = [self.ax.metadata(parent).get("AXRole") for parent in ancestors]
        # A web page's own search/URL inputs are ordinary input fields.
        if "AXWebArea" in roles:
            return False
        identifier = " ".join(str(self.ax.attr(node, key) or "") for key in
                              ("AXIdentifier", "AXDOMIdentifier", "AXDOMClassList")).casefold()
        normalized = "".join(char for char in identifier if char.isalnum())
        if any(part in normalized for part in ("omnibox", "urlbar", "addressfield", "addressbar",
                                                "addressandsearchfield")):
            return True
        # Firefox can expose a separate toolbar search box. It must keep the
        # ordinary immediate-focus behavior; unknown toolbar fields do too.
        if any(part in normalized for part in ("searchbar", "find", "searchfield")):
            return False
        # Chromium's omnibox explicitly exposes ⌘L as AXKeyShortcutsValue.
        # This is control metadata, never the URL/text currently in the field.
        shortcut = str(self.ax.attr(node, "AXKeyShortcutsValue") or "").replace(" ", "").casefold()
        if shortcut in {"⌘l", "meta+l", "command+l", "cmd+l"}:
            return True
        return (bundle in {"com.apple.Safari", "com.apple.SafariTechnologyPreview"}
                and self.ax.metadata(node).get("AXRole") == "AXTextField" and "AXToolbar" in roles)

    def make_plan(self, scope, target, focus, count, index, partial=False, selection=()):
        handle = uuid.uuid4().hex
        self.plans[handle] = Plan(scope, target, focus, count, index, self.clock() + 0.8, partial, selection)
        while len(self.plans) > 8:
            self.plans.popitem(last=False)
        return handle

    def handle(self, operation, *, ignored_pid=0, expected=None, action="inspect", plan="",
               selection="", direction=""):
        self.deadline = self.clock() + self.BUDGET_SECONDS
        try:
            if operation == "focus_apply":
                return self._apply(plan, ignored_pid)
            if operation == "focus_selection":
                if self.selection is None:
                    from .text_selection import TextSelectionSession
                    self.selection = TextSelectionSession(self)
                return self.selection.handle(action, ignored_pid=ignored_pid, expected=expected,
                                             token=selection, direction=direction)
            scope, focus = self._capture(ignored_pid, expected)
            if operation == "focus_probe":
                return {"status": "ready", "scope": scope.token, "stamp": scope.stamp}
            if operation != "focus_plan" or action not in {"inspect", "restore", "next"}:
                raise ValueError("未知文本框操作")
            fields = self._fields(scope)
            if not fields and self.scan_incomplete:
                raise FocusError("unavailable")
            current, focus_after = self._capture(ignored_pid, scope.stamp)
            if current.token != scope.token or focus_after != focus:
                raise FocusError("stale")
            result = {"status": "available" if fields else "no_fields", "scope": scope.token,
                      "stamp": scope.stamp, "count": len(fields), "index": 0,
                      "partial": self.scan_incomplete}
            if not fields or action == "inspect":
                return result
            if action == "restore" and focus in fields and self.is_address_bar(focus, scope):
                # An already-focused address bar was explicitly chosen by the
                # user. Auto-discovery must never move that focus to the page.
                return {**result, "status": "focused", "index": fields.index(focus) + 1}
            if action == "next":
                index = (fields.index(focus) + 1) % len(fields) if focus in fields else 0
            else:
                index = fields.index(scope.last) if scope.last in fields else 0
            if self.is_address_bar(fields[index], scope):
                if action == "restore":
                    ordinary = [i for i, field in enumerate(fields) if not self.is_address_bar(field, scope)]
                    if ordinary:
                        index = ordinary[0]
                    else:
                        return {**result, "deferred": True}
                else:
                    return {**result, "index": index + 1, "deferred": True}
            handle = self.make_plan(scope, fields[index], focus, len(fields), index + 1, self.scan_incomplete)
            return {**result, "plan": handle, "index": index + 1}
        except FocusError as exc:
            return {"status": exc.status, "count": 0, "index": 0}

    def _apply(self, handle, ignored_pid):
        plan = self.plans.pop(handle, None)
        if plan is None or self.clock() > plan.expires:
            raise FocusError("stale")
        if plan.selection and (self.selection is None or not self.selection.valid_plan(plan.selection)):
            raise FocusError("stale")
        scope, focus = self._capture(ignored_pid, plan.scope.stamp)
        if scope.token != plan.scope.token or focus != plan.original_focus:
            raise FocusError("stale")
        ancestors = self._ancestry(plan.target, scope.root)
        if not ancestors or not self._editable(plan.target):
            raise FocusError("stale")
        if any(self.ax.metadata(node).get("AXHidden") for node in ancestors):
            raise FocusError("stale")
        # Check the foreground before each mutation. Never activate an app,
        # raise a window, press Tab or read/write text.
        if self.ax.front() != scope.stamp:
            raise FocusError("stale")
        if focus != plan.target:
            try:
                self.ax.focus(plan.target)
            except FocusError as exc:
                if exc.status != "not_focusable":
                    raise
                # Some implementations report a setter error even though the
                # async focus request took effect. Trust verified readback.
        confirm = getattr(self.ax, "confirmed_focus", None)
        focus_method = "accessibility"
        confirmed = (confirm(scope.app, plan.target, focus) if confirm is not None
                     else self.ax.attr(scope.app, "AXFocusedUIElement"))
        if confirmed == focus and focus != plan.target:
            confirmed = self._safari_address_handoff(plan, scope, focus, ancestors, confirmed)
            if confirmed == plan.target:
                focus_method = "safari_click"
        if confirmed != plan.target:
            raise FocusError("not_focusable")
        # The successful focus change can itself dismiss a native suggestion
        # window. Adopt its new stamp only after confirming the same AX scope.
        scope_after, focus_after = self._capture(ignored_pid)
        if scope_after.token != scope.token or focus_after != plan.target:
            raise FocusError("stale")
        scope.last = plan.target
        if plan.selection:
            return {**self.selection.did_focus(plan, scope_after), "focus_method": focus_method}
        return {"status": "focused", "scope": scope_after.token, "stamp": scope_after.stamp,
                "count": plan.count, "index": plan.index, "partial": plan.partial,
                "focus_method": focus_method}

    def _safari_address_handoff(self, plan, scope, focus, ancestors, confirmed):
        # Safari can acknowledge AXFocused on a WebKit editor while its native
        # address-field editor remains first responder. Do not infer success
        # from that setter, clear selected text, or send Escape/Tab to the page.
        if (focus is None or self.ax.bundle(scope.stamp["pid"]) not in
                {"com.apple.Safari", "com.apple.SafariTechnologyPreview"}
                or not self.is_address_bar(focus, scope)
                or not any(self.ax.metadata(node).get("AXRole") == "AXWebArea" for node in ancestors)):
            return confirmed
        rect = self.ax.rect(plan.target)
        if rect is None:
            return confirmed
        for node in ancestors:
            if node == scope.root or self.ax.metadata(node).get("AXRole") == "AXScrollArea":
                rect = intersection(rect, self.ax.rect(node))
        if not all(math.isfinite(value) for value in rect) or rect[2] <= 0 or rect[3] <= 0:
            return confirmed
        point = (rect[0] + rect[2]/2, rect[1] + rect[3]/2)
        # Hit-test the visible screen, not just cached field bounds. An overlay,
        # suggestion list, button or link must never receive the fallback click.
        hit = self.ax.hit_test(point)
        path = self._ancestry(hit, plan.target)
        if not path or any(self.ax.metadata(node).get("AXRole") not in
                           {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXStaticText", "AXGroup"}
                           for node in path):
            return confirmed
        current, current_focus = self._capture(expected=scope.stamp)
        if (current.token != scope.token or current_focus != focus or self.clock() > plan.expires
                or not self._editable(plan.target) or not self._ancestry(plan.target, scope.root)
                or any(self.ax.metadata(node).get("AXHidden") for node in ancestors)
                or self.ax.hit_test(point) != hit
                or self.ax.front() != scope.stamp):
            raise FocusError("stale")
        self.ax.click_focus(scope, point, expected_focus=focus)
        return self.ax.confirmed_focus(scope.app, plan.target, focus)
