from pathlib import Path
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from PySide6.QtCore import QRect

from proximic_ring.ui.gesture_hud import hud_geometry, screen_for_window


def test_foreground_window_screen_wins_over_mouse_and_negative_origins():
    left = SimpleNamespace(geometry=lambda: QRect(-1440, 0, 1440, 900))
    right = SimpleNamespace(geometry=lambda: QRect(0, 0, 1728, 1117))
    assert screen_for_window([left, right], QRect(-1300, 40, 1100, 750)) is left
    assert screen_for_window([left, right], QRect(-100, 20, 1200, 700)) is right
    assert screen_for_window([left, right], None) is None
    assert screen_for_window([left, right], QRect(9000, 0, 800, 600)) is None
    area = QRect(-1440, 25, 1440, 810)
    geometry = hud_geometry(area)
    assert geometry.size().width() == 360 and area.contains(geometry)
    assert area.right() - geometry.right() == 24
    assert area.bottom() - geometry.bottom() == 24
    assert QRect(0, 0, 480, 420).contains(hud_geometry(QRect(0, 0, 480, 420)))
    assert QRect(-200, -300, 200, 240).contains(hud_geometry(QRect(-200, -300, 200, 240)))


def test_hud_modes_reset_timer_do_not_activate_and_do_not_start_voice(tmp_path):
    # A dedicated process avoids sharing another test's QCoreApplication and
    # runs the real QML, rendering and timer together without user settings.
    script = r'''
import sys
import math
import os
from pathlib import Path
from PySide6.QtCore import QMetaObject, QRectF, Qt, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtTest import QTest
from proximic_ring.ui.gesture_hud import GestureHud

messages = []
qInstallMessageHandler(lambda kind, context, message: messages.append(message))
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', sys.argv[1]))
shots.mkdir(parents=True, exist_ok=True)
editor = QLineEdit()
editor.setText('keep focus here')
editor.show(); editor.setFocus(); editor.activateWindow()
QTest.qWait(60)
focus = app.focusWidget()
hud = GestureHud(app)
hud.preview('input')
root = hud.window.rootObject()
QTest.qWait(100)
assert hud.window.isVisible()
assert hud.window.transientParent() is None
for flag in (Qt.WindowTransparentForInput, Qt.WindowDoesNotAcceptFocus, Qt.WindowStaysOnTopHint):
    assert hud.window.flags() & flag
assert app.focusWidget() is focus
assert not hud.window.isActive()
assert root.property('mode') == 'input'
assert hud.window.width() == hud.window.height() == 360
assert root.property('animationRunning')
assert 0 <= root.property('entranceTime') < 300
assert not hud.window.grabWindow().isNull()
QTest.qWait(800)
hud.preview('input')  # Even the same mode must replay from the spiral's start.
assert root.property('entranceTime') < 50
QTest.qWait(1900)

def visual_items(item):
    for child in item.childItems():
        yield child
        yield from visual_items(child)

def check_layout(count):
    satellites = sorted((item for item in visual_items(root)
                         if item.objectName().startswith('gestureSatellite')),
                        key=lambda item: item.property('index'))
    assert len(satellites) == count
    hub = next(item for item in visual_items(root) if item.objectName() == 'gestureHudHub')
    hub_rect = hub.mapRectToScene(QRectF(0, 0, hub.width(), hub.height()))
    circles = [sat.mapRectToScene(QRectF(6, 6, 32, 32)) for sat in satellites]
    labels = []
    for index, sat in enumerate(satellites):
        angle = math.radians(index*360/count)
        assert abs(sat.x()+24 - (180 + 112*math.sin(angle))) < .1
        assert abs(sat.y()+24 - (160 - 112*math.cos(angle))) < 1
        label = next(item for item in visual_items(sat) if item.objectName() == 'satelliteLabel')
        rect = label.mapRectToScene(QRectF(0, 0, label.width(), label.height()))
        assert QRectF(0, 0, 360, 360).contains(rect)
        assert not rect.intersects(hub_rect), (index, rect, hub_rect)
        assert not any(rect.intersects(circle) for circle in circles), (index, rect, circles)
        caption = next(item for item in visual_items(label) if item.objectName() == 'satelliteCaption')
        assert caption.property("font").pixelSize() >= 12
        assert not any(rect.intersects(other) for other in labels), (index, rect, labels)
        labels.append(rect)

check_layout(6)
assert hud.window.grabWindow().save(str(shots / 'input-hud.png'))
hud.preview('operation')
assert root.property('entranceTime') < 50
QTest.qWait(1500)
assert hud.window.isVisible()
assert root.property('mode') == 'operation'
check_layout(3)
assert hud.window.grabWindow().save(str(shots / 'operation-hud.png'))
assert app.focusWidget() is focus
QTest.qWait(1700)
assert hud.window.isVisible()  # Past both 3 s and the preceding display's 5 s deadline.
QTest.qWait(2100)
assert not hud.window.isVisible()
assert not root.property('animationRunning')
hud.show_mode('input', input_fields_available=False)
QTest.qWait(1900)
assert root.property('inputFieldsAvailable') is False
check_layout(6)
remaining, entrance = hud._timer.remainingTime(), root.property('entranceTime')
hud.update_input_fields(False, '输入框读取未完成，请重试')
assert root.property('inputFieldsHint') == '输入框读取未完成，请重试'
assert root.property('fieldHint') == '输入框读取未完成，请重试'
assert root.property('entranceTime') >= entrance
assert hud._timer.remainingTime() <= remaining
check_layout(6)
hud.update_input_fields(True, '')
assert root.property('inputFieldsAvailable') is True
hud.update_input_fields(True, '部分区域未提供控件')
assert root.property('inputFieldsAvailable') is True
check_layout(6)
hud.show_mode('input', message='请先结束本句')
assert root.property('notice') == '请先结束本句'
assert root.property('entranceTime') < 50
assert app.focusWidget() is focus
hud.preview('operation')
assert root.property('notice') == ''  # A later request must clear a previous notice.
hud.update_input_fields(False, '需要辅助功能权限')
assert root.property('inputFieldsAvailable') is True  # Preview stays independent.
keys = ['swipe-left', 'swipe-right', 'swipe-up', 'swipe-down', 'tap', 'snap', 'circle-clockwise', 'circle-counterclockwise']
rows = [dict(key=g, gesture=g, action='很长的场景动作名称用于检查省略和字号', scope='scene', application='PowerPoint',
             scene='presentation', sceneLabel='放映', voiceDisabled=True) for g in keys]
hud.show_mode('input', scene_actions=rows)
QTest.qWait(1900)
assert next(item for item in visual_items(root) if item.objectName() == 'gestureHudHub').isVisible()
assert root.property('voiceDisabled') and root.property('overflow') == 2
assert root.property('sceneActive') and root.property('modeTitle') == '放映'
assert root.property('fieldHint') == ''
assert not next(item for item in visual_items(root) if item.objectName() == 'gestureHudGlobalShortcuts').isVisible()
check_layout(6)
assert app.focusWidget() is focus and not hud.window.isActive()
assert hud.window.grabWindow().save(str(shots / 'presentation-hud.png'))
original = root.property('orbs')
hud.show_mode('operation', scene_actions=rows)
QTest.qWait(1900)
assert root.property('orbs') == original
assert root.property('modeTitle') == '放映'
assert root.property('mode') == 'operation' and root.property('contextLabel') == 'PowerPoint · 放映'
check_layout(6)
assert hud.window.grabWindow().save(str(shots / 'presentation-operation-hud.png'))
for count in range(1, 6):
    hud.show_mode('input', scene_actions=rows[:count])
    QTest.qWait(1900)
    assert hud.window.grabWindow().save(str(shots / ('scene-' + str(count) + '-hud.png')))
    check_layout(count)
hud.show_mode('input')
assert next(item for item in visual_items(root) if item.objectName() == 'gestureHudHub').isVisible()
assert not any('Error' in line or 'ReferenceError' in line or 'TypeError' in line for line in messages), messages
assert 'proximic_ring.app_runtime' not in sys.modules
assert 'proximic_ring.host_gestures' not in sys.modules
assert 'torch' not in sys.modules
hud.close(); editor.close()
assert not root.property('animationRunning')
'''
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                       PROXIMIC_DATA_HOME=str(tmp_path), PROXIMIC_STARTUP_PROBE="1")
    run = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=environment,
                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("native_failure", [False, True])
