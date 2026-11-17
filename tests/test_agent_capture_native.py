"""Opt-in actual ScreenCaptureKit + Qt transaction, using a disposable test canvas.

No Agent RPC, speech service or model calls. Only the synthetic cropped region
is saved; captured full displays stay in memory and are released on exit.
"""
import os
import subprocess
import sys
from pathlib import Path
import pytest

SCRIPT = r'''
import json, sys, time
from pathlib import Path
from types import SimpleNamespace
import AppKit, Quartz
from PySide6.QtCore import Qt, QPoint, QPointF, QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication, QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from pytest import MonkeyPatch
from test_inline_ui import inline_ui
from test_agent_ui import finish_fixture_voice
from test_ui_shell import visual_child
from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
from proximic_ring.agent_workflow.ui.recommendation_focus import native_window
from proximic_ring.agent_workflow.capture import capture_controller as module
from proximic_ring.agent_workflow.agent.attachments import turn_input

app = QApplication([]); app.setQuitOnLastWindowClosed(False)
original = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
root = Path(sys.argv[1]); patch = MonkeyPatch()
fixture = inline_ui.__wrapped__(root, patch, SimpleNamespace())
owner, bridge, requests, main, event = next(fixture)
agent = owner.agent; finish_fixture_voice(owner)
owner._proximity.close()  # The Downloads tree may contain a built presence helper.
agent._workflow_timer.stop(); agent._memory_timer.stop()
sent = []; patch.setattr(agent, '_send', sent.append)
patch.setattr(module, 'cache_root', lambda: root/'cache')
agent._visible = agent._focused = True
overlay = AgentOverlay(agent, owner); overlay.reduce_motion = False
canvas = QWidget(); canvas.setWindowTitle('Screenshot validation canvas')
canvas.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
screen = QGuiApplication.primaryScreen(); area = screen.geometry()
canvas.setGeometry(area); canvas.setStyleSheet('background:#2384bf;')
canvas.show(); QTest.qWait(200)
reports = []
def wait_capture(cap):
    # Drive the native event loop while the capture worker completes.
    loop = QEventLoop()
    poll = QTimer(); poll.setInterval(20)
    poll.timeout.connect(lambda: loop.quit() if cap.phase != 'preparing' else None)
    deadline = QTimer(); deadline.setSingleShot(True); deadline.timeout.connect(loop.quit)
    poll.start(); deadline.start(15000)
    if cap.phase == 'preparing': loop.exec()
    poll.stop(); deadline.stop()
try:
    for state in ('visible', 'minimized', 'hidden'):
        if state == 'visible': main.showNormal()
        elif state == 'minimized': main.showMinimized()
        else: main.hide()
        native_window(canvas).orderFrontRegardless(); QTest.qWait(100)
        overlay.focus('task'); QTest.qWait(450)  # Let the real entrance animation finish.
        before = (main.isVisible(), main.windowState())
        task = overlay.windows['task']
        button = visual_child(task.rootObject(), 'agentCaptureButton')
        point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
        QTest.mouseClick(task, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(30)
        wait_capture(agent.capture)
        cap = agent.capture
        assert cap.phase == 'locating', (cap.phase, agent.errorMessage)
        assert not task.isVisible()
        index = next(i for i,w in enumerate(cap.windows) if w.spec['geometry'][:2] == (area.x(),area.y()))
        window = cap.windows[index]; image = window.image
        available = screen.availableGeometry()
        toolbar_bottom = window.mapToGlobal(window.toolbar.geometry().bottomLeft()).y() + 1
        assert toolbar_bottom <= min(available.y()+available.height()-32,area.y()+area.height()-96)
        sx, sy, sw, sh = window.spec['geometry']
        # Select a known rectangle entirely inside the disposable blue canvas.
        x, y = sx+100, sy+100
        QTest.mouseMove(window,QPoint(100,100)); QTest.qWait(60)
        QTest.mouseClick(window,Qt.LeftButton,pos=QPoint(100,100))
        QTest.qWait(30)
        QTest.mouseMove(window,QPoint(280,100)); QTest.qWait(30)
        QTest.mouseMove(window,QPoint(280,220)); QTest.qWait(30)
        QTest.mouseClick(window,Qt.LeftButton,pos=QPoint(280,220))
        QTest.qWait(30)
        assert cap.phase == 'preview', (cap.phase,cap.notice,cap.selection)
        px,py,pw,ph = cap.selection.pixels(image.width(), image.height())
        assert (pw,ph) == (round(180*image.width()/sw),round(120*image.height()/sh)), cap.selection
        # An excluded overlay must not darken the captured frame.
        actual = image.pixelColor(px+pw//2, py+ph//2)
        expected = QColor('#2384bf')
        assert max(abs(a-b) for a,b in zip(actual.getRgb()[:3],expected.getRgb()[:3])) < 12, actual.name()
        # Two native canvas previews saved only after proving the test region.
        if state == 'visible':
            window.grab(window.rect().adjusted(80,70,-max(0,sw-500),-max(0,sh-320))).save(str(root/'selection-preview.png'))
            window.toolbar.grab().save(str(root/'toolbar-preview.png'))
        cap.consume(SimpleNamespace(name='swipe-down'), owner._disconnect_event); QTest.qWait(30)
        assert cap.phase == 'preview' and not agent.inputAttachments
        # Another pair of clicks replaces the selection on the same frozen frame.
        cap.select_point(index,x,y); cap.move(x+180,y+120); cap.select_point(index,x+180,y+120)
        assert cap.phase == 'preview'
        cap.consume(SimpleNamespace(name='swipe-right'), owner._disconnect_event)
        QTest.qWait(400)
        assert not cap.active and not sent
        item = agent.inputAttachments[-1]; crop=QImage(item['path'])
        assert (crop.width(),crop.height()) == (pw,ph)
        assert turn_input('',[item]) == [{'type':'localImage','path':item['path']}]
        assert (main.isVisible(),main.windowState()) == before
        assert task.isVisible() and task.opacity() > .8 and native_window(task).isKeyWindow()
        if state == 'visible':
            ids=[int(w['kCGWindowNumber']) for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly,0)]
            assert ids.index(int(native_window(main).windowNumber())) > ids.index(int(native_window(canvas).windowNumber()))
            task.grabWindow().save(str(root/'task-preview.png'))
        reports.append({'main':state,'crop_pixels':[pw,ph], 'scale':window.spec['scale'],
                        'foreground_restored_to_task':True,'commands_sent':len(sent)})
        agent.workspace.remove_attachment('task',item['path'])
    # The same native capture UI also delivers a durable global screenshot.
    agent._visible = agent._focused = False
    agent.changed.emit(); main.hide()
    native_window(canvas).orderFrontRegardless(); QTest.qWait(150)
    cap = owner.ringGestures.screenshot
    assert cap.setDirectory(str(root/'saved-images'))
    cap.start()
    wait_capture(cap)
    assert cap.phase == 'locating', (cap.phase,cap.status)
    index = next(i for i,w in enumerate(cap.windows) if w.spec['geometry'][:2] == (area.x(),area.y()))
    window = cap.windows[index]
    QTest.mouseClick(window,Qt.LeftButton,pos=QPoint(100,100))
    QTest.mouseMove(window,QPoint(280,220))
    QTest.mouseClick(window,Qt.LeftButton,pos=QPoint(280,220))
    assert cap.phase == 'preview' and not list((root/'saved-images').glob('*.png'))
    cap.consume(SimpleNamespace(name='swipe-right'), owner._disconnect_event); QTest.qWait(200)
    assert not cap.active and not agent.inputAttachments and not sent
    crop = QImage(cap.lastSavedPath)
    assert not crop.isNull() and QGuiApplication.clipboard().image() == crop
    actual = crop.pixelColor(crop.width()//2,crop.height()//2)
    assert max(abs(a-b) for a,b in zip(actual.getRgb()[:3],QColor('#2384bf').getRgb()[:3])) < 12, actual.name()
    assert not main.isVisible()
    reports.append({'global_saved_and_copied':True,'crop_pixels':[crop.width(),crop.height()]})
    print(json.dumps(reports),flush=True)
finally:
    owner.ringGestures.screenshot.cancel()
    agent.capture.cancel(); overlay.close(); canvas.close(); main.hide()
    agent.close()
    try: next(fixture)
    except StopIteration: pass
    patch.undo()
    original.activateWithOptions_(0)
'''


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_CAPTURE_NATIVE') != '1',
                   reason='opt-in actual screen capture of a disposable test canvas')
def test_native_capture_excludes_overlays_crops_and_preserves_focus(tmp_path):
    script = tmp_path/'capture_probe.py'; script.write_text(SCRIPT)
    env = {**os.environ, 'QT_QPA_PLATFORM':'cocoa', 'QT_QUICK_CONTROLS_STYLE':'Basic',
           'PYTHONPATH':os.pathsep.join([str(Path(__file__).parent.resolve()),
                                       str(Path(__file__).resolve().parents[1]/'src')])}
    result = subprocess.run([sys.executable,str(script),str(tmp_path)],env=env,
                            capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    assert result.stdout.count('foreground_restored_to_task') == 3, result.stdout
    assert '"global_saved_and_copied": true' in result.stdout
    print(result.stdout)
