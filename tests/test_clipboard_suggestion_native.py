"""Opt-in metadata-only check against this test's own two native editors."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = r'''
import os, threading, time
import AppKit
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTextEdit, QLineEdit
from PySide6.QtTest import QTest
from proximic_ring.platforms.macos.clipboard_suggestion import MacClipboardSuggestionAccess

app = QApplication([])
previous = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
window = QWidget(); window.setWindowTitle('Mythlink Clipboard Focus Test')
layout = QVBoxLayout(window)
first, second, secure = QTextEdit('test editor one'), QTextEdit('test editor two'), QLineEdit()
secure.setEchoMode(QLineEdit.Password)
for editor in (first, second, secure): layout.addWidget(editor)
window.resize(420, 300); window.show(); window.activateWindow(); first.setFocus()
AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
access = MacClipboardSuggestionAccess()

def capture(editor):
    editor.setFocus()
    QTest.qWait(150)
    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    assert front and front.processIdentifier() == os.getpid(), 'test did not acquire its own window'
    result, complete = {}, threading.Event()
    def work():
        try: result['field'] = access.capture()
        except Exception as exc: result['error'] = repr(exc)
        finally: complete.set()
    threading.Thread(target=work, daemon=True).start()
    deadline = time.monotonic() + 4
    while not complete.is_set() and time.monotonic() < deadline: QTest.qWait(10)
    assert complete.is_set() and 'error' not in result, result
    return result['field']

try:
    a = capture(first)
    assert a is not None and a.target.pid == os.getpid() and a.rect[3] > 0, 'first editor unavailable'
    b = capture(second)
    assert b is not None and b.key != a.key, 'editors share an identity'
    again = capture(first)
    assert again is not None and again.key == a.key, 'same editor lost identity on refocus'
    protected = capture(secure)
    if protected is not None:
        details = access.ax.metadata(protected.target.focus)
        details.update({name: access.ax.attr(protected.target.focus, name)
                        for name in ('AXRoleDescription', 'AXProtectedContent')})
        raise AssertionError('secure field should not show clipboard preview: ' + repr(details))
    print('PASS real AX: two distinct editors, stable identity on refocus, caret geometry, secure-field exclusion')
finally:
    window.close()
    if previous: previous.activateWithOptions_(0)
'''


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('MYTHLINK_TEST_CLIPBOARD_NATIVE') != '1',
                    reason='opt-in native focus test; no system clipboard access')
def test_native_editor_identity_without_switching_input_source():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-c', SCRIPT], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='cocoa', PYTHONPATH=str(root / 'src')),
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
