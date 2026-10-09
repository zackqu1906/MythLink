"""Recognition feedback must never join or delay the input/action path."""
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest
from PySide6.QtCore import QObject, QCoreApplication, QSettings, QRect, Signal, Qt

from proximic_ring.ui import gesture_trigger_controller as module
from proximic_ring.ui.gesture_trigger_overlay import trigger_geometry


@pytest.fixture
def feedback(tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])

    class Owner(QObject):
        connectedChanged = Signal()

    owner = Owner()
    owner._settings = QSettings(str(tmp_path / "hints.ini"), QSettings.IniFormat)
    owner._disconnect_event = threading.Event()
    owner.connected = True
    controller = module.GestureTriggerController(owner)
    yield controller, owner, app
    controller.close()
    app.processEvents()


def test_opt_in_persists_and_disable_cancels_queued_hint(feedback):
    controller, owner, app = feedback
    shown, hidden = [], []
    controller.triggered.connect(shown.extend)
    controller.hideRequested.connect(lambda: hidden.append(True))
    controller.submit("tap", owner._disconnect_event)
    app.processEvents()
    assert not shown and not controller.enabled
    controller.enabled = True
    assert owner._settings.value(module.SETTING_KEY, type=bool)
    restored = module.GestureTriggerController(owner)
    assert restored.enabled
    restored.close()
    controller.submit("tap", owner._disconnect_event)
    controller.enabled = False
    controller.enabled = True
    app.processEvents()
    assert not shown and hidden == [True]
    controller.submit("snap", owner._disconnect_event)
    app.processEvents()
    assert shown == ["snap"]


def test_worker_burst_batches_in_order_without_waiting_for_ui(feedback):
    controller, owner, app = feedback
    controller.enabled = True
    shown, wakes = [], []
    controller.triggered.connect(shown.extend)
    controller._wake.connect(lambda: wakes.append(True), Qt.DirectConnection)

    def burst():
        for _ in range(10000):
            controller.submit("tap", owner._disconnect_event)
        controller.submit("double-click", owner._disconnect_event)

    worker = threading.Thread(target=burst)
    worker.start()
    worker.join(1)
    assert not worker.is_alive() and not shown and wakes == [True]
    app.processEvents()
    assert shown == ["tap"] * (module.MAX_PENDING_HINTS - 1) + ["double-click"]
    assert not controller._pending and not controller._queued
    shown.clear()
    # Even a contended UI mailbox cannot block an input worker.
    with controller._lock:
        worker = threading.Thread(target=lambda: controller.submit("tap", owner._disconnect_event))
        worker.start()
        worker.join(.5)
        assert not worker.is_alive()
    for name in ("click", "click", "double-click"):
        controller.submit(name, owner._disconnect_event)
    app.processEvents()
    assert shown == ["click", "click", "double-click"]


