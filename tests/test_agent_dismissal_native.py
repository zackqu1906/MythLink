"""Real close-button mouse events through the full companion and focus stack."""
import os
import subprocess
import sys

import pytest
from test_inline_ui import inline_ui
from test_agent_ui import finish_fixture_voice
from test_ui_shell import visual_child

pytestmark = pytest.mark.skipif(
    sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_AGENT_NATIVE') != '1',
    reason='opt-in native close-button focus regression')


@pytest.fixture
def native_ui(request):
    # Match the production runtime before QApplication loads the Cocoa style.
    from proximic_ring.runtime_paths import configure_runtime_environment
    configure_runtime_environment()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(['companion-close-test', '-platform', 'cocoa'])
    if app.platformName() != 'cocoa': pytest.skip('requires an isolated native Qt process')
    app.setQuitOnLastWindowClosed(False)
    from proximic_ring.platforms.macos.mac_app_activation import configure_app_activation
    configure_app_activation(background=False)
    return request.getfixturevalue('inline_ui')


@pytest.mark.parametrize('main_state', ['visible', 'minimized', 'hidden'])
@pytest.mark.parametrize('card_state', ['opening-close', 'expanded', 'compact', 'other-task-focused'])
def test_actual_close_button_never_activates_main(main_state, card_state, request, monkeypatch):
    if os.environ.get('PROXIMIC_DISMISS_WORKER') == '1':
        _exercise_close(request.getfixturevalue('native_ui'), main_state, card_state, monkeypatch)
        return
    # AppKit activation is process-global. Each native lifecycle starts fresh,
    # so a previous fixture's pending OS notifications cannot affect the next.
    target = __file__ + f'::test_actual_close_button_never_activates_main[{card_state}-{main_state}]'
    result = subprocess.run([sys.executable, '-m', 'pytest', '-o', 'addopts=', '-q', target],
        text=True, capture_output=True, timeout=25,
        env={**os.environ, 'PROXIMIC_DISMISS_WORKER': '1', 'QT_QPA_PLATFORM': 'cocoa'})
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('main_state', ['visible', 'minimized', 'hidden'])
@pytest.mark.parametrize('entry', ['dock', 'menu'])
@pytest.mark.parametrize('phase', ['active', 'released', 'dismissed'])
def test_explicit_main_open_with_companion(main_state, entry, phase, request, monkeypatch):
    if os.environ.get('PROXIMIC_DISMISS_WORKER') == '1':
        _exercise_close(request.getfixturevalue('native_ui'), main_state, 'expanded', monkeypatch,
                        main_entry=(entry, phase))
        return
    target = __file__ + f'::test_explicit_main_open_with_companion[{phase}-{entry}-{main_state}]'
    result = subprocess.run([sys.executable, '-m', 'pytest', '-o', 'addopts=', '-q', target],
        text=True, capture_output=True, timeout=25,
        env={**os.environ, 'PROXIMIC_DISMISS_WORKER': '1', 'QT_QPA_PLATFORM': 'cocoa'})
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise_close(native_ui, main_state, card_state, monkeypatch, main_entry=None):
    import ctypes
    import AppKit
    import Quartz
    import objc
    from PySide6.QtCore import QPoint, QPointF, QTimer, Qt
    from PySide6.QtGui import QCursor, QGuiApplication
    from PySide6.QtTest import QTest
    from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
    from proximic_ring.ui.shared.main_window_activation import MainWindowActivation

    def native(window):
        return objc.objc_object(c_void_p=ctypes.c_void_p(int(window.winId()))).window()

    cover_source = '''import ctypes, objc, sys, threading
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
app=QApplication(['close-test-cover','-platform','cocoa'])
from proximic_ring.platforms.macos.mac_app_activation import configure_app_activation
configure_app_activation(background=False)
w=QWindow(); w.setTitle('Companion close regression cover')
w.setGeometry(100,150,600,400); w.show(); w.raise_(); w.requestActivate()
class Control(QObject):
    requested=Signal()
    @Slot()
    def focus(self):
        w.raise_(); w.requestActivate()
control=Control(); control.requested.connect(control.focus)
def commands():
    for line in sys.stdin:
        if line.strip()=='focus': control.requested.emit()
threading.Thread(target=commands,daemon=True).start()
QTest.qWait(150)
print(objc.objc_object(c_void_p=ctypes.c_void_p(int(w.winId()))).window().windowNumber(),flush=True)
app.exec()
'''
    original = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    original_pointer = QCursor.pos()
    owner, _, _, main, _ = native_ui
    agent = owner.agent
    finish_fixture_voice(owner)
    agent._memory_timer.stop()
    main.show(); main.requestActivate(); QTest.qWait(100)
    agent._visible = agent._focused = True
    agent._apply_state({'turnId': 'old', 'status': '执行中'})
    second = agent.sessions.create(agent) if card_state == 'other-task-focused' else None
    agent._apply_state({'taskOutcome': 'completed', 'status': '本轮结束', 'agent': '任务已完成'})
    if card_state == 'compact': agent._focused = False
    overlay = AgentOverlay(agent, owner); overlay.reduce_motion = True
    main_activation = MainWindowActivation(main, overlay)
    # Keep disposable cards away from the user's live bottom-right companions.
    overlay.offset = QPoint(-max(0, QGuiApplication.primaryScreen().availableGeometry().width()-520), 0)
    cover = subprocess.Popen([sys.executable, '-c', cover_source], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    observed = []
    try:
        cover_id = int(cover.stdout.readline())
        def focus_cover():
            # Ask the disposable application to key its actual window. Merely
            # activating an app can leave a nonactivating panel holding keys.
            cover.stdin.write('focus\n'); cover.stdin.flush()
            for _ in range(100):
                QTest.qWait(20)
                if (AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == cover.pid
                        and QGuiApplication.focusWindow() is None): break
            QTest.qWait(100)
            assert AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == cover.pid
            assert QGuiApplication.focusWindow() is None
        main_id = native(main).windowNumber()
        if main_state == 'visible': native(main).orderBack_(None)
        elif main_state == 'minimized':
            main.showMinimized(); QTest.qWait(600)
        else: main.hide()
        focus_cover()
        if card_state == 'opening-close':
            # Make AppKit's asynchronous activation tail deterministic: the
            # request was made while entering this panel but completes later.
            from proximic_ring.agent_workflow.ui import agent_overlay as module
            current = AppKit.NSRunningApplication.currentApplication()
            def delayed_activation():
                QTimer.singleShot(700, lambda: current.activateWithOptions_(2))
            monkeypatch.setattr(module, '_activate_input_owner', delayed_activation)
        target = second or agent
        if card_state != 'compact': target.focusCard('task')
        QTest.qWait(250)
        if card_state == 'compact':
            # Start with an already dormant companion. Cross-app deactivation
            # is covered separately; this case exercises its close button.
            assert not agent.workspaceActive
        window = overlay.windows['task']
        if card_state in {'expanded', 'other-task-focused'}:
            focused_window = overlay.windows[agent.sessions.window_key(target, 'task')]
            assert target.focused and native(focused_window).isKeyWindow()
        before = (main.isVisible(), main.windowState())

        def observe(stage):
            ids = [int(w['kCGWindowNumber']) for w in Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, 0)]
            observed.append((stage, main.isActive(), native(main).isKeyWindow(),
                ids.index(main_id) if main_id in ids else -1, ids.index(cover_id),
                QGuiApplication.focusWindow().objectName() if QGuiApplication.focusWindow() else None))
            assert (main.isVisible(), main.windowState()) == before, observed
            if main_state == 'visible': assert ids.index(main_id) > ids.index(cover_id), observed
            else: assert main_id not in ids, observed
            assert not main.isActive() and not native(main).isKeyWindow(), observed

        observe('before')
        if main_entry:
            entry, phase = main_entry
            agent.workspace.drafts['task'] = '保留这个任务草稿'
            if phase == 'released': overlay._release_focus()
            elif phase == 'dismissed': agent.dismiss()
            QTest.qWait(50)
            visible = agent.visible
            if entry == 'dock':
                # Deliver the same native reopen event used by a Dock click,
                # addressed only to this disposable test process.
                import struct
                from Foundation import NSAppleEventDescriptor, NSAppleEventSendNoReply
                fourcc = lambda value: int.from_bytes(value, 'big')
                pid = struct.pack('i', os.getpid())
                address = NSAppleEventDescriptor.descriptorWithDescriptorType_bytes_length_(fourcc(b'kpid'), pid, len(pid))
                reopen = NSAppleEventDescriptor.alloc().initWithEventClass_eventID_targetDescriptor_returnID_transactionID_(
                    fourcc(b'aevt'), fourcc(b'rapp'), address, -1, 0)
                _, error = reopen.sendEventWithOptions_timeout_error_(NSAppleEventSendNoReply, 1, None)
                assert error is None
            else:
                main_activation.show()
            # Include the entire release tail: a successful first frame is not
            # enough if a pending focus guard pulls the main window back again.
            restored = []
            for _ in range(40):
                QTest.qWait(25)
                restored.append((main.isActive(), native(main).isKeyWindow(), native(main).isMiniaturized(),
                    str(main.windowState()),
                    QGuiApplication.focusWindow().objectName() if QGuiApplication.focusWindow() else None,
                    AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier(),
                    overlay.focus_session.engaged, overlay.focus_session.return_pending))
            assert main.isVisible() and not native(main).isMiniaturized()
            if not (main.isActive() and native(main).isKeyWindow()):
                import json
                print('Main restore trace ' + json.dumps({'host': os.getpid(), 'cover': cover.pid,
                      'states': restored}), flush=True)
            assert main.isActive() and native(main).isKeyWindow(), restored
            assert AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == os.getpid()
            ids = [int(w['kCGWindowNumber']) for w in Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, 0)]
            assert ids.index(main_id) < ids.index(cover_id)
            assert agent.visible == visible and not agent.focused
            assert agent.draft('task') == '保留这个任务草稿'
            return
        button = visual_child(window.rootObject(),
            'agentCompactCloseButton' if window.rootObject().property('compact') else 'agentCloseButton')
        assert button.isVisible() and button.isEnabled()
        point = button.mapToScene(QPointF(button.width()/2, button.height()/2))
        location = window.mapToGlobal(point.toPoint())
        if card_state != 'expanded' or main_state != 'visible':
            # Deliver to the known QML button; keep other desktop applications
            # out of the target selection in these state/activation-tail cases.
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        else:
            for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                event = Quartz.CGEventCreateMouseEvent(None, kind, (location.x(), location.y()), Quartz.kCGMouseButtonLeft)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
                QTest.qWait(30)
                observe(str(kind))
        for _ in range(24 if card_state == 'opening-close' else 15):
            QTest.qWait(25); observe('settled')
        assert not agent.visible and not window.isVisible(), observed
        if second: assert second.visible
        else:
            assert AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == cover.pid, observed
        if main_state == 'visible' and card_state == 'expanded':
            # An intentional main-window click during the release tail wins;
            # protection must never send that user selection back to the cover.
            for kind in (AppKit.NSEventTypeLeftMouseDown, AppKit.NSEventTypeLeftMouseUp):
                event = AppKit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                    kind, (300, main.height()-10), 0, 0, native(main).windowNumber(), None, 1, 1, 1)
                AppKit.NSApplication.sharedApplication().sendEvent_(event)
            assert not overlay.focus_session.return_pending
            native(main).makeKeyAndOrderFront_(None)
            AppKit.NSRunningApplication.currentApplication().activateWithOptions_(2)
            QTest.qWait(250)
            assert native(main).isKeyWindow()
    finally:
        main_activation.close()
        overlay.close(); agent.close(); main.hide()
        cover.terminate(); cover.wait(timeout=5)
        QCursor.setPos(original_pointer)
        original.activateWithOptions_(0)
