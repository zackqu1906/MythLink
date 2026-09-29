"""Opt-in native routing regression, using only a disposable two-field window."""
import os
import select
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("PROXIMIC_TEST_AX_FOCUS") != "1",
                    reason="opt-in macOS focus delivery check")
def test_click_reaches_foreign_window_without_moving_pointer_or_typing():
    import AppKit
    import Quartz
    from proximic_ring.mac_permissions import read_permission_state
    from proximic_ring.text_focus import TextFocusSession
    if not read_permission_state().ready:
        pytest.skip("Native validation requires existing Accessibility and event-posting access")
    foreground = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    script = r'''
import AppKit as A
app = A.NSApplication.sharedApplication()
app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    ((120, 120), (360, 220)), A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable,
    A.NSBackingStoreBuffered, False)
window.setTitle_('Ring 聚焦路由测试 · 自动关闭')
fields = []
for y in (140, 60):
    field = A.NSTextField.alloc().initWithFrame_(((20, y), (320, 30)))
    window.contentView().addSubview_(field)
    fields.append(field)
window.makeKeyAndOrderFront_(None)
app.activateIgnoringOtherApps_(True)
window.makeFirstResponder_(fields[0])
print('ready', flush=True)
app.run()
'''
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == "ready"
        time.sleep(.3)
        session = TextFocusSession()
        session.deadline = time.monotonic() + 2
        scope, original = session._capture()
        assert scope.stamp["pid"] == process.pid
        fields = session._fields(scope)
        assert len(fields) == 2 and original in fields
        target = next(field for field in fields if field != original)
        x, y, w, h = session.ax.rect(target)
        point = (x+w/2, y+h/2)
        hit = session.ax.hit_test(point)
        assert session._ancestry(hit, target), "Test field is covered; do not click"
        pointer = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        session.ax.click_focus(scope, point, expected_focus=original)
        assert session.ax.confirmed_focus(scope.app, target, original) == target
        assert Quartz.CGEventGetLocation(Quartz.CGEventCreate(None)) == pointer
        # Only disposable blank test fields are inspected, never user content.
        assert all(session.ax.attr(field, "AXValue") in (None, "") for field in fields)
    finally:
        process.terminate()
        process.communicate(timeout=3)
        if foreground is not None:
            foreground.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
