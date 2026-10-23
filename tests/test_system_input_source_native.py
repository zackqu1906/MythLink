"""Opt-in fresh-process Ring worker / real Cocoa TIS regression, read-only."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = r'''
import json, threading, time, traceback
from types import SimpleNamespace
from Foundation import NSThread
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from proximic_ring.ui.gestures.ring_gesture_controller import RingGestureController
from proximic_ring.voice.ime.focus_probe import probe_input_focus
from proximic_ring.voice.ime.system_input_source import SystemInputSources

# No TIS access on the main thread before the first worker request. Otherwise
# HIToolbox's warmed cache can hide the original first-initialization crash.
app = QGuiApplication([])
app.setQuitOnLastWindowClosed(False)
done, connection = threading.Event(), threading.Event()
results, failures, native_reads, ticks = [], [], [], []
front = ('fixture.editor', 42)
state = {}
bridge = SimpleNamespace(epoch='fixture', connected=True, focus_state=lambda **_: state.copy())

def select(pid, *, timeout):
    backend = SystemInputSources()
    for method in ('_sources', '_current'):
        original = getattr(backend, method)
        def checked(original=original):
            assert NSThread.isMainThread(), 'native TIS read escaped main queue'
            start = time.monotonic()
            result = original()
            native_reads.append((time.monotonic() - start) * 1000)
            return result
        setattr(backend, method, checked)
    assert backend.sources(timeout=timeout)
    assert backend.current(timeout=timeout)
    # Only the application/client fixture is simulated. Real native reads
    # above run on every first-start path; no shortcut/audio/text is emitted.
    state.update(epoch='fixture', ready=True, client_id='fixture-client', application=front[0], selection=[0, 0])

proofs = []
inline = SimpleNamespace(
    capture_start_focus=lambda: probe_input_focus(bridge, lambda: front, select),
    pin_start_focus=lambda target, created: proofs.append(target))
owner = SimpleNamespace(_disconnect_event=connection, _inline_input=inline,
    _global_gesture_bindings=SimpleNamespace(action_for=lambda name: None),
    _app_gestures=SimpleNamespace(foreground_has_binding=lambda name: False),
    _gesture_bindings=SimpleNamespace(confirm=('tap',), end=()))
gate = SimpleNamespace(owner=owner, session_blocked=lambda: False, speech_busy=lambda: False,
    _selector=SimpleNamespace(blocked=threading.Event()), _lock=threading.Lock(),
    _transition=None, _scroll=SimpleNamespace(applying=threading.Event()), _generation=0)

def worker():
    try:
        for _ in range(50):
            state.clear()
            started = time.monotonic()
            assert RingGestureController.filter(gate, SimpleNamespace(name='tap'), False, connection)
            assert proofs[-1] == ('fixture', 'fixture-client', '', front[0], 42, (0, 0))
            results.append((time.monotonic() - started) * 1000)
            time.sleep(.005)
    except BaseException:
        failures.append(traceback.format_exc())
    finally:
        done.set()

poll = QTimer()
poll.setInterval(5)
def check():
    ticks.append(time.monotonic())
    if done.is_set(): app.quit()
poll.timeout.connect(check)
poll.start()
QTimer.singleShot(0, lambda: threading.Thread(target=worker, name='RingGestureRegression', daemon=True).start())
QTimer.singleShot(10000, app.quit)
app.exec()
assert done.is_set() and not failures and len(results) == 50, (failures, len(results))
assert len(native_reads) == 100 and len(ticks) > 10
print(json.dumps(dict(taps=len(results), main_queue_reads=len(native_reads), gui_ticks=len(ticks),
    first_ms=round(results[0], 2), max_ms=round(max(results), 2),
    native_read_max_ms=round(max(native_reads), 2))), flush=True)
'''


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_INPUT_SOURCES') != '1',
                   reason='opt-in real Cocoa main-queue input-source check')
def test_first_ring_tap_reads_tis_on_main_queue_in_fresh_gui_process():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM='cocoa', QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM='1',
               PYTHONPATH=str(root / 'src'))
    result = subprocess.run([sys.executable, '-c', SCRIPT], cwd=root, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
