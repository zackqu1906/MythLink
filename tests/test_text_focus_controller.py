import threading
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QTest

from proximic_ring.platforms.macos.mac_permissions import MacPermissionError, PermissionState
from proximic_ring.ui.desktop.text_focus_controller import TextFocusController
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


def test_retained_controller_is_inert_by_default(route, monkeypatch):
    c, _, _, _, _, _, _ = route
    monkeypatch.delenv("PROXIMIC_STARTUP_PROBE", raising=False)
    calls = []
    channel = SimpleNamespace(call=lambda *a, **kw: calls.append((a, kw)), close=lambda: None)
    service = TextFocusController(c.ringGestures, channel=channel,
                                  stamp_reader=lambda: calls.append("stamp"))
    try:
        service.sync()
        service.refresh()
        service.poll()
        assert service.capture_stamp() is None
        assert service.picker.claim("swipe-down", None) is None
        QTest.qWait(550)
        assert not calls and not service._timer.isActive()
        assert not service.picker.active.is_set()
    finally:
        service.close()


@pytest.mark.parametrize("phase", ["idle", "listening", "finishing", "editing", "dictated"])
def test_runtime_never_discovers_selects_or_refreshes_fields(route, monkeypatch, phase):
    c, _, inline, _, sent, _, _ = route
    ring, calls, requests, shown = c.ringGestures, [], [], []
    fields = ring.textFields
    for name in ("sync", "refresh", "capture_stamp"):
        monkeypatch.setattr(fields, name, lambda *a, **kw: calls.append((a, kw)))
    for name in ("claim", "handle", "discard", "stop"):
        monkeypatch.setattr(fields.picker, name, lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(fields._channel, "call", lambda *a, **kw: calls.append((a, kw)))
    ring._requested.connect(lambda *args: requests.append(args))
    ring.showRequested.connect(lambda *args: shown.append(args))
    c.connectedChanged.emit()
    ring.changed.emit()
    ring._selector.changed.emit()
    ring.showMenu()
    shown.clear()
    inline._view["phase"] = phase
    generation = ring._generation
    for name in ("swipe-down", "tap", "swipe-left", "swipe-right", "swipe-up"):
        assert request(c, name)
    assert not requests and not shown and not sent
    assert ring._generation == generation
    inline._view["phase"] = "idle"
    request(c, "middle-pinch")
    request(c, "middle-pinch")
    c._connected = False
    c.connectedChanged.emit()
    c._connected = True
    c.connectedChanged.emit()
    for blocked in (True, False):
        c._proximity._gesture_state = (blocked, "")
        ring.lock_state_changed()
    ring.input_owner_changed()
    QTest.qWait(550)
    assert not calls and not fields._timer.isActive()
    assert not fields.pending.is_set() and not fields.picker.active.is_set()


@pytest.mark.parametrize("action", ["enter", "move", "confirm", "voice"])
def test_legacy_queued_selection_is_ignored(route, monkeypatch, action):
    import time
    from proximic_ring.ui.gestures.ring_gesture_controller import MenuRequest
    c, _, _, _, _, _, _ = route
    ring, calls = c.ringGestures, []
    monkeypatch.setattr(ring.textFields.picker, "handle", lambda *a: calls.append(a))
    monkeypatch.setattr(ring.textFields.picker, "discard", lambda *a: calls.append(a))
    transition = object()
    ring._transition = transition
    ring._apply(MenuRequest("swipe-down", ring._generation, time.monotonic(), False,
                           transition=transition, selection={"action": action}), c._disconnect_event)
    assert not calls and ring._transition is None and ring.mode == "input"


def test_retired_field_state_does_not_block_voice_or_application_routes(route, monkeypatch):
    from test_application_menus import configure, BUNDLE, ACTION
    from dataclasses import replace
    c, service, _, backend, sent, _, _ = route
    ring = c.ringGestures
    catalog = configure(service, monkeypatch)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    assert catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    fields = ring.textFields
    fields.pending.set()
    fields.applying.set()
    fields.picker.active.set()
    try:
        assert request(c, "tap")
        event = ring.envelope(SimpleNamespace(name="circle-clockwise"))
        assert ring.accepts(event)
        c._apply_gesture(event, c._disconnect_event)
        assert sent == [(BUNDLE, "Ctrl+Tab")]
        assert ring.setGlobalGestureAction("clench", "show_menu")
    finally:
        fields.pending.clear()
        fields.applying.clear()
        fields.picker.active.clear()


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
