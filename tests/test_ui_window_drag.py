"""Background presses can move the window without taking over controls."""
import sys

import pytest

from test_inline_ui import inline_ui
from test_ui_shell import visual_child
from test_ui_voice_page import seed_history


@pytest.mark.skipif(sys.platform != 'darwin', reason='Unified macOS window chrome')
def test_main_window_press_does_not_wrap_visual_children(inline_ui, monkeypatch):
    from PySide6.QtCore import QObject, QPoint, Qt
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    root = inline_ui[3]
    root.show()
    QTest.qWait(100)
    area = root.findChild(QObject, 'mainWindowDragArea')
    nav = visual_child(root.contentItem(), 'mainNav0')
    point = nav.mapToScene(nav.boundingRect().center()).toPoint()
    calls = []
    monkeypatch.setattr(QQuickItem, 'childItems', lambda self: calls.append(self) or [])
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, QPoint(450, 10))
    assert area.property('pressed')
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, QPoint(450, 10))
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point)
    assert root.property('currentPage') == 0
    assert calls == []


@pytest.mark.skipif(sys.platform != "darwin", reason="Unified macOS window chrome")
@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_background_drag_routes_around_controls_and_preserves_scrolling(inline_ui, size):
    from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QMetaObject
    from PySide6.QtTest import QTest

    controller, bridge, _, root, _ = inline_ui
    seed_history(controller)
    root.resize(*size)
    root.show()
    QTest.qWait(100)
    before = list(bridge.messages)

    def descendants(target):
        for child in target.childItems():
            yield child
            yield from descendants(child)

    def background_pressed():
        return any(target.property("pressed") for target in descendants(root.contentItem())
                   if target.metaObject().className().startswith("UiWindowDragArea"))

    def item(name):
        return root.findChild(QObject, name) or visual_child(root.contentItem(), name)

    def position(name, local=None):
        target = item(name)
        local = local or QPointF(target.width() / 2, target.height() / 2)
        return target.mapToScene(local).toPoint()

    def press(point, background):
        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
        assert background_pressed() == background
        QTest.mouseMove(root, point + QPoint(18, 8))
        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point + QPoint(18, 8))
        QTest.qWait(30)
        assert not background_pressed()

    root.setProperty("currentPage", 0)
    QTest.qWait(60)
    for point in (QPoint(450, 10), position("mythlinkLogo"), QPoint(90, 540),
                  position("mainPageHeader"), position("homePage", QPointF(15, 15))):
        press(point, True)

    for page, name in [(1, "voiceStatusTitle"), (2, "gestureMappingPanel"),
                       (3, "touchpadStatusTitle")]:
        root.setProperty("currentPage", page)
        QTest.qWait(60)
        press(position(name, QPointF(15, 15)), True)

    root.setProperty("currentPage", 1)
    QMetaObject.invokeMethod(item("mainPageScroll"), "scrollToHistory")
    QTest.qWait(100)
    press(position("voiceHistoryCard", QPointF(7, 7)), True)
    first_text = min((target for target in descendants(item("voiceHistoryList"))
                      if target.objectName() == "voiceHistoryPrimaryText"),
                     key=lambda target: target.mapToScene(QPointF()).y())
    press(first_text.mapToScene(QPointF(15, 8)).toPoint(), True)
    press(position("voiceHistorySearch"), False)
    press(position("voiceHistoryModeFilter"), False)
    QTest.qWait(180)
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(220)
    press(position("mainNav0"), False)
    assert root.property("currentPage") == 0
    QTest.qWait(200)

    # Mouse/trackpad wheel scrolling must still reach the ScrollView beneath
    # a background drag area; the control must not swallow wheel events.
    root.resize(940, 700)  # Wheel movement requires a viewport smaller than the content.
    root.setProperty("currentPage", 1)
    flick = item("mainPageScroll").property("contentItem")
    flick.setProperty("contentY", 0)
    QTest.qWait(60)
    wheel_position = position("voiceInputCard", QPointF(15, 15))
    QTest.mouseMove(root, wheel_position)
    QTest.qWait(30)
    for _ in range(2):
        QTest.wheelEvent(root, wheel_position, QPoint(0, -120))
        QTest.qWait(30)
    QTest.qWait(180)
    assert flick.property("contentY") > 0
    assert bridge.messages == before
