import threading

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest

from proximic_ring.mac_permissions import MacPermissionError, PermissionState
from proximic_ring.page_scroll import SCROLL_FRAMES
from proximic_ring.ui.page_scroll_controller import PageScrollController
from test_app_gestures import route
from test_page_scroll import ScrollDesktop
from test_ring_gestures import request
from test_text_focus_controller import until


@pytest.fixture
def scroll(route):
    c, _, inline, _, _, _, _ = route
    d = ScrollDesktop()
    calls, threads, notices = [], [], []
    class Channel:
        wait_plan = None
        wait_apply = None
        denied = False
        def call(self, operation, **params):
            calls.append(operation)
            threads.append(threading.get_ident())
            wait = self.wait_plan if operation == "scroll_plan" else self.wait_apply
            if wait is not None:
                assert wait.wait(2)
            if self.denied:
                raise MacPermissionError(PermissionState(False, False))
            return d.scroller.handle(operation, **params)
        def close(self): pass
    channel = Channel()
    ring = c.ringGestures
    ring._scroll.close()
    service = PageScrollController(ring, enabled=True, channel=channel, stamp_reader=d.front)
    ring._scroll = service
    service.notice.connect(notices.append)
    yield c, inline, service, channel, d, calls, threads, notices
    for wait in (channel.wait_plan, channel.wait_apply):
        if wait is not None: wait.set()
    until(lambda: not service.pending.is_set())
    service.close()


def test_only_operation_swipes_scroll_and_success_does_not_wake_menu(scroll):
    c, _, service, _, d, calls, threads, notices = scroll
    shown = []
    c.ringGestures.showRequested.connect(lambda *args: shown.append(args))
    assert request(c, "swipe-up")  # Input's original send route.
    assert not calls
    request(c, "middle-pinch")
    shown.clear()
    for direction in ("swipe-down", "swipe-up"):
        assert not request(c, direction)
        until(lambda: not service.pending.is_set())
    assert sum(s[-1] for s in d.scrolls if s[-1] < 0) == -300
    assert sum(s[-1] for s in d.scrolls if s[-1] > 0) == 300
    assert max(abs(s[-1]) for s in d.scrolls) < 75
    assert not shown and not notices
    for gesture in ("tap", "swipe-left", "swipe-right"):
        assert not request(c, gesture)
    assert calls == (["scroll_plan"] + ["scroll_apply"] * SCROLL_FRAMES) * 2
    assert all(t != threading.get_ident() for t in threads)


@pytest.mark.parametrize("change", ["mode", "round_trip", "disconnect", "speech", "window", "close"])
def test_pending_discovery_is_not_executed_after_context_change(scroll, change):
    c, inline, service, channel, d, calls, _, _ = scroll
    request(c, "middle-pinch")
    channel.wait_plan = threading.Event()
    request(c, "swipe-down")
    until(lambda: calls == ["scroll_plan"])
    if change in {"mode", "round_trip"}:
        request(c, "middle-pinch")
        if change == "round_trip": request(c, "middle-pinch")
    elif change == "disconnect":
        c._disconnect_event.set()
        c._connected = False
        c.connectedChanged.emit()
    elif change == "speech": inline._view["phase"] = "editing"
    elif change == "window": d.stamp["window"] = 999
    elif change == "close": service.close()
    channel.wait_plan.set()
    until(lambda: not service.pending.is_set())
    assert calls == ["scroll_plan"] and not d.scrolls


def test_recognition_stamp_busy_and_expiry_survive_gui_queue(scroll, monkeypatch):
    from proximic_ring.ui import ring_gesture_controller as module
    c, _, service, _, d, calls, _, _ = scroll
    request(c, "middle-pinch")
    request(c, "swipe-down", deliver=False)
    d.stamp["window"] = 99
    QCoreApplication.processEvents()
    until(lambda: not service.pending.is_set())
    assert not d.scrolls
    calls.clear()
    request(c, "swipe-up", busy=True)
    assert not calls
    request(c, "swipe-down", deliver=False)
    now = module.time.monotonic()
    monkeypatch.setattr(module.time, "monotonic", lambda: now + 2)
    QCoreApplication.processEvents()
    assert not calls


def test_repeated_gestures_do_not_accumulate_and_actual_post_gates_mode_switch(scroll):
    c, _, service, channel, d, calls, _, _ = scroll
    request(c, "middle-pinch")
    channel.wait_apply = threading.Event()
    request(c, "swipe-down")
    until(lambda: calls == ["scroll_plan", "scroll_apply"])
    for _ in range(4): request(c, "swipe-down")
    request(c, "middle-pinch")
    assert c.ringGestures.mode == "operation"
    request(c, "index-pinch")  # Hint is still usable during a post.
    channel.wait_apply.set()
    until(lambda: not service.pending.is_set())
    QTest.qWait(25)
    assert sum(s[-1] for s in d.scrolls) == -300
    assert calls == ["scroll_plan"] + ["scroll_apply"] * SCROLL_FRAMES
    request(c, "middle-pinch")
    assert c.ringGestures.mode == "input"


def test_failure_reports_reason_without_retry_and_next_gesture_can_recover(scroll):
    c, _, service, channel, d, calls, _, notices = scroll
    request(c, "middle-pinch")
    channel.denied = True
    request(c, "swipe-down")
    until(lambda: not service.pending.is_set())
    assert "权限" in notices[-1] and not d.scrolls
    channel.denied = False
    request(c, "swipe-down")
    until(lambda: not service.pending.is_set())
    assert sum(s[-1] for s in d.scrolls) == -300
    assert calls == ["scroll_plan", "scroll_plan"] + ["scroll_apply"] * SCROLL_FRAMES


@pytest.mark.parametrize("change", ["disconnect", "window", "speech", "close"])
def test_animation_stops_between_frames_when_context_changes(scroll, change):
    c, inline, service, _, d, _, _, _ = scroll
    request(c, "middle-pinch")
    original = d.scroll
    def interrupt(*args):
        original(*args)
        if len(d.scrolls) == 3:
            if change == "disconnect": c._disconnect_event.set()
            elif change == "window": d.stamp["window"] = 99
            elif change == "speech": inline._view["phase"] = "editing"
            else: service.close()
    d.scroll = interrupt
    request(c, "swipe-down")
    until(lambda: not service.pending.is_set())
    assert len(d.scrolls) == 3 and -300 < sum(s[-1] for s in d.scrolls) < 0
    QTest.qWait(50)
    assert len(d.scrolls) == 3  # No remaining animation/gesture is queued.
