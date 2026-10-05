"""Service forms preserve saved profiles and the running voice interaction."""
from pathlib import Path
import os

import pytest

from test_inline_ui import inline_ui


def item(root, name):
    from PySide6.QtCore import QObject
    result = root.findChild(QObject, name)
    assert result is not None, name
    return result


def click(root, name):
    from PySide6.QtCore import QMetaObject
    assert QMetaObject.invokeMethod(item(root, name), "click")


def open_page(root, page):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtTest import QTest
    root.setProperty("currentPage", 0)
    root.show()
    assert QMetaObject.invokeMethod(root, "showSettings", Q_ARG("QVariant", page))
    QTest.qWait(160)
    dialog = item(root, "advancedSettingsDialog")
    for _ in range(30):
        if dialog.property("opacity") >= 0.999:
            break
        QTest.qWait(20)
    assert dialog.property("opacity") >= 0.999


def settings(controller):
    return {key: controller._settings.value(key) for key in controller._settings.allKeys()}


def scroll_bottom(root):
    from PySide6.QtTest import QTest
    QTest.qWait(40)  # Allow conditional groups to update the scroll extent.
    flick = item(root, "advancedSettingsScroll").property("contentItem")
    flick.setProperty("contentY", max(0, flick.property("contentHeight") - flick.height()))
    QTest.qWait(40)


def screenshot(root, tmp_path, name):
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / name))


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_service_navigation_preserves_custom_profiles_and_live_voice(inline_ui, tmp_path, size):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest

    controller, bridge, _, root, _ = inline_ui
    controller.asrBackend = "funasr_nano"
    controller.asrHotwords = "Mythlink\n常用人名"
    controller.asrApiKey = "test-only-asr-key"
    controller.llmProvider = "volcengine"
    controller.llmModel = "ep-existing-custom-model"
    controller.llmBaseUrl = "https://example.invalid/api/v3"
    controller.llmApiKey = "test-only-text-key"
    root.resize(*size)
    before, messages, bindings = settings(controller), list(bridge.messages), controller.gestureBindings
    phase = controller.inlineInput.status
    open_page(root, 4)
    assert item(root, "asrHotwordsGroup").property("visible")
    assert not item(root, "asrOnlineGroup").property("visible")
    assert item(root, "asrAdvancedGroup").property("visible")
    screenshot(root, tmp_path, f"speech-service-{size[0]}.png")
    scroll_bottom(root)
    screenshot(root, tmp_path, f"speech-service-advanced-{size[0]}.png")
    assert item(root, "asrPerformanceRow").property("visible")
    click(root, "advancedSettingsDoneButton")
    QTest.qWait(140)
    assert not item(root, "advancedSettingsDialog").property("visible")

    open_page(root, 5)
    assert not item(root, "localTextModelGroup").property("visible")
    assert "ep-existing-custom-model" in item(root, "llmModelCombo").property("displayText")
    assert item(root, "llmApiKeyField").property("displayText") != controller.llmApiKey
    screenshot(root, tmp_path, f"text-service-{size[0]}.png")
    click(root, "showLlmApiKeyButton")
    assert item(root, "llmApiKeyField").property("displayText") == controller.llmApiKey
    click(root, "advancedSettingsDoneButton")
    QTest.qWait(140)
    open_page(root, 5)
    assert item(root, "llmApiKeyField").property("displayText") != controller.llmApiKey
    scroll_bottom(root)
    screenshot(root, tmp_path, f"text-service-advanced-{size[0]}.png")
    for name in ("llmModelField", "advancedSettingsDoneButton"):
        control = item(root, name)
        point = control.mapToScene(QPointF())
        assert 0 <= point.x() <= root.width() - control.width()
        assert 0 <= point.y() <= root.height() - control.height()
    click(root, "advancedSettingsDoneButton")
    QTest.qWait(120)
    assert not item(root, "advancedSettingsDialog").property("visible")
    assert settings(controller) == before
    assert bridge.messages == messages
    assert controller.gestureBindings == bindings
    assert controller.inlineInput.status == phase


