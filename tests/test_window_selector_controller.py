import threading
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.ui.desktop.window_selector_controller import WindowSelectorController
from test_app_gestures import route
from test_ring_gestures import request
from test_text_focus_controller import until
from test_window_selector import Desktop


@pytest.fixture
def selector(route):
    c, _, inline, backend, sent, _, _ = route
    backend.target = replace(backend.target, input_context="nontext", focus="page", role="AXGroup")
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
    service.notice.connect(notices.append)
    yield c, inline, service, channel, d, calls, notices, threads, sent
    if channel.wait: channel.wait.set()
    service.close()


def opened(c, service):
    request(c, "clench")
    until(lambda: service.phase == "ready")


@pytest.mark.parametrize("mode", ["operation"])
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
    assert not request(c, "index-pinch")
    assert not request(c, "tap")
    until(lambda: not s.blocked.is_set())
    assert d.activated == [(10, d.b)] and hidden and not notices
    c._apply_gesture(old, c._disconnect_event)
    assert not sent and c.ringGestures.mode == mode
    assert request(c, "double-tap")
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
    assert not d.activated and c.ringGestures.mode == "operation"


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
    assert not shown and not calls and not s.blocked.is_set()


@pytest.mark.parametrize("confirm", [False, True])
def test_window_selection_never_starts_field_discovery(selector, monkeypatch, confirm):
    c, _, s, _, _, _, _, _, _ = selector
    fields = c.ringGestures.textFields
    calls = []
    monkeypatch.setattr(fields._channel, "call", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(fields, "sync", lambda: calls.append("sync"))
    opened(c, s)
    if confirm:
        request(c, "swipe-right")
        request(c, "tap")
        until(lambda: s.phase == "closed")
    else:
        request(c, "clench")
    assert not calls and not fields._timer.isActive() and not fields.picker.active.is_set()


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
    assert c.ringGestures.mode == "operation"


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
    assert s.cards[s.selected]["title"] == "1 个窗口 · 单击或 Tap 进入"
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


def test_missing_gesture_origin_stamp_still_opens_global_selector(selector, monkeypatch):
    c, _, s, _, _, calls, notices, _, _ = selector
    monkeypatch.setattr(s, "_stamp_reader", lambda: None)
    opened(c, s)
    assert calls == ["selector_list"] and not notices


def test_windowless_frontend_keeps_overlay_open_and_confirms_another_app(selector):
    c, _, s, _, d, _, notices, _, _ = selector
    d.rows = [r for r in d.rows if r["pid"] == 20]
    d.app_nodes[10].attrs["AXFocusedWindow"] = None
    opened(c, s)
    assert len(s.cards) == 1 and s.cards[0]["pid"] == 20 and not notices
    request(c, "tap")
    until(lambda: s.phase == "closed")
    assert d.activated == [(20, d.c)] and not notices


def test_native_exception_keeps_reason_stage_location_and_never_retries_activation(selector, monkeypatch):
    c, _, s, channel, _, calls, notices, _, _ = selector
    log = []
    monkeypatch.setattr(c, "_event_log", lambda event, **fields: log.append(fields))
    opened(c, s)
    original = channel.call
    attempts = []
    def fail(operation, **params):
        if operation == "selector_activate":
            attempts.append(operation)
            channel.last_diagnostic = dict(reason="channel_timeout", stage="activate_target", target_pid=20,
                error_type="TimeoutError", error_frames=[dict(module="window_selector.py", function="activate", line=100)],
                title="private window title", url="https://private.example")
            raise TimeoutError("private contents")
        return original(operation, **params)
    monkeypatch.setattr(channel, "call", fail)
    request(c, "swipe-right")
    request(c, "tap")
    until(lambda: s.phase == "closed")
    assert attempts == ["selector_activate"] and len(notices) == 1
    outcome = next(row for row in log if row.get("action") == "selector_activate")
    assert outcome["reason"] == "channel_timeout"
    assert outcome["native_stage"] == "activate_target" and outcome["native_target_pid"] == 20
    error = next(row for row in log if row.get("action") == "native_error")
    assert error["error_type"] == "TimeoutError" and "window_selector.py:100:activate" in error["locations"]
    assert "private" not in str(log)


def test_activation_waits_for_current_ui_turn_and_submits_only_once(selector, monkeypatch):
    c, _, s, _, d, calls, _, _, sent = selector
    logs = []
    monkeypatch.setattr(c, '_event_log', lambda event, **fields: logs.append(fields))
    opened(c, s)
    s.pick(1)
    s.confirm()
    assert s.phase == 'activating' and 'selector_activate' not in calls
    s.confirm()
    until(lambda: s.phase == 'closed')
    assert calls.count('selector_activate') == 1 and d.activated == [(20, d.c)] and not sent
    dispatch = next(row for row in logs if row.get('action') == 'activation_dispatch')
    assert dispatch['dismiss_ms'] >= 0 and dispatch['ui_turn_ms'] >= 0


@pytest.mark.parametrize('change', ['cancel', 'disconnect', 'generation', 'speech'])
def test_cancel_before_deferred_activation_never_switches_windows(selector, change):
    c, inline, s, _, d, calls, _, _, _ = selector
    opened(c, s)
    s.pick(1)
    s.confirm()
    if change == 'cancel': s.cancel()
    elif change == 'disconnect': c._disconnect_event.set()
    elif change == 'generation': c.ringGestures._generation += 1
    elif change == 'speech': inline._view['phase'] = 'listening'
    QCoreApplication.processEvents()
    s.poll()
    assert s.phase == 'closed' and not d.activated and 'selector_activate' not in calls


def test_pointer_click_expands_then_enters_exact_window_once(selector):
    c, _, s, _, d, calls, _, _, _ = selector
    opened(c, s)
    s.click(0)
    assert not s.atApps and s.phase == 'ready' and not d.activated
    s.click(1)
    s.click(0)  # A second confirm cannot alter the in-flight destination.
    until(lambda: s.phase == 'closed')
    assert d.activated == [(10, d.b)] and calls.count('selector_activate') == 1


@pytest.mark.parametrize('confirm', ['click', 'tap'])
def test_hover_and_both_confirmation_methods_share_the_selected_target(selector, confirm):
    c, _, s, _, d, calls, _, _, _ = selector
    opened(c, s)
    s.hover(1)
    assert s.selected == 1 and not d.activated
    if confirm == 'click': s.click(1)
    else: request(c, 'tap')
    until(lambda: s.phase == 'closed')
    assert d.activated == [(20, d.c)] and calls.count('selector_activate') == 1


@pytest.mark.parametrize('index', [-1, 2, 999])
def test_invalid_pointer_card_never_confirms_the_previous_selection(selector, index):
    c, _, s, _, d, calls, _, _, _ = selector
    opened(c, s)
    s.hover(index)
    s.click(index)
    assert s.atApps and s.phase == 'ready' and not d.activated
    assert 'selector_activate' not in calls


def test_motion_on_same_card_renews_timeout_without_repeating_preview_selection(selector):
    c, _, s, _, d, _, _, _, _ = selector
    opened(c, s)
    selections, updates = [], []
    s.selectionChanged.connect(lambda: selections.append(s.selected))
    s.changed.connect(lambda: updates.append(s.remaining))
    for i in range(200):
        d.now = i / 200
        s.hover(s.selected)
    assert not selections and not updates
    d.now = 8
    s.hover(s.selected)
    d.now = 11
    s.poll()
    assert s.phase == 'ready' and s.remaining == 7 and not selections


def test_pointer_context_tracks_entry_activation_and_exit(selector):
    c, _, s, _, _, _, _, _, _ = selector
    original = s.pointer_context
    request(c, 'clench', deliver=False)
    QCoreApplication.processEvents()
    assert s.pointer_context[1] in {'loading', 'ready'} and s.pointer_context != original
    until(lambda: s.phase == 'ready')
    s.click(1)
    assert s.pointer_context[1] == 'activating'
    until(lambda: s.phase == 'closed')
    assert s.pointer_context[1] == 'closed' and s.pointer_context[0] != original[0]


@pytest.mark.parametrize('enabled', [False, True])
def test_selector_preserves_touchpad_toggle_and_output(enabled, selector):
    from test_ui_touchpad import Source, Output
    c, _, s, _, _, _, _, _, _ = selector
    tp, source = c.touchpad, Source()
    tp.output_factory = Output
    tp.attach(source, c._disconnect_event)
    if enabled:
        tp.start()
        source.future.set_result(None)
        until(lambda: tp.state == 'running')
    original = tp._run.output if tp._run else None
    try:
        opened(c, s)
        assert tp.active is enabled
        if enabled:
            assert tp._run.output is original and original.active
            assert original.options['click_context']() == s.pointer_context
        s.cancel()
        assert tp.active is enabled
        assert len(source.starts) == int(enabled) and source.stops == 0
        if enabled:
            assert tp._run.output is original and original.active
            assert original.options['click_context']()[1] == 'closed'
    finally:
        tp.stop()
        QCoreApplication.processEvents()
