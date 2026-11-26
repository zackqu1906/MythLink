"""Explicit gesture selection of our input mode, without key injection or AX."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from .input_method_install import BUNDLE_ID, payload_directory


def foreground_pid() -> int:
    if sys.platform == "win32":
        from .windows_bridge import foreground_identity
        target = foreground_identity()
        if not target:
            raise RuntimeError("未找到前台应用，请点入文本框后重试")
        return target[1]
    if sys.platform != "darwin":
        raise RuntimeError("输入法切换仅支持 macOS")
    from ...platforms.macos.mac_workspace import frontmost_application
    app = frontmost_application()
    if app is None:
        raise RuntimeError("未找到前台应用，请点入文本框后重试")
    return int(app.processIdentifier())


def select_voice_input_source(pid: int, *, helper: Path | None = None, timeout=3) -> None:
    if sys.platform == "win32" and helper is None:
        import ctypes
        from ...runtime_paths import resource_root, is_frozen
        root = resource_root() if is_frozen() else resource_root() / ".build"
        library = ctypes.WinDLL(str(root / "windows-ime" / "MythlinkTsf.dll"))
        select = library.MythlinkSelectProfile
        select.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        select.restype = ctypes.c_int32
        result = select(pid, max(1, min(250, int(timeout * 1000))))
        if result != 0:
            raise RuntimeError(f"无法选中 Mythlink 输入法（0x{result & 0xffffffff:08X}），请用 Win+空格检查输入法列表")
        return
    helper = helper if helper is not None else payload_directory() / "InputMethodAdmin"
    if not helper.is_file():
        raise RuntimeError("缺少输入法切换工具，请构建或更新完整应用")
    try:
        result = subprocess.run([str(helper), "select", str(pid)],
                                capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("切换输入法超时，请重新触发手势") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "切换输入法失败，请检查组件是否已安装")
    try:
        state = json.loads(result.stdout)
        selected = any(s.get("id") == BUNDLE_ID + ".dictation" and s.get("selected")
                       for s in state["sources"])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError("无法确认输入法切换结果，请更新完整应用") from exc
    if not selected:
        raise RuntimeError("系统未确认输入法切换，请点入文本框后重试")

