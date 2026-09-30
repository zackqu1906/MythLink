"""The hint must describe the foreground context, independently of the editor."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.ui.gesture_hud_model import hint_view
from test_app_gestures import route
from test_application_menus import ACTION, BUNDLE, configure
from test_gesture_scenes import presentation


@pytest.mark.parametrize("mode", ["input", "operation"])
def test_menu_resolves_regular_app_without_scene_or_voice_override(route, monkeypatch, mode):
    import proximic_ring.ui.ring_gesture_controller as module

    c, service, _, backend, _, _, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, "circle-clockwise", ACTION["id"])
    assert not catalog.voice_overridden(BUNDLE)
    assert BUNDLE not in catalog.configured_scenes()
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    captures = []
    def capture(**kwargs):
        captures.append(kwargs)
        return backend.target
    monkeypatch.setattr(backend, "capture", capture)
    # Keep the real queued signal delivery, avoiding native calls or a worker race.
    monkeypatch.setattr(module, "threading", SimpleNamespace(
        Thread=lambda *, target, **kwargs: SimpleNamespace(start=target)))
    ring = c.ringGestures
    ring._mode = mode
    monkeypatch.setattr(ring._fields, "refresh", lambda: None)
    shown, defaults = [], []
    ring.sceneHudRequested.connect(lambda mode, rows: shown.append(hint_view(mode, rows)))
    ring.showRequested.connect(lambda *args: defaults.append(args))
    ring.showMenu()
    QCoreApplication.processEvents()
    assert captures == [dict(menu_action=True, scene=True)]
    assert not defaults and len(shown) == 1
    assert shown[0]["orbs"][0]["action"] == ACTION["label"]
    assert not shown[0]["sceneActive"]
    assert shown[0]["modeTitle"] == ("输入模式" if mode == "input" else "操作模式")


def test_detected_scene_hides_other_actions_then_restores_app_modes_on_exit(presentation):
    c, _, _, backend, _, _, _ = presentation
    ring = c.ringGestures
    views = []
    ring.sceneHudRequested.connect(lambda mode, rows: views.append(hint_view(mode, rows)))
    def show(target):
        ring._show_scene_hud(ring._generation, time.monotonic(), c._disconnect_event, target)
    for mode in ("input", "operation"):
        ring._mode = mode
        show(backend.target)
    assert len(views) == 2 and views[0] == views[1]
    assert views[0]["modeTitle"] == "放映"
    assert {orb["key"] for orb in views[0]["orbs"]} == {"swipe-left", "swipe-right", "tap"}
    assert all(orb["scope"] != "global" for orb in views[0]["orbs"])
    defaults = []
    ring.showRequested.connect(lambda mode, message: defaults.append(mode))
    show(replace(backend.target, scene="", input_context="text"))
    assert defaults == ["operation"]  # The user's previous mode survives leaving the scene.


@pytest.mark.parametrize("change", ["generation", "expired", "connection"])
def test_delayed_context_does_not_replace_newer_hints(presentation, change):
    import threading
    c, _, _, backend, _, _, _ = presentation
    ring = c.ringGestures
    shown = []
    ring.sceneHudRequested.connect(lambda *args: shown.append(args))
    ring.showRequested.connect(lambda *args: shown.append(args))
    generation, created, connection = ring._generation, time.monotonic(), c._disconnect_event
    if change == "generation": generation -= 1
    elif change == "expired": created -= 2
    else: connection = threading.Event()
    ring._show_scene_hud(generation, created, connection, backend.target)
    assert not shown
