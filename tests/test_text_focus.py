from dataclasses import dataclass, field

import pytest

from proximic_ring.text_focus import FocusError, MacAX, TextFocusSession


@dataclass(eq=False)
class Node:
    role: str
    children: list = field(default_factory=list)
    parent: object = None
    editable: bool = False
    focusable: bool = True
    hidden: bool = False
    enabled: bool = True
    frame: tuple = (20, 20, 100, 30)
    attrs: dict = field(default_factory=dict)

    def add(self, *nodes):
        for node in nodes:
            node.parent = self
        self.children.extend(nodes)
        return self


class Desktop:
    def __init__(self):
        self.window = Node("AXWindow", frame=(0, 0, 900, 700))
        self.a, self.b = Node("AXTextField", editable=True), Node("AXTextArea", editable=True)
        self.window.add(Node("AXGroup").add(self.a), self.b)
        self.app = Node("AXApplication", attrs={"AXFocusedWindow": self.window, "AXFocusedUIElement": None})
        self.stamp = {"pid": 10, "window": 20}
        self.now = 0
        self.writes = []
        self.reads = []
        self.session = TextFocusSession(self, clock=lambda: self.now)

    def front(self): return dict(self.stamp)
    def application(self, pid): return self.app
    def prepare_application(self, pid, app): return True
    def bundle(self, pid): return self.app.attrs.get("bundle", "")
    def metadata(self, node):
        return {"AXRole": node.role, "AXHidden": node.hidden, "AXEnabled": node.enabled,
                "AXIsEditable": node.editable if node.role.startswith("AXText") else None,
                "AXSubrole": node.attrs.get("AXSubrole")}
    def attr(self, node, name):
        self.reads.append(name)
        assert name not in {"AXValue", "AXSelectedText", "AXTitle", "AXDescription"}
        return node.parent if name == "AXParent" else node.attrs.get(name)
    def children(self, node, limit):
        values = node.attrs.get("AXVisibleChildren", node.children)
        if len(values) > limit: raise FocusError("limited")
        return values
    def settable(self, node, name): return node.focusable if name == "AXFocused" else node.editable
    def rect(self, node): return node.frame
    def focus(self, node):
        self.writes.append(node)
        self.app.attrs["AXFocusedUIElement"] = node
    def plan(self, action="next", **kw): return self.session.handle("focus_plan", action=action, **kw)
    def apply(self, result): return self.session.handle("focus_apply", plan=result["plan"])


def test_default_tree_order_wraps_without_reading_or_modifying_text():
    d = Desktop()
    assert d.apply(d.plan("restore"))["index"] == 1
    assert d.writes == [d.a]
    assert d.apply(d.plan())["index"] == 2
    assert d.writes[-1] is d.b
    assert d.apply(d.plan())["index"] == 1
    assert set(d.reads).isdisjoint({"AXValue", "AXSelectedText", "AXTitle", "AXDescription"})


def test_remembers_last_editable_per_window_but_preserves_a_new_manual_field_choice():
    d = Desktop()
    d.app.attrs["AXFocusedUIElement"] = d.b
    d.session.handle("focus_probe")
    other = Node("AXWindow", frame=d.window.frame).add(Node("AXTextField", editable=True))
    d.app.attrs.update(AXFocusedWindow=other, AXFocusedUIElement=None)
    d.stamp["window"] = 30
    d.apply(d.plan("restore"))
    d.app.attrs.update(AXFocusedWindow=d.window, AXFocusedUIElement=None)
    d.stamp["window"] = 20
    assert d.apply(d.plan("restore"))["index"] == 2
    d.app.attrs["AXFocusedUIElement"] = d.a
    assert d.apply(d.plan("restore"))["index"] == 1


@pytest.mark.parametrize("kind", ["hidden", "disabled", "readonly", "nonfocusable", "offscreen"])
def test_unusable_fields_are_excluded(kind):
    d = Desktop()
    if kind == "hidden": d.b.hidden = True
    elif kind == "disabled": d.b.enabled = False
    elif kind == "readonly": d.b.editable = False
    elif kind == "nonfocusable": d.b.focusable = False
    else: d.b.frame = (1000, 20, 100, 30)
    result = d.plan()
    assert result["count"] == 1
    d.apply(result)
    d.apply(d.plan())
    assert d.writes == [d.a]  # Single field wraps without another focus mutation.


