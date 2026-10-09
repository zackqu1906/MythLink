"""Optional scene metadata cannot suppress or resurrect a requested hint."""
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QCoreApplication
from test_app_gestures import route
from test_gesture_scenes import presentation


@pytest.fixture
def pending_hints(presentation, monkeypatch):
    import proximic_ring.ui.ring_gesture_controller as module
    c, _, _, backend, _, _, _ = presentation
    ring = c.ringGestures
    tasks, defaults, scenes = [], [], []
    monkeypatch.setattr(ring._fields, 'refresh', lambda: None)
    monkeypatch.setattr(module, 'threading', SimpleNamespace(
        Thread=lambda *, target, **kw: SimpleNamespace(start=lambda: tasks.append(target))))
    ring.showRequested.connect(lambda *args: defaults.append(args))
    ring.sceneHudRequested.connect(lambda *args: scenes.append(args))
    return c, ring, backend, tasks, defaults, scenes


def test_stalled_scene_lookup_shows_basic_hint_once_and_ignores_late_reply(pending_hints):
    c, ring, backend, tasks, defaults, scenes = pending_hints
    ring.showMenu()
    assert ring._hud_timer.isActive() and ring._hud_timer.interval() == 1000
    assert not defaults and not scenes
    ring._hud_timer.timeout.emit()
    assert defaults == [('input', '')] and not scenes
    assert not ring._hud_timer.isActive() and ring._pending_hud is None
    tasks[0]()
    QCoreApplication.processEvents()
    assert defaults == [('input', '')] and not scenes


def test_expired_context_reply_falls_back_even_if_qt_timer_has_not_fired(pending_hints):
    c, ring, backend, _, defaults, scenes = pending_hints
    ring.showMenu()
    ring._pending_hud = (ring._generation, time.monotonic() - 2, c._disconnect_event)
    ring._show_scene_hud(*ring._pending_hud, backend.target)
    assert defaults == [('input', '')] and not scenes
    assert not ring._hud_timer.isActive()


@pytest.mark.parametrize('change', ['hide', 'notice', 'generation', 'connection', 'locked', 'close'])
def test_pending_hint_cannot_resurrect_after_a_newer_ui_or_session_state(pending_hints, change):
    import threading
    c, ring, _, tasks, defaults, scenes = pending_hints
    ring.showMenu()
    if change == 'hide': ring.hideRequested.emit()
    elif change == 'notice': ring.showRequested.emit('input', 'newer notice')
    elif change == 'generation': ring._generation += 1
    elif change == 'connection': c._disconnect_event = threading.Event()
    elif change == 'locked': c._proximity._gesture_state = (True, '')
    else: ring.close()
    expected = list(defaults)
    ring._hud_timer.timeout.emit()
    tasks[0]()
    QCoreApplication.processEvents()
    assert defaults == expected and not scenes


def test_repeated_request_keeps_only_the_latest_scene_hint(pending_hints):
    c, ring, _, tasks, defaults, scenes = pending_hints
    ring.showMenu()
    ring.showMenu()
    tasks[0]()
    QCoreApplication.processEvents()
    assert not defaults and not scenes and ring._hud_timer.isActive()
    tasks[1]()
    QCoreApplication.processEvents()
    assert len(scenes) == 1 and not defaults and not ring._hud_timer.isActive()


def test_manual_hints_are_available_without_a_ring_connection(pending_hints):
    c, ring, _, tasks, defaults, scenes = pending_hints
    c._connected = False
    c._disconnect_event.set()
    ring.showMenu()
    tasks[0]()
    QCoreApplication.processEvents()
    assert len(scenes) == 1 and not defaults
