"""Exercise the real PySide binding without requesting macOS TCC access."""
import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS permission binding")
def test_real_qt_request_accepts_bound_callback_without_late_attribute_error():
    # A plain Python executable has no permission usage descriptions. Qt rejects
    # these requests locally before reaching TCC. Never run this in an app bundle.
    code = '''
import sys
from Foundation import NSBundle
from PySide6.QtCore import QBluetoothPermission, QMicrophonePermission, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from proximic_ring.ui.permission_callback import PermissionCallback
assert not getattr(sys, "frozen", False)
for key in ("NSBluetoothAlwaysUsageDescription", "NSMicrophoneUsageDescription"):
    assert NSBundle.mainBundle().objectForInfoDictionaryKey_(key) is None
app = QGuiApplication([])
answers, callbacks = [], []
for permission in (QBluetoothPermission(), QMicrophonePermission()):
    receiver = PermissionCallback(lambda result: answers.append(result.status()))
    callbacks.append(receiver)
    app.requestPermission(permission, receiver, receiver.completed)
QTimer.singleShot(100, app.quit)
app.exec()
assert answers == [Qt.PermissionStatus.Denied, Qt.PermissionStatus.Denied], answers
print("bound permission callbacks passed")
'''
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                            text=True, timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "bound permission callbacks passed" in result.stdout
    assert 'requires "NSBluetoothAlwaysUsageDescription"' in result.stderr
    assert 'requires "NSMicrophoneUsageDescription"' in result.stderr
