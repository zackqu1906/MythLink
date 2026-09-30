from pathlib import Path
import json

import pytest


def test_single_audio_menu_and_independent_control_mode_remain_editable(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QSettings, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    import proximic_ring.ui.controller as module

    app = QCoreApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("QML needs its own QApplication process")
    app = app or QApplication(["speech-modes", "-platform", "offscreen"])
    monkeypatch.setattr(module, "app_data_root", lambda: tmp_path)
    row = {"name": "USB microphone", "api": "Core Audio", "index": 1}
    row.update(value=json.dumps(row), label="USB microphone")
    monkeypatch.setattr(module, "input_device_choices", lambda: [row])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    monkeypatch.setattr(module, "QSettings", lambda *args: QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    controller = module.AppController(inline_input_enabled=False)
    controller._text_processing_worker.close(wait=True)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.rootContext().setContextProperty("appController", controller)
    engine.load(QUrl.fromLocalFile(str(Path(module.__file__).parent / "qml/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)
    controls = {name: window.findChild(QObject, name) for name in (
        "runtimeSettingsDialog", "audioSourceCombo", "speechControlModeCombo",
        "speechControlModeHint", "stage1SensitivitySlider",
    )}
    assert all(controls.values())
    try:
        window.show()
        QMetaObject.invokeMethod(controls["runtimeSettingsDialog"], "open")
        QTest.qWait(100)
        assert controls["audioSourceCombo"].property("currentIndex") == 0
        assert controls["speechControlModeCombo"].property("currentIndex") == 1
        controller.speechControlMode = "proximity"  # Exercise both modes; new users default to tap.
        QTest.qWait(20)
        assert window.findChild(QObject, "microphoneDeviceCombo") is None
        controller.selectAudioInput(row["value"])
        controller.speechControlMode = "gesture"
        assert controller.setGestureBinding("confirm", 0, "snap")
        QTest.qWait(100)
        assert controls["audioSourceCombo"].property("currentIndex") == 1
        assert controls["audioSourceCombo"].property("displayText") == "USB microphone"
        assert controls["speechControlModeCombo"].property("currentIndex") == 1
        assert "弹指" in controls["speechControlModeHint"].property("text")
        assert not controls["stage1SensitivitySlider"].property("enabled")
        controller._connected = True
        controller.connectedChanged.emit()
        app.processEvents()
        for name in ("audioSourceCombo", "speechControlModeCombo"):
            assert controls[name].property("enabled")
        assert window.grabWindow().save(str(tmp_path / "speech-mode-settings.png"))
        assert not warnings
    finally:
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        controller._close_voice_history()
