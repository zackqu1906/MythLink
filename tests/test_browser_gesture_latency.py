"""Slow browser IO must not block recognition feedback or replay stale work."""
from dataclasses import replace
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QTimer

from test_app_gestures import route


@pytest.fixture(autouse=True)
def no_proximity_helper(monkeypatch):
    from proximic_ring.ui.proximity.proximity_controller import ProximityController
    monkeypatch.setattr(ProximityController, 'refresh', lambda self: None)


def pump_until(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.001)
    assert predicate()


@pytest.mark.parametrize('custom', [False, True])
def test_browser_action_leaves_gui_and_recognition_feedback_responsive(route, monkeypatch, custom):
    c, service, _, backend, _, _, _ = route
    target = replace(backend.target, bundle='com.google.Chrome', profile='com.google.Chrome',
                     scene='video', input_context='nontext', browser_target={'media': 'video1'})
    binding = SimpleNamespace(action_id='custom:Cmd+K' if custom else 'web-video:play', shortcut='Cmd+K' if custom else 'Space')
    entered, release = threading.Event(), threading.Event()
    calls, done, ticks, shown = [], [], [], []
    main_thread = threading.get_ident()

    def blocked(target, action, **kwargs):
        calls.append((action, threading.get_ident()))
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(backend, 'browser_action', blocked, raising=False)
    monkeypatch.setattr(backend, 'post', blocked)
    monkeypatch.setattr(c, '_event_log', lambda *args, **kwargs: done.append(kwargs))
    c._gesture_trigger.enabled = True
    c._gesture_trigger.triggered.connect(shown.extend)
    try:
        service._dispatch('scene:tap', binding, target)
        assert entered.wait(.5)
        assert calls == [('Cmd+K' if custom else 'play', calls[0][1])]
        assert calls[0][1] != main_thread
        QTimer.singleShot(0, lambda: ticks.append(True))
        c._gesture_trigger.submit('tap', c._disconnect_event)
        pump_until(lambda: ticks and shown)
        assert shown == ['tap'] and not done and not release.is_set()
    finally:
        release.set()
        pump_until(lambda: bool(done))
    assert done[-1]['result'] == 'posted'


@pytest.mark.parametrize('invalidate', ['settings', 'disconnect', 'expired', 'close'])
def test_queued_browser_gesture_does_not_run_after_invalidation(route, monkeypatch, invalidate):
    c, service, _, backend, _, _, _ = route
    target = replace(backend.target, scene='video', input_context='nontext', browser_target={'media': 'v'})
    entered, release = threading.Event(), threading.Event()
    calls, outcomes = [], []
    def blocked(target, action):
        calls.append(action)
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(backend, 'browser_action', blocked, raising=False)
    monkeypatch.setattr(c, '_event_log', lambda *args, **kwargs: outcomes.append(kwargs))
    try:
        service._dispatch('scene:tap', SimpleNamespace(action_id='web-video:play', shortcut='Space'), target)
        assert entered.wait(.5)
        service._dispatch('scene:swipe-right', SimpleNamespace(action_id='web-video:forward', shortcut='Right'), target)
        assert calls == ['play']
        if invalidate == 'settings': service._generation += 1
        elif invalidate == 'disconnect': c._disconnect_event.set()
        elif invalidate == 'expired':
            import proximic_ring.ui.gestures.app_gesture_controller as module
            future = time.monotonic() + 2
            monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: future))
        else: service._browser_actions.close()
    finally:
        release.set()
    if invalidate != 'close':
        pump_until(lambda: len(outcomes) == 2)
        assert outcomes[-1]['reason'] in {'gesture_state_changed', 'browser_command_expired'}
    assert calls == ['play']


def test_browser_failure_keeps_worker_diagnostics_and_next_action_order(route, monkeypatch):
    from proximic_ring.diagnostics.scene_diagnostics import SceneActionError
    c, service, _, backend, _, _, _ = route
    target = replace(backend.target, scene='video', input_context='nontext', browser_target={'media':'v'})
    calls, outcomes = [], []
    def apply(target, action):
        calls.append(action)
        if action == 'play':
            raise SceneActionError('page_changed', {'reason':'page_changed', 'delivery':'not_sent'})
    monkeypatch.setattr(backend, 'browser_action', apply, raising=False)
    monkeypatch.setattr(c, '_event_log', lambda *args, **kwargs: outcomes.append(kwargs))
    service._dispatch('scene:tap', SimpleNamespace(action_id='web-video:play', shortcut='Space'), target)
    service._dispatch('scene:swipe-right', SimpleNamespace(action_id='web-video:forward', shortcut='Right'), target)
    pump_until(lambda: len(outcomes) == 2)
    assert calls == ['play', 'forward']
    assert outcomes[0]['result'] == 'blocked' and outcomes[0]['reason'] == 'page_changed'
    assert outcomes[1]['result'] == 'posted'
