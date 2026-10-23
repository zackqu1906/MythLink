"""Opt-in real window switching through the shared AX channel with a busy Qt UI."""
import os
from pathlib import Path
import subprocess
import sys
import select

import pytest


SCRIPT = r'''
import json, os, threading, time
from types import SimpleNamespace
import AppKit
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from proximic_ring.platforms.macos.mac_app_activation import configure_app_activation
from proximic_ring.desktop.text_focus import foreground_stamp
from proximic_ring.platforms.macos.native_access import NativeAccessChannel
from proximic_ring.ui.desktop.window_selector_controller import WindowSelectorController
from proximic_ring.ui.desktop.window_selector_overlay import WindowSelectorOverlay

def until(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        QTest.qWait(10)
        if predicate(): return
    assert predicate()

original = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
configure_app_activation(background=False)
window = QQuickWindow()
window.setTitle('Mythlink host activation fixture')
window.resize(600, 400)
window.show()
logs, calls = [], []
class Ring(QObject):
    changed = Signal()
    hideRequested = Signal()
    _generation = 1
    globalBindings = dict(show_menu='index-pinch', switch_mode='middle-pinch', window_selector='clench')
    owner = SimpleNamespace(_disconnect_event=threading.Event(), _connected=True, _runtime_active=True,
        _event_log=lambda event, **fields: logs.append(fields))
    def speech_busy(self): return False
class Channel(NativeAccessChannel):
    def call(self, operation, **params):
        calls.append(operation)
        result = super().call(operation, **params)
        if operation == 'selector_list' and result.get('status') == 'ready':
            result['cards'] = [c for c in result['cards'] if c['pid'] == os.getpid()]
        return result
ring = Ring()
s = WindowSelectorController(ring, enabled=True, channel=Channel())
s.set_main_window(window)
assert s._channel.permissions().accessibility is True, 'Accessibility permission required'
overlay = WindowSelectorOverlay(s, app)
# Only this fixture's card is displayed; no user content is captured.
overlay.icons.prepare = lambda cards: {}
overlay.previews.start = lambda *args, **kwargs: None
QTest.qWait(250)
number = s._host_window()['number']
bundles = os.environ.get('PROXIMIC_SELECTOR_TEST_ORIGINS', 'com.apple.finder').split(',')
origins = [a for a in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
           if a.bundleIdentifier() in bundles]
assert {str(a.bundleIdentifier()) for a in origins} == set(bundles), 'Requested origin app is not running'
try:
    for origin in origins:
        for pause in (0, .12, .12):
            origin.activateWithOptions_(0)
            until(lambda: foreground_stamp()['pid'] == origin.processIdentifier())
            s._epoch += 1
            s._connection, s._generation = ring.owner._disconnect_event, ring._generation
            s.blocked.set()
            s._started = time.monotonic()
            s._submit('selector_list', host_pid=os.getpid(), host_window=s._host_window())
            until(lambda: s.phase == 'ready')
            own = next(i for i, c in enumerate(s.cards) if c['pid'] == os.getpid())
            s.pick(own)
            QTest.qWait(100)
            assert overlay.window.isVisible() and foreground_stamp()['pid'] == origin.processIdentifier()
            start = time.monotonic()
            # Keep the UI busy just after the worker starts. The shared AX
            # route must recover from the short timeout without skipping AX.
            submit = s._submit
            def busy_submit(operation, **params):
                submit(operation, **params)
                if operation == 'selector_activate' and pause: time.sleep(pause)
            s._submit = busy_submit
            s._handle_gesture('tap', s._epoch, s._connection, start, False)
            until(lambda: s.phase == 'closed')
            outcome = [r for r in logs if r.get('action') == 'selector_activate'][-1]
            assert outcome['status'] == 'activated', outcome
            assert foreground_stamp()['pid'] == os.getpid() and window.isActive()
            assert not overlay.window.isVisible() and 'selector_activate' in calls
            s._submit = submit
            print(json.dumps(dict(origin=str(origin.bundleIdentifier()), ui_pause_ms=round(pause*1000),
                elapsed_ms=round((time.monotonic()-start)*1000, 2), outcome=outcome)), flush=True)
finally:
    s.close()
    overlay.close()
    window.close()
    if original: original.activateWithOptions_(0)
'''


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_WINDOW_SELECTOR') != '1',
                    reason='opt-in native shared AX activation check')
def test_shared_ax_activation_survives_busy_ui():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM='cocoa', QT_QUICK_CONTROLS_STYLE='Basic', PYTHONPATH=str(root / 'src'))
    result = subprocess.run([sys.executable, '-c', SCRIPT], cwd=root, env=env,
                            capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_WINDOW_SELECTOR') != '1',
                    reason='opt-in native external application timeout check')
