"""Opt-in native system-hotkey capture in an isolated, temporary Qt window."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = r"""
import json, os, tempfile
from pathlib import Path
import AppKit, ApplicationServices, Quartz, pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from proximic_ring.platforms.macos.mac_app_activation import configure_app_activation
from proximic_ring.voice.ime.system_input_source import SystemInputSources
from test_interaction_controls import _controller, _close

app = QApplication([])
assert ApplicationServices.AXIsProcessTrusted() and Quartz.CGPreflightPostEventAccess()
original = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
configure_app_activation(background=False)
sources = SystemInputSources()
source_before = sources.current()
with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as patch:
    owner = _controller(Path(tmp), patch)
    owner.proximity.close()
    service = owner.appGestures
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('controller', owner)
    engine.loadData(b'''
        import QtQuick
        import QtQuick.Controls
        ApplicationWindow {
            visible: true; width: 480; height: 180; title: "Mythlink shortcut recording check"
            property string captured: ""
            ShortcutField {
                id: field; objectName: "recorder"; anchors.centerIn: parent; width: 360
                service: controller.appGestures; sequence: ""
                onRecorded: function(value) { captured = value }
            }
        }
    ''', QUrl.fromLocalFile(str(Path.cwd() / 'src/proximic_ring/ui/qml/RecorderHarness.qml')))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.requestActivate()
    AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    QTest.qWait(250)
    def front_pid():
        return AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
    def post(code, down, flags):
        event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGSessionEventTap, event)
    native = service._recording_guard
    try:
        assert front_pid() == os.getpid()
        field = window.findChild(QObject, 'recorder')
        field.forceActiveFocus()
        QTest.qWait(60)
        assert service.recording, service.recordingError
        carbon = native._native._carbon
        assert carbon.GetSymbolicHotKeyMode() == 1
        # Real session events would reach Spotlight/input-source switching without
        # the guard. QTest.keyClick alone would not exercise that system path.
        assert front_pid() == os.getpid()
        post(55, True, Quartz.kCGEventFlagMaskCommand)
        post(49, True, Quartz.kCGEventFlagMaskCommand)
        QTest.qWait(100)
        assert service.recording and window.property('captured') == ''
        assert front_pid() == os.getpid() and sources.current() == source_before
        post(49, False, Quartz.kCGEventFlagMaskCommand)
        post(55, False, 0)
        QTest.qWait(200)
        assert window.property('captured') == 'Cmd+Space', (window.property('captured'), service.recordingError)
        assert not service.recording and carbon.GetSymbolicHotKeyMode() == 0
        assert front_pid() == os.getpid() and sources.current() == source_before
        print(json.dumps(dict(captured='Cmd+Space', guard_restored=True,
                              foreground_unchanged=True, input_source_unchanged=True)), flush=True)
    finally:
        post(49, False, Quartz.kCGEventFlagMaskCommand)
        post(55, False, 0)
        service.close()
        _close(owner)
        window.close()
        QTest.qWait(100)
        if original:
            original.activateWithOptions_(0)
"""


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_SHORTCUT_RECORDER') != '1',
                   reason='opt-in native shortcut recording check')
def test_cmd_space_is_recorded_without_invoking_system_hotkey():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM='cocoa', QT_QUICK_CONTROLS_STYLE='Basic',
               PYTHONPATH=os.pathsep.join([str(root / 'src'), str(root / 'tests')]))
    result = subprocess.run([sys.executable, '-c', SCRIPT], cwd=root, env=env,
                            capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
