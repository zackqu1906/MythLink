"""The Figma presentation must preserve controller configuration and input state."""
import os
from pathlib import Path

import pytest
from test_inline_ui import inline_ui
from test_ui_shell import visual_child
from test_ui_voice_page import seed_history


@pytest.fixture(autouse=True)
def isolate_presence_probe(monkeypatch):
    # UI snapshots must not start the native lock/password helper.
    from proximic_ring.ui.proximity.proximity_controller import ProximityController
    monkeypatch.setattr(ProximityController, "_launch", lambda self, command, args=(): None)


def click(root, name):
    from PySide6.QtCore import QMetaObject, QObject
    item = visual_child(root.contentItem(), name) or root.findChild(QObject, name)
    assert item is not None, name
    QMetaObject.invokeMethod(item, "click")


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_redesigned_routes_preserve_configuration(inline_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPointF, QMetaObject
    from PySide6.QtTest import QTest
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    seed_history(controller)
    root.resize(*size)
    root.setProperty("topPadding", 28)  # Native macOS traffic-light safe area.
    root.show()
    QTest.qWait(100)
    settings = {k: controller._settings.value(k) for k in controller._settings.allKeys()}
    bindings = controller.gestureBindings
    recognition = controller.recognitionEnabled
    out = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    for index, name in [(0,"homePage"),(1,"mainPageScroll"),(4,"mainPageScroll"),(3,"touchpadPage"),(5,"lockPage")]:
        click(root, f"mainNav{index}")
        QTest.qWait(120)
        assert root.property("currentPage") == index
        page = root.findChild(QObject, name)
        assert page.property("visible")
        pos = page.mapToScene(QPointF())
        assert pos.x() >= 220
        assert pos.x() + page.width() <= root.width()
        assert pos.y() + page.height() <= root.height()
        assert root.grabWindow().save(str(out / f"page-{index}-{size[0]}.png"))
    assert controller.gestureBindings == bindings
    assert controller.recognitionEnabled == recognition
    assert {k: controller._settings.value(k) for k in controller._settings.allKeys()} == settings
    click(root, "mainNav0")
    click(root, "homeAllHistoryButton")
    assert root.property("currentPage") == 4
    assert root.findChild(QObject, "mainPageScroll").property("historyOnly")
    click(root, "mainNav1")
    for word in ["觅思", "Mythlink", "思源黑体", "语音交互", "OpenTelemetry", "张玮"]:
        controller.hotwords.add(word)
    click(root, "voiceHotwordLibraryButton")
    QTest.qWait(150)
    dialog = root.findChild(QObject, "hotwordDialog")
    assert dialog.property("visible")
    assert root.grabWindow().save(str(out / f"hotwords-{size[0]}.png"))
    QMetaObject.invokeMethod(dialog, "close")
    click(root, "runtimeSettingsButton")
    QTest.qWait(150)
    assert root.grabWindow().save(str(out / f"settings-{size[0]}.png"))
    QMetaObject.invokeMethod(root.findChild(QObject,"runtimeSettingsDialog"), "close")
    click(root,"mainNav5")
    click(root,"lockAdvancedButton")
    QTest.qWait(100)
    assert root.grabWindow().save(str(out / f"lock-advanced-{size[0]}.png"))
    QMetaObject.invokeMethod(root.findChild(QObject,"lockAdvancedDialog"),"close")
    click(root, "mainNav0")
    click(root, "accountEntryButton")
    QTest.qWait(150)
    assert root.grabWindow().save(str(out / f"account-{size[0]}.png"))
    QMetaObject.invokeMethod(root.findChild(QObject, "runtimeSettingsDialog"), "close")
    QMetaObject.invokeMethod(root.findChild(QObject, "tutorialPreviewDialog"), "open")
    QTest.qWait(150)
    assert root.grabWindow().save(str(out / f"tutorial-{size[0]}.png"))
    QMetaObject.invokeMethod(root.findChild(QObject, "tutorialPreviewDialog"), "close")
    assert controller.gestureBindings == bindings
    assert controller.recognitionEnabled == recognition


def test_history_filters_and_row_actions_remain_available(inline_ui):
    from PySide6.QtCore import QObject, QMetaObject, Q_ARG
    from PySide6.QtTest import QTest
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    seed_history(controller)
    root.show()
    click(root,"mainNav4")
    QTest.qWait(100)
    model = controller.filteredVoiceHistoryModel
    combo = root.findChild(QObject,"voiceHistoryModeFilter")
    combo.setProperty("currentIndex", 3)
    QMetaObject.invokeMethod(combo,"activated",Q_ARG(int,3))
    assert model.modeFilter == "edit"
    click(root,"voiceHistoryResetFilters")
    assert model.modeFilter == "all"
    assert root.findChild(QObject,"voiceTrialEditor") is not None


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_lock_preparation_selects_and_saves_unlock_gesture(inline_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPointF, Qt
    from PySide6.QtTest import QTest

    controller, _, _, root, event = inline_ui
    event(phase="idle", ready=True)
    root.resize(*size); root.show()
    click(root, "mainNav5"); QTest.qWait(100)
    choice = root.findChild(QObject, "lockUnlockGestureSelect")
    preparation = root.findChild(QObject, "lockPreparationCard")
    assert choice.property("currentValue") == "snap"
    assert choice.property("enabled")  # Can prepare the setting before connecting Ring.
    point = choice.mapToItem(preparation, QPointF())
    assert 0 <= point.y() <= preparation.height() - choice.height()
    bindings = controller.gestureBindings
    before = {k: controller._settings.value(k) for k in controller._settings.allKeys()}
    # Change through the real combo's keyboard path, including popup activation.
    choice.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(root, Qt.Key_Space)
    QTest.keyClick(root, Qt.Key_Home)
    QTest.keyClick(root, Qt.Key_Return)
    QTest.qWait(30)
    assert choice.property("currentValue") == "tap"
    assert controller.proximity.unlockGesture == "tap"
    assert root.findChild(QObject, "lockCurrentGesture").property("text") == controller.proximity.unlockGestureLabel
    assert controller.gestureBindings == bindings
    after = {k: controller._settings.value(k) for k in controller._settings.allKeys()}
    assert after == dict(before, **{"proximity/unlockGesture": "tap"})
    controller.proximity.unlockGesture = "clench"
    QTest.qWait(30)
    assert choice.property("currentValue") == "clench"
    assert root.findChild(QObject, "lockCurrentGesture").property("text") == "握拳"
    controller.proximity.copyLockScreenMessage()
    from PySide6.QtGui import QGuiApplication
    assert "握拳" in QGuiApplication.clipboard().text()
    out = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(out / f"lock-gesture-{size[0]}.png"))


def test_figma_pointer_focus_and_disabled_states(inline_ui):
    from PySide6.QtCore import QObject, QPointF, QPoint, Qt
    from PySide6.QtTest import QTest
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    root.show()
    click(root, "mainNav3")
    click(root, "pointerModeButton")
    QTest.qWait(100)
    control = root.findChild(QObject, "touchpadInvertSwitch")
    before = controller.touchpad.invertY
    position = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
    QTest.mouseMove(root, position, 20)
    assert control.property("stateAsset") == "off-hover"
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, position)
    assert control.property("stateAsset") == "off-pressed"
    QTest.mouseMove(root, position + QPoint(-100, -100), 20)
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, position + QPoint(-100, -100))
    control.forceActiveFocus(Qt.TabFocusReason)
    assert control.property("stateAsset") == "off-focus"
    control.setProperty("enabled", False)
    assert control.property("stateAsset") == "disabled"
    assert controller.touchpad.invertY == before
    assert not bridge.messages or bridge.messages[-1]["type"] != "commit"


