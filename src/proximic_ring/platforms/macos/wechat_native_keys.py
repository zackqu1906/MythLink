"""Process-targeted sentence undo/caret keys, with a WeChat edit override.

Text is read and written by the input method. No clipboard, Return, source
switching or window activation is exposed. Every request pins its target app.
"""
from __future__ import annotations

from .mac_permissions import require_post_event_access


def _refresh_workspace() -> None:
    # The private-pipe worker blocks on stdin, without an AppKit event loop.
    # Deliver activation notifications before using NSWorkspace's cached app.
    # Keep each refresh bounded even for a multi-key caret operation.
    from .mac_workspace import refresh_workspace
    refresh_workspace(interval=.001)


def send_wechat_key(command: str, count: int, event_tag: int, *, expected_pid=None) -> None:
    # Prefix edits select to the caret, then may restore an original selection
    # boundary by moving left within the replacement. Keep the dedicated target.
    if command not in {"select_all", "select_to_start", "select_previous", "delete",
                       "caret_from_end", "caret_backward", "caret_forward"}:
        raise ValueError("微信兼容命令无效")
    _send_key(command, count, event_tag, "com.tencent.xinWeChat", expected_pid=expected_pid)


def send_codex_key(command: str, count: int, event_tag: int) -> None:
    """Compatibility entry point for existing callers."""
    send_input_method_key("com.openai.codex", command, count, event_tag)


def send_system_undo(application: str, count: int, event_tag: int, *, expected_pid: int) -> None:
    if type(expected_pid) is not int or expected_pid <= 0 or type(count) is not int or count != 0:
        raise ValueError("系统撤销需要已确认的目标进程")
    _send_key("system_undo", 0, event_tag, application, expected_pid=expected_pid)


def post_system_undo(pid: int, event_tag: int) -> None:
    from .native_access import native_access
    native_access().call("system_undo", pid=pid, event_tag=event_tag)


def _post_system_undo_direct(pid: int, event_tag: int) -> None:
    import Quartz
    if type(pid) is not int or pid <= 0 or type(event_tag) is not int or not 0 < event_tag <= 1 << 50:
        raise ValueError("无效的系统撤销按键")
    require_post_event_access()
    _post_key_pair(Quartz, pid, 6, Quartz.kCGEventFlagMaskCommand, event_tag)


def _post_preview_undo_caret(pid: int, event_tag: int, expires: float) -> bool:
    import time
    import Quartz
    from .mac_workspace import frontmost_application
    if (type(pid) is not int or pid <= 0 or type(event_tag) is not int
            or not (1 << 52) < event_tag < (1 << 52) + (1 << 50)
            or type(expires) not in (float, int) or not 0 < expires - time.monotonic() <= .3):
        return False
    front = frontmost_application()
    if front is None or front.processIdentifier() != pid:
        return False
    require_post_event_access()
    if time.monotonic() >= expires:
        return False
    _post_key_pair(Quartz, pid, 124, 0, event_tag)
    return True


def send_stroke_backspace(application: str, pid: int) -> None:
    """One Backspace, after the stroke controller proves its live IMK client."""
    import uuid
    _send_key('delete', 1, (uuid.uuid4().int % (1 << 50)) or 1, application, expected_pid=pid)


def send_stroke_select_to_start(application: str, pid: int) -> None:
    """Select the prefix; the caller must verify the selection before deleting."""
    import uuid
    _send_key('select_to_start', 0, (uuid.uuid4().int % (1 << 50)) or 1, application, expected_pid=pid)


def send_input_method_key(application: str, command: str, count: int, event_tag: int, *, expected_pid=None) -> None:
    if (not isinstance(application, str) or application != application.strip()
            or (not application and (type(expected_pid) is not int or expected_pid <= 0))):
        raise ValueError("输入法操作缺少目标应用")
    if application == "com.tencent.xinWeChat":
        raise ValueError("微信需要使用专用兼容入口")
    if command not in {"select_all", "select_to_start", "select_previous", "delete", "caret_from_end", "caret_to_end", "caret_backward", "caret_forward"}:
        raise ValueError("默认输入法操作只允许原生选取、删除和光标定位")
    if command == "caret_to_end" and count != 0:
        raise ValueError("文末定位不接受移动次数")
    if expected_pid is None:
        _send_key(command, count, event_tag, application)
    else:
        _send_key(command, count, event_tag, application, expected_pid=expected_pid)


