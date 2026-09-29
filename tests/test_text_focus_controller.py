import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest

from proximic_ring.mac_permissions import MacPermissionError, PermissionState
from proximic_ring.ui.text_focus_controller import TextFocusController
from test_app_gestures import route
from test_ring_gestures import request
from test_text_focus import Desktop, Node


def until(predicate):
    for _ in range(250):
        QTest.qWait(10)
        if predicate():
            return
    assert predicate()


@pytest.fixture
def fields(route):
    c, _, inline, _, _, _, _ = route
    desktop = Desktop()
    desktop.b.frame = (20, 140, 100, 30)
    calls, threads, notices = [], [], []
    class Channel:
        denied = False
        wait_for_plan = None
        def call(self, operation, **params):
            calls.append(operation)
            threads.append(threading.get_ident())
            if self.denied:
                raise MacPermissionError(PermissionState(False, False))
            if (self.wait_for_plan is not None and operation in {"focus_plan", "focus_selection"}
                    and params.get("action") != "inspect"):
                assert self.wait_for_plan.wait(2)
            return desktop.session.handle(operation, **params)
        def close(self): pass
    channel = Channel()
    ring = c.ringGestures
    ring._fields.close()
    service = TextFocusController(ring, enabled=True, channel=channel, stamp_reader=desktop.front)
    ring._fields = service
    ring.changed.connect(service.sync)
    service.notice.connect(notices.append)
    yield c, inline, service, channel, desktop, calls, threads, notices
    channel.wait_for_plan = None
    service.close()


def start(service):
    service.sync()
    until(lambda: not service._working and service._last_scope is not None)
    service._timer.stop()


def settled(service):
    until(lambda: not service.picker._working and service.picker._snapshot is not None)
    service.picker.landed(service.picker._serial)
    service.picker._probe.stop()


def test_autofocus_once_and_down_enters_spatial_selection_without_legacy_actions(fields):
    c, _, service, _, d, calls, threads, notices = fields
    start(service)
    assert d.writes == [d.a] and service.available
    button = Node("AXButton", parent=d.window)
    d.app.attrs["AXFocusedUIElement"] = button
    for _ in range(2):
        service.poll()
        until(lambda: not service._working)
    service.refresh()  # Waking the hint does not refocus a manually moved cursor.
    until(lambda: not service._working)
    assert d.writes == [d.a]
    request(c, "swipe-down")
    settled(service)
    assert d.writes[-1] is d.a
    request(c, "swipe-down")
    settled(service)
    assert d.writes[-1] is d.b
    request(c, "swipe-down")
    settled(service)
    assert d.writes[-1] is d.b  # No wrap when there is no field below.
    request(c, "swipe-up")
    settled(service)
    assert d.writes[-1] is d.a and service.picker._snapshot["index"] == 1
    assert all(t != threading.get_ident() for t in threads)
    request(c, "middle-pinch")
    assert not service._active
    before = list(calls)
    request(c, "swipe-down")
    QTest.qWait(20)
    assert calls == before


def test_window_change_focuses_once_and_does_not_queue_when_speech_is_busy(fields):
    c, inline, service, _, d, _, _, _ = fields
    start(service)
    new = Node("AXWindow", frame=d.window.frame).add(Node("AXTextField", editable=True))
    d.app.attrs.update(AXFocusedWindow=new, AXFocusedUIElement=None)
    d.stamp["window"] = 30
    inline._view["phase"] = "listening"
    service.poll()
    until(lambda: not service._working)
    assert d.writes == [d.a]
    inline._view["phase"] = "dictated"
    service.poll()
    until(lambda: not service._working)
    assert d.writes == [d.a]  # No deferred autofocus after finishing speech.
    request(c, "middle-pinch")
    request(c, "middle-pinch")
    until(lambda: not service._working)
    service._timer.stop()
    assert d.writes[-1] is new.children[0]


