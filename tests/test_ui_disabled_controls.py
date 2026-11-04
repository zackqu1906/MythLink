"""Disabled controls have no pointer response, including disable-while-hovered."""
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QUrl, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QSignalSpy, QTest

from test_inline_ui import inline_ui


@pytest.mark.parametrize("inherited", [False, True])
def test_disabled_controls_clear_hover_and_ignore_pointer(inline_ui, tmp_path, inherited):
    import proximic_ring.ui.controller as module
    qml_dir = Path(module.__file__).parent / "qml"
    source = f'''import QtQuick
import QtQuick.Controls
import "{qml_dir.as_uri()}" as UI
ApplicationWindow {{
    visible: true; width: 820; height: 450
    Grid {{
        objectName: "controls"; x: 20; y: 20; columns: 4; spacing: 18
        UI.FigmaAction {{ objectName: "plain"; text: "按钮"; width: 170 }}
        UI.FigmaAction {{ objectName: "primary"; primary: true; text: "主要按钮"; width: 170 }}
        UI.FigmaAction {{ objectName: "quiet"; quiet: true; symbol: "settings"; width: 170 }}
        UI.FigmaEnableControl {{ objectName: "enable"; width: 170 }}
        UI.FigmaNavItem {{ objectName: "nav"; text: "导航"; width: 170 }}
        UI.FigmaSwitch {{ objectName: "switch" }}
        UI.PreferenceSwitch {{ objectName: "preference" }}
        UI.UiAction {{ objectName: "legacy"; text: "按钮"; width: 170 }}
        UI.FigmaSelect {{ objectName: "select"; model: ["选项"]; readonly property bool popupVisible: popup.visible }}
        UI.FigmaStepper {{ objectName: "stepper"; value: 5; from: 0; to: 10 }}
        UI.VoiceHistoryAction {{ objectName: "history" }}
    }}
}}'''
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
    engine.loadData(source.encode(), QUrl.fromLocalFile(str(tmp_path / "hover.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    container = window.findChild(QObject, "controls")
    window.requestActivate(); QTest.qWait(100)
    try:
        for name in ("plain", "primary", "quiet", "enable", "nav", "switch",
                     "preference", "legacy", "select", "stepper", "history"):
            control = window.findChild(QObject, name)
            QTest.mouseMove(window, QPoint(810, 440)); QTest.qWait(120)
            background = control.property("background")
            idle = background.property("color") if background is not None else None
            point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
            QTest.mouseMove(window, point); QTest.qWait(120)
            assert control.property("hovered"), name
            target = container if inherited else control
            target.setProperty("enabled", False)
            # Qt can retain its read-only hovered flag until the next hover
            # event; disabled controls must ignore it when painting or acting.
            assert not control.property("hoverEnabled"), name
            if name not in ("legacy", "switch", "preference"):
                assert background.property("color") == idle, name
            if name in ("switch", "preference"):
                assert "disabled" in control.property("stateAsset"), name
            clicked = QSignalSpy(control.clicked) if hasattr(control, "clicked") else None
            QTest.mouseMove(window, QPoint(810, 440))
            QTest.mouseMove(window, point); QTest.qWait(120)
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            assert not control.property("hoverEnabled"), name
            if name != "stepper":
                assert not control.property("down"), name
            if name not in ("legacy", "switch", "preference"):
                assert background.property("color") == idle, name
            if clicked is not None:
                assert clicked.count() == 0, name
            if name == "select":
                assert not control.property("popupVisible")
            if name == "stepper":
                assert control.property("value") == 5
            target.setProperty("enabled", True)
        assert not warnings
    finally:
        window.close()
        engine.deleteLater()
