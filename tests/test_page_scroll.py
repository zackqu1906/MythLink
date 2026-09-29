import pytest

from proximic_ring.page_scroll import MacScrollAX, PageScrollSession, animate_scroll, SCROLL_DURATION
from proximic_ring.text_focus import FocusError, Scope
from test_text_focus import Desktop, Node


class ScrollDesktop(Desktop):
    def __init__(self):
        super().__init__()
        self.sidebar = Node("AXScrollArea", frame=(0, 0, 180, 600))
        self.main = Node("AXScrollArea", frame=(200, 0, 700, 600))
        self.content = Node("AXStaticText", frame=self.main.frame)
        self.main.add(self.content)
        self.window.children = []
        self.window.add(self.sidebar, self.main, self.a)
        self.app.attrs["AXFocusedUIElement"] = self.a
        self.hit_node = self.content
        self.scrolls = []
        self.scroller = PageScrollSession(self, clock=lambda: self.now)

    def hit(self, app, point): return self.hit_node
    def window_number(self, scope): return scope.stamp["window"]
    def scroll(self, *args): self.scrolls.append(args)
    def prepare(self, direction="down", **kw):
        return self.scroller.handle("scroll_plan", direction=direction, **kw)
    def post(self, result): return self.scroller.handle("scroll_apply", plan=result["plan"])


@pytest.mark.parametrize("direction,pixels", [("down", -300), ("up", 300)])
def test_main_content_half_viewport_no_focus_mouse_or_text_mutation(direction, pixels):
    d = ScrollDesktop()
    plan = d.prepare(direction, expected=d.stamp)
    assert not d.scrolls
    assert d.post(plan) == {"status": "posted", "pixels": pixels}
    assert d.scrolls == [(10, 20, (550, 300), pixels)]
    assert not d.writes and d.app.attrs["AXFocusedUIElement"] is d.a
    assert not set(d.reads) & {"AXValue", "AXSelectedText", "AXTitle", "AXDescription"}
    assert d.post(plan)["status"] == "stale"  # A post cannot be replayed.


def test_browser_document_extent_is_clipped_to_visible_scroll_view():
    d = ScrollDesktop()
    web = Node("AXWebArea", frame=(200, -2000, 700, 8000)).add(d.content)
    d.main.children = []
    d.main.add(web)
    assert d.post(d.prepare())["pixels"] == -300


def test_nested_main_chat_region_selected_and_small_editor_avoided():
    d = ScrollDesktop()
    inner = Node("AXScrollArea", frame=(200, 0, 700, 500)).add(d.content)
    d.main.children = []
    d.main.add(inner)
    assert d.post(d.prepare())["pixels"] == -250
    inner.frame = (200, 450, 700, 100)
    assert d.prepare()["status"] == "no_region"  # No fallback to sidebar.
    inner.frame = (200, 0, 700, 500)
    d.content.role = "AXTextArea"
    d.content.editable = True
    assert d.prepare()["status"] == "no_region"


def test_obstructed_center_tries_another_point_and_never_clicks():
    d = ScrollDesktop()
    editor = Node("AXTextField", editable=True)
    d.main.add(editor)
    d.hit = lambda app, p: editor if p == (550, 300) else d.content
    d.post(d.prepare())
    assert d.scrolls[0][2] == (655, 210)
    assert not d.writes


@pytest.mark.parametrize("change", ["pid", "window", "focus", "sheet", "hidden", "moved", "removed", "expired", "restart", "hit"])
def test_delayed_scroll_is_cancelled_after_context_changes(change):
    d = ScrollDesktop()
    plan = d.prepare()
    if change == "pid": d.stamp["pid"] = 88
    elif change == "window": d.stamp["window"] = 88
    elif change == "focus": d.app.attrs["AXFocusedUIElement"] = d.b
    elif change == "sheet": d.window.attrs["AXSheets"] = [Node("AXSheet")]
    elif change == "hidden": d.main.hidden = True
    elif change == "moved": d.main.frame = (220, 0, 700, 600)
    elif change == "removed": d.main.parent = None
    elif change == "expired": d.now = 1
    elif change == "restart": d.scroller = PageScrollSession(d, clock=lambda: d.now)
    elif change == "hit": d.hit_node = d.sidebar
    assert d.post(plan)["status"] == "stale"
    assert not d.scrolls


def test_no_unbounded_document_traversal_and_no_blind_scroll_when_metadata_missing():
    d = ScrollDesktop()
    original = d.children
    d.children = lambda node, limit: (_ for _ in ()).throw(AssertionError("document traversal")) if node is d.main else original(node, limit)
    assert d.prepare()["status"] == "ready"
    d.main.frame = None
    assert d.prepare()["status"] == "no_region"
    assert not d.scrolls


