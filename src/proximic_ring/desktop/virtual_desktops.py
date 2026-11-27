"""Cycle the focused display's existing desktops and verify each system switch."""
from functools import lru_cache
import os
import sys
import time


def windows_layout():
    import ctypes
    from ctypes import wintypes
    import winreg

    root = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
    session = wintypes.DWORD()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise RuntimeError("无法读取 Windows 登录会话")
    shared = root + r"\VirtualDesktops"
    local = root + rf"\SessionInfo\{session.value}\VirtualDesktops"

    def value(path, name):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                data, kind = winreg.QueryValueEx(key, name)
                if kind != winreg.REG_BINARY:
                    raise ValueError("桌面列表格式异常")
                return data
        except FileNotFoundError:
            return b""

    packed = value(shared, "VirtualDesktopIDs") or value(local, "VirtualDesktopIDs")
    current = value(shared, "CurrentVirtualDesktop") or value(local, "CurrentVirtualDesktop")
    if not packed and not current:
        # Explorer does not create these values until a second desktop exists.
        return (b"default",), b"default"
    if len(packed) % 16:
        raise ValueError("桌面列表格式异常")
    order = tuple(packed[i:i + 16] for i in range(0, len(packed), 16))
    return order, current or (order[0] if len(order) == 1 else None)


def windows_step(direction):
    from .windows_shortcuts import WindowsAppShortcuts
    WindowsAppShortcuts().switch_desktop(direction)


@lru_cache(maxsize=1)
def _space_functions():
    import objc
    from Foundation import NSBundle
    bundle = NSBundle.bundleWithPath_("/System/Library/PrivateFrameworks/SkyLight.framework")
    functions = {}
    objc.loadBundleFunctions(bundle, functions, [
        ("SLSMainConnectionID", b"i"),
        ("SLSCopyManagedDisplaySpaces", b"@i", "", {"retval": {"already_retained": True}}),
        ("SLSGetActiveSpace", b"Qi"),
    ], skip_undefined=False)
    return functions


def mac_layout():
    functions = _space_functions()
    connection = functions["SLSMainConnectionID"]()
    current = functions["SLSGetActiveSpace"](connection)
    for display in functions["SLSCopyManagedDisplaySpaces"](connection) or ():
        order = tuple(space["ManagedSpaceID"] for space in display["Spaces"])
        if current in order:
            return order, current
    raise RuntimeError("无法读取当前显示器的空间")


def mac_step(direction):
    import Quartz
    from Foundation import NSUserDefaults
    right = direction > 0
    defaults = NSUserDefaults.standardUserDefaults().persistentDomainForName_("com.apple.symbolichotkeys") or {}
    binding = defaults.get("AppleSymbolicHotKeys", {}).get("81" if right else "79", {})
    if not binding.get("enabled", True):
        raise RuntimeError("请在键盘设置的「调度中心」启用向左和向右移动空间")
    _, key, flags = binding.get("value", {}).get("parameters", [0, 124 if right else 123, Quartz.kCGEventFlagMaskControl])
    if not Quartz.CGPreflightPostEventAccess():
        raise RuntimeError("请在辅助功能中允许 MythLink 切换桌面")
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, key, down)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def cycle_desktop(direction, cancelled, *, layout=None, step=None):
    if layout is None:
        layout, step = (windows_layout, windows_step) if sys.platform == "win32" else (mac_layout, mac_step)
    order, current = layout()
    if direction not in {-1, 1} or current not in order or len(set(order)) != len(order):
        raise ValueError("无法确认当前桌面")
    index = order.index(current)
    target = (index + direction) % len(order)
    # The OS shortcuts stop at the edges. Walk the opposite way for a wrap,
    # reading back each move before posting another key to the desktop.
    while index != target:
        if cancelled.is_set():
            return None
        stride = 1 if index < target else -1
        expected = index + stride
        step(stride)
        deadline = time.monotonic() + 2
        while not cancelled.wait(.04):
            live_order, active = layout()
            if live_order != order or active not in (current, order[expected]):
                raise RuntimeError("桌面顺序或当前空间已变化，请重新画圈")
            if active == order[expected]:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("系统未完成桌面切换，请检查桌面切换快捷键")
        if cancelled.is_set():
            return None
        current, index = order[expected], expected
    return index + 1, len(order)