@pytest.mark.parametrize("change", ["mode", "disconnect", "speech", "target"])
def test_pending_discovery_cannot_apply_after_context_changes(fields, change):
    c, inline, service, channel, d, calls, _, notices = fields
    start(service)
    channel.wait_for_plan = threading.Event()
    request(c, "swipe-down")
    until(lambda: service.pending.is_set() and service.picker._working)
    assert not request(c, "tap", deliver=False)
    if change == "mode": request(c, "middle-pinch")
    elif change == "disconnect":
        c._connected = False
        c._disconnect_event.set()
        c.connectedChanged.emit()
    elif change == "speech": inline._view["phase"] = "editing"
    else: d.stamp["pid"] = 99
    channel.wait_for_plan.set()
    until(lambda: not service.picker._working)
    assert d.writes == [d.a]
    assert not service.pending.is_set()
    if change == "speech": assert notices[-1] == "请先结束本句"


def test_permission_and_empty_scope_are_distinct_grey_states(fields):
    c, _, service, channel, d, _, _, notices = fields
    channel.denied = True
    service.sync()
    until(lambda: not service._working)
    service._timer.stop()
    assert not service.available and service.hint == "需要辅助功能权限"
    channel.denied = False
    d.window.children = []
    service.poll()
    until(lambda: not service._working)
    assert not service.available and service.hint == "当前窗口未提供文本框"
    request(c, "swipe-down")
    until(lambda: not service.picker._working)
    assert not d.writes and notices[-1] == "当前窗口未提供文本框"


def test_permission_recovery_refreshes_hint_without_recapturing_focus(fields):
    _, _, service, channel, d, _, _, _ = fields
    start(service)
    button = Node("AXButton", parent=d.window)
    d.app.attrs["AXFocusedUIElement"] = button
    channel.denied = True
    service.poll()
    until(lambda: not service._working)
    assert service.hint == "需要辅助功能权限"
    channel.denied = False
    service.poll()
    until(lambda: not service._working)
    assert service.available
    assert d.app.attrs["AXFocusedUIElement"] is button
    assert d.writes == [d.a]


def test_partial_tree_still_enables_switching_and_explains_missing_regions(fields):
    c, _, service, _, d, _, _, _ = fields
    from proximic_ring.text_focus import FocusError
    broken = Node("AXUnknown")
    d.window.children.insert(0, broken)
    children = d.children
    def read(node, limit):
        if node is broken:
            raise FocusError("unavailable")
        return children(node, limit)
    d.children = read
    start(service)
    assert service.available and service.hint == "部分区域未提供控件"
    request(c, "swipe-down")
    settled(service)
    request(c, "swipe-down")
    settled(service)
    assert d.writes[-1] is d.b
    assert service.available and service.hint == "部分区域未提供控件"


def make_browser(d):
    d.app.attrs["bundle"] = "com.apple.Safari"
    d.a.attrs["AXIdentifier"] = "address-field"
    d.window.children = []
    d.window.add(Node("AXToolbar").add(d.a), Node("AXWebArea").add(d.b))


@pytest.mark.parametrize("change", ["popup_stamp", "transient_no_window", "before_passive_probe"])
def test_passive_monitor_never_undoes_address_confirmation(fields, change):
    c, _, service, _, d, calls, _, _ = fields
    make_browser(d)
    start(service)
    if change == "before_passive_probe":
        service._last_scope = None
    request(c, "swipe-down")
    settled(service)
    request(c, "swipe-up")
    settled(service)
    assert not request(c, "tap")
    until(lambda: not service.picker._working)
    assert d.app.attrs["AXFocusedUIElement"] is d.a
    before = list(d.writes)
    call_count = len(calls)
    if change == "popup_stamp": d.stamp["window"] = 99
    elif change == "transient_no_window":
        d.app.attrs["AXFocusedWindow"] = None
        service.poll()
        until(lambda: not service._working)
        d.app.attrs["AXFocusedWindow"] = d.window
    for _ in range(3):
        service.poll()
        until(lambda: not service._working)
    assert d.writes == before and d.app.attrs["AXFocusedUIElement"] is d.a
    assert "focus_apply" not in calls[call_count:]


