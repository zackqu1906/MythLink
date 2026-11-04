"""Opt-in ScreenCaptureKit capture of a disposable, synthetic window only."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = r'''
import json, os, threading, time
from pathlib import Path
import Quartz
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from proximic_ring.agent_workflow.capture.auto_context import capture_window, current_target
from proximic_ring.agent_workflow.capture.capture_controller import CaptureController
from proximic_ring.platforms.macos import screen_capture
if __import__('sys').argv[4]=='macos13':screen_capture.screenshot_manager=lambda _:None

app=QApplication([])
window=QWidget()
window.setWindowTitle('Mythlink automatic context test canvas')
test_width,test_height=map(int,__import__('sys').argv[2:4])
window.resize(test_width,test_height)
window.setStyleSheet('background:#2384bf;')
window.show()
QTest.qWait(300)
rows=Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly,0)
matches=[r for r in rows if r.get('kCGWindowOwnerPID')==os.getpid()
         and r.get('kCGWindowName')=='Mythlink automatic context test canvas']
assert len(matches)==1, 'Expected exactly one synthetic window'
target=dict(pid=os.getpid(),window_id=int(matches[0]['kCGWindowNumber']),
            window_title='Mythlink automatic context test canvas',document_url='file:///old-page')
# The same live window changes title AND pixels after the initial observation.
window.setWindowTitle('Mythlink translated page test canvas')
window.setStyleSheet('background:#39a86b;')
overlay=QWidget(None,Qt.Tool | Qt.WindowStaysOnTopHint)
overlay.setWindowTitle('Synthetic Agent input overlay')
overlay.resize(200,100);overlay.setStyleSheet('background:#ff0000;')
overlay.move(window.x()+100,window.y()+100);overlay.show()
QTest.qWait(300)
excluded=CaptureController._excluded_windows()
assert excluded, 'Synthetic input panel must be excluded'
current=current_target(excluded)
assert current['pid']==os.getpid() and current['window_id']==target['window_id'], current
# Assert synthetic ownership before every pixel read, never capture user apps.
result=[];latencies=[]
def capture():
    try:
        for _ in range(5):
            started=time.monotonic()
            image=capture_window(target,threading.Event())
            latencies.append(round((time.monotonic()-started)*1000,1))
        assert target['window_title']=='Mythlink translated page test canvas',target
        assert 'document_url' not in target
        result.append(image)
    except Exception as exc: result.append(exc)
worker=threading.Thread(target=capture);worker.start()
for _ in range(1500):
    app.processEvents()
    if not worker.is_alive():break
    QTest.qWait(10)
worker.join(timeout=1)
assert result and not isinstance(result[0],Exception), repr(result)
image=result[0]
pixel=image.pixelColor(image.width()//2,image.height()//2)
assert max(abs(a-b) for a,b in zip(pixel.getRgb()[:3],(57,168,107)))<12,pixel.name()
assert image.width()>=test_width and image.height()>=test_height
if __import__('sys').argv[4]=='macos13':
    from Foundation import NSMutableData
    from PySide6.QtGui import QImage
    # This rectangle is inside the synthetic green canvas and covered by the
    # synthetic red Agent panel. Exercise the legacy display compositing API.
    cg=screen_capture.legacy_screen_image(Quartz,(window.x()+110,window.y()+110,40,40),set(excluded))
    assert cg is not None
    data=NSMutableData.data()
    dest=Quartz.CGImageDestinationCreateWithData(data,'public.png',1,None)
    Quartz.CGImageDestinationAddImage(dest,cg,None)
    assert Quartz.CGImageDestinationFinalize(dest)
    sample=QImage.fromData(bytes(data),'PNG')
    color=sample.pixelColor(sample.width()//2,sample.height()//2)
    assert max(abs(a-b) for a,b in zip(color.getRgb()[:3],(57,168,107)))<12,color.name()
image.save(str(Path(__import__('sys').argv[1])/'automatic-context-canvas.png'))
print(json.dumps(dict(window_id=target['window_id'],width=image.width(),
                      height=image.height(),center=pixel.name(),single_window=True,
                      changed_title_captured=True,agent_overlay_excluded=True,
                      capture_ms=latencies)))
overlay.close();window.close()
'''


@pytest.mark.skipif(sys.platform!='darwin' or os.environ.get('PROXIMIC_TEST_AUTO_CONTEXT_NATIVE')!='1',
                   reason='opt-in synthetic native window capture')
@pytest.mark.parametrize('size',[(520,340),(1180,760)])
@pytest.mark.parametrize('backend',['automatic','macos13'])
def test_screen_capture_kit_exact_window(tmp_path,size,backend):
    script=tmp_path/'auto_context_probe.py';script.write_text(SCRIPT)
    result=subprocess.run([sys.executable,str(script),str(tmp_path),*map(str,size),backend],
        env={**os.environ,'QT_QPA_PLATFORM':'cocoa','QT_QUICK_CONTROLS_STYLE':'Basic',
             'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')},
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    print(result.stdout)
    assert '"single_window": true' in result.stdout
    assert '"changed_title_captured": true' in result.stdout
    assert '"agent_overlay_excluded": true' in result.stdout
