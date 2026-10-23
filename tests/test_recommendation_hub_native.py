"""Opt-in real macOS windows; synthetic recommendations, no desktop tasks."""
import os
import subprocess
import sys

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from test_recommendation_hub import hub, swipe
from test_inline_ui import inline_ui
from test_recommendation_execution import catalog
from test_recommendation_ui import wait

pytestmark = [
    pytest.mark.usefixtures('legacy_workflow_pipeline'),
    pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_HUB_NATIVE') != '1',
                      reason='opt-in native Hub focus and click-away check'),
]


@pytest.fixture(scope='module', autouse=True)
def native_app():
    import AppKit as A
    original = A.NSWorkspace.sharedWorkspace().frontmostApplication()
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == 'cocoa', 'Run this file separately with QT_QPA_PLATFORM=cocoa'
    yield app
    original.activateWithOptions_(0)


def test_real_hub_focus_mouse_gestures_parameters_and_click_away(hub, tmp_path):
    import AppKit as A
    import Quartz as Q
    from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
    from proximic_ring.agent_workflow.ui.recommendation_focus import native_window
    from PySide6.QtGui import QCursor
    owner, agent, runtime, sent, fixture = hub
    main = fixture[3]; dialog = runtime.dialog
    original_pointer = QCursor.pos()
    overlay = AgentOverlay(agent, owner)
    cover_script = tmp_path/'hub_cover.py'
    cover_script.write_text('''from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtTest import QTest
app=QApplication([]);w=QWidget();w.setWindowTitle('Disposable Hub test cover')
w.setGeometry(30,60,600,500);w.show();w.activateWindow();QTest.qWait(100)
print('ready',flush=True);app.exec()
''')
    child = None
    def ids():
        return [int(w['kCGWindowNumber']) for w in Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly, 0)]
    def click(widget):
        point = widget.mapTo(dialog, widget.rect().center())
        for kind in (A.NSEventTypeLeftMouseDown, A.NSEventTypeLeftMouseUp):
            event = A.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                kind, (point.x(), dialog.height()-point.y()), 0, 0, native_window(dialog).windowNumber(), None, 1, 1, 1)
            A.NSApplication.sharedApplication().sendEvent_(event)
        QTest.qWait(40)
    try:
        dialog.reject();wait(lambda:not dialog.isVisible())
        main.show();main.requestActivate();QTest.qWait(100)
        agent._visible=agent._focused=True;overlay.focus('task');QTest.qWait(100)
        child=subprocess.Popen([sys.executable,str(cover_script)],stdout=subprocess.PIPE,text=True,
                               env={**os.environ,'QT_QPA_PLATFORM':'cocoa'})
        assert child.stdout.readline().strip()=='ready'
        external=A.NSRunningApplication.runningApplicationWithProcessIdentifier_(child.pid)
        for state in ('visible','minimized','hidden'):
            if state=='visible':native_window(main).orderBack_(None)
            elif state=='minimized':main.showMinimized()
            else:main.hide()
            external.activateWithOptions_(2);QTest.qWait(150)
            before=(main.isVisible(),main.windowState())
            runtime.open('gesture');wait(lambda:runtime.snapshot and dialog.has_panel_focus())
            wait(lambda:all(c['exposure'] for c in dialog.cards))
            assert runtime.input.focused and not agent.focused
            assert not native_window(dialog).isOpaque() and not native_window(dialog).hasShadow()
            # Both input paths operate the same four frozen recommendations.
            for rank,gesture in enumerate(('swipe-up','swipe-right','swipe-down','swipe-left'),1):
                if rank%2:swipe(owner,gesture)
                else:click(dialog.cards[rank-1]['choose'])
                assert dialog.panel_direction==rank and not sent
                assert dialog.has_panel_focus() and not native_window(main).isKeyWindow()
                assert (main.isVisible(),main.windowState())==before
                dialog.back();QTest.qWait(30)
            rank=next(i['position'] for i in runtime.snapshot['items'] if i['workflow']['parameters'])
            runtime.snapshot['items'][rank-1]['workflow']['parameters']=[
                dict(name='title',description='标题'),dict(name='body',description='正文')]
            click(dialog.cards[rank-1]['title'])
            for field in ('title','body'):
                dialog.enter_word(dialog.sentence.names.index(field));QTest.qWait(40)
                editor=dialog.editors[field];assert editor.selection_preview and not dialog.sentence.editing
                # Entering a word is a chip preview; real typing explicitly opens the native client.
                QTest.keyClicks(editor,'native Hub input')
                assert editor.toPlainText()=='native Hub input' and runtime.input.owns_input_focus()
                assert runtime.input.field==field
            assert dialog.has_panel_focus() and not native_window(main).isKeyWindow()
            windows=Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly,0)
            cover_id=next(int(w['kCGWindowNumber']) for w in windows if w['kCGWindowOwnerPID']==child.pid)
            if state=='visible':assert ids().index(native_window(main).windowNumber())>ids().index(cover_id)
            else:assert native_window(main).windowNumber() not in ids()
            assert (main.isVisible(),main.windowState())==before
            # AppKit click-away: deliver to this disposable process, not another
            # user's app. The global monitor must observe it without eating it.
            for kind in (Q.kCGEventLeftMouseDown,Q.kCGEventLeftMouseUp):
                event=Q.CGEventCreateMouseEvent(None,kind,(50,90),Q.kCGMouseButtonLeft)
                Q.CGEventPost(Q.kCGHIDEventTap,event)
            wait(lambda:dialog.closing)
            assert not runtime.input.focused and runtime.snapshot is None
            wait(lambda:not dialog.isVisible())
            QTest.qWait(200)
            assert A.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()==child.pid
            assert not native_window(main).isKeyWindow()
            assert (main.isVisible(),main.windowState())==before
            if state=='visible':assert ids().index(native_window(main).windowNumber())>ids().index(cover_id)
            else:assert native_window(main).windowNumber() not in ids()
            assert not sent
        wait(lambda:not dialog.click_away.monitors)
    finally:
        runtime.close();overlay.close();main.hide()
        if child:child.terminate();child.wait(timeout=5)
        QCursor.setPos(original_pointer)