def _send_key(command: str, count: int, event_tag: int, bundle: str, *, expected_pid=None) -> None:
    from .mac_workspace import frontmost_application
    from .native_access import native_access
    app = frontmost_application()
    if (app is None or (app.bundleIdentifier() or "") != bundle
            or (expected_pid is not None and app.processIdentifier() != expected_pid)):
        raise RuntimeError("输入目标已切换，未发送快捷键")
    native_access().call("sentence_key", command=command, count=count, event_tag=event_tag,
                         bundle=bundle, pid=int(app.processIdentifier()))


def _send_key_direct(command: str, count: int, event_tag: int, bundle: str, *, expected_pid=None) -> None:
    label = "微信" if bundle == "com.tencent.xinWeChat" else "输入法"
    if command == "system_undo":
        if count != 0 or type(expected_pid) is not int or expected_pid <= 0:
            raise ValueError("系统撤销需要已确认的目标进程")
    elif command not in {"select_all", "select_to_start", "select_previous", "delete", "caret_from_end", "caret_to_end", "caret_backward", "caret_forward"}:
        raise ValueError(f"{label}兼容命令无效")
    if type(count) is not int or not 0 <= count <= 512:
        raise ValueError(f"{label}光标移动范围无效")
    if command in {"select_previous", "caret_backward", "caret_forward"} and count == 0:
        raise ValueError(f"{label}本句选取范围为空")
    if type(event_tag) is not int or not 0 < event_tag <= 1 << 50:
        raise ValueError(f"{label}兼容事件标记无效")
    if not bundle and (type(expected_pid) is not int or expected_pid <= 0):
        raise ValueError("输入法操作缺少目标进程")
    import AppKit
    import Quartz

    workspace = AppKit.NSWorkspace.sharedWorkspace()
    _refresh_workspace()
    application = workspace.frontmostApplication()
    if application is None or (application.bundleIdentifier() or "") != bundle:
        raise RuntimeError(f"{label}已不在前台，未发送快捷键")
    pid = application.processIdentifier()
    if expected_pid is not None and pid != expected_pid:
        raise RuntimeError("输入目标进程已变化，未发送快捷键")
    require_post_event_access()
    command_mask = Quartz.kCGEventFlagMaskCommand
    keys = {"system_undo": [(6, command_mask)], "select_all": [(0, command_mask)],
            "select_to_start": [(126, command_mask | Quartz.kCGEventFlagMaskShift)],
            "select_previous": [(123, Quartz.kCGEventFlagMaskShift)] * count,
            "delete": [(51, 0)], "caret_from_end": [(125, command_mask)] + [(123, 0)] * count,
            "caret_to_end": [(125, command_mask)], "caret_backward": [(123, 0)] * count,
            "caret_forward": [(124, 0)] * count}[command]
    for code, flags in keys:
        _refresh_workspace()
        current = workspace.frontmostApplication()
        if (current is None or (current.bundleIdentifier() or "") != bundle
                or current.processIdentifier() != pid):
            raise RuntimeError(f"{label}输入目标已切换，已停止快捷键")
        _post_key_pair(Quartz, pid, code, flags, event_tag)


def _post_key_pair(quartz, pid, code, flags, event_tag):
    events = []
    for pressed in (True, False):
        event = quartz.CGEventCreateKeyboardEvent(None, code, pressed)
        if event is None:
            raise RuntimeError("无法创建输入法快捷键")
        quartz.CGEventSetFlags(event, flags)
        quartz.CGEventSetIntegerValueField(event, quartz.kCGEventSourceUserData, event_tag)
        events.append(event)
    for event in events:
        quartz.CGEventPostToPid(pid, event)