@pytest.mark.parametrize("state", ["connected", "busy"])
def test_online_speech_form_retains_connection_locks_and_live_gain(inline_ui, state):
    controller, bridge, _, root, _ = inline_ui
    controller.asrBackend = "volcengine"
    controller.asrHotwords = "保留的本地热词"
    setattr(controller, "_" + state, True)
    getattr(controller, state + "Changed").emit()
    before, messages = settings(controller), list(bridge.messages)
    open_page(root, 4)
    assert item(root, "asrOnlineGroup").property("visible")
    assert not item(root, "asrHotwordsGroup").property("visible")
    for name in ("asrBackendCombo", "asrLanguageCombo", "asrApiKeyField", "showAsrApiKeyButton"):
        assert not item(root, name).property("enabled")
    assert not item(root, "asrPerformanceRow").property("visible")
    assert item(root, "asrGainSlider").property("enabled")
    assert settings(controller) == before and bridge.messages == messages


def test_service_editors_save_on_done_and_escape_without_touching_other_preferences(inline_ui):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    controller, bridge, _, root, _ = inline_ui
    controller.asrBackend = "volcengine"
    controller.llmProvider = "volcengine"
    messages, bindings, phase = list(bridge.messages), controller.gestureBindings, controller.inlineInput.status
    open_page(root, 4)
    before = settings(controller)
    field = item(root, "asrApiKeyField")
    assert field.property("placeholderText") == "填写豆包语音 App Key"
    field.forceActiveFocus(Qt.OtherFocusReason)
    assert not field.property("placeholderText")
    field.setProperty("text", " test-only-new-key ")
    click(root, "advancedSettingsDoneButton")
    QTest.qWait(120)
    assert controller.asrApiKey == "test-only-new-key"
    after = settings(controller)
    assert {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)} == {"asr/volcengineApiKey"}

    open_page(root, 5)
    scroll_bottom(root)
    before = settings(controller)
    field = item(root, "llmModelField")
    field.forceActiveFocus(Qt.OtherFocusReason)
    assert field.property("activeFocus")
    field.setProperty("text", "ep-new-custom-model")
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(140)
    assert not item(root, "advancedSettingsDialog").property("visible")
    assert item(root, "runtimeSettingsDialog").property("visible")
    assert controller.llmModel == "ep-new-custom-model"
    after = settings(controller)
    assert {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)} == {"llm/model"}
    assert controller.gestureBindings == bindings and controller.inlineInput.status == phase
    assert bridge.messages == messages


def test_local_and_existing_online_profiles_display_real_status_without_requests(inline_ui, tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtTest import QTest

    controller, bridge, _, root, _ = inline_ui
    monkeypatch.setattr(controller, "installLocalModel", lambda: pytest.fail("opening settings must not download"))
    monkeypatch.setattr(controller, "warmLocalModel", lambda: pytest.fail("opening settings must not load a model"))
    controller.llmLocalServerPath = str(tmp_path / "missing-server")
    controller.llmLocalModelPath = str(tmp_path / "missing-model")
    open_page(root, 5)
    before, messages = settings(controller), list(bridge.messages)
    assert item(root, "localTextModelGroup").property("visible")
    assert not item(root, "onlineTextModelGroup").property("visible")
    assert item(root, "installLocalModelButton").property("enabled")
    controller._local_model_installing = True
    controller._local_model_install_status = "测试下载：42%"
    controller.localModelInstallationChanged.emit()
    assert not item(root, "installLocalModelButton").property("enabled")
    screenshot(root, tmp_path, "text-service-local-download.png")
    controller._local_model_installing = False
    controller._local_model_install_status = "下载失败：测试错误"
    controller.localModelInstallationChanged.emit()
    assert item(root, "installLocalModelButton").property("enabled")
    assert settings(controller) == before and bridge.messages == messages

    # Legacy compatible services must not be relabelled as Ark or a preset model.
    controller.llmProvider = "openai"
    controller.llmModel = "existing-compatible-model"
    controller.llmBaseUrl = "https://example.invalid/v1"
    before = settings(controller)
    combo = item(root, "llmProviderCombo")
    assert QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, 1))
    QTest.qWait(20)
    assert controller.llmProvider == "openai"
    assert not item(root, "llmModelCombo").property("visible")
    assert item(root, "onlineTextModelGroup").property("title") == "当前在线服务"
    assert settings(controller) == before and bridge.messages == messages
