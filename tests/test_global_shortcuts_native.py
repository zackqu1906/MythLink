"""Opt-in: receive actual macOS keys in an isolated foreground window."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = r'''
import json, os, tempfile, threading, time
from pathlib import Path
from types import SimpleNamespace
import AppKit
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from proximic_ring.platforms.macos.mac_app_activation import configure_app_activation
from proximic_ring.platforms.macos.native_access import native_access
from test_interaction_controls import _controller, _close

app = QApplication([])
original = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
configure_app_activation(background=False)
received, traces, timings = [], [], []

class Receiver(QQuickWindow):
    def keyPressEvent(self, event):
        if event.key() not in {Qt.Key_Shift, Qt.Key_Control, Qt.Key_Meta, Qt.Key_Alt}:
            received.append(dict(key=event.key(), modifiers=event.modifiers().value,
                native_key=event.nativeVirtualKey(), native_modifiers=event.nativeModifiers(),
                repeat=event.isAutoRepeat()))
        event.accept()

def until(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline: QTest.qWait(10)
    assert predicate(), (received, traces[-8:])

with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as patch:
    owner = _controller(Path(tmp), patch)
    owner._runtime_active = owner._connected = True
    ring, service = owner.ringGestures, owner._app_gestures
    patch.setattr(service, '_trace', lambda *args, **kw: traces.append((args, kw)))
    window = Receiver(); window.setTitle('Mythlink'); window.resize(600, 400); window.show()
    window.requestActivate(); AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    QTest.qWait(250)

    def trigger(name, expect_key=True):
        before, trace_count, started = len(received), len(traces), time.monotonic()
        errors, done = [], threading.Event()
        def recognize():
            try:
                assert not ring.filter(SimpleNamespace(name=name), False, owner._disconnect_event)
            except BaseException as exc: errors.append(repr(exc))
            finally: done.set()
        threading.Thread(target=recognize, daemon=True).start()
        until(done.is_set)
        assert not errors, errors
        until(lambda: any(args[1] == 'outcome' for args, kw in traces[trace_count:]))
        until(lambda: service._browser_actions._active is None)
        if expect_key:
            until(lambda: len(received) > before)
            timings.append(round((time.monotonic()-started)*1000, 1))
        QTest.qWait(120)
        assert len(received) == before + int(expect_key), traces[trace_count:]

    try:
        assert native_access().permissions().accessibility, 'Accessibility permission required'
        assert ring.setGlobalCustomBinding('snap', '测试 Command 组合键', 'Cmd+Shift+K')
        trigger('snap')
        assert received[-1]['key'] == Qt.Key_K and received[-1]['native_key'] == 40
        assert received[-1]['modifiers'] == (Qt.ControlModifier | Qt.ShiftModifier).value
        assert ring.setGlobalCustomBinding('snap', '测试 Control 组合键', 'Ctrl+Alt+J')
        trigger('snap')
        assert received[-1]['key'] == Qt.Key_J and received[-1]['native_key'] == 38
        assert received[-1]['modifiers'] == (Qt.MetaModifier | Qt.AltModifier).value
        assert ring.setGlobalGestureAction('circle-clockwise', 'custom:snap')
        trigger('snap', False)
        trigger('circle-clockwise')
        assert received[-1]['key'] == Qt.Key_J
        assert ring.setGlobalGestureAction('circle-clockwise', '')
        trigger('circle-clockwise', False)
        report = dict(received=received, delivery_ms=timings,
                      old_gesture_released=True, unbinding_stops_delivery=True)
        print(json.dumps(report), flush=True)
    finally:
        _close(owner); native_access().close(); window.close()
        if original: original.activateWithOptions_(0)
'''


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_GLOBAL_SHORTCUTS') != '1',
                   reason='opt-in native global shortcut receiver')
def test_custom_global_shortcuts_reach_real_window_and_old_binding_stops():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM='cocoa', QT_QUICK_CONTROLS_STYLE='Basic',
               PYTHONPATH=os.pathsep.join([str(root / 'src'), str(root / 'tests')]))
    result = subprocess.run([sys.executable, '-c', SCRIPT], cwd=root, env=env,
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
