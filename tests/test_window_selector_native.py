"""Opt-in desktop validation against two temporary windows in one app."""
import os
import select
import subprocess
import sys
import time

import pytest

from proximic_ring.native_access import NativeAccessChannel
from proximic_ring.text_focus import window_stamp


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("PROXIMIC_TEST_WINDOW_SELECTOR") != "1",
                    reason="opt-in native AX window activation check")
def test_precise_native_window_activation_and_replay_rejection():
    import AppKit
    channel = NativeAccessChannel()
    if channel.permissions().accessibility is not True:
        channel.close()
        pytest.skip("Accessibility permission required")
    foreground = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    script = r'''
import AppKit as A
app = A.NSApplication.sharedApplication()
app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
windows=[]
for i, frame in enumerate([((180,160),(620,420)),((220,220),(660,400))]):
    w = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(frame,
        A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable | A.NSWindowStyleMaskMiniaturizable,
        A.NSBackingStoreBuffered, False)
    w.setTitle_('RingSelectorFixture-' + str(i))
    w.makeKeyAndOrderFront_(None)
    windows.append(w)
app.activateIgnoringOtherApps_(True)
# A layer-zero, non-key popup changes CG ordering without changing AX focus.
popup = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    ((240, 240), (260, 140)), A.NSWindowStyleMaskBorderless, A.NSBackingStoreBuffered, False)
popup.setTitle_('RingSelectorFixture-Popup')
timer = A.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
    1.0, False, lambda timer: popup.orderFront_(None))
print('ready', flush=True)
app.run()
'''
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == "ready"
        time.sleep(.3)
        snapshot = channel.call("selector_list", expected=window_stamp(process.pid))
        assert snapshot["status"] == "ready", {k: snapshot.get(k) for k in ("status", "reason")}
        cards = [c for c in snapshot["cards"] if c["pid"] == process.pid]
        assert len(cards) == 2, (snapshot["partial"], len(cards))
        # Popup ordering can change while the snapshot is displayed. No
        # periodic context check; activation validates only the selected target.
        time.sleep(1.8)
        assert window_stamp(process.pid)["window"] != snapshot["cards"][snapshot["selected"]]["number"]
        target = next(c for c in cards if c["title"] == "RingSelectorFixture-0")
        result = channel.call("selector_activate", token=snapshot["token"], target=target["id"])
        assert result == {"status": "activated"}, result
        assert channel.call("selector_activate", token=snapshot["token"], target=target["id"])["status"] == "stale"
        second = channel.call("selector_list", expected=window_stamp(process.pid))
        focused = second["cards"][second["selected"]]
        assert focused["pid"] == process.pid and focused["title"] == "RingSelectorFixture-0"
        # Also verify activation from a different application, not only AXRaise
        # among two windows of the already active test process.
        foreground.activateWithOptions_(0)
        time.sleep(.25)
        third = channel.call("selector_list", expected=window_stamp(foreground.processIdentifier()))
        assert third["status"] == "ready", third["status"]
        target = next(c for c in third["cards"] if c["pid"] == process.pid
                      and c["title"] == "RingSelectorFixture-1")
        assert channel.call("selector_activate", token=third["token"], target=target["id"]) == {"status": "activated"}
        # Treat this fixture process as the host: retain only the registered
        # main window, excluding its other standard window and non-key popup.
        host = channel.call("selector_list", host_pid=process.pid,
                            host_window={"number": target["number"], "title": target["title"]})
        assert host["status"] == "ready", host.get("reason")
        own = [c for c in host["cards"] if c["pid"] == process.pid]
        assert len(own) == 1 and own[0]["number"] == target["number"]
        assert own[0]["app"] == "Mythlink"
        assert channel.call("selector_activate", token=host["token"], target=own[0]["id"]) == {"status": "activated"}
    finally:
        channel.close()
        process.terminate()
        process.communicate(timeout=3)
        if foreground: foreground.activateWithOptions_(0)