def test_busy_external_application_has_same_timeout_and_recovers():
    import AppKit
    from proximic_ring.desktop.text_focus import FocusError
    from proximic_ring.desktop.window_selector import MacWindowAX, WindowSelectorSession
    foreground = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    child_script = r'''
import sys, threading, time
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QObject, Signal, Slot
from proximic_ring.platforms.macos.mac_app_activation import configure_app_activation
app = QApplication([])
configure_app_activation(background=False)
w = QWidget(); w.setWindowTitle('External AX response fixture'); w.resize(650, 430); w.show()
class Pause(QObject):
    requested = Signal()
    def __init__(self):
        super().__init__(); self.requested.connect(self.pause)
    @Slot()
    def pause(self):
        print('busy', flush=True)
        time.sleep(.12)
pause = Pause()
def read():
    for line in sys.stdin: pause.requested.emit()
threading.Thread(target=read, daemon=True).start()
print('ready', flush=True)
app.exec()
'''
    env = dict(os.environ, QT_QPA_PLATFORM='cocoa')
    child = subprocess.Popen([sys.executable, '-u', '-c', child_script], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    def line():
        assert select.select([child.stdout], [], [], 5)[0], 'Fixture response timed out'
        return child.stdout.readline().strip()
    def busy():
        child.stdin.write('pause\n'); child.stdin.flush()
        assert line() == 'busy'
    try:
        assert line() == 'ready'
        ax = MacWindowAX()
        import time
        time.sleep(.25)
        assert ax.windows(child.pid)
        busy()
        with pytest.raises(FocusError) as failed:
            ax.windows(child.pid)
        assert failed.value.ax_error == -25204
        # The target is an independent app, with no Mythlink host registration.
        session = WindowSelectorSession(ax)
        time.sleep(.15)
        listing = session.handle('selector_list')
        card = next(c for c in listing['cards'] if c['pid'] == child.pid)
        foreground.activateWithOptions_(0)
        time.sleep(.15)
        busy()
        result = session.handle('selector_activate', token=listing['token'], target=card['id'])
        assert result['status'] == 'activated', session.last_diagnostic
        assert session.last_diagnostic['ax_read_timeouts'] >= 1
        print('external_application_timeout_recovered', session.last_diagnostic['ax_read_timeouts'])
    finally:
        child.terminate()
        child.communicate(timeout=3)
        if foreground: foreground.activateWithOptions_(0)


@pytest.mark.skipif(sys.platform != 'darwin' or not os.environ.get('PROXIMIC_SELECTOR_CAPTURE_TEST_APP'),
                    reason='opt-in: choose an already running app for live preview teardown validation')
def test_preview_teardown_keeps_the_selected_window_when_a_snapshot_contains_destroyed_helpers():
    import time
    import AppKit
    import Foundation
    import Quartz
    from proximic_ring.desktop.window_capture import WindowCapture
    from proximic_ring.desktop.window_selector import MacWindowAX, WindowSelectorSession
    if not Quartz.CGPreflightScreenCaptureAccess():
        pytest.skip('Screen recording authorization required')
    bundle = os.environ['PROXIMIC_SELECTOR_CAPTURE_TEST_APP']
    target = next((a for a in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
                   if a.bundleIdentifier() == bundle), None)
    assert target is not None, 'Requested test app must already be running'
    foreground = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = int(target.processIdentifier())
    ax = MacWindowAX()
    session = WindowSelectorSession(ax)
    listing = session.handle('selector_list')
    card = next(c for c in listing['cards'] if c['pid'] == pid and c['number'])
    before = len(ax.windows(pid))
    engine = WindowCapture()
    def pump(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(.02))
    try:
        # No images are retained or saved. Let macOS create its sharing helper.
        engine.start([card], lambda key, image: None, lambda status: None)
        deadline = time.monotonic() + 5
        snapshot = ax.windows(pid)
        while len(snapshot) <= before and time.monotonic() < deadline:
            pump(.1)
            snapshot = ax.windows(pid)
        if len(snapshot) <= before:
            pytest.skip('This OS/app did not expose a temporary sharing window')
        engine.stop()
        pump(.4)
        assert any(ax.ax.AXUIElementCopyAttributeValue(node, 'AXRole', None)[0] == -25202
                   for node in snapshot), 'Expected a real destroyed sharing helper'
        original_windows = ax.windows
        # Deterministically hold the native snapshot across teardown, as in
        # the race between AXWindows enumeration and the following AXRole read.
        retained = [snapshot]
        ax.windows = lambda requested_pid: (retained.pop() if requested_pid == pid and retained
                                             else original_windows(requested_pid))
        result = session.handle('selector_activate', token=listing['token'], target=card['id'])
        assert result['status'] == 'activated', session.last_diagnostic
        assert session.last_diagnostic['vanished_windows'] >= 1
        assert ax.front()['pid'] == pid
        print('preview_teardown_recovered', bundle, session.last_diagnostic['vanished_windows'])
    finally:
        engine.close()
        pump(.3)
        if foreground:
            ax.ax.AXUIElementSetAttributeValue(ax.application(int(foreground.processIdentifier())),
                                             'AXFrontmost', True)
            foreground.activateWithOptions_(0)
