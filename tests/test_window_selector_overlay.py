import os
from pathlib import Path
import subprocess
import sys

import pytest


OVERLAY_SCRIPT = r'''
import os
from types import SimpleNamespace
from PySide6.QtCore import QObject, QPoint, QPointF, Signal, Qt, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtTest import QTest
from proximic_ring.ui.desktop.window_selector_controller import WindowSelectorController
from proximic_ring.ui.desktop.window_selector_overlay import WindowSelectorOverlay

messages = []
qInstallMessageHandler(lambda kind, ctx, message: messages.append(message))
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
class Ring(QObject):
    changed = Signal()
    hideRequested = Signal()
    owner = SimpleNamespace(_event_log=lambda *args, **kwargs: None)
    globalBindings = dict(show_menu='index-pinch', switch_mode='middle-pinch', window_selector='clench')
    def speech_busy(self): return False
ring = Ring()
activations = []
class Channel:
    def close(self): pass
    def call(self, operation, **params):
        if operation == 'selector_activate':
            activations.append(params['target'])
            return dict(status='activated')
        return dict(status='cancelled')
s = WindowSelectorController(ring, enabled=False, channel=Channel())
s._valid = lambda epoch: True
names = [('Safari', 'ChatGPT · 项目讨论'), ('Codex', 'Ring 全局手势菜单'),
         ('Finder', '项目文件'), ('微信', '聊天窗口'), ('Terminal', 'ring-sdk'),
         ('Chrome', '设计参考与文档'), ('Notes', '手势交互笔记'), ('Preview', '设计草图')]
fixture_cards = [dict(id=str(i), pid=0, bundle='fixture.'+str(i), app=n, title=t, focused=i==0)
                 for i,(n,t) in enumerate(names)]
fixture_cards.append(dict(id='8', pid=0, bundle='fixture.1', app='Codex', title='第二个窗口', focused=False))
s._set_snapshot(dict(token='fixture', bounds=[0,0,800,800], cards=fixture_cards))
s._phase = 'ready'
s._started = __import__('time').monotonic()
s._selected = 1
editor = QLineEdit('unchanged')
editor.show(); editor.activateWindow(); editor.setFocus()
QTest.qWait(100)
native = os.environ.get('RING_NATIVE_OVERLAY') == '1'
if native:
    import AppKit
    import Quartz
    from proximic_ring.desktop.text_focus import MacAX
    ax = MacAX()
    original = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    original_pointer = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    external = next(a for a in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
                    if a.bundleIdentifier() == 'com.apple.finder')
    external.activateWithOptions_(0)
    QTest.qWait(200)
    before = ax.front()
else:
    before = app.focusWidget()
overlay = WindowSelectorOverlay(s, app)
# Preview uses initials and fixture titles only; no user window data.
overlay.icons.prepare = lambda cards: None
first_frames=[]
began=__import__('time').monotonic()
overlay.window.frameSwapped.connect(lambda: first_frames.append(__import__('time').monotonic()-began))
overlay.show()
if not native: overlay.window.resize(1440, 900)
QTest.qWait(500)
assert overlay.window.isVisible() and not overlay.window.isActive()
assert overlay.window.flags() & Qt.WindowDoesNotAcceptFocus
assert not overlay.window.flags() & Qt.WindowTransparentForInput
assert s._columns >= 3
assert not overlay.window.grabWindow().isNull()
assert first_frames and first_frames[0] < .5, first_frames
print('selector_initial_frame_ms',round(first_frames[0]*1000),flush=True)
if native:
    assert ax.front() == before, (before, ax.front())
    assert overlay._material is not None
    effect = overlay._material.effect
    assert effect.superview() == overlay._material.content.superview()
    siblings = list(effect.superview().subviews())
    assert siblings.index(effect) < siblings.index(overlay._material.content)
    assert effect.blendingMode() == AppKit.NSVisualEffectBlendingModeBehindWindow
    assert not effect.isHidden() and effect.frame().size == overlay._material.content.frame().size
else:
    assert app.focusWidget() is before
s.pick(7)
QTest.qWait(250)
assert s.selected == 7
s._remaining = 2; s.changed.emit()
QTest.qWait(120)
assert s.selected == 7
s.pick(1)
QTest.qWait(220)
assert s.atApps and len(s.cards) == 8 and s.cards[1]['windowCount'] == 2
apps_preview = os.environ.get('RING_SELECTOR_APPS_PREVIEW')
if apps_preview: assert overlay.window.grabWindow().save(apps_preview)
s.confirm()
QTest.qWait(220)
assert not s.atApps and len(s.cards) == 2 and s.heading == 'Codex'
assert [c['id'] for c in overlay.previews._cards] == ['1', '8']
s.pick(1)
s.back()
QTest.qWait(220)
assert s.atApps and s.selected == 1 and len(overlay.previews._cards) == 8
s.confirm()
QTest.qWait(220)
assert not s.atApps and s.selected == 1 and len(overlay.previews._cards) == 2
# Synthetic window contents exercise the real QML image-provider path and
# produce a shareable preview without capturing any user's application data.
from PySide6.QtGui import QImage, QPainter, QColor, QFont
for index,card in enumerate(s.cards):
    img=QImage(640,400,QImage.Format_ARGB32); img.fill(QColor('#f3f5f8' if index%2==0 else '#172235'))
    painter=QPainter(img)
    painter.fillRect(0,0,640,30,QColor('#e1e6ee' if index%2==0 else '#263349'))
    painter.setPen(QColor('#354255' if index%2==0 else '#d9e5f5'))
    painter.setFont(QFont('Arial',12)); painter.drawText(16,20,card['app'])
    painter.fillRect(0,30,122,370,QColor('#e6ebf1' if index%2==0 else '#1e2b41'))
    painter.setFont(QFont('Arial',20)); painter.drawText(150,86,card['title'])
    painter.setPen(QColor('#607590' if index%2==0 else '#8099b8'))
    painter.setFont(QFont('Arial',13))
    for row in range(6):
        painter.drawText(15,70+row*38,['概览','项目','文件','最近使用','收藏','设置'][row])
        painter.fillRect(150,115+row*35,380 if row%2==0 else 260,9,QColor('#d7e0eb' if index%2==0 else '#344962'))
    painter.end()
    overlay.previews.provider.images[card['id']]=img
    overlay.previews.frameReady.emit(card['id'],'image://windowPreviews/'+card['id']+'/1')
overlay.previews._state='live'; overlay.previews.changed.emit()
QTest.qWait(150)
def items(parent):
    for child in parent.childItems():
        yield child
        yield from items(child)
thumbnails=[item for item in items(overlay.window.rootObject()) if item.objectName()=='windowThumbnail']
assert thumbnails and all(item.property('source').toString().lower().startswith('image://windowpreviews/') for item in thumbnails), [i.property('source').toString() for i in thumbnails]
assert all(item.property('sourceSize').width() == 640 for item in thumbnails)
preview = os.environ.get('RING_SELECTOR_PREVIEW')
if preview: assert overlay.window.grabWindow().save(preview)
overlay.previews._set_status(overlay.previews._epoch, 'permission')
QTest.qWait(80)
notice = overlay.window.rootObject().findChild(QObject, 'windowSelectorPermissionNotice')
grid = overlay.window.rootObject().findChild(QObject, 'windowSelectorGrid')
assert notice.isVisible() and grid.y() + grid.height() < notice.y()
assert notice.y() + notice.height() < overlay.window.height()
label = overlay.window.rootObject().findChild(QObject, 'windowSelectorPermissionText')
assert '未开启屏幕录制权限' in label.property('text')
permission_preview = os.environ.get('RING_SELECTOR_PERMISSION_PREVIEW')
if permission_preview: assert overlay.window.grabWindow().save(permission_preview)
if not native:
    overlay.window.resize(940, 700)
    QTest.qWait(80)
    assert grid.y() + grid.height() < notice.y()
    assert notice.y() + notice.height() < overlay.window.height()
overlay.previews._set_status(overlay.previews._epoch, 'live')
QTest.qWait(30)
assert not notice.isVisible()
# Real pointer events select cards; animated layout and page changes under a
# stationary pointer must not override subsequent gesture navigation.
def card_point(index):
    card = next(item for item in items(overlay.window.rootObject())
                if item.objectName() == 'windowPreviewCard' and item.parentItem().property('index') == index)
    return card.mapToScene(QPointF(card.width()/2, card.height()/2)).toPoint()
def move_pointer(point):
    if native:
        position = overlay.window.mapToGlobal(point)
        event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved,
            (position.x(), position.y()), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        QTest.qWait(40)
    else:
        QTest.mouseMove(overlay.window, point)
def click_pointer(point, count=1):
    if native:
        position = overlay.window.mapToGlobal(point)
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            event = Quartz.CGEventCreateMouseEvent(None, kind,
                (position.x(), position.y()), Quartz.kCGMouseButtonLeft)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, count)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            QTest.qWait(10)
    elif count == 2:
        QTest.mouseDClick(overlay.window, Qt.LeftButton, pos=point)
        QTest.mouseRelease(overlay.window, Qt.LeftButton, pos=point)
    else:
        QTest.mouseClick(overlay.window, Qt.LeftButton, pos=point)
point = card_point(0)
move_pointer(QPoint(10, 10))
move_pointer(point)
QTest.qWait(40)
assert s.selected == 0
s.pick(1)
QTest.qWait(200)
assert s.selected == 1, 'Stationary mouse must not reclaim gesture selection'
move_pointer(point + QPoint(3, 0))
QTest.qWait(30)
assert s.selected == 0
s.back()
QTest.qWait(180)
assert s.atApps and s.selected == 1
click_pointer(card_point(1))
QTest.qWait(180)
assert not s.atApps and not activations
if native:
    assert ax.front() == before, (before, ax.front())
click_pointer(card_point(1), count=2)
QTest.qWait(80)
assert activations == ['8']
QTest.qWait(50)
assert not overlay.window.isVisible()
if native:
    assert ax.front() == before, (before, ax.front())
    panel = overlay._panel
    assert not panel.isVisible() and overlay._mouse_monitor is None
    # A second session reuses the panel without losing focus, geometry or
    # creating extra native listeners; closing blank space still cancels.
    s._set_snapshot(dict(token='fixture2', bounds=[0,0,800,800], cards=fixture_cards))
    s._phase = 'ready'
    s._selected = 1
    overlay.show()
    QTest.qWait(200)
    assert overlay._panel is panel and panel.isVisible()
    assert overlay._mouse_monitor is not None and ax.front() == before
    assert panel.frame().size.width == overlay.window.width()
    assert panel.frame().size.height == overlay.window.height()
    click_pointer(QPoint(10, 10))
    QTest.qWait(60)
    assert not panel.isVisible() and overlay._mouse_monitor is None
    assert ax.front() == before
    Quartz.CGWarpMouseCursorPosition(original_pointer)
    if original: original.activateWithOptions_(0)
else:
    assert app.focusWidget() is before
assert not any('ReferenceError' in m or 'TypeError' in m or 'Error:' in m for m in messages), messages
overlay.close(); editor.close(); s.close()
'''


def test_qml_cards_selection_layout_and_nonactivating_flags(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
               PROXIMIC_STARTUP_PROBE="1", PROXIMIC_DATA_HOME=str(tmp_path))
    run = subprocess.run([sys.executable, "-c", OVERLAY_SCRIPT], env=env,
                         capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("PROXIMIC_TEST_WINDOW_SELECTOR") != "1",
                    reason="opt-in native overlay focus check")
def test_cocoa_selector_keeps_other_app_frontmost(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="cocoa", RING_NATIVE_OVERLAY="1",
               PROXIMIC_STARTUP_PROBE="1", PROXIMIC_DATA_HOME=str(tmp_path))
    run = subprocess.run([sys.executable, "-c", OVERLAY_SCRIPT], env=env,
                         capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stdout + run.stderr
