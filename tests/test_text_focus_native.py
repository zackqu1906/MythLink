"""Opt-in desktop check against a disposable, two-field Qt application."""
import os
import select
import subprocess
import sys
import time

import pytest

from proximic_ring.native_access import NativeAccessChannel


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("PROXIMIC_TEST_AX_FOCUS") != "1",
                    reason="opt-in macOS Accessibility focus check")
def test_real_native_fields_cycle_and_a_sheet_limits_scope():
    import AppKit
    import Foundation
    channel = NativeAccessChannel()
    if channel.permissions().accessibility is not True:
        channel.close()
        pytest.skip("Current executable needs Accessibility permission for native focus validation")
    foreground = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    script = r'''
import sys
from PySide6.QtCore import QSocketNotifier, Qt
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton, QDialog
app = QApplication([])
app.setApplicationName('Ring 输入框检查')
window = QWidget()
window.setWindowTitle('Ring 输入框检查 · 自动关闭')
layout = QVBoxLayout(window)
first, second = QLineEdit(), QLineEdit()
first.setPlaceholderText('第一个输入框')
second.setPlaceholderText('第二个输入框')
disabled = QLineEdit(); disabled.setEnabled(False)
button = QPushButton('测试按钮')
for widget in (first, second, disabled, button): layout.addWidget(widget)
window.resize(360, 220)
window.show(); window.raise_(); window.activateWindow(); button.setFocus()
dialog = None
def command():
    global dialog
    line = sys.stdin.readline().strip()
    if line == 'sheet':
        dialog = QDialog(window, Qt.Sheet)
        dialog.setWindowModality(Qt.WindowModal)
        QVBoxLayout(dialog).addWidget(QLineEdit())
        dialog.show()
    elif line == 'quit': app.quit()
    print('ack', flush=True)
notifier = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Read)
notifier.activated.connect(lambda *_: command())
print('ready', flush=True)
app.exec()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="cocoa")
    process = subprocess.Popen([sys.executable, "-c", script], env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == "ready"
        time.sleep(.4)
        probe = channel.call("focus_probe", ignored_pid=os.getpid())
        assert probe["status"] == "ready", probe
        assert probe["stamp"]["pid"] == process.pid
        for action, index in (("restore", 1), ("next", 2), ("next", 1)):
            plan = channel.call("focus_plan", action=action, expected=probe["stamp"])
            assert plan["count"] == 2, plan
            result = channel.call("focus_apply", plan=plan["plan"])
            assert result["status"] == "focused" and result["index"] == index, result
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(.01))
            assert AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == process.pid
        selection = channel.call("focus_selection", action="enter", expected=probe["stamp"])
        assert selection["count"] == 2 and selection["index"] == 1, selection
        assert all(field["rect"][2] > 0 and field["rect"][3] > 0 for field in selection["fields"])
        assert channel.call("focus_apply", plan=selection["plan"])["status"] == "focused"
        moved = channel.call("focus_selection", action="move", selection=selection["selection"], direction="down")
        assert moved["index"] == 2, moved
        assert channel.call("focus_apply", plan=moved["plan"])["status"] == "focused"
        inspected = channel.call("focus_selection", action="inspect", selection=selection["selection"])
        assert inspected["index"] == 2, inspected
        channel.call("focus_selection", action="cancel", selection=selection["selection"])
        # Cancellation must leave the current field focused.
        restored = channel.call("focus_plan", action="restore")
        assert restored["index"] == 2, restored
        process.stdin.write("sheet\n"); process.stdin.flush()
        assert select.select([process.stdout], [], [], 3)[0]
        assert process.stdout.readline().strip() == "ack"
        time.sleep(.2)
        result = channel.call("focus_plan", action="inspect")
        assert result["status"] == "available" and result["count"] == 1, result
        # The pipe worker must refresh NSWorkspace after another app activates.
        plan = channel.call("focus_plan", action="next")
        assert "plan" in plan, plan
        foreground.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        time.sleep(.2)
        assert channel.call("focus_apply", plan=plan["plan"])["status"] == "stale"
    finally:
        channel.close()
        process.terminate()
        process.communicate(timeout=3)
        if foreground is not None:
            foreground.activateWithOptions_(0)
