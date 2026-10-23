"""Icon actions stay centered and keep the same hover appearance over time."""
import pytest
from PySide6.QtCore import QObject, QPointF, QPoint, Qt, QMetaObject
from PySide6.QtTest import QTest
from test_inline_ui import inline_ui


@pytest.mark.parametrize('size', [(1200, 780), (940, 700)])
def test_footer_and_close_icons_are_centered_with_stable_hover(inline_ui, size):
    controller, bridge, _, root, event = inline_ui
    event(phase='idle', ready=True)
    root.resize(*size); root.show(); root.requestActivate(); QTest.qWait(200)
    before = list(bridge.messages)
    assert root.findChild(QObject, 'contactButton') is None
    assert root.findChild(QObject, 'contactDialog') is None
    bar = root.findChild(QObject, 'sidebarUtilityBar')
    buttons = [root.findChild(QObject, name) for name in ('sidebarAccountButton', 'runtimeSettingsButton', 'helpButton')]
    first = buttons[0].mapToItem(bar, QPointF()).x()
    last = buttons[-1].mapToItem(bar, QPointF(buttons[-1].width(), 0)).x()
    assert bar.height() == 44
    assert first == 6
    assert first == pytest.approx(bar.width() - last)
    assert all(button.width() == 32 and button.height() == 44 for button in buttons)
    assert buttons[1].mapToItem(bar, QPointF()).x() == bar.width() - 70
    assert buttons[2].mapToItem(bar, QPointF()).x() == bar.width() - 38

    def check_hover(button):
        icon = button.findChild(QObject, button.objectName() + 'Icon')
        center = icon.mapToItem(button, QPointF(icon.width()/2, icon.height()/2))
        assert center.x() == pytest.approx(button.width()/2)
        assert center.y() == pytest.approx(button.height()/2)
        point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
        QTest.mouseMove(root, point)
        background = button.property('background')
        for delay in (20, 300, 700):
            QTest.qWait(delay)
            assert button.property('hovered')
            assert background.property('color').name() == '#f6f7fd'
            assert button.property('opacity') == 1
        QTest.mouseMove(root, QPoint(400, 100)); QTest.qWait(30)
        assert not button.property('hovered')
        assert background.property('color').alpha() == 0

    for button in buttons:
        check_hover(button)
    QMetaObject.invokeMethod(buttons[1], 'click'); QTest.qWait(200)
    close = root.findChild(QObject, 'settingsApplyButton')
    check_hover(close)
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, close.mapToScene(QPointF(close.width()/2, close.height()/2)).toPoint())
    QTest.qWait(180)
    assert not root.findChild(QObject, 'runtimeSettingsDialog').property('visible')
    assert bridge.messages == before
