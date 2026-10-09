import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _isolate_app_data(tmp_path, monkeypatch):
    """UI tests must never create synthetic Interactions in the real dataset."""
    monkeypatch.setenv("PROXIMIC_DATA_HOME", str(tmp_path / "app-data"))
    pytest.importorskip("PySide6")
    from PySide6 import QtCore
    import proximic_ring.ui.controller as controller_module

    settings_class = QtCore.QSettings

    class IsolatedSettings(settings_class):
        def __init__(self, *args, **kwargs):
            if args[:2] == ("ProxiMic", "ProxiMic Voice"):
                # The organization/application overload still uses NativeFormat
                # on macOS despite setDefaultFormat(). Use an explicit file.
                super().__init__(str(tmp_path / "settings.ini"), settings_class.IniFormat)
            else:
                super().__init__(*args, **kwargs)

    monkeypatch.setattr(QtCore, "QSettings", IsolatedSettings)
    monkeypatch.setattr(controller_module, "QSettings", IsolatedSettings)


def test_edit_result_summary_is_short_and_user_facing():
    pytest.importorskip("PySide6")

    from proximic_ring.ui.controller import AppController

    assert AppController._edit_result_summary("今天下雨。", "今天下大雨。") == '已添加：“大”'
    assert AppController._edit_result_summary("请删除这个词。", "请删除词。") == '已删除：“这个”'
    assert AppController._edit_result_summary("原文", "") == "已清空当前文本"


def test_compute_device_discovery_lists_cuda(monkeypatch):
    pytest.importorskip("PySide6")
    import torch

    from proximic_ring.ui.controller import AppController

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 2)
    monkeypatch.setattr(
        torch.cuda,
        "get_device_name",
        lambda index: ["Example GPU A", "Example GPU B"][index],
    )

    devices, message = AppController._detect_compute_devices()

    assert [item["value"] for item in devices] == ["cpu", "cuda:0", "cuda:1"]
    assert devices[1]["label"] == "GPU 1 · Example GPU A"
    assert "2 张" in message

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    devices, message = AppController._detect_compute_devices("Example GPU A")
    assert devices == [{"label": "CPU（兼容性最佳）", "value": "cpu"}]
    assert ("macOS" if sys.platform == "darwin" else "CPU 版 PyTorch") in message

    monkeypatch.setattr(sys, "platform", "darwin")
    devices, message = AppController._detect_compute_devices()
    assert devices == [{"label": "CPU（兼容性最佳）", "value": "cpu"}]
    assert "macOS" in message
    assert AppController._detect_nvidia_gpu_name() == ""


