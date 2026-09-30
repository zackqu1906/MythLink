import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from proximic_ring import mac_app_activation


def test_non_macos_does_not_load_appkit(monkeypatch):
    monkeypatch.setattr(mac_app_activation.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "AppKit", None)
    mac_app_activation.configure_app_activation(background=True)
    mac_app_activation.configure_app_activation(background=False)


def test_rejected_activation_policy_is_not_silently_ignored(monkeypatch):
    monkeypatch.setattr(mac_app_activation.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSApplication=SimpleNamespace(sharedApplication=lambda: SimpleNamespace(
            activationPolicy=lambda: 0,
            setActivationPolicy_=lambda policy: False)),
        NSApplicationActivationPolicyAccessory=1,
        NSApplicationActivationPolicyRegular=0))
    with pytest.raises(RuntimeError, match="前后台"):
        mac_app_activation.configure_app_activation(background=True)


@pytest.mark.parametrize("background, policy", [(True, 1), (False, 0)])
def test_already_correct_policy_needs_no_transition(monkeypatch, background, policy):
    monkeypatch.setattr(mac_app_activation.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSApplication=SimpleNamespace(sharedApplication=lambda: SimpleNamespace(
            activationPolicy=lambda: policy,
            setActivationPolicy_=lambda value: pytest.fail("already at requested policy"))),
        NSApplicationActivationPolicyAccessory=1,
        NSApplicationActivationPolicyRegular=0))
    mac_app_activation.configure_app_activation(background=background)


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS activation")
def test_qt_host_can_become_regular_after_background_bundle_start():
    # Run separately: never change the test runner's own activation policy.
    script = '''
import json
from Foundation import NSBundle
NSBundle.mainBundle().infoDictionary()["LSUIElement"] = True
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QWindow
from PySide6.QtCore import QTimer
from AppKit import NSApplication, NSWorkspace
from proximic_ring.mac_app_activation import configure_app_activation
app = QApplication([])
native = NSApplication.sharedApplication()
configure_app_activation(background=True)
NSWorkspace.sharedWorkspace().runningApplications()
hidden = native.activationPolicy()
configure_app_activation(background=False)
window = QWindow()
window.setTitle("MythLink activation regression")
window.show()
window.requestActivate()
def done():
    print(json.dumps({"hidden": hidden, "main": native.activationPolicy(),
                      "focused": app.focusWindow() is window}))
    app.quit()
QTimer.singleShot(300, done)
app.exec()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="cocoa")
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"hidden": 1, "main": 0, "focused": True}


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS activation")
def test_worker_stays_out_of_dock_across_restarts():
    from AppKit import NSRunningApplication, NSApplicationActivationPolicyAccessory

    # Set this in packaging validation to exercise the actual frozen executable.
    bundle = os.environ.get("PROXIMIC_TEST_MACOS_APP")
    args = ([str(Path(bundle) / "Contents/MacOS/ProximicVoice"), "--native-access-worker"]
            if bundle else [sys.executable, "-B", "-m", "proximic_ring.native_access_worker"])
    for _ in range(3):
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            process.stdin.write('{"id":1,"operation":"status"}\n')
            process.stdin.flush()
            deadline = time.monotonic() + 10
            while not select.select([process.stdout], [], [], 0.02)[0]:
                assert time.monotonic() < deadline, "worker did not reply"
                if bundle:
                    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(process.pid)
                    assert app is None or app.activationPolicy() != 0, "worker briefly entered Dock at startup"
            line = process.stdout.readline()
            assert line, process.stderr.read()
            reply = json.loads(line)
            assert reply["id"] == 1 and not reply.get("error"), reply
            assert reply["permissions"]["control_pid"] == process.pid
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(process.pid)
            assert app is not None
            assert app.activationPolicy() == NSApplicationActivationPolicyAccessory
            process.stdin.close()
            assert process.wait(timeout=5) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
