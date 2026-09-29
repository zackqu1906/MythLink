import os
from pathlib import Path
import subprocess
import sys


def test_real_band_morph_countdown_exit_and_nonactivating_flags(tmp_path):
    script = r'''
from PySide6.QtCore import QObject, Signal, Qt, qInstallMessageHandler
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtTest import QTest
from proximic_ring.ui.focus_band import FocusBand
import os

class Picker(QObject):
    shown = Signal(object)
    exited = Signal(str)
    progress = Signal(float,bool)
    def __init__(self): super().__init__(); self.settled=[]
    def landed(self, serial): self.settled.append(serial)

messages=[]
qInstallMessageHandler(lambda kind, ctx, message: messages.append(message))
app=QApplication([])
app.setQuitOnLastWindowClosed(False)
editor=QLineEdit('focus stays here'); editor.show(); editor.activateWindow(); editor.setFocus()
QTest.qWait(50)
focused=app.focusWidget()
picker=Picker(); overlay=FocusBand(picker, app)
fields=[{'rect':[40,96,520,46],'address':True}, {'rect':[40,178,250,46],'address':False},
        {'rect':[310,178,250,46],'address':False}, {'rect':[40,260,520,110],'address':False}]
data={'frame':[0,0,600,520], 'fields':fields, 'index':2, 'deferred':False, 'motion':'enter', 'serial':1}
picker.shown.emit(data); QTest.qWait(620)
root=overlay.window.rootObject()
assert picker.settled==[1]
assert overlay.window.isVisible() and not overlay.window.isActive()
assert app.focusWidget() is focused
for flag in (Qt.WindowTransparentForInput, Qt.WindowDoesNotAcceptFocus, Qt.WindowStaysOnTopHint):
    assert overlay.window.flags() & flag
assert root.property('bw')==250 and root.property('bx')==80
picker.progress.emit(.2, True)
QTest.qWait(30)
assert root.property('warning')
assert abs(root.property('remainingRatio')-.2)<.001
picker.shown.emit({**data, 'index':3, 'motion':'move', 'serial':2, 'direction':'right'})
QTest.qWait(110)
assert 80 < root.property('bx') < 350
QTest.qWait(300)
assert picker.settled==[1,2] and root.property('bx')==350

# Render a deterministic UI preview on a synthetic background, without reading any app content.
path=os.environ.get('RING_BAND_PREVIEW')
if path:
    img=QImage(680,600,QImage.Format_ARGB32_Premultiplied); img.fill(QColor('#080A14'))
    painter=QPainter(img); painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#2A3448'),1)); painter.setBrush(QColor('#111827'))
    for index, field in enumerate(fields):
        x,y,w,h=field['rect']; painter.drawRoundedRect(x+40,y+40,w,h,10,10)
        painter.setPen(QColor('#77869D')); painter.drawText(x+54,y+68,['浏览器地址栏','输入框 2','输入框 3','正文输入框'][index]); painter.setPen(QColor('#2A3448'))
    painter.drawImage(0,0,overlay.window.grabWindow()); painter.end(); assert img.save(path)

picker.shown.emit({**data, 'index':1, 'deferred':True, 'motion':'move', 'serial':3, 'direction':'up'})
QTest.qWait(420)
assert root.property('deferred') and root.property('bw')==520
picker.exited.emit('timeout'); QTest.qWait(360)
assert not overlay.window.isVisible()
picker.shown.emit(data); QTest.qWait(620)
picker.exited.emit('confirm'); QTest.qWait(100)
assert overlay.window.isVisible()
picker.shown.emit({**data,'serial':4})  # An old exit animation must not hide a new selection.
QTest.qWait(620)
assert overlay.window.isVisible() and picker.settled[-1]==4
picker.exited.emit('confirm'); QTest.qWait(600)
assert not overlay.window.isVisible()
assert app.focusWidget() is focused
assert not any('ReferenceError' in m or 'TypeError' in m or 'Error:' in m for m in messages), messages
overlay.close(); editor.close()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
               PROXIMIC_STARTUP_PROBE="1", PROXIMIC_DATA_HOME=str(tmp_path))
    run = subprocess.run([sys.executable, "-c", script], env=env,
                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stdout+run.stderr
