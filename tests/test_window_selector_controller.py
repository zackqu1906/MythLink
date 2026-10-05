import threading

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.ui.window_selector_controller import WindowSelectorController
from test_app_gestures import route
from test_ring_gestures import request
from test_text_focus_controller import fields, start, until
from test_window_selector import Desktop


@pytest.fixture
def selector(route):
    c, _, inline, _, sent, _, _ = route
    ring = c.ringGestures
    ring._selector.close()
    d = Desktop()
    calls, notices, threads = [], [], []
    class Channel:
        wait = None
        def call(self, operation, **params):
            calls.append(operation)
            threads.append(threading.get_ident())
            if self.wait and operation == "selector_list": assert self.wait.wait(2)
            return d.session.handle(operation, **params)
        def close(self): pass
    channel = Channel()
    service = WindowSelectorController(ring, enabled=True, channel=channel,
                                       clock=lambda: d.now, stamp_reader=d.front)
    ring._selector = service
    service.changed.connect(ring._fields.sync)
    service.notice.connect(notices.append)
    yield c, inline, service, channel, d, calls, notices, threads, sent
    if channel.wait: channel.wait.set()
    service.close()


def opened(c, service):
    request(c, "clench")
    until(lambda: service.phase == "ready")


@pytest.mark.parametrize("mode", ["input", "operation"])
def test_gestures_navigate_confirm_exact_window_and_never_start_voice(selector, mode):
    c, _, s, _, d, calls, notices, threads, sent = selector
    shown, hidden = [], []
    c.ringGestures.showRequested.connect(lambda *args: shown.append(args))
    c.ringGestures.hideRequested.connect(lambda: hidden.append(True))
    if mode == "operation": request(c, "middle-pinch")
    old = c.ringGestures.envelope(type("Event", (), {"name": "circle-clockwise"})())
    request(c, "clench", deliver=False)
    assert not request(c, "tap", deliver=False)
    QCoreApplication.processEvents()
    until(lambda: s.phase == "ready")
    shown.clear()
    s.setColumns(2)
    assert s.atApps and len(s.cards) == 2 and s.cards[0]["windowCount"] == 2
    assert not request(c, "swipe-right") and s.selected == 1
    assert not request(c, "swipe-left") and s.selected == 0
    request(c, "middle-pinch")  # At app level there is no parent; no mode switch.
    assert s.atApps and c.ringGestures.mode == mode
    assert not request(c, "tap") and not s.atApps and s.phase == "ready"
    assert not d.activated and "selector_activate" not in calls
    assert not request(c, "swipe-down") and s.selected == 0
    assert not request(c, "swipe-right") and s.selected == 1
    assert c.ringGestures.mode == mode
    assert not request(c, "index-pinch") and shown == [(mode, "")]
    assert not request(c, "tap")
    until(lambda: not s.blocked.is_set())
    assert d.activated == [(10, d.b)] and hidden and not notices
    c._apply_gesture(old, c._disconnect_event)
    assert not sent and c.ringGestures.mode == mode
    if mode == "input": assert request(c, "tap")
    assert all(t != threading.get_ident() for t in threads)


@pytest.mark.parametrize("action", ["clench", "timeout", "disconnect", "speech"])
def test_cancel_and_timeout_keep_original_window_and_mode(selector, action):
    c, inline, s, _, d, _, _, _, _ = selector
    opened(c, s)
    if action == "clench": request(c, "clench")
    elif action == "timeout": d.now += 10.1; s.poll()
    elif action == "disconnect":
        c._connected = False
        c._disconnect_event.set()
        c.connectedChanged.emit()
    else: inline._view["phase"] = "listening"; s.poll()
    until(lambda: not s.blocked.is_set())
    assert not d.activated and c.ringGestures.mode == "input"


@pytest.mark.parametrize("change", ["front", "focus", "missing_focus", "space", "screen"])
def test_context_changes_never_poll_native_or_dismiss_selector(selector, change):
    c, _, s, _, d, calls, notices, _, _ = selector
    opened(c, s)
    if change == "front": d.stamp = {"pid": 20, "window": 3}
    elif change == "focus": d.app_nodes[10].attrs["AXFocusedWindow"] = d.b
    elif change == "missing_focus": d.app_nodes[10].attrs["AXFocusedWindow"] = None
    elif change == "space": d.rows.clear()
    else: d.displays.clear()
    before = list(calls)
    for second in range(1, 25):
        d.now = second
        if second % 5 == 0:
            request(c, "index-pinch")
        s.poll()
        QCoreApplication.processEvents()
        assert s.phase == "ready" and not notices
    assert calls == before  # No native polling, not merely a relaxed predicate.
    request(c, "clench")
    until(lambda: not d.session.token)
    assert s.phase == "closed"


def test_activity_renews_ten_second_timeout_and_old_discovery_cannot_reopen(selector):
    c, _, s, channel, d, _, _, _, _ = selector
    opened(c, s)
    d.now = 9
    request(c, "index-pinch")
    d.now = 11
    s.poll()
    assert s.phase == "ready" and s.remaining == 8
    request(c, "clench")
    channel.wait = threading.Event()
    request(c, "clench")
    until(lambda: s.phase == "loading")
    request(c, "clench")
    channel.wait.set()
    QCoreApplication.processEvents()
    assert s.phase == "closed" and not s.blocked.is_set() and not d.activated


