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


def test_figma_pointer_focus_and_disabled_states(inline_ui):
    from PySide6.QtCore import QObject, QPointF, QPoint, Qt
    from PySide6.QtTest import QTest
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    root.show()
    click(root, "mainNav3")
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