def test_sheet_scope_does_not_leak_to_parent_and_browser_only_visits_visible_children():
    d = Desktop()
    sheet = Node("AXSheet", frame=d.window.frame).add(Node("AXTextField", editable=True))
    sheet.parent = d.window
    d.window.attrs["AXSheets"] = [sheet]
    assert d.plan()["count"] == 1
    d.apply(d.plan())
    assert d.writes == sheet.children
    sheet.children = []
    d.app.attrs["AXFocusedUIElement"] = None
    assert d.plan()["status"] == "no_fields"
    d.window.attrs.pop("AXSheets")
    old_page = Node("AXWebArea").add(Node("AXTextArea", editable=True))
    page = Node("AXWebArea").add(d.b)
    tabs = Node("AXGroup").add(old_page, page)
    tabs.attrs["AXVisibleChildren"] = [page]
    d.window.children = []
    d.window.add(tabs)
    assert d.plan()["count"] == 1
    d.apply(d.plan())
    assert d.writes[-1] is d.b


def test_focused_popup_and_nested_scroll_clipping():
    d = Desktop()
    popup = Node("AXGroup", frame=d.window.frame, attrs={"AXSubrole": "AXDialog"})
    popup.add(Node("AXTextField", editable=True))
    d.window.add(popup)
    d.app.attrs["AXFocusedUIElement"] = popup.children[0]
    assert d.plan()["count"] == 1
    d.app.attrs["AXFocusedUIElement"] = None
    d.window.children = []
    scroll = Node("AXScrollArea", frame=(0, 0, 100, 100))
    scroll.add(d.a, d.b)
    d.b.frame = (0, 150, 80, 30)
    d.window.add(scroll)
    assert d.plan()["count"] == 1


@pytest.mark.parametrize("change", ["pid", "window", "popup", "manual_focus", "removed", "expired", "restart"])
def test_delayed_focus_never_targets_changed_context(change):
    d = Desktop()
    result = d.plan()
    if change == "pid": d.stamp["pid"] = 99
    elif change == "window": d.stamp["window"] = 99
    elif change == "popup": d.window.attrs["AXSheets"] = [Node("AXSheet")]
    elif change == "manual_focus": d.app.attrs["AXFocusedUIElement"] = d.b
    elif change == "removed": d.a.parent = None
    elif change == "expired": d.now = 2
    else: d.session = TextFocusSession(d, clock=lambda: d.now)
    assert d.apply(result)["status"] == "stale"
    assert not d.writes


def test_read_failure_and_incomplete_tree_never_report_no_fields_or_focus_partial_result():
    d = Desktop()
    d.session.MAX_NODES = 2
    assert d.plan()["status"] == "limited"
    assert not d.writes
    d.session.MAX_NODES = 1200
    def fail(node, limit): raise FocusError("unavailable")
    d.children = fail
    assert d.plan()["status"] == "unavailable"
    assert not d.writes


def test_failed_focus_and_stale_plans_are_not_retried():
    d = Desktop()
    def reject(node): raise FocusError("not_focusable")
    d.focus = reject
    plan = d.plan()
    assert d.apply(plan)["status"] == "not_focusable"
    assert d.apply(plan)["status"] == "stale"


@pytest.mark.parametrize("setter_error", [False, True])
def test_async_focus_readback_is_confirmed_before_reporting_failure(setter_error):
    d = Desktop()
    attempts = []
    def request_focus(node):
        attempts.append(node)
        if setter_error:
            raise FocusError("not_focusable")
    def confirmed_focus(app, target, previous):
        assert previous is None
        d.focus = Desktop.focus.__get__(d)
        d.focus(target)
        return target
    d.focus = request_focus
    d.confirmed_focus = confirmed_focus
    assert d.apply(d.plan())["status"] == "focused"
    assert attempts == d.writes == [d.a]


def test_native_focus_confirmation_is_bounded_and_stops_on_user_focus_change(monkeypatch):
    import proximic_ring.text_focus as module
    ax = object.__new__(MacAX)
    now = [0.0]
    ax.clock = lambda: now[0]
    monkeypatch.setattr(module.time, "sleep", lambda delay: now.__setitem__(0, now[0]+delay))
    before, target, other = object(), object(), object()
    ax.attr = lambda *_: before
    assert ax.confirmed_focus(object(), target, before) is before
    assert .25 <= now[0] < .28
    reads = iter([before, target])
    ax.attr = lambda *_: next(reads)
    assert ax.confirmed_focus(object(), target, before) is target
    start = now[0]
    ax.attr = lambda *_: other
    assert ax.confirmed_focus(object(), target, before) is other
    assert now[0] == start


def test_scope_excludes_own_app_and_checks_recognition_time_window():
    d = Desktop()
    assert d.plan(ignored_pid=10)["status"] == "own_app"
    assert d.plan(expected={"pid": 10, "window": 19})["status"] == "stale"
    assert not d.writes


