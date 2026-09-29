"""Opt-in: real wheel delivery to a disposable AppKit main pane, preserving focus."""
import json
import os
import select
import subprocess
import sys
import time

import pytest

from proximic_ring.native_access import NativeAccessChannel
from proximic_ring.page_scroll import animate_scroll
from proximic_ring.text_focus import window_stamp


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("PROXIMIC_TEST_PAGE_SCROLL") != "1",
                    reason="opt-in macOS scrolling check")
def test_native_main_pane_half_scroll_preserves_focus_and_pointer():
    import AppKit
    import Quartz
    channel = NativeAccessChannel()
    if not channel.permissions().ready:
        channel.close()
        pytest.skip("Native scroll validation needs Accessibility and event-posting permissions")
    foreground = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    script = r'''
import json, sys, threading, queue, time
import AppKit as A
import Foundation as F
from objc import super
class TestApplication(A.NSApplication):
    def sendEvent_(self, event):
        if event.type() == A.NSEventTypeScrollWheel:
            print('wheel', event.windowNumber(), event.locationInWindow(), event.scrollingDeltaY(), event.hasPreciseScrollingDeltas(), file=sys.stderr, flush=True)
        super().sendEvent_(event)
app = TestApplication.sharedApplication()
app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    ((100, 100), (800, 600)), A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable,
    A.NSBackingStoreBuffered, False)
window.setTitle_('Ring 页面滚动检查 · 自动关闭')
class Document(A.NSView):
    def isFlipped(self): return True
def pane(x, width):
    scroll = A.NSScrollView.alloc().initWithFrame_(((x, 60), (width, 520)))
    scroll.setHasVerticalScroller_(True)
    content = Document.alloc().initWithFrame_(((0, 0), (width - 20, 2400)))
    for i in range(80):
        label = A.NSTextField.labelWithString_('测试行 ' + str(i))
        label.setFrame_(((8, i * 30), (width - 32, 25)))
        content.addSubview_(label)
    scroll.setDocumentView_(content)
    window.contentView().addSubview_(scroll)
    return scroll
sidebar, main = pane(10, 160), pane(180, 600)
field = A.NSTextField.alloc().initWithFrame_(((10, 15), (770, 25)))
window.contentView().addSubview_(field)
window.makeKeyAndOrderFront_(None)
app.activateIgnoringOtherApps_(True)
window.makeFirstResponder_(field)
commands = queue.Queue()
samples = []
def read():
    for line in sys.stdin: commands.put(line.strip())
threading.Thread(target=read, daemon=True).start()
class Commands(F.NSObject):
    def tick_(self, timer):
        samples.append((time.monotonic(), main.contentView().bounds().origin.y))
        del samples[:-250]
        if commands.empty(): return
        cmd = commands.get()
        if cmd == 'quit': app.terminate_(None)
        print(json.dumps(dict(sidebar=sidebar.contentView().bounds().origin.y,
                              main=main.contentView().bounds().origin.y,
                              height=main.contentView().bounds().size.height,
                              at=time.monotonic(), samples=samples,
                              focus=window.firstResponder() == field.currentEditor())), flush=True)
handler = Commands.alloc().init()
timer = F.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
    .02, handler, 'tick:', None, True)
print('ready', flush=True)
app.run()
'''
    process = subprocess.Popen([sys.executable, "-c", script], env=dict(os.environ, QT_QPA_PLATFORM="cocoa"),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    def snapshot():
        process.stdin.write("state\n"); process.stdin.flush()
        assert select.select([process.stdout], [], [], 3)[0]
        return json.loads(process.stdout.readline())
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == "ready"
        time.sleep(.4)
        before = snapshot()
        pointer = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        stamp = window_stamp(process.pid)
        for direction in ("down", "up"):
            beginning = snapshot()
            plan = channel.call("scroll_plan", direction=direction, expected=stamp)
            assert plan["status"] == "ready", plan
            result = animate_scroll(lambda progress: channel.call("scroll_apply", plan=plan["plan"], progress=progress),
                                    lambda: True)
            assert result["status"] == "posted", result
            time.sleep(.15)
            after = snapshot()
            assert after["focus"] and after["sidebar"] == 0, after
            assert Quartz.CGEventGetLocation(Quartz.CGEventCreate(None)) == pointer
            intermediate = [(t, y) for t, y in after["samples"]
                            if t >= beginning["at"] and 2 < y < before["height"] / 2 - 2]
            assert len({y for _, y in intermediate}) >= 5, after
            assert intermediate[-1][0] - intermediate[0][0] >= .15, intermediate
            if direction == "down":
                assert abs(after["main"] - before["height"] / 2) < 8, (before, after, plan)
            else:
                assert after["main"] == 0, after
        plan = channel.call("scroll_plan", direction="down", expected=stamp)
        partial = channel.call("scroll_apply", plan=plan["plan"], progress=.2)
        assert partial["status"] == "scrolling"
        time.sleep(.05)
        partial_position = snapshot()["main"]
        foreground.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        time.sleep(.15)
        assert channel.call("scroll_apply", plan=plan["plan"])["status"] == "stale"
        assert snapshot()["main"] == partial_position
    finally:
        channel.close()
        process.terminate()
        _, diagnostics = process.communicate(timeout=3)
        print(diagnostics)
        if foreground is not None:
            foreground.activateWithOptions_(0)