def test_stale_disconnected_and_closed_events_are_not_replayed(feedback, monkeypatch):
    controller, owner, app = feedback
    controller.enabled = True
    shown, hidden = [], []
    controller.triggered.connect(shown.extend)
    controller.hideRequested.connect(lambda: hidden.append(True))
    clock = [10.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    old = owner._disconnect_event
    controller.submit("tap", old)
    clock[0] += 2
    app.processEvents()
    assert not shown
    controller.submit("tap", old)
    old.set()
    owner.connected = False
    owner.connectedChanged.emit()
    owner._disconnect_event = threading.Event()
    owner.connected = True
    controller.submit("snap", old)
    app.processEvents()
    assert not shown and hidden
    controller.close()
    controller.submit("click", owner._disconnect_event)
    app.processEvents()
    assert not shown


@pytest.mark.parametrize("count", [1, 3, 100])
@pytest.mark.parametrize("area", [QRect(-1920, 25, 1920, 1055), QRect(0, 0, 1280, 720),
                                  QRect(-200, -300, 150, 80), QRect(0, 0, 10, 10)])
def test_toast_stays_in_bottom_right_of_available_screen(area, count):
    geometry = trigger_geometry(area, count)
    assert area.contains(geometry)
    if area.width() >= 296 and area.height() >= 120:
        assert geometry.width() == 248 and geometry.height() == min(count * 80 - 8, area.height() - 48)
        assert area.right() - geometry.right() == area.bottom() - geometry.bottom() == 24


def test_real_toasts_push_up_and_expire_independently_without_taking_focus(tmp_path):
    script = r'''
import sys
import os
import time
import threading
from pathlib import Path
from PySide6.QtCore import QObject, QPointF, QSettings, Signal, Qt, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtTest import QTest
from proximic_ring.ui.gesture_trigger_controller import GestureTriggerController
from proximic_ring.ui.gesture_trigger_overlay import GestureTriggerOverlay

messages = []
qInstallMessageHandler(lambda kind, context, message: messages.append(message))
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
class Owner(QObject):
    connectedChanged = Signal()
owner = Owner()
owner._settings = QSettings(str(Path(sys.argv[1]) / 'settings.ini'), QSettings.IniFormat)
owner._disconnect_event = threading.Event()
owner.connected = True
controller = GestureTriggerController(owner)
errors = []
toast = GestureTriggerOverlay(controller, app, diagnostic=errors.append)
assert toast.window is None and not toast._timer.isActive()
editor = QLineEdit('retain focus')
editor.show(); editor.setFocus(); editor.activateWindow()
QTest.qWait(30)
focus = app.focusWidget()
controller.enabled = True
assert toast.window is not None and not toast.window.isVisible()
assert not toast._visibility_guard._timer.isActive()
controller.submit('index-pinch', owner._disconnect_event)
QTest.qWait(50)
root = toast.window.rootObject()
def visual_items(item):
    for child in item.childItems():
        yield child
        yield from visual_items(child)
def cards():
    return sorted([item for item in visual_items(root) if item.objectName() == 'gestureTriggerCard'],
                  key=lambda item: item.mapToScene(QPointF()).y())
def names():
    return [card.property('gestureName') for card in cards()]
assert toast.window.isVisible() and cards()[0].property('gestureLabel') == '食指捏合'
first_card = cards()[0]
first_lifetime = first_card.property('lifetimeMs')
first_deadline = toast._entries[0][1]
first_y = toast.window.y()
bottom = toast.window.geometry().bottom()
for flag in (Qt.WindowTransparentForInput, Qt.WindowDoesNotAcceptFocus, Qt.WindowStaysOnTopHint):
    assert toast.window.flags() & flag
assert app.focusWidget() is focus and not toast.window.isActive()
assert 0 < cards()[0].property('color').alphaF() < 1
QTest.qWait(350)
controller.submit('double-click', owner._disconnect_event)
app.processEvents()
assert names() == ['index-pinch', 'double-click']  # No entrance delay.
assert cards()[0] is first_card
assert cards()[1].property('opacity') == 1 and cards()[1].property('stackOffset') == 0
QTest.qWait(50)
assert names() == ['index-pinch', 'double-click']
assert toast._entries[0][1] == first_deadline
assert first_card.property('lifetimeMs') == first_lifetime
assert toast.window.y() == first_y - 80
assert toast.window.geometry().bottom() == bottom
assert 0 < first_card.property('stackOffset') < 80  # A real intermediate animation frame.
assert first_y - 80 < toast.window.y() + first_card.y() < first_y
assert toast._timer.remainingTime() < 700  # Still scheduled for the first toast.
shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', sys.argv[1]))
shots.mkdir(parents=True, exist_ok=True)
assert toast.window.grabWindow().save(str(shots / 'gesture-trigger-pushing.png'))
QTest.qWait(150)
assert first_card.property('stackOffset') == 80
assert cards()[1].mapToScene(QPointF()).y() - cards()[0].mapToScene(QPointF()).y() == 80
assert toast.window.grabWindow().save(str(shots / 'gesture-trigger-stack.png'))
QTest.qWait(max(0, round((first_deadline - .08 - time.monotonic()) * 1000)))
fading_opacity = first_card.property('opacity')
assert 0 < fading_opacity < 1
assert .97 < first_card.property('scale') < 1
assert cards()[1].property('opacity') == 1  # Each card has its own clock.
assert toast.window.grabWindow().save(str(shots / 'gesture-trigger-fading.png'))
controller.submit('snap', owner._disconnect_event)  # Arrival during a fade must not restart it.
app.processEvents()
assert cards()[0] is first_card and first_card.property('opacity') <= fading_opacity
assert first_card.property('lifetimeMs') == first_lifetime
assert cards()[-1].property('gestureName') == 'snap' and cards()[-1].property('opacity') == 1
QTest.qWait(180)
assert toast.window.isVisible() and names() == ['double-click', 'snap']
assert toast.window.geometry().bottom() == bottom
QTest.qWait(350)
assert names() == ['snap']
assert toast.window.geometry().bottom() == bottom and toast.window.height() == 72
QTest.qWait(500)
assert not toast.window.isVisible() and not toast._timer.isActive()
assert not toast._visibility_guard._timer.isActive()
controller.submit('click', owner._disconnect_event)
QTest.qWait(400)
controller.submit('click', owner._disconnect_event)
QTest.qWait(100)
assert names() == ['click', 'click']
assert all(card.property('gestureLabel') == '单击' for card in cards())
QTest.qWait(600)
assert toast.window.isVisible() and names() == ['click']
QTest.qWait(350)
assert not toast.window.isVisible() and not toast._timer.isActive()
for name in ('tap', 'click', 'double-click'):
    controller.submit(name, owner._disconnect_event)
QTest.qWait(30)
assert names() == ['tap', 'click', 'double-click']
controller.enabled = False
assert not toast.window.isVisible() and not toast._timer.isActive() and not names()
controller.enabled = True
controller.submit('snap', owner._disconnect_event)
QTest.qWait(20)
owner.connected = False
owner.connectedChanged.emit()
assert not toast.window.isVisible() and not toast._entries and not names()
assert not errors, errors
assert not any('Error' in line or 'ReferenceError' in line or 'TypeError' in line for line in messages), messages
assert 'torch' not in sys.modules and 'proximic_ring.app_runtime' not in sys.modules
toast.close(); controller.close(); editor.close()
'''
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software")
    run = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=environment,
                         capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stdout + run.stderr