def test_macos_desktop_output_migrates_old_forced_off_setting(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QSettings

    from proximic_ring.ui.controller import AppController

    _app = QCoreApplication.instance() or QCoreApplication(["mac-output-migration"])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    settings = QSettings("ProxiMic", "ProxiMic Voice")
    settings.setValue("input/desktopOutput", False)
    settings.remove("input/macosDesktopOutputMigrated")
    settings.sync()
    monkeypatch.setattr(sys, "platform", "darwin")

    controller = AppController(inline_input_enabled=False)
    assert controller.desktopOutputEnabled is True
    assert controller._settings.value("input/macosDesktopOutputMigrated") is True
    controller.desktopOutputEnabled = False
    controller._text_processing_worker.close(wait=True)

    restarted = AppController(inline_input_enabled=False)
    assert restarted.desktopOutputEnabled is False
    restarted._text_processing_worker.close(wait=True)


@pytest.mark.parametrize(
    ("provider", "saved_model", "expected_model"),
    [
        ("volcengine", "deepseek-v4-flash-260425", "deepseek-v4-1-flash-260910"),
        ("volcengine", "deepseek-v4-1-flash-260910", "deepseek-v4-1-flash-260910"),
        ("volcengine", "ep-custom-model", "ep-custom-model"),
        ("volcengine", "doubao-seed-2-0-lite-260215", "doubao-seed-2-0-lite-260215"),
        ("openai", "deepseek-v4-flash-260425", "deepseek-v4-flash-260425"),
    ],
)
def test_expired_ark_deepseek_model_migrates_on_startup(provider, saved_model, expected_model):
    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtWidgets import QApplication
    from proximic_ring.ui.controller import AppController

    _app = QCoreApplication.instance()
    if _app is not None and not isinstance(_app, QApplication):
        pytest.skip("Run GUI controller tests separately from QCoreApplication tests")
    _app = _app or QApplication(["llm-model-migration"])
    settings = QSettings("ProxiMic", "ProxiMic Voice")
    settings.setValue("llm/provider", provider)
    settings.setValue("llm/model", saved_model)
    settings.setValue("llm/baseUrl", "https://ark.cn-beijing.volces.com/api/v3")
    settings.setValue("llm/apiKey", "test-only-key")
    settings.sync()

    controller = AppController(inline_input_enabled=False)
    try:
        assert controller.llmModel == expected_model
        assert controller._settings.value("llm/model") == expected_model
        assert controller.llmProvider == provider
        assert controller.llmBaseUrl == "https://ark.cn-beijing.volces.com/api/v3"
        assert controller.llmApiKey == "test-only-key"
    finally:
        controller._text_processing_worker.close(wait=True)


def test_macos_edit_does_not_report_success_without_verified_replacement(
    tmp_path, monkeypatch
):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QSettings

    from proximic_ring.desktop_target import DesktopTargetRef, DesktopTextSnapshot
    from proximic_ring.ui.controller import AppController, _EditReview

    _app = QCoreApplication.instance() or QCoreApplication(["mac-edit-verification"])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    controller = AppController(inline_input_enabled=False)
    controller._text_processing_worker.close(wait=True)
    target = DesktopTargetRef(0, 0, "测试编辑器", process_id=4321)

    class TargetThatIgnoresReplacement:
        def __init__(self):
            self.replace_calls = 0

        def replace(self, snapshot, text):
            self.replace_calls += 1

        def capture_text(self, captured_target):
            return DesktopTextSnapshot(captured_target, "仍然是原文")

        def release_selection(self, captured_target):
            return None

    desktop_target = TargetThatIgnoresReplacement()
    controller._desktop_target = desktop_target
    controller._edit_review = _EditReview(
        request_id=999,
        session_id=1,
        instruction="改得正式",
        proposed_text="正式的新文本",
        snapshot=DesktopTextSnapshot(target, "仍然是原文"),
    )
    controller._set_interaction_state("review")

    controller._apply_edit_result()

    assert desktop_target.replace_calls == 2
    assert controller.interactionState == "error"
    assert "修改未应用" in controller.transcriptText
    assert "修改 · 应用失败" in controller.sessionHistoryText
    assert "修改 · 已应用" not in controller.sessionHistoryText


def test_macos_edit_accepts_equivalent_line_endings_and_unicode(
    tmp_path, monkeypatch
):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QSettings

    from proximic_ring.desktop_target import DesktopTargetRef, DesktopTextSnapshot
    from proximic_ring.ui.controller import AppController, _EditReview

    _app = QCoreApplication.instance() or QCoreApplication(["mac-edit-normalization"])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    controller = AppController(inline_input_enabled=False)
    controller._text_processing_worker.close(wait=True)
    target = DesktopTargetRef(0, 0, "测试编辑器", process_id=4321)

    class NormalizingTarget:
        def replace(self, snapshot, text):
            return None

        def capture_text(self, captured_target):
            return DesktopTextSnapshot(captured_target, "第一行\r\nCafe\u0301")

        def release_selection(self, captured_target):
            return None

    controller._desktop_target = NormalizingTarget()
    controller._edit_review = _EditReview(
        request_id=1000,
        session_id=1,
        instruction="整理一下",
        proposed_text="第一行\nCaf\u00e9",
        snapshot=DesktopTextSnapshot(target, "原文"),
    )
    controller._set_interaction_state("review")

    controller._apply_edit_result()

    assert controller.interactionState == "applied"
    assert "修改 · 已应用" in controller.sessionHistoryText


def test_applied_dictation_and_edit_stay_visible_until_undo(
    tmp_path, monkeypatch, request
):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QSettings

    from proximic_ring.desktop_target import DesktopTargetRef, DesktopTextSnapshot
    from proximic_ring.text_processing import TextProcessingResult
    from proximic_ring.ui.controller import AppController, _EditReview

    _app = QCoreApplication.instance() or QCoreApplication(["applied-undo"])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    controller = AppController(inline_input_enabled=False)
    request.addfinalizer(controller._close_voice_history)
    controller._text_processing_worker.close(wait=True)
    monkeypatch.setattr(controller, "_copy_text_to_clipboard", lambda _text: None)
    target = DesktopTargetRef(10, 20, "测试编辑器", process_id=30)

    class FakeDesktopTarget:
        def __init__(self):
            self.current_text = "原始文本"
            self.injected: list[str] = []
            self.undone: list[DesktopTargetRef] = []
            self.replaced: list[str] = []

        def inject(self, captured_target, text):
            assert captured_target == target
            self.injected.append(text)
            self.current_text += text

        def undo(self, captured_target):
            assert captured_target == target
            self.undone.append(captured_target)
            self.current_text = "原始文本"

        def replace(self, snapshot, text):
            assert snapshot.target == target
            assert snapshot.text == self.current_text
            self.current_text = text
            self.replaced.append(text)

        def capture_text(self, captured_target):
            assert captured_target == target
            return DesktopTextSnapshot(target, self.current_text)

        def observe_text(self, captured_target):
            assert captured_target == target
            return DesktopTextSnapshot(target, self.current_text)

        def release_selection(self, captured_target):
            assert captured_target == target

    desktop_target = FakeDesktopTarget()
    controller._desktop_target = desktop_target
    controller._desktop_output = True

    controller._commit_input_text(
        TextProcessingResult(
            request_id=101,
            session_id=11,
            mode="dictation",
            raw_text="一段听写",
            final_text="一段听写。",
            latency_s=0.1,
            used_llm=True,
        ),
        target,
    )

    assert desktop_target.injected == ["一段听写。"]
    assert controller._transcript_active is False
    assert controller.interactionState == "applied"
    assert controller.undoAvailable is True
    assert controller.interactionCanCancel is False

    controller.undoLastApplied()

    assert desktop_target.undone == [target]
    assert desktop_target.replaced == []
    assert controller.undoAvailable is True
    assert controller.interactionState == "applied"
    assert "听写 · 已发送撤销" in controller.sessionHistoryText

    controller._edit_review = _EditReview(
        request_id=102,
        session_id=12,
        instruction="改正式一点",
        proposed_text="正式文本",
        snapshot=DesktopTextSnapshot(target, "原始文本"),
    )
    controller._set_interaction_state("review")

    controller._apply_edit_result()

    assert desktop_target.current_text == "正式文本"
    assert controller._transcript_active is False
    assert controller.interactionState == "applied"
    assert controller.undoAvailable is True
    assert controller.interactionCanCancel is False

    controller.undoLastApplied()

    assert desktop_target.current_text == "原始文本"
    assert desktop_target.replaced == ["正式文本"]
    assert desktop_target.undone == [target, target]
    assert controller.undoAvailable is True
    assert controller.interactionState == "applied"
    assert controller.associationRecommendationVisible is False
    assert "修改 · 已发送撤销" in controller.sessionHistoryText
    controller._close_voice_history()


def test_voice_history_can_reveal_audio_file_and_reject_outside_path(
    tmp_path, monkeypatch
):
    pytest.importorskip("PySide6")

    from proximic_ring.ui import controller as controller_module
    from proximic_ring.ui.controller import (
        _open_data_directory,
        _open_voice_history_location,
        _resolve_voice_history_path,
    )

    audio_path = tmp_path / "voice_history" / "entry" / "utterance.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"RIFF")
    resolved = _resolve_voice_history_path(
        str(audio_path), tmp_path / "voice_history"
    )
    opened: list[list[str]] = []
    monkeypatch.setattr(controller_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        controller_module.subprocess,
        "Popen",
        lambda command: opened.append(list(command)),
    )

    _open_voice_history_location(resolved)

    assert opened == [["open", "-R", str(audio_path.resolve())]]

    data_directory = tmp_path / "dataset" / "user-id"
    data_directory.mkdir(parents=True)
    _open_data_directory(data_directory)
    assert opened[-1] == ["open", "-R", str(data_directory.resolve())]

    outside_path = tmp_path / "outside.wav"
    outside_path.write_bytes(b"RIFF")
    with pytest.raises(ValueError):
        _resolve_voice_history_path(
            str(outside_path), tmp_path / "voice_history"
        )


def test_auto_routing_dispatches_to_dictation_and_edit_with_timing_log(
    tmp_path,
):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QSettings

    from proximic_ring.desktop_target import DesktopTargetRef, DesktopTextSnapshot
    from proximic_ring.text_processing import InputModeRoutingResult
    from proximic_ring.ui.controller import AppController

    _app = QCoreApplication.instance() or QCoreApplication(["routing-test"])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    controller = AppController(inline_input_enabled=False)
    controller._llm_enabled = True
    controller._input_routing_mode = "manual"
    controller._text_processing_worker.close(wait=True)

    routed = []
    submitted = []

    class FakeWorker:
        def submit_routing(self, request):
            routed.append(request)

        def submit(self, request):
            submitted.append(request)

        def close(self, *, wait=False):
            return None

    target = DesktopTargetRef(1, 2, "测试编辑器")

    class FakeDesktopTarget:
        def __init__(self):
            self.injected = []

        def capture_reference(self):
            return target

        def capture_text(self, captured_target):
            assert captured_target == target
            return DesktopTextSnapshot(target, "已有文本。")

        def inject(self, captured_target, text):
            assert captured_target == target
            self.injected.append(text)

        def release_selection(self, _target):
            return None

    desktop_target = FakeDesktopTarget()
    controller._text_processing_worker = FakeWorker()
    controller._desktop_target = desktop_target
    controller._desktop_output = True
    controller.llmEnabled = False
    controller.inputRoutingMode = "auto"

    controller._apply_runtime_update("这是一段要输入的话。", True, "", 701)
    assert len(routed) == 1
    assert len(submitted) == 1
    assert submitted[0].mode == "edit"
    assert routed[0].settings.enabled is True
    assert controller.transcriptMode == ""
    assert controller.transcriptText == "正在判断听写或指令"
    assert "自动路由判断开始" in controller.logText
    controller._apply_input_mode_routed(
        InputModeRoutingResult(
            request_id=routed[0].request_id,
            session_id=701,
            raw_text=routed[0].raw_text,
            mode="dictation",
            latency_s=0.234,
            model_output="dictation",
        )
    )
    assert controller.transcriptMode == ""
    assert desktop_target.injected == ["这是一段要输入的话。"]
    assert controller._dictation_commit_timer.isActive() is False
    assert "自动路由判断完成：听写（耗时 0.234s）" in controller.logText

    controller._apply_runtime_update("把上一句改正式一点", True, "", 702)
    controller._apply_input_mode_routed(
        InputModeRoutingResult(
            request_id=routed[1].request_id,
            session_id=702,
            raw_text=routed[1].raw_text,
            mode="edit",
            latency_s=0.125,
            model_output="edit",
        )
    )
    assert controller.transcriptMode == ""
    assert controller.transcriptText == (
        "正在处理文本 · 指令：把上一句改正式一点"
    )
    assert len(submitted) == 2
    assert submitted[1].mode == "edit"
    assert submitted[1].target_text == "已有文本。"
    assert "自动路由判断完成：编辑指令（耗时 0.125s）" in controller.logText
    assert re.search(
        r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}\] "
        r"\[INFO\] \[runtime\] run=\w+ process=\d+ thread='[^']+' "
        r"\[session=702 route=\d+\] 自动路由判断完成",
        controller.logText,
    )
    controller._cancel_pending_text_processing()