@pytest.mark.parametrize("saved_enabled", [False, True])
def test_lock_control_disables_on_manual_disconnect_and_restores_saved_choice(inline_ui, monkeypatch, tmp_path, saved_enabled):
    import threading
    from types import SimpleNamespace
    from PySide6.QtCore import QObject, QPointF, Qt
    from PySide6.QtTest import QSignalSpy, QTest

    controller, _, _, root, event = inline_ui
    event(phase="idle", ready=True)
    proximity = controller.proximity
    proximity._enabled = saved_enabled
    controller._settings.setValue("proximity/enabled", saved_enabled)
    proximity.changed.emit()
    root.show(); click(root, "mainNav5"); QTest.qWait(100)
    control = root.findChild(QObject, "lockPageEnabled")
    clicks = QSignalSpy(control.clicked)

    def assert_disabled():
        assert not control.property("enabled")
        assert control.property("opacity") == pytest.approx(0.45)
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(20)
        assert clicks.count() == 0
        assert proximity.enabled == saved_enabled
        assert controller._settings.value("proximity/enabled", type=bool) == saved_enabled

    assert_disabled()
    controller._selector = "a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3"
    controller._runtime_active = True
    controller._apply_runtime_connected()
    assert control.property("enabled") and control.property("opacity") == 1
    assert control.property("checked") == saved_enabled
    # Connection transitions must disable the control before disconnection ends.
    controller._busy = True; controller.busyChanged.emit()
    assert_disabled()
    controller._busy = False; controller.busyChanged.emit()
    assert control.property("enabled")
    monkeypatch.setattr(controller, "_worker", SimpleNamespace(is_alive=lambda: True))
    controller.disconnectDevice()
    assert not controller.connected
    assert_disabled()
    out = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(out / f"lock-manually-disconnected-{saved_enabled}.png"))
    # A confirmed new connection restores availability without changing intent.
    controller._disconnect_event = threading.Event()
    controller._proximity_reconnect_cancelled = False
    controller._busy = False; controller.busyChanged.emit()
    controller._apply_runtime_connected()
    assert control.property("enabled") and control.property("opacity") == 1
    assert control.property("checked") == saved_enabled
    assert controller._settings.value("proximity/enabled", type=bool) == saved_enabled


