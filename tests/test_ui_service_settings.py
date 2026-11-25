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
    dialog = item(root, "runtimeSettingsDialog")
    assert item(root, "advancedSettingsPage").property("visible")
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
    flick = item(root, "runtimeSettingsScroll").property("contentItem")
    flick.setProperty("contentY", max(0, flick.property("contentHeight") - flick.height()))
    QTest.qWait(40)


def screenshot(root, tmp_path, name):
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / name))


@pytest.mark.parametrize("inline_ui", [{}, {"llm/provider": "volcengine"}], indirect=True)
def test_ark_defaults_to_pro_and_can_select_flash(inline_ui):
    from PySide6.QtCore import Q_ARG, QMetaObject

    controller, _, _, root, _ = inline_ui
    controller.llmProvider = "volcengine"
    open_page(root, 5)
    combo = item(root, "llmModelCombo")
    assert combo.property("displayText") == "DeepSeek V4 Pro"
    assert controller._llm_settings().model == "deepseek-v4-pro-ga-260813"

    combo.setProperty("currentIndex", 1)
    assert QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, 1))
    assert controller._llm_settings().model == "deepseek-v4-1-flash-260910"
    assert controller._settings.value("llm/model") == controller.llmModel


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
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
    click(root, "settingsApplyButton")
    QTest.qWait(140)
    assert not item(root, "runtimeSettingsDialog").property("visible")

    open_page(root, 5)
    assert not item(root, "localTextModelGroup").property("visible")
    assert "ep-existing-custom-model" in item(root, "llmModelCombo").property("displayText")
    assert item(root, "llmApiKeyField").property("displayText") != controller.llmApiKey
    screenshot(root, tmp_path, f"text-service-{size[0]}.png")
    click(root, "showLlmApiKeyButton")
    assert item(root, "llmApiKeyField").property("displayText") == controller.llmApiKey
    click(root, "settingsApplyButton")
    QTest.qWait(140)
    open_page(root, 5)
    assert item(root, "llmApiKeyField").property("displayText") != controller.llmApiKey
    scroll_bottom(root)
    screenshot(root, tmp_path, f"text-service-advanced-{size[0]}.png")
    for name in ("llmModelField", "settingsApplyButton"):
        control = item(root, name)
        point = control.mapToScene(QPointF())
        assert 0 <= point.x() <= root.width() - control.width()
        assert 0 <= point.y() <= root.height() - control.height()
    click(root, "settingsApplyButton")
    QTest.qWait(120)
    assert not item(root, "runtimeSettingsDialog").property("visible")
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
    controller._voice_runtime_ready = state == "connected"
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


def test_service_editors_save_on_back_and_escape_without_touching_other_preferences(inline_ui):
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
    click(root, "settingsOverviewBackButton")
    QTest.qWait(120)
    assert item(root, "runtimeSettingsDialog").property("opened")
    assert not item(root, "advancedSettingsPage").property("visible")
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
    assert not item(root, "runtimeSettingsDialog").property("visible")
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
    controller.llmProvider = "local"
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


def test_bailian_advanced_settings_use_separate_credentials_and_models(inline_ui):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from proximic_ring.asr.backends.bailian import MESSAGE_MODEL, STREAMING_MODEL

    controller, _, _, root, _ = inline_ui
    controller.inlineInput.reset()
    controller._set_interaction_state("idle")
    controller._selector = "test-ring"
    controller._audio_source = "microphone"
    controller.asrBackend = "volcengine"
    controller.asrApiKey = "seed-test-only"
    controller.asrModel = "existing-seed-profile"
    open_page(root, 4)

    def select(name, index):
        combo = item(root, name)
        combo.setProperty("currentIndex", index)
        assert QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, index))

    select("asrBackendCombo", 3)
    assert controller.asrBackend == "bailian" and controller.asrModel == STREAMING_MODEL
    assert not item(root, "asrApiKeyField").property("visible")
    assert item(root, "bailianApiKeyField").property("text") == ""
    select("bailianModelCombo", 1)
    for name, value in (("bailianApiKeyField", "bailian-test-only"),
                        ("bailianApiHostField", "example.cn-beijing.maas.aliyuncs.com")):
        field = item(root, name)
        assert field.property("visible") and field.property("enabled")
        field.setProperty("text", value)
        assert QMetaObject.invokeMethod(field, "editingFinished")
    assert item(root, "bailianApiKeyField").property("displayText") != "bailian-test-only"
    runtime = controller._runtime_settings()
    args = runtime.to_namespace()
    assert args.asr_model == [f"bailian={MESSAGE_MODEL}"]
    assert args.asr_option == ["bailian.api_key=bailian-test-only",
                               "bailian.api_host=example.cn-beijing.maas.aliyuncs.com"]
    assert "bailian-test-only" not in repr(runtime)
    practice = controller._practice_configuration()
    assert practice.asr.options == {"api_key": controller.bailianApiKey, "api_host": controller.bailianApiHost}
    select("asrBackendCombo", 2)
    assert controller.asrModel == "existing-seed-profile"
    assert controller._runtime_settings().to_namespace().asr_option == ["volcengine.api_key=seed-test-only"]
    select("asrBackendCombo", 3)
    assert controller.asrModel == MESSAGE_MODEL
    assert controller._settings.value("asr/bailianApiKey") == "bailian-test-only"
    assert controller._settings.value("asr/volcengineApiKey") == "seed-test-only"