def test_sheet_limits_scroll_scope_and_budgets_fail_closed():
    d = ScrollDesktop()
    sheet = Node("AXSheet", frame=(100, 100, 700, 500))
    d.window.attrs["AXSheets"] = [sheet]
    assert d.prepare()["status"] == "no_region"
    d.window.attrs.clear()
    d.scroller.context.MAX_NODES = 1
    assert d.prepare()["status"] == "limited"
    assert not d.scrolls


def test_native_post_failure_is_not_retried():
    d = ScrollDesktop()
    def fail(*args):
        d.scrolls.append(args)
        raise FocusError("unavailable")
    d.scroll = fail
    plan = d.prepare()
    assert d.post(plan)["status"] == "unavailable"
    assert d.post(plan)["status"] == "stale"
    assert len(d.scrolls) == 1


def test_native_sheet_window_routing_does_not_fall_back_to_parent(monkeypatch):
    import sys
    from types import SimpleNamespace
    d = ScrollDesktop()
    sheet = Node("AXSheet", frame=(100, 100, 600, 400))
    scope = Scope("test", d.stamp, d.app, d.window, sheet)
    def row(number, frame):
        return dict(pid=10, number=number, bounds=dict(zip(("X", "Y", "Width", "Height"), frame)))
    windows = [row(20, d.window.frame), row(30, sheet.frame)]
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1, kCGWindowListExcludeDesktopElements=2,
        kCGWindowOwnerPID="pid", kCGWindowBounds="bounds", kCGWindowNumber="number",
        CGWindowListCopyWindowInfo=lambda *_: windows))
    assert MacScrollAX.window_number(d, scope) == 30
    windows.pop()
    with pytest.raises(FocusError, match="变化"):
        MacScrollAX.window_number(d, scope)
    sheet.role = "AXGroup"  # An in-page dialog uses the host browser window.
    assert MacScrollAX.window_number(d, scope) == 20


def test_missing_optional_native_symbol_is_explicitly_unsupported():
    ax = object.__new__(MacScrollAX)
    ax._set_window_location = None
    with pytest.raises(FocusError) as error:
        ax._window_location_setter()
    assert error.value.status == "unsupported"


@pytest.mark.parametrize("direction,total", [("down", -301), ("up", 301)])
def test_larger_wheel_steps_preserve_total_distance_and_start_and_stop_gently(direction, total):
    d = ScrollDesktop()
    d.main.frame = (200, 0, 700, 602)
    plan = d.prepare(direction)
    times = []
    def post(progress):
        times.append(d.now)
        return d.scroller.handle("scroll_apply", plan=plan["plan"], progress=progress)
    def sleep(delay): d.now += delay
    result = animate_scroll(post, lambda: True, clock=lambda: d.now, sleep=sleep)
    steps = [s[-1] for s in d.scrolls]
    assert result == {"status": "posted", "pixels": total}
    assert sum(steps) == total and all(s * total > 0 for s in steps)
    assert 6 <= len(steps) <= 10 and max(abs(s) for s in steps) < abs(total) * .25
    assert abs(steps[0]) < abs(steps[len(steps)//2]) > abs(steps[-1])
    assert min(b - a for a, b in zip(times, times[1:])) >= .04
    assert times[-1] - times[0] == pytest.approx(SCROLL_DURATION)
    assert d.post(plan)["status"] == "stale"


@pytest.mark.parametrize("progress", [.2, .1, 0, -1, 1.1, float("nan")])
def test_duplicate_backwards_or_invalid_animation_progress_invalidates_plan(progress):
    d = ScrollDesktop()
    plan = d.prepare()
    assert d.scroller.handle("scroll_apply", plan=plan["plan"], progress=.2)["status"] == "scrolling"
    assert d.scroller.handle("scroll_apply", plan=plan["plan"], progress=progress)["status"] == "stale"
    assert len(d.scrolls) == 1 and d.post(plan)["status"] == "stale"


def test_slow_channel_cannot_create_a_large_catchup_jump_or_extend_forever():
    d = ScrollDesktop()
    plan = d.prepare()
    def post(progress):
        result = d.scroller.handle("scroll_apply", plan=plan["plan"], progress=progress)
        d.now += .4  # Far slower than a frame; never batch the remaining distance.
        return result
    result = animate_scroll(post, lambda: True, clock=lambda: d.now, sleep=lambda _: None)
    assert result["status"] == "cancelled"
    assert 1 < len(d.scrolls) < 10
    assert -300 < sum(s[-1] for s in d.scrolls) < 0
    assert max(abs(s[-1]) for s in d.scrolls) < 75


def test_partially_delivered_animation_expires_without_renewing_its_deadline():
    d = ScrollDesktop()
    plan = d.prepare()
    d.scroller.handle("scroll_apply", plan=plan["plan"], progress=.1)
    d.now = .9
    assert d.scroller.handle("scroll_apply", plan=plan["plan"], progress=.2)["status"] == "scrolling"
    d.now = 1.3
    assert d.post(plan)["status"] == "stale" and len(d.scrolls) == 2