def test_tutorial_demo_is_not_device_completion(inline_ui, tmp_path):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    root.show()
    tutorial = root.findChild(QObject, "tutorialPreviewDialog")
    completed = []
    controller.voicePractice.completed.connect(completed.append)
    QMetaObject.invokeMethod(tutorial, "open")
    QTest.qWait(150)
    click(root, "tutorialPrepareButton")
    click(root, "tutorialDemoButton")
    out = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    for step in (2, 3, 4):
        assert tutorial.property("step") == step
        assert not controller.voicePractice.active
        assert not root.findChild(QObject, "tutorialNextButton").property("enabled")
        click(root, "tutorialExerciseButton")
        QTest.qWait(100)
        assert tutorial.property("stepDone")
        assert root.grabWindow().save(str(out / f"tutorial-step-{step}.png"))
        click(root, "tutorialNextButton")
    assert tutorial.property("step") == 5
    assert not completed
    QMetaObject.invokeMethod(tutorial, "close")


def test_voice_practice_ui_never_sends_to_ime_and_leaving_cancels(inline_ui, monkeypatch):
    import numpy as np
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    from proximic_ring.ui.voice.practice_controller import PracticeConfiguration
    from proximic_ring.asr.factory import ASRBackendSettings
    from proximic_ring.text_processing.model import LLMSettings
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    root.show()
    click(root, "mainNav1")
    QTest.qWait(100)
    practice = controller.voicePractice
    config = PracticeConfiguration("ring", "", True, "volcengine", ASRBackendSettings(), LLMSettings(), controller._gesture_bindings)
    monkeypatch.setattr(practice, "_configuration", lambda: config)
    monkeypatch.setattr(practice, "_recognize", lambda token, *args: practice._result.emit(token, "dictation", "仅在练习框内", ""))
    before = list(bridge.messages)
    click(root, "voicePracticeStartButton")
    assert practice.recording
    practice.consume_audio(np.full(8000, .1, dtype=np.float32))
    click(root, "voicePracticeStartButton")
    QTest.qWait(100)
    assert root.findChild(QObject, "voiceTrialEditor").property("text") == "仅在练习框内"
    assert bridge.messages == before
    click(root, "mainNav4")
    assert not practice.active and not practice.processing
    assert not practice.audioAvailable