def test_ordinary_tap_immediately_preserves_voice_route_and_address_tap_only_focuses(fields):
    c, inline, service, _, d, _, _, _ = fields
    make_browser(d)
    start(service)
    assert d.writes == [d.b]  # Never open the address dropdown during automatic focus.
    exited = []
    service.picker.exited.connect(exited.append)
    request(c, "swipe-down")
    settled(service)
    request(c, "swipe-up")
    settled(service)
    assert service.picker._deferred and d.writes == [d.b]
    assert not request(c, "tap")
    until(lambda: not service.picker._working)
    assert not service.picker.active.is_set() and exited == ["confirm"]
    assert d.writes == [d.b, d.a]
    assert request(c, "tap")
    request(c, "swipe-down")
    settled(service)
    request(c, "swipe-down")
    settled(service)
    assert not service.picker._deferred
    assert request(c, "tap", deliver=False)
    assert not service.picker.active.is_set()  # Before the audio endpoint sees this Tap.
    inline._view["phase"] = "listening"
    QCoreApplication.processEvents()
    assert exited == ["confirm", "confirm"] and not service.pending.is_set()
    assert d.app.attrs["AXFocusedUIElement"] is d.b


@pytest.mark.parametrize("address", [False, True])
def test_idle_clock_starts_at_landing_and_timeout_preserves_focus(fields, address):
    c, _, service, _, d, _, _, _ = fields
    if address:
        make_browser(d)
    start(service)
    picker = service.picker
    now = [100.0]
    picker.clock = lambda: now[0]
    exits, progress = [], []
    picker.exited.connect(exits.append)
    picker.progress.connect(lambda ratio, warn: progress.append((ratio, warn)))
    request(c, "swipe-down")
    settled(service)
    if address:
        request(c, "swipe-up")
        settled(service)
    before = list(d.writes)
    now[0] += 3.9
    picker.tick()
    assert progress[-1][1] is True and .21 < progress[-1][0] < .23
    # Recognition freezes the idle clock even if delivery to Qt is delayed.
    assert not request(c, "swipe-left", deliver=False)
    assert not request(c, "tap", deliver=False)
    now[0] += 1.15
    picker.tick()
    assert picker.active.is_set() and not exits
    QCoreApplication.processEvents()
    until(lambda: not picker._working)
    assert picker._deadline is None
    now[0] += .4
    picker.landed(picker._serial)
    picker._probe.stop()
    assert picker._deadline == now[0]+5
    now[0] += 4.99
    picker.tick()
    assert picker.active.is_set()
    now[0] += .02
    picker.tick()
    assert not picker.active.is_set() and exits == ["timeout"]
    assert d.writes == before and d.app.attrs["AXFocusedUIElement"] is (d.b if address else d.a)


@pytest.mark.parametrize("change", ["window", "manual_focus", "disconnect", "mode"])
def test_active_selection_cancels_on_context_changes(fields, change):
    c, _, service, _, d, _, _, _ = fields
    start(service)
    old_action = c.ringGestures.envelope(SimpleNamespace(name="swipe-up"))
    request(c, "swipe-down", deliver=False)
    assert not c.ringGestures.accepts(old_action)
    for name in ("tap", "swipe-left", "swipe-up"):
        assert not request(c, name, deliver=False)
    QCoreApplication.processEvents()
    settled(service)
    before = list(d.writes)
    if change == "window":
        d.stamp["window"] = 99
        d.app.attrs["AXFocusedWindow"] = Node("AXWindow", frame=d.window.frame)
    elif change == "manual_focus": d.app.attrs["AXFocusedUIElement"] = Node("AXButton", parent=d.window)
    elif change == "disconnect":
        c._connected = False
        c.connectedChanged.emit()
    else: request(c, "middle-pinch")
    service.picker.poll()
    until(lambda: not service.picker._working)
    assert not service.picker.active.is_set() and not service.pending.is_set()
    assert d.writes == before
    assert not c.ringGestures.accepts(old_action)
