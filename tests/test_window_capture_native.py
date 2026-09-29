"""Opt-in: stream only a disposable animated window, even behind an occluder."""
import json
import os
import select
import subprocess
import sys
import threading
import time

import pytest


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("PROXIMIC_TEST_WINDOW_PREVIEW") != "1",
                    reason="opt-in ScreenCaptureKit desktop validation")
def test_real_occluded_window_stream_updates_and_stops_without_saving_images():
    import AppKit
    import Foundation
    import Quartz
    from proximic_ring.window_capture import WindowCapture
    from proximic_ring.window_selector import MacWindowAX
    if not Quartz.CGPreflightScreenCaptureAccess(): pytest.skip("Screen recording authorization required")
    foreground = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    script = r'''
import AppKit as A, Foundation as F, Quartz as Q, json
app=A.NSApplication.sharedApplication(); app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
def window(title):
    w=A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(((200,200),(640,400)), A.NSWindowStyleMaskTitled, A.NSBackingStoreBuffered, False)
    w.setTitle_(title); w.contentView().setWantsLayer_(True); w.makeKeyAndOrderFront_(None); return w
target=window('Ring 实时预览测试 · 自动关闭')
label=A.NSTextField.labelWithString_('实时窗口内容 · 自动测试')
label.setFrame_(((40,160),(550,55))); label.setFont_(A.NSFont.systemFontOfSize_(28)); target.contentView().addSubview_(label)
cover=window('Ring 遮挡测试 · 自动关闭')
cover.contentView().layer().setBackgroundColor_(Q.CGColorCreateGenericRGB(.1,.1,.1,1))
class Animator(F.NSObject):
    tick=0
    def tick_(self,timer):
        self.tick+=1
        target.contentView().layer().setBackgroundColor_(Q.CGColorCreateGenericRGB(.1 if self.tick%2 else .6,.25,.5,1))
animator=Animator.alloc().init()
timer=F.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(.2,animator,'tick:',None,True)
app.activateIgnoringOtherApps_(True)
print(json.dumps(dict(number=target.windowNumber(), cover=cover.windowNumber())),flush=True)
app.run()
'''
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    engine = None
    frames, statuses = [], []
    lock = threading.Lock()
    def frame(identifier, image):
        with lock:
            frames.append((identifier, image.pixelColor(10, image.height()//2).rgba(), image.size()))
    def pump(seconds):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(.02))
    try:
        assert select.select([proc.stdout],[],[],5)[0]
        info=json.loads(proc.stdout.readline())
        pump(.3)
        row=next(r for r in MacWindowAX().on_screen() if r['number']==info['number'])
        card=dict(id='fixture',pid=proc.pid,number=info['number'],frame=row['frame'])
        engine=WindowCapture()
        started=time.monotonic()
        engine.start([card],frame,statuses.append)
        deadline=time.monotonic()+8
        while len({f[1] for f in frames})<2 and time.monotonic()<deadline: pump(.1)
        assert len({f[1] for f in frames})>=2, (len(frames),statuses)
        assert all(f[0]=='fixture' and f[2].width()<=720 and f[2].height()<=480 for f in frames)
        print('live_preview_verified_ms',round((time.monotonic()-started)*1000),'frames',len(frames))
        engine.stop(); pump(.15)
        count=len(frames); pump(.35)
        assert len(frames)==count
        # The setup verifier uses this same external fixture, returns only a
        # status, and stops after its first frame. It never saves window data.
        from proximic_ring.screen_preview_check import verify_screen_preview
        verified=[]
        worker=threading.Thread(target=lambda: verified.append(verify_screen_preview(
            threading.Event(), target_reader=lambda:card, timeout=5)))
        worker.start()
        deadline=time.monotonic()+7
        while worker.is_alive() and time.monotonic()<deadline: pump(.05)
        worker.join(.1)
        assert verified == ['verified'], verified
    finally:
        if engine: engine.close()
        proc.terminate(); proc.communicate(timeout=3)
        if foreground: foreground.activateWithOptions_(0)