def test_device_scan_keeps_one_snapshot_until_manual_rescan(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QSettings

    from proximic_ring.ui.controller import AppController

    _app = QCoreApplication.instance() or QCoreApplication(["device-scan-test"])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    controller = AppController(inline_input_enabled=False)
    scans = []
    monkeypatch.setattr(controller, "_scan_devices_once", lambda: scans.append(True))

    controller.scanDevices()
    assert scans == [True]

    controller._scan_busy = True
    controller._apply_scan_finished(
        [{"name": "Ringo Test", "identifier": "RING-ID", "rssi": -40}],
        "",
    )
    assert scans == [True]
    assert controller.availableDevices == [
        {"name": "Ringo Test", "identifier": "RING-ID", "rssi": -40}
    ]
    assert controller.scanMessage == "已发现 1 个设备，显示 1 个匹配“Ringo”的设备"

    controller.scanDevices()
    assert scans == [True, True]
    assert controller.availableDevices == []


def test_qml_main_window_loads_without_retired_floating_ui(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QObject, QSettings, QUrl, QEvent
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQml import QQmlApplicationEngine
    import proximic_ring.ui.controller as module
    app = QCoreApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("QML needs QApplication")
    app = app or QApplication(["main-ui", "-platform", "offscreen"])
    monkeypatch.setattr(module, "app_data_root", lambda: tmp_path)
    monkeypatch.setattr(module, "QSettings", lambda *_: QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    controller = module.AppController(inline_input_enabled=False)
    controller._text_processing_worker.close(wait=True)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    engine.rootContext().setContextProperty("appController", controller)
    engine.load(QUrl.fromLocalFile(str(Path(module.__file__).parent / "qml/Main.qml")))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    for name in ("transcriptOverlay", "appliedActionOverlay", "appliedOverlayStyleCombo", "appliedOverlayDurationSlider"):
        assert root.findChild(QObject, name) is None
    for name in ("voiceHistoryList", "runtimeSettingsDialog", "associationCenterWindow"):
        assert root.findChild(QObject, name) is not None
    mode_label = root.findChild(QObject, "ringGestureModeLabel")
    assert "输入模式" in mode_label.property("text")
    controller._connected = controller._runtime_active = True
    controller.ringGestures.filter(SimpleNamespace(name="middle-pinch"), False, controller._disconnect_event)
    QCoreApplication.processEvents()
    assert "操作模式" in mode_label.property("text")
    root.hide()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    controller._close_voice_history()
    assert not warnings