def test_popup_windowserver_number_does_not_reenter_same_ax_window_or_revalidate_old_plan():
    d = Desktop()
    first = d.session.handle("focus_probe")
    plan = d.plan("restore")
    d.stamp["window"] = 99  # Suggestion panel sorts ahead of the same AX window.
    second = d.session.handle("focus_probe")
    assert first["scope"] == second["scope"]
    assert first["stamp"] != second["stamp"]
    assert d.apply(plan)["status"] == "stale" and not d.writes
    d.app.attrs["AXFocusedWindow"] = Node("AXWindow", frame=d.window.frame)
    assert d.session.handle("focus_probe")["scope"] != first["scope"]


def test_empty_optional_visible_children_does_not_hide_real_children():
    from types import SimpleNamespace
    children = [object(), object()]
    copied = []
    def count(node, name, out):
        return (0, 0 if name == "AXVisibleChildren" else len(children))
    def copy(node, name, start, length, out):
        copied.append(name)
        return 0, children[start:start+length]
    ax = object.__new__(MacAX)
    ax.ax = SimpleNamespace(AXUIElementGetAttributeValueCount=count,
                            AXUIElementCopyAttributeValues=copy,
                            kAXErrorAttributeUnsupported=-25205, kAXErrorNoValue=-25212)
    assert ax.children(object(), 10) == children
    assert copied == ["AXChildren"]
    with pytest.raises(FocusError, match="读取未完成"):
        ax.children(object(), 1)


@pytest.mark.parametrize("failure", ["children", "metadata"])
def test_unreadable_safari_branch_does_not_hide_verified_sibling_fields(failure):
    d = Desktop()
    broken = Node("AXUnknown")
    d.window.children.insert(0, broken)
    original = getattr(d, failure)
    def read(node, *args):
        if node is broken:
            raise FocusError("unavailable")
        return original(node, *args)
    setattr(d, failure, read)
    plan = d.plan()
    assert plan["status"] == "available" and plan["count"] == 2 and plan["partial"]
    result = d.apply(plan)
    assert result["status"] == "focused" and result["partial"]
    assert d.writes == [d.a]
    assert d.apply(d.plan())["index"] == 2
    d.window.children = [broken]
    assert d.plan()["status"] == "unavailable"  # Never claim an unreadable tree has no fields.


@pytest.mark.parametrize("bundle,initial,requested", [
    ("com.openai.codex", False, True),
    ("com.tencent.xinWeChat", False, True),
    ("com.openai.codex", True, False),
    ("com.apple.Safari", False, False),
])
def test_approved_lazy_ax_initialization_is_once_and_waits_for_content(bundle, initial, requested):
    from collections import OrderedDict
    from types import SimpleNamespace
    ax = object.__new__(MacAX)
    state, writes = {"enabled": initial, "now": 0.0}, []
    ax._enhanced_apps = OrderedDict()
    ax.clock = lambda: state["now"]
    ax._application_identity = lambda pid: (bundle, (pid, 123.0))
    ax.attr = lambda node, name: state["enabled"]
    ax.settable = lambda node, name: True
    def enable(node, name, value):
        writes.append((name, value))
        state["enabled"] = True
        return -25208  # Observed in real Codex: the setting still takes effect.
    ax.ax = SimpleNamespace(AXUIElementSetAttributeValue=enable)
    app = object()
    assert ax.prepare_application(440, app) is not requested
    assert ax.prepare_application(440, app) is not requested
    state["now"] = 3
    assert ax.prepare_application(440, app)
    assert writes == ([("AXEnhancedUserInterface", True)] if requested else [])


def test_lazy_ax_warmup_does_not_report_no_fields_or_queue_focus():
    d = Desktop()
    ready = False
    d.prepare_application = lambda *_: ready
    assert d.plan()["status"] == "checking"
    assert not d.writes
    ready = True
    assert d.plan("inspect")["count"] == 2
    assert not d.writes


@pytest.mark.parametrize("failure", ["none", "permission", "allocation", "carrier", "missing_window",
                                    "wrong_pid", "outside", "foreground", "focus", "window_changed"])