@pytest.mark.parametrize("inline_ui,initial", [({}, False), ({"llm/qwenThinkingEnabled": True}, True)],
                         indirect=["inline_ui"])
def test_dictation_preferences_follow_selected_text_model(inline_ui, initial):
    from PySide6.QtCore import Q_ARG, QMetaObject, QSettings

    controller, _, _, root, _ = inline_ui
    root.show()
    assert QMetaObject.invokeMethod(root, "showSettings", Q_ARG("QVariant", 0))
    assert QMetaObject.invokeMethod(item(root, "settingsAudioEntry"), "clicked")
    group = item(root, "settingsInputGroup")
    switch = item(root, "qwenThinkingSwitch")
    assert group.property("visible") and "qwen3.8-max" in group.property("description")
    assert switch.property("visible") and switch.property("checked") is initial
    previous_request = controller._voice_llm_settings()
    click(root, "qwenThinkingSwitch")
    assert previous_request.qwen_thinking_enabled is initial
    assert controller._voice_llm_settings().qwen_thinking_enabled is not initial
    controller._settings.sync()
    saved = QSettings(controller._settings.fileName(), QSettings.IniFormat)
    assert saved.value("llm/qwenThinkingEnabled", type=bool) is not initial
    controller.llmProvider = "volcengine"
    assert not switch.property("visible") and controller.llmModel in group.property("description")
    controller.llmProvider = "local"
    assert controller._llm_settings().model in group.property("description") and not switch.property("visible")
    controller.llmProvider = "bailian"
    assert switch.property("visible") and switch.property("checked") is not initial
    assert controller.routedEditVariant == "hybrid_edit_v1"
    assert item(root, "routedEditVariantCombo").property("currentText") == "规则＋模型编辑（默认）"


def test_switch_online_model_services_restores_independent_credentials(inline_ui):
    from PySide6.QtCore import Q_ARG, QMetaObject, QSettings
    from proximic_ring.text_processing.service_profiles import load_service

    controller, _, _, root, _ = inline_ui
    assert controller.llmProvider == "bailian" and controller.llmModel == "qwen3.8-max"
    assert controller.routedEditVariant == "hybrid_edit_v1"
    open_page(root, 5)
    combo = item(root, "llmOnlineServiceCombo")
    assert combo.property("currentIndex") == 0
    controller.llmApiKey = "qwen-test-key"
    controller.llmBaseUrl = "https://qwen.example.test/v1"
    assert QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, 1))
    assert controller.llmProvider == "volcengine" and controller.llmApiKey == ""
    assert controller.llmApiKeyEnv == "ARK_API_KEY"
    controller.llmApiKey = "ark-test-key"
    controller.llmModel = "deepseek-v4-1-flash-260910"
    controller._settings.sync()
    saved = QSettings(controller._settings.fileName(), QSettings.IniFormat)
    assert load_service(saved).model == controller.llmModel
    assert QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, 0))
    assert controller.llmModel == "qwen3.8-max" and controller.llmApiKey == "qwen-test-key"
    assert controller.llmBaseUrl == "https://qwen.example.test/v1" and controller.llmApiKeyEnv == "DASHSCOPE_API_KEY"
    controller.llmProvider = "local"
    assert controller._llm_settings().api_key == ""
    controller.llmProvider = controller.lastOnlineLlmProvider
    assert controller.llmProvider == "bailian" and controller.llmApiKey == "qwen-test-key"
    controller.llmProvider = "volcengine"
    assert controller.llmModel == "deepseek-v4-1-flash-260910" and controller.llmApiKey == "ark-test-key"