def test_cocoa_hint_never_uses_qt_process_activation(monkeypatch, native_failure):
    from proximic_ring.ui import gesture_hud as module, notifications

    calls, diagnostics = [], []
    # Simulate Qt's Cocoa raise implementation bringing the main UI forward.
    window = SimpleNamespace(
        rootObject=lambda: SimpleNamespace(setProperty=lambda *_: None),
        show=lambda: calls.append("show_hint"),
        raise_=lambda: calls.append("activate_main_ui"),
    )
    hud = SimpleNamespace(window=window, _screen=lambda: None,
                          _timer=SimpleNamespace(start=lambda: calls.append("timer")),
                          _diagnostic=diagnostics.append)
    def order_only_hint(target):
        assert target is window
        if native_failure:
            raise RuntimeError("native window unavailable")
        calls.append("order_hint")
    monkeypatch.setattr(module, "QMetaObject", SimpleNamespace(invokeMethod=lambda *_: None))
    monkeypatch.setattr(module, "QGuiApplication", SimpleNamespace(platformName=lambda: "cocoa"))
    monkeypatch.setattr(notifications, "_show_on_macos_spaces", order_only_hint)
    for mode in ("input", "operation", "input"):
        module.GestureHud.show_mode(hud, mode)
    assert calls.count("show_hint") == calls.count("timer") == 3
    assert "activate_main_ui" not in calls
    assert calls.count("order_hint") == (0 if native_failure else 3)
    assert bool(diagnostics) is native_failure


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("PROXIMIC_TEST_COCOA_HUD") != "1",
                    reason="opt-in native macOS desktop focus check")