def test_native_focus_click_routes_one_balanced_pair_to_ax_window(monkeypatch, failure):
    import sys
    from types import SimpleNamespace
    import proximic_ring.mac_permissions as permissions
    import proximic_ring.page_scroll as scroll
    from proximic_ring.text_focus import Scope
    posted = []
    address = object()
    # The first WindowServer entry is the suggestion popup, not the AX window.
    scope = Scope("s", {"pid": 10, "window": 5660}, object(), object(), object())
    bounds = {"X": 100, "Y": 80, "Width": 900, "Height": 700}
    def allowed():
        if failure == "permission":
            raise RuntimeError("permission denied")
    def carrier(kind, point, flags, timestamp, window, context, number, count, pressure):
        if failure == "carrier" and kind == 2:
            return None
        event = {"kind": kind, "window": window, "count": count}
        return SimpleNamespace(CGEvent=lambda: event)
    def copy(event):
        if failure == "allocation" and event["kind"] == 2:
            return None
        return dict(event)
    calls = []
    def window_number(actual):
        assert actual is scope
        calls.append(actual)
        return 99 if failure == "window_changed" and len(calls) > 1 else 54
    router = SimpleNamespace(window_number=window_number,
        _window_location_setter=lambda: lambda event, point: event.update(local=point))
    monkeypatch.setattr(scroll, "MacScrollAX", lambda: router)
    monkeypatch.setattr(permissions, "require_post_event_access", allowed)
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSEventTypeLeftMouseDown=1, NSEventTypeLeftMouseUp=2,
        NSEvent=SimpleNamespace(mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_=carrier)))
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(
        kCGWindowListOptionIncludingWindow=8, kCGWindowBounds="bounds", kCGWindowNumber="window",
        kCGWindowOwnerPID="pid", kCGMouseEventWindowUnderMousePointer=5,
        kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent=6, kCGEventSourceUserData=4,
        CGWindowListCopyWindowInfo=lambda *args: [] if failure == "missing_window" else
            [{"window": 54, "pid": 99 if failure == "wrong_pid" else 10, "bounds": bounds}],
        CGEventCreateCopy=copy,
        CGEventSetLocation=lambda event, point: event.update(point=point),
        CGEventSetFlags=lambda event, flags: event.update(flags=flags),
        CGEventSetIntegerValueField=lambda event, key, value: event.update({key: value}),
        CGEventPostToPid=lambda pid, event: posted.append((pid, event))))
    ax = object.__new__(MacAX)
    ax.front = lambda: {"pid": 99, "window": 1} if failure == "foreground" else scope.stamp
    ax.attr = lambda *args: None if failure == "focus" else address
    point = (0, 0) if failure == "outside" else (300, 230)
    if failure != "none":
        with pytest.raises(RuntimeError):
            ax.click_focus(scope, point, expected_focus=address)
        assert not posted
    else:
        ax.click_focus(scope, point, expected_focus=address)
        assert [e["kind"] for _, e in posted] == [1, 2]
        assert all(pid == 10 and e["point"] == (300, 230) and e["local"] == (200, 150)
                   and e["flags"] == 0 and e["count"] == 1
                   and e["window"] == e[5] == e[6] == 54 for pid, e in posted)


def test_native_focus_events_have_real_appkit_window_number_without_posting(monkeypatch):
    """Exercise the actual macOS event bridge, but never deliver a mouse event."""
    import sys
    if sys.platform != "darwin":
        pytest.skip("macOS event bridge")
    import AppKit
    import Quartz as Q
    from types import SimpleNamespace
    import proximic_ring.mac_permissions as permissions
    import proximic_ring.page_scroll as scroll
    from proximic_ring.text_focus import Scope
    router = scroll.MacScrollAX()
    # Loading the coordinate setter creates no UI and posts no input.
    setter = router._window_location_setter()
    local_points = []
    def set_local(event, point):
        local_points.append(point)
        setter(event, point)
    monkeypatch.setattr(scroll, "MacScrollAX", lambda: SimpleNamespace(
        window_number=lambda scope: 54, _window_location_setter=lambda: set_local))
    monkeypatch.setattr(permissions, "require_post_event_access", lambda: None)
    monkeypatch.setattr(Q, "CGWindowListCopyWindowInfo", lambda *args: [{
        Q.kCGWindowNumber: 54, Q.kCGWindowOwnerPID: 10,
        Q.kCGWindowBounds: {"X": 8, "Y": 33, "Width": 1904, "Height": 973}}])
    events = []
    monkeypatch.setattr(Q, "CGEventPostToPid", lambda pid, event: events.append(event))
    scope = Scope("s", {"pid": 10, "window": 5660}, object(), object(), object())
    address = object()
    ax = object.__new__(MacAX)
    ax.front = lambda: scope.stamp
    ax.attr = lambda *args: address
    ax.click_focus(scope, (757, 151.5), expected_focus=address)
    assert len(events) == 2 and local_points == [(749, 118.5), (749, 118.5)]
    for event, kind in zip(events, (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp)):
        native = AppKit.NSEvent.eventWithCGEvent_(event)
        assert native.windowNumber() == 54  # The original plain CG event yielded zero.
        assert native.clickCount() == 1
        assert Q.CGEventGetType(event) == kind and Q.CGEventGetFlags(event) == 0
        assert Q.CGEventGetIntegerValueField(event, Q.kCGMouseEventWindowUnderMousePointer) == 54
        assert Q.CGEventGetIntegerValueField(event, Q.kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent) == 54
        assert tuple(Q.CGEventGetLocation(event)) == (757, 151.5)
