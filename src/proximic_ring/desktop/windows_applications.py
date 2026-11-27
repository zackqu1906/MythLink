"""Windows application identities shared by discovery, foreground matching and launch."""
import ctypes
from ctypes import wintypes as w
from functools import lru_cache
import hashlib
import json
import ntpath
import os
from pathlib import Path
import subprocess


def candidate(path, label="", *, running=False):
    if not isinstance(path, str):
        return None
    if path.startswith("shell:AppsFolder\\"):
        identity = path.split("\\", 1)[1]
        if "!" not in identity or any(c in identity for c in '\\/:"'):
            return None
    elif not ntpath.isabs(path) or ntpath.splitext(path)[1].lower() != ".exe":
        return None
    path = ntpath.normpath(path)
    bundle = "win." + hashlib.sha256(path.casefold().encode("utf-8")).hexdigest()[:32]
    label = label or ntpath.splitext(ntpath.basename(path))[0]
    return dict(value=bundle, label=label, path=path, running=running,
                search=(label + " " + path).casefold())


class GuiThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD)] + [
        (name, w.HWND) for name in ("active", "focus", "capture", "menu", "move", "caret")
    ] + [("caretRect", w.RECT)]


@lru_cache(maxsize=1)
def api():
    user, kernel = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    for dll, name, result, args in (
        (user, "GetForegroundWindow", w.HWND, []),
        (user, "GetWindowThreadProcessId", w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
        (user, "GetGUIThreadInfo", w.BOOL, [w.DWORD, ctypes.POINTER(GuiThreadInfo)]),
        (user, "IsWindowVisible", w.BOOL, [w.HWND]),
        (user, "GetWindowTextW", ctypes.c_int, [w.HWND, w.LPWSTR, ctypes.c_int]),
        (user, "GetWindowLongW", w.LONG, [w.HWND, ctypes.c_int]),
        (user, "GetShellWindow", w.HWND, []),
        (user, "GetWindow", w.HWND, [w.HWND, w.UINT]),
        (user, "EnumWindows", w.BOOL, [callback, w.LPARAM]),
        (user, "SetForegroundWindow", w.BOOL, [w.HWND]),
        (user, "IsIconic", w.BOOL, [w.HWND]),
        (user, "ShowWindowAsync", w.BOOL, [w.HWND, ctypes.c_int]),
        (kernel, "OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
        (kernel, "CloseHandle", w.BOOL, [w.HANDLE]),
        (kernel, "QueryFullProcessImageNameW", w.BOOL, [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]),
        (kernel, "GetApplicationUserModelId", w.LONG, [w.HANDLE, ctypes.POINTER(w.DWORD), w.LPWSTR]),
    ):
        func = getattr(dll, name)
        func.argtypes, func.restype = args, result
    return user, kernel, callback


def window_info(hwnd):
    if not hwnd:
        return None
    user, kernel, _ = api()
    pid = w.DWORD()
    thread = user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel.OpenProcess(0x1000, False, pid.value) if thread else None
    if not handle:
        return None
    try:
        length = w.DWORD(32768)
        buf = ctypes.create_unicode_buffer(length.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(length)):
            return None
        path = buf.value
        length.value = 512
        if not kernel.GetApplicationUserModelId(handle, ctypes.byref(length), buf) and "!" in buf.value:
            path = "shell:AppsFolder\\" + buf.value
        app = candidate(path, running=True)
        return (app, pid.value, thread) if app else None
    finally:
        kernel.CloseHandle(handle)


def running_windows():
    user, _, callback = api()
    windows = []
    @callback
    def visit(hwnd, unused):
        if user.IsWindowVisible(hwnd):
            info = window_info(hwnd)
            if info:
                windows.append((hwnd, info[0]))
        return True
    user.EnumWindows(visit, 0)
    return windows


# AppsFolder includes both Start-menu programs and packaged applications.
_SHELL_APPS = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$apps = (New-Object -ComObject Shell.Application).Namespace('shell:AppsFolder').Items()
foreach ($item in $apps) {
    $path = $item.ExtendedProperty('System.Link.TargetParsingPath')
    if (!$path -or $path -notlike '*.exe') {
        $id = $item.ExtendedProperty('System.AppUserModel.ID')
        if ($id -like '*!*') { $path = 'shell:AppsFolder\' + $id } else { continue }
    }
    ConvertTo-Json -Compress -InputObject @{ path = [string]$path; label = $item.Name }
}
$links = New-Object -ComObject WScript.Shell
$roots = [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('CommonPrograms')
foreach ($link in Get-ChildItem -LiteralPath $roots -Filter '*.lnk' -File -Recurse -ErrorAction SilentlyContinue) {
    $target = $links.CreateShortcut($link.FullName)
    if ($target.TargetPath -like '*.exe' -and !$target.Arguments) {
        ConvertTo-Json -Compress -InputObject @{ path = $target.TargetPath; label = $link.BaseName }
    }
}
"""


def installed_windows_applications():
    apps = {item["value"]: item for _, item in running_windows()}
    import winreg
    # App Paths also covers programs that do not publish a Start-menu shortcut.
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
                                    0, winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as key:
                                path, _ = winreg.QueryValueEx(key, "")
                            app = candidate(os.path.expandvars(path.strip('"')))
                            if app and os.path.isfile(app["path"]):
                                apps.setdefault(app["value"], app)
                        except (OSError, AttributeError):
                            continue
            except OSError:
                continue
    explorer = candidate(str(Path(os.environ["SystemRoot"]) / "explorer.exe"), "文件资源管理器")
    apps.setdefault(explorer["value"], explorer)
    partial = False
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        proc = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", _SHELL_APPS],
                              capture_output=True, encoding="utf-8-sig", errors="replace", timeout=12,
                              creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        for line in proc.stdout.splitlines():
            data = json.loads(line)
            app = candidate(data["path"], data["label"])
            if app:
                app["running"] = app["value"] in apps and apps[app["value"]]["running"]
                apps[app["value"]] = app
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        partial = True
    return dict(candidates=sorted(apps.values(), key=lambda app: (not app["running"], app["label"].casefold())),
                partial=partial)


def open_application(bundle, path):
    app = candidate(path)
    if not app or app["value"] != bundle:
        raise ValueError("请重新选择要打开的应用")
    for hwnd, running in running_windows():
        if running["value"] == bundle:
            user = api()[0]
            if user.IsIconic(hwnd):
                user.ShowWindowAsync(hwnd, 9)
            if user.SetForegroundWindow(hwnd):
                return
            raise OSError("Windows 未允许切换到所选窗口")
    if path.startswith("shell:AppsFolder\\"):
        os.startfile(path)
    else:
        if not Path(path).is_file():
            raise OSError("应用路径已变化，请重新选择")
        subprocess.Popen([path], cwd=ntpath.dirname(path))