def test_cocoa_hud_keeps_foreground_app_and_main_window_state(tmp_path):
    script = r'''
import ctypes
import os
import objc
import AppKit
import Quartz
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtTest import QTest
from proximic_ring.ui.gesture_hud import GestureHud
from proximic_ring.ui.focus_band import FocusBand

workspace = AppKit.NSWorkspace.sharedWorkspace()
foreground = workspace.frontmostApplication()
pid = int(foreground.processIdentifier())
assert pid != os.getpid()
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
main = QLineEdit('Ring 菜单焦点检查 · 自动关闭')
main.setWindowTitle('Ring HUD focus regression')
main.resize(340, 50)
main.show()
QTest.qWait(150)
view = objc.objc_object(c_void_p=ctypes.c_void_p(int(main.winId())))
native_main = view.window()
main_id = int(native_main.windowNumber())
hud = GestureHud(app)
class Picker(QObject):
    shown = Signal(object)
    exited = Signal(str)
    progress = Signal(float, bool)
    def landed(self, serial): pass
picker = Picker()
band = FocusBand(picker, app)
assert app.platformName() == 'cocoa'
states = []
app.applicationStateChanged.connect(states.append)

def other_windows_above_main():
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, 0)
    ordered = [int(w[Quartz.kCGWindowNumber]) for w in windows if w[Quartz.kCGWindowLayer] == 0]
    if main_id not in ordered:
        return set()
    return set(ordered[:ordered.index(main_id)])

try:
    for state in ('background', 'minimized', 'hidden'):
        if state == 'minimized': main.showMinimized()
        elif state == 'hidden': main.hide()
        foreground.activateWithOptions_(0)
        QTest.qWait(350)
        assert int(workspace.frontmostApplication().processIdentifier()) == pid
        assert not AppKit.NSApp.isActive()
        before = (main.isVisible(), main.isMinimized(), bool(native_main.isVisible()),
                  bool(native_main.isMiniaturized()))
        above = other_windows_above_main()
        states.clear()
        for mode in ('input', 'operation', 'input'):
            hud.show_mode(mode)
            QTest.qWait(250)
            assert hud.window.isVisible() and not hud.window.isActive()
            assert int(workspace.frontmostApplication().processIdentifier()) == pid, (state, mode)
            assert not AppKit.NSApp.isActive(), (state, mode)
            assert Qt.ApplicationActive not in states, (state, mode, states)
            assert before == (main.isVisible(), main.isMinimized(), bool(native_main.isVisible()),
                              bool(native_main.isMiniaturized())), (state, mode)
            assert above <= other_windows_above_main(), (state, mode, 'main window raised')
        hud.hide()
        for index in (1, 2):
            picker.shown.emit({'frame': [100,100,500,300],
                'fields': [{'rect':[120,130,300,40]}, {'rect':[120,230,300,40]}],
                'index':index, 'deferred':False, 'serial':index,
                'motion':'enter' if index == 1 else 'move'})
            QTest.qWait(650)
            assert band.window.isVisible() and not band.window.isActive()
            assert int(workspace.frontmostApplication().processIdentifier()) == pid
            assert not AppKit.NSApp.isActive() and Qt.ApplicationActive not in states
            assert before == (main.isVisible(), main.isMinimized(), bool(native_main.isVisible()),
                              bool(native_main.isMiniaturized()))
            assert above <= other_windows_above_main()
        picker.exited.emit('timeout')
        QTest.qWait(380)
        assert not band.window.isVisible()
        QTest.qWait(100)
        assert int(workspace.frontmostApplication().processIdentifier()) == pid
        print(state + ': foreground and main window preserved', flush=True)
finally:
    band.close()
    hud.close()
    main.close()
    foreground.activateWithOptions_(0)
'''
    environment = dict(os.environ, QT_QPA_PLATFORM="cocoa", PROXIMIC_DATA_HOME=str(tmp_path),
                       PROXIMIC_STARTUP_PROBE="1")
    run = subprocess.run([sys.executable, "-c", script], env=environment,
                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
