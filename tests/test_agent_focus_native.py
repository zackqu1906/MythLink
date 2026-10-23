"""Opt-in WindowServer regression using only disposable windows and text."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = r'''
import ctypes, os, subprocess, sys
from types import SimpleNamespace
import AppKit, Quartz, objc
from PySide6.QtCore import QObject, Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from proximic_ring.ui.agent.agent_overlay import AgentOverlay, _set_input_focus_enabled
from proximic_ring.ui.shared.overlay_stacking import OverlayVisibilityGuard
from proximic_ring.platforms.macos.wechat_native_keys import send_input_method_key

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
from proximic_ring.platforms.macos.mac_app_activation import configure_app_activation
configure_app_activation(background=False)
def native(window):
    return objc.objc_object(c_void_p=ctypes.c_void_p(int(window.winId()))).window()
def front():
    return AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()

if '--cover' in sys.argv:
    window = QWindow()
    window.setTitle('Agent focus regression cover')
    window.setGeometry(100, 150, 600, 400)
    window.show(); window.raise_(); window.requestActivate()
    QTest.qWait(100)
    print(native(window).windowNumber(), flush=True)
    app.exec()
    sys.exit()

original = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
main = QWindow()
main.setTitle('Agent focus regression main')
main.setGeometry(100, 150, 600, 400)
main.show(); main.raise_(); main.requestActivate()
agent = QQuickView()
agent.setFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
_set_input_focus_enabled(agent, False)
agent.setGeometry(800, 500, 300, 180)
class Entry(QObject):
    activate = Signal()
entry = Entry()
agent.rootContext().setContextProperty('agentEntry', entry)
agent.setSource(QUrl.fromLocalFile(sys.argv[1]))
agent.show()
guard = OverlayVisibilityGuard(agent, input_workspace=True)
guard.refresh()
QTest.qWait(200)  # Finish Qt's initial show/activation before covering it.
host = SimpleNamespace(refresh=lambda: None, layout=lambda: None,
                       windows={'task': agent}, guards={'task': guard}, _closed=False)
controller = SimpleNamespace(pickingAttachments=False, focused=False,
    workspaceFocused=lambda value: setattr(controller, 'focused', value),
    windowFocused=lambda *_: None, rememberLocalWindow=lambda *_: None)
host.controller = controller
host.host = SimpleNamespace(_inline_input=SimpleNamespace(_source_activation=None))
host._focus_timer = QTimer()
host._focus_timer.setSingleShot(True)
host._focus_changed = lambda window: AgentOverlay._focus_changed(host, window)
host._release_focus = lambda: AgentOverlay._release_focus(host)
host._focus_timer.timeout.connect(lambda: AgentOverlay._settle_focus(host))
app.focusWindowChanged.connect(host._focus_changed)
entry.activate.connect(lambda: AgentOverlay.focus(host, 'task'))
child = subprocess.Popen([sys.executable, __file__, '--cover'], stdout=subprocess.PIPE, text=True)
try:
    cover_id = int(child.stdout.readline())
    main_id = native(main).windowNumber()
    external = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(child.pid)
    for _ in range(100):
        if external is not None: break
        QTest.qWait(10)
        external = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(child.pid)
    assert external is not None and child.poll() is None
    for state in ('visible', 'minimized', 'hidden'):
        if state == 'visible': native(main).orderBack_(None)
        elif state == 'minimized': main.showMinimized()
        else: main.hide()
        QTest.qWait(300)
        external.activateWithOptions_(2)
        QTest.qWait(200)
        assert front() == child.pid, (state, front(), child.pid)
        assert not controller.focused
        before = (main.isVisible(), main.windowState())
        if sys.argv[2] == 'mouse':
            # Deliver a real AppKit click to the dormant panel. Its QML click
            # handler explicitly requests focus despite refusing automatic
            # key-window restoration when the host application is activated.
            for kind in (AppKit.NSEventTypeLeftMouseDown, AppKit.NSEventTypeLeftMouseUp):
                event = AppKit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                    kind, (50, 50), 0, 0, native(agent).windowNumber(), None, 1, 1, 1)
                appkit = AppKit.NSApplication.sharedApplication()
                appkit.sendEvent_(event)
        else:
            AgentOverlay.focus(host, 'task')
        QTest.qWait(200)
        ids = [int(w['kCGWindowNumber']) for w in Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, 0)]
        assert front() == os.getpid() and agent.isActive() and native(agent).isKeyWindow(), (
            state, front(), os.getpid(), agent.isActive(), native(agent).isKeyWindow(),
            str(native(agent).className()), native(agent).canBecomeMainWindow())
        assert controller.focused
        assert (main.isVisible(), main.windowState()) == before
        if state == 'visible': assert ids.index(main_id) > ids.index(cover_id), ids
        else: assert main_id not in ids
        assert agent.rootObject().property('editorFocused')
        print('PASS focus preserves main window: ' + state, flush=True)
        external.activateWithOptions_(2)
        QTest.qWait(300)
        assert not controller.focused
        assert agent.isVisible() and agent.flags() & Qt.WindowDoesNotAcceptFocus
        AppKit.NSRunningApplication.currentApplication().activateWithOptions_(2)
        QTest.qWait(300)
        assert not controller.focused and not native(agent).isKeyWindow()
        assert (main.isVisible(), main.windowState()) == before
        if state == 'visible': assert native(main).isKeyWindow()
        print('PASS application activation leaves Agent dormant: ' + state, flush=True)
    AgentOverlay.focus(host, 'task')
    QTest.qWait(200)
    for key in (Qt.Key_A, Qt.Key_B, Qt.Key_C): QTest.keyClick(agent, key)
    assert agent.rootObject().property('text') == 'abc'
    send_input_method_key('', 'select_previous', 3, 123456, expected_pid=os.getpid())
    QTest.qWait(200)
    assert agent.rootObject().property('selection') == 'abc'
    send_input_method_key('', 'delete', 0, 123457, expected_pid=os.getpid())
    QTest.qWait(200)
    assert agent.rootObject().property('text') == ''
    assert agent.rootObject().property('caret') == 0
    print('PASS unbundled Qt native sentence selection and deletion', flush=True)
finally:
    child.terminate(); child.wait(timeout=5)
    main.hide(); agent.hide()
    original.activateWithOptions_(0)
'''


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_AGENT_NATIVE') != '1',
                    reason='opt-in native Agent focus and key delivery check')
@pytest.mark.parametrize('entry', ['explicit', 'mouse'])
def test_agent_focus_preserves_main_window_and_accepts_native_undo_keys(tmp_path, entry):
    script = tmp_path / 'agent_focus_probe.py'
    script.write_text(SCRIPT)
    qml = tmp_path / 'Editor.qml'
    qml.write_text('''import QtQuick
Rectangle {
    width: 300; height: 180; color: "#333333"
    property alias text: editor.text
    property alias selection: editor.selectedText
    property alias caret: editor.cursorPosition
    property alias editorFocused: editor.activeFocus
    function focusEditor() { editor.forceActiveFocus() }
    TextEdit { id: editor; anchors.fill: parent; color: "white" }
    MouseArea { anchors.fill: parent; onClicked: agentEntry.activate() }
}''')
    result = subprocess.run([sys.executable, str(script), str(qml), entry], capture_output=True,
                            text=True, timeout=25, env={**os.environ, 'QT_QPA_PLATFORM': 'cocoa'})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('PASS') == 7, result.stdout
