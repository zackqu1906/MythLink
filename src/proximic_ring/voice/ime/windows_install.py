"""Check and repair the same Windows TSF component used by the app installer."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import winreg

from ...runtime_paths import resource_root


REGISTRATION = r"SOFTWARE\Classes\CLSID\{40b2749b-c169-4a28-921c-c3367b96482b}\InprocServer32"


def component_dir():
    return resource_root() / "windows-ime"


def version_status():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRATION, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            installed = Path(winreg.QueryValueEx(key, "")[0])
        current = sha256(installed.read_bytes()).digest() == sha256((component_dir() / "MythlinkTsf.dll").read_bytes()).digest()
    except FileNotFoundError:
        return dict(state="not_installed", current=False, text="组件缺失", detail="可在这里重新安装 MythLink 输入法。")
    except OSError:
        return dict(state="unknown", current=False, text="无法校验", detail="Windows 无法读取输入法组件。")
    if current:
        return dict(state="current", current=True, text="已是当前版本",
                    detail="输入法随应用安装更新。升级前已打开的编辑器需完全退出再打开，才能加载新版。")
    return dict(state="outdated", current=False, text="需要更新", detail="已注册的输入法与应用内的版本不同。")


def repair():
    folder = component_dir()
    setup = folder / "MythlinkInputSetup.exe"
    if not setup.is_file():
        raise RuntimeError("缺少输入法安装程序，请重新运行完整安装包并勾选「添加 MythLink 输入法」。")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    args = [str(powershell), "-NoProfile", "-NonInteractive"]
    # Only the packaged Inno installer requests elevation. The profile is
    # enabled separately as the desktop user, including for over-the-shoulder UAC.
    result = subprocess.run([*args, "-Command",
        "$ErrorActionPreference='Stop'; try { $p=Start-Process -FilePath $env:MYTHLINK_IME_SETUP "
        "-ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-' -Verb RunAs -PassThru -Wait; "
        "exit $p.ExitCode } catch { exit 1 }"],
        env={**os.environ, "MYTHLINK_IME_SETUP": str(setup)}, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not version_status()["current"]:
        raise RuntimeError("Windows 未完成输入法注册。请在管理员授权提示中确认后重试。")
    result = subprocess.run([*args, "-ExecutionPolicy", "Bypass", "-File",
        str(folder / "windows-ime-prototype.ps1"), "-Action", "Enable"], creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("输入法组件已更新，添加到当前用户键盘列表失败，请再次修复。")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Mythlink\Installer") as key:
        winreg.SetValueEx(key, "InputProfile", 0, winreg.REG_DWORD, 1)