def test_dismissal_does_not_promote_main_or_override_selected_application(hub,tmp_path):
    """Use two disposable apps to distinguish returning focus from stealing it."""
    import AppKit as A
    import Quartz as Q
    from PySide6.QtGui import QCursor
    from proximic_ring.agent_workflow.ui.recommendation_focus import native_window
    owner,agent,runtime,sent,fixture=hub
    main=fixture[3];dialog=runtime.dialog;child=None;second=None
    original_pointer=QCursor.pos()
    script=tmp_path/'focus_return_cover.py'
    script.write_text('''import sys
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtTest import QTest
app=QApplication([]);w=QWidget();w.setWindowTitle('Disposable Hub dismissal target')
w.setGeometry(int(sys.argv[1]),60,500,400);w.show();w.activateWindow();QTest.qWait(100)
print('ready',flush=True);app.exec()
''')
    def front():return A.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
    def windows():return list(Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly,0))
    def click_external(x,y=90):
        for kind in (Q.kCGEventLeftMouseDown,Q.kCGEventLeftMouseUp):
            Q.CGEventPost(Q.kCGHIDEventTap,Q.CGEventCreateMouseEvent(None,kind,(x,y),Q.kCGMouseButtonLeft))
        QTest.qWait(150)
    try:
        dialog.reject();wait(lambda:not dialog.isVisible())
        main.show();main.requestActivate();QTest.qWait(100)
        child=subprocess.Popen([sys.executable,str(script),'30'],stdout=subprocess.PIPE,text=True,
                               env={**os.environ,'QT_QPA_PLATFORM':'cocoa'})
        assert child.stdout.readline().strip()=='ready'
        external=A.NSRunningApplication.runningApplicationWithProcessIdentifier_(child.pid)
        second=subprocess.Popen([sys.executable,str(script),'1250'],stdout=subprocess.PIPE,text=True,
                                env={**os.environ,'QT_QPA_PLATFORM':'cocoa'})
        assert second.stdout.readline().strip()=='ready'
        main_id=native_window(main).windowNumber()
        for state in ('visible','minimized','hidden'):
            if state=='visible':native_window(main).orderBack_(None)
            elif state=='minimized':main.showMinimized()
            else:main.hide()
            for exit_kind in ('close','escape','external_selection','close_then_select'):
                click_external(50)
                wait(lambda:front()==child.pid)
                before=(main.isVisible(),main.windowState())
                runtime.open('gesture');wait(lambda:runtime.snapshot and dialog.has_panel_focus())
                if exit_kind=='close':dialog.reject()
                elif exit_kind=='escape':QTest.keyClick(dialog,Qt.Key_Escape)
                elif exit_kind=='close_then_select':
                    dialog.reject();QTest.qWait(100);click_external(1270)
                else:click_external(1270)
                QTest.qWait(100)
                wait(lambda:not dialog.isVisible())
                QTest.qWait(200)
                expected=second.pid if exit_kind in ('external_selection','close_then_select') else child.pid
                info=dict(state=state,exit_kind=exit_kind,front=front(),expected=expected,
                          main_key=bool(native_window(main).isKeyWindow()))
                assert front()==expected,info
                assert not native_window(main).isKeyWindow(),info
                assert (main.isVisible(),main.windowState())==before,info
                if state=='visible':
                    listed=windows();ids=[int(w['kCGWindowNumber']) for w in listed]
                    cover=next(int(w['kCGWindowNumber']) for w in listed if w['kCGWindowOwnerPID']==expected)
                    assert ids.index(main_id)>ids.index(cover),info
                else:assert main_id not in [int(w['kCGWindowNumber']) for w in windows()],info
                assert not sent
        main.showNormal();click_external(50)
        runtime.open('gesture');wait(lambda:runtime.snapshot and dialog.has_panel_focus())
        dialog.reject();QTest.qWait(100)
        target=native_window(main)
        # Dispatch to the known test window rather than clicking desktop
        # coordinates that might be covered by the user's real applications.
        for kind in (A.NSEventTypeLeftMouseDown,A.NSEventTypeLeftMouseUp):
            event=A.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                kind,(300,main.height()-10),0,0,target.windowNumber(),None,1,1,1)
            A.NSApplication.sharedApplication().sendEvent_(event)
        assert not dialog.focus_session.return_pending
        target.makeKeyAndOrderFront_(None)
        A.NSRunningApplication.currentApplication().activateWithOptions_(2)
        wait(lambda:not dialog.isVisible());QTest.qWait(300)
        assert not dialog.focus_session.return_pending and not dialog.click_away.monitors
        assert target.isKeyWindow() and not sent
    finally:
        runtime.close();main.hide()
        if child:child.terminate();child.wait(timeout=5)
        if second:second.terminate();second.wait(timeout=5)
        QCursor.setPos(original_pointer)
