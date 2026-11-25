from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace

from proximic_ring import runtime_paths


def test_windows_input_method_is_enabled_when_its_registered_dll_exists(monkeypatch, tmp_path):
    dll = tmp_path / "MythlinkTsf.dll"
    registry = SimpleNamespace(
        HKEY_LOCAL_MACHINE=1, KEY_READ=2, KEY_WOW64_64KEY=4,
        OpenKey=lambda *_: nullcontext(), QueryValueEx=lambda *_: (str(dll), 1),
    )
    monkeypatch.setattr(runtime_paths.sys, "platform", "win32")
    monkeypatch.setitem(runtime_paths.sys.modules, "winreg", registry)
    monkeypatch.delenv("MYTHLINK_WINDOWS_TSF", raising=False)
    runtime_paths._enable_registered_windows_ime()
    assert "MYTHLINK_WINDOWS_TSF" not in runtime_paths.os.environ
    dll.touch()
    runtime_paths._enable_registered_windows_ime()
    assert runtime_paths.os.environ["MYTHLINK_WINDOWS_TSF"] == "1"
    monkeypatch.setenv("MYTHLINK_WINDOWS_TSF", "0")
    runtime_paths._enable_registered_windows_ime()
    assert runtime_paths.os.environ["MYTHLINK_WINDOWS_TSF"] == "0"


def test_data_home_override_is_shared_by_packaged_runtime(monkeypatch, tmp_path):
    target = tmp_path / "userdata"
    monkeypatch.setenv(runtime_paths.DATA_HOME_ENV, str(target))

    runtime_paths.configure_runtime_environment()

    assert runtime_paths.app_data_root() == target.resolve()
    assert runtime_paths.cache_root() == (target / "cache").resolve()
    assert Path(runtime_paths.os.environ["PROXIMIC_LLM_HOME"]).is_dir()
    assert Path(runtime_paths.os.environ["MODELSCOPE_CACHE"]).is_dir()


def test_source_runtime_uses_dot_runtime_for_local_llm(monkeypatch, tmp_path):
    monkeypatch.delenv(runtime_paths.DATA_HOME_ENV, raising=False)
    monkeypatch.delenv("PROXIMIC_LLM_HOME", raising=False)
    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: False)
    monkeypatch.setattr(runtime_paths, "resource_root", lambda: tmp_path)

    runtime_paths.configure_runtime_environment()

    assert Path(runtime_paths.os.environ["PROXIMIC_LLM_HOME"]) == (
        tmp_path / ".runtime" / "local-llm"
    )


def test_frozen_macos_uses_application_support(monkeypatch, tmp_path):
    monkeypatch.delenv(runtime_paths.DATA_HOME_ENV, raising=False)
    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: True)
    monkeypatch.setattr(runtime_paths.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert (
        runtime_paths.app_data_root()
        == (tmp_path / "Library/Application Support/ProxiMic Voice").resolve()
    )
