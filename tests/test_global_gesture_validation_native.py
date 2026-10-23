"""Opt-in real self-AX validation through the global gesture and selector UI."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = r'''
import json, os, threading, time
from types import SimpleNamespace
import AppKit
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from proximic_ring.desktop.app_shortcuts import MacAppShortcuts
from proximic_ring.gestures.gesture_settings import GlobalGestureBindings
from proximic_ring.platforms.macos.mac_app_activation import configure_app_activation
from proximic_ring.platforms.macos.native_access import NativeAccessChannel, native_access
from proximic_ring.ui.gestures.ring_gesture_controller import RingGestureController
from proximic_ring.ui.desktop.window_selector_overlay import WindowSelectorOverlay

app = QApplication([])
original = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
configure_app_activation(background=False)
window = QQuickWindow(); window.setTitle('Mythlink'); window.resize(600, 400); window.show()
window.requestActivate(); AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
QTest.qWait(200)
backend = MacAppShortcuts()
logs, traces, heartbeats = [], [], []

class Owner(QObject):
    connectedChanged = Signal()
    _connected = _runtime_active = True
    _pending_inline_audio_start = None
    _utterance_active = False
    _global_gesture_bindings = GlobalGestureBindings()
    def __init__(self):
        super().__init__()
        self._disconnect_event = threading.Event()
        self._app_gestures = SimpleNamespace(backend=backend, _generation=0, _pending=None,
            phase_state=lambda: dict(phase='idle', composing=False, writing=False, edit_requested=False),
            cancel_pending=lambda *args: None, outside_binding=lambda event: None,
            _override_stop_reason=lambda event: '', _trace=lambda *args, **fields: traces.append((args, fields)))
    def _inline_enabled(self): return True
    def _event_log(self, kind, **fields): logs.append((kind, fields))

class OwnWindowChannel(NativeAccessChannel):
    def call(self, operation, **params):
        result = super().call(operation, **params)
        if operation == 'selector_list' and result.get('status') == 'ready':
            result['cards'] = [c for c in result['cards'] if c['pid'] == os.getpid()]
        return result

def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline: QTest.qWait(10)
    assert predicate(), (logs, traces)

def capture():
    result = {}
    done = threading.Event()
    def read():
        try: result['target'] = backend.capture(menu_action=True, focus_sensitive=False)
        except Exception as exc: result['error'] = str(exc)
        finally: done.set()
    threading.Thread(target=read, daemon=True).start()
    until(done.is_set)
    target = result.get('target')
    assert target is not None and not target.blocked and target.pid == os.getpid(), result
    return target

owner = Owner(); ring = RingGestureController(owner)
selector = ring.windowSelector
selector._channel.close(); selector._channel = OwnWindowChannel()
selector.set_main_window(window)
overlay = WindowSelectorOverlay(selector, app)
# The test displays only its own empty window, never user previews/content.
overlay.icons.prepare = lambda cards: {}
overlay.previews.start = lambda *args, **kwargs: None
timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: heartbeats.append(time.monotonic())); timer.start()
try:
    assert native_access().permissions().accessibility is True, 'Accessibility permission required'
    for attempt in range(3):
        target = capture()
        count = len(heartbeats)
        event = SimpleNamespace(route='outside', capture_error='', target=target, generation=0,
            created=time.monotonic(), trace_id='native-clench-' + str(attempt),
            source=SimpleNamespace(event=SimpleNamespace(name='clench')))
        start = time.monotonic()
        ring.handle_outside(event, owner._disconnect_event)
        dispatch_ms = (time.monotonic() - start) * 1000
        until(lambda: selector.phase == 'ready')
        assert overlay.window.isVisible() and selector.cards
        assert len(heartbeats) > count
        assert any(args[2] == 'global_action_started' and args[0] == event.trace_id for args, _ in traces)
        assert not any(args[2] in {'target_unavailable', 'target_validation_failed'} for args, _ in traces)
        print(json.dumps(dict(attempt=attempt, dispatch_ms=round(dispatch_ms, 2),
            selector_phase=selector.phase, visible=overlay.window.isVisible(), gui_ticks=len(heartbeats)-count)), flush=True)
        selector.cancel(); QTest.qWait(100)
finally:
    timer.stop(); ring.close(); overlay.close(); native_access().close(); window.close()
    if original: original.activateWithOptions_(0)
'''


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_GLOBAL_GESTURES') != '1',
                    reason='opt-in native foreground Mythlink global gesture check')
def test_foreground_mythlink_clench_opens_real_selector_without_blocking_qt():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM='cocoa', QT_QUICK_CONTROLS_STYLE='Basic', PYTHONPATH=str(root / 'src'))
    result = subprocess.run([sys.executable, '-c', SCRIPT], cwd=root, env=env,
                            capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
