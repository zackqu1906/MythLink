"""Check the exit status the main installer receives from the TSF installer."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_inline_ui import inline_ui


@pytest.mark.parametrize("routing", ["auto", "manual_routed_edit"])
def test_running_windows_editor_negotiates_plain_dictation_per_sentence(inline_ui, monkeypatch, routing):
    controller, bridge, _, _, event = inline_ui
    inline = controller._inline_input
    inline.reset()
    inline._windows = True
    monkeypatch.setattr(controller._text_processing_worker, "warmup_router", lambda: None)
    controller.inputRoutingMode = routing
    origin = ("win32:42", 42)
    monkeypatch.setattr(inline, "_foreground_identity", lambda: origin)
    target = (inline._epoch, inline._client_id, "", *origin, (7, 0))

    def start(capabilities):
        from time import monotonic
        inline.pin_start_focus(target, monotonic())
        assert inline.begin()
        event(phase="idle", ready=True, utterance_id="", application=origin[0], selection=[7, 0],
              request_id=inline._startup_focus_request, **capabilities)
        return bridge.messages[-1]

    begin = start({})
    assert begin["type"] == "begin" and begin["selection_revision"] == 7
    assert not begin["automatic_mode"] and not begin["routed_edit_mode"]
    if routing == "auto":
        controller._submit_automatic_voice("兼容听写", 31, None, inline=True)
    else:
        assert inline.update("兼容听写", True, 31)
    assert not any(m.get("text") == "兼容听写" for m in bridge.messages)
    event(phase="listening", ready=True, application=origin[0], selection=[7, 0])
    assert inline.session_basic_dictation and inline.session_routing_mode == "manual"
    assert not inline.error
    assert [m["text"] for m in bridge.messages if m["type"] == "update" and m.get("final")] == ["兼容听写"]
    inline.reset()
    begin = start(dict(automatic_routing_version=3, routed_edit_version=1))
    assert not inline.session_basic_dictation and controller.inputRoutingMode == routing
    assert begin["automatic_mode"] == (routing == "auto")
    assert begin["routed_edit_mode"] == (routing == "manual_routed_edit")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows settings")
def test_windows_settings_exposes_working_repair_for_current_dll(tmp_path, monkeypatch, request):
    from PySide6.QtCore import QObject, QMetaObject, Q_ARG
    from PySide6.QtTest import QTest
    from proximic_ring.voice.ime import windows_install
    from test_ui_device_discovery import wait_until

    (tmp_path / "MythlinkInputSetup.exe").touch()
    monkeypatch.setattr(windows_install, "component_dir", lambda: tmp_path)
    monkeypatch.setattr(windows_install, "version_status", lambda: dict(
        state="current", current=True, text="已是当前版本", detail="随应用更新"))
    repairs = []
    monkeypatch.setattr(windows_install, "repair", lambda: repairs.append(True))
    controller, _, _, window, _ = request.getfixturevalue("inline_ui")
    window.show()
    QMetaObject.invokeMethod(window, "showSettings", Q_ARG("QVariant", 6))
    QTest.qWait(100)
    button = window.findChild(QObject, "settingsInputMethodButton")
    assert button.isVisible() and button.property("text") == "修复输入法"
    assert not window.findChild(QObject, "settingsAccessibilityButton").isVisible()
    controller._inline_input.reset()
    QMetaObject.invokeMethod(button, "click")
    wait_until(lambda: repairs and "已安装并启用" in controller._inline_input.installationMessage)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows TSF registration")
def test_registered_component_is_compared_by_content_and_guidance_is_actionable(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from proximic_ring.voice.ime import windows_install
    from proximic_ring.ui.voice.inline_controller import InlineInputController

    registered = tmp_path / "previous-or-current.dll"
    bundled = tmp_path / "MythlinkTsf.dll"
    bundled.write_bytes(b"new 0.6.43")
    monkeypatch.setattr(windows_install, "component_dir", lambda: tmp_path)
    monkeypatch.setattr(windows_install.winreg, "OpenKey", lambda *args: nullcontext())
    monkeypatch.setattr(windows_install.winreg, "QueryValueEx", lambda *args: (str(registered), 1))
    inline = SimpleNamespace(_windows=True, _set_input_method_version=lambda state: None)
    assert windows_install.version_status()["state"] == "not_installed"
    registered.write_bytes(b"old 0.6.43")
    assert windows_install.version_status()["state"] == "outdated"
    assert "修复输入法" in InlineInputController._routing_version_error(inline, "自动判断")
    registered.write_bytes(bundled.read_bytes())
    assert windows_install.version_status()["current"]
    assert "重新点入文本框" in InlineInputController._routing_version_error(inline, "自动判断")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows TSF installer")
def test_repair_requires_registration_before_enabling_user_profile(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from proximic_ring.voice.ime import windows_install

    (tmp_path / "MythlinkInputSetup.exe").touch()
    monkeypatch.setattr(windows_install, "component_dir", lambda: tmp_path)
    registered = {"current": False}
    monkeypatch.setattr(windows_install, "version_status", lambda: registered)
    calls, markers = [], []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(windows_install.subprocess, "run", run)
    monkeypatch.setattr(windows_install.winreg, "CreateKey", lambda *args: nullcontext())
    monkeypatch.setattr(windows_install.winreg, "SetValueEx", lambda *args: markers.append(args))
    with pytest.raises(RuntimeError, match="注册"):
        windows_install.repair()
    assert len(calls) == 1 and not markers
    calls.clear()
    registered["current"] = True
    windows_install.repair()
    assert "RunAs" in calls[0][-1] and calls[1][-2:] == ["-Action", "Enable"]
    assert len(markers) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows Inno Setup")
def test_failed_dll_registration_fails_install_and_reinstall(tmp_path):
    root = Path(__file__).resolve().parents[1]
    compiler = shutil.which("ISCC") or root / ".build/tools/InnoSetup-6.7.4/ISCC.exe"
    if not Path(compiler).is_file():
        pytest.skip("Inno Setup compiler unavailable")

    # Run the release file and registration steps in a private directory with
    # an invalid DLL. No elevation, uninstall entry or TSF profile is needed.
    recipe = (root / "packaging/windows/MythlinkInput.iss").read_text(encoding="utf-8")
    install_steps = recipe[recipe.index("[Files]"):].replace(
        r"{commonpf64}\Mythlink Input Method", "{app}"
    )
    destination = tmp_path / "app"
    script = tmp_path / "fixture.iss"
    script.write_text(
        '#define InputDll "invalid.dll"\n#define InstalledDll "invalid.dll"\n'
        "[Setup]\nAppName=Mythlink IME registration test\nAppVersion=1\n"
        f"DefaultDirName={destination}\nPrivilegesRequired=lowest\n"
        "Uninstallable=no\nCloseApplications=no\nUsePreviousAppDir=no\n"
        "ArchitecturesAllowed=x64compatible\nArchitecturesInstallIn64BitMode=x64compatible\n"
        + install_steps, encoding="utf-8-sig"
    )
    (tmp_path / "invalid.dll").write_bytes(b"invalid test DLL")
    subprocess.run([compiler, "/Q", f"/O{tmp_path}", "/Ftest-setup", script], check=True)

    for _ in range(2):
        result = subprocess.run([tmp_path / "test-setup.exe", "/VERYSILENT",
                                 "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART"], timeout=60)
        assert (destination / "invalid.dll").read_bytes() == b"invalid test DLL"
        assert result.returncode != 0