@pytest.mark.parametrize("phase", ["starting", "listening", "finishing", "editing"])
def test_busy_sentence_never_queues_window_selection(selector, phase):
    c, inline, s, _, d, calls, _, _, _ = selector
    shown = []
    c.ringGestures.showRequested.connect(lambda *args: shown.append(args))
    inline._view["phase"] = phase
    request(c, "clench", deliver=False)
    inline._view["phase"] = "dictated"
    QCoreApplication.processEvents()
    assert shown[-1][1] == "请先结束本句" and not calls and not s.blocked.is_set()


def test_selection_suspends_field_plans_and_cancel_preserves_focus(selector, fields):
    c, _, s, _, _, _, _, _, _ = selector
    fc, _, f, channel, d, calls, _, _ = fields
    assert fc is c
    s.changed.connect(f.sync)
    start(f)
    channel.wait_for_plan = threading.Event()
    request(c, "swipe-down")
    until(lambda: f.pending.is_set() and f.picker._working)
    opened(c, s)
    assert not f._active and not f.picker.active.is_set()
    channel.wait_for_plan.set()
    until(lambda: not f.picker._working)
    before = list(calls)
    f.poll(); f.refresh()
    assert calls == before and d.writes == [d.a]
    request(c, "clench")
    until(lambda: not f._working)
    assert f._active and f.available and d.writes == [d.a]


def test_first_presentation_does_not_wait_for_window_discovery(selector):
    c, _, s, channel, d, calls, _, _, _ = selector
    shown = []
    s.presentRequested.connect(lambda: shown.append(s.phase))
    channel.wait = threading.Event()
    request(c, "clench")
    assert shown == ["loading"] and s.blocked.is_set()
    assert not request(c, "tap") and not d.activated
    channel.wait.set()
    until(lambda: s.phase == "ready")
    assert shown == ["loading", "ready"]


def test_left_selects_and_middle_pinch_returns_without_changing_mode(selector):
    c, _, s, _, d, calls, notices, _, _ = selector
    opened(c, s)
    request(c, "tap")
    request(c, "swipe-right")
    request(c, "swipe-left")
    assert not s.atApps and s.selected == 0 and s.phase == "ready"
    request(c, "swipe-right")
    d.now = 8
    request(c, "middle-pinch")
    assert s.atApps and s.selected == 0 and s.remaining == 10
    assert len(s.cards) == 2 and not d.activated
    request(c, "tap")
    assert not s.atApps and s.selected == 1
    request(c, "swipe-left")
    assert s.selected == 0
    request(c, "swipe-right")
    assert s.selected == 1
    assert calls == ["selector_list"] and not notices
    assert c.ringGestures.mode == "input"


@pytest.mark.parametrize("child", [False, True])
def test_clench_cancels_both_levels_without_entering_a_window(selector, child):
    c, _, s, _, d, _, _, _, _ = selector
    opened(c, s)
    if child: request(c, "tap")
    request(c, "clench")
    assert s.phase == "closed" and not d.activated


def test_single_window_app_enters_on_first_tap_without_changing_page(selector):
    c, _, s, _, d, calls, _, _, _ = selector
    opened(c, s)
    pages = []
    s.pageChanged.connect(lambda: pages.append(s.atApps))
    request(c, "swipe-right")
    assert s.cards[s.selected]["title"] == "1 个窗口 · Tap 进入"
    request(c, "tap")
    until(lambda: s.phase == "closed")
    assert d.activated == [(20, d.c)] and not pages
    assert calls.count("selector_activate") == 1


def test_host_main_descriptor_is_sent_instead_of_excluding_process(selector, monkeypatch):
    import os
    c, _, s, channel, d, _, _, _, _ = selector
    captured = []
    original = channel.call
    def call(operation, **params):
        if operation == "selector_list": captured.append(params)
        return original(operation, **params)
    channel.call = call
    monkeypatch.setattr(s, "_host_window", lambda: {"number": 42, "title": "Mythlink"})
    opened(c, s)
    assert captured == [{"host_pid": os.getpid(), "host_window": {"number": 42, "title": "Mythlink"}}]


def test_bundle_identity_groups_multiple_processes_but_missing_bundle_uses_pid(selector):
    _, _, s, _, _, _, _, _, _ = selector
    s._set_snapshot(dict(cards=[
        dict(id="a", pid=1, app="Same name", bundle="one", focused=False),
        dict(id="b", pid=2, app="Same name", bundle="one", focused=True),
        dict(id="c", pid=3, app="Same name", bundle="two"),
        dict(id="d", pid=4, app="Same name", bundle=""),
        dict(id="e", pid=5, app="Same name", bundle=""),
    ]))
    assert len(s.cards) == 4 and s.selected == 0
    assert s.cards[0]["windowCount"] == 2 and s.cards[0]["id"] == "b"
    s._app_index = 0
    assert [c["id"] for c in s.cards] == ["a", "b"]
