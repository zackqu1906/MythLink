"""Foreground-checked Windows gesture shortcuts and per-application presets."""
import ctypes
from ctypes import wintypes as w
import ntpath

from .app_shortcuts import ShortcutTarget
from .windows_applications import api, GuiThreadInfo, window_info
from ..gestures.app_gestures import normalize_shortcut
from ring_python_sdk.touchpad.windows import _Input, _KeyboardInput


def application_actions(path):
    """Return the action picker and the initial bindings for an explicitly added app."""
    common = [("copy", "复制", "Ctrl+C"), ("paste", "粘贴", "Ctrl+V"), ("undo", "撤销", "Ctrl+Z"),
              ("find", "查找", "Ctrl+F"), ("save", "保存", "Ctrl+S")]
    name = ntpath.basename(path).casefold()
    if name in {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "code.exe", "opera.exe"}:
        prev, nxt = ("prev-tab", "上一个标签页", "Ctrl+Shift+Tab"), ("next-tab", "下一个标签页", "Ctrl+Tab")
    elif name == "explorer.exe":
        prev, nxt = ("back", "后退", "Alt+Left"), ("forward", "前进", "Alt+Right")
    elif name == "snippingtool.exe" or name.startswith("microsoft.screensketch_"):
        prev, nxt = ("new-snip", "新建截图", "Ctrl+N"), common[-1]
    else:
        prev, nxt = ("page-up", "上一页", "PageUp"), ("page-down", "下一页", "PageDown")
    def action(entry):
        key, label, shortcut = entry
        return dict(id="windows:" + key, label=label, path="Windows 应用快捷键", shortcut=shortcut)
    defaults = {"circle-counterclockwise": action(prev), "circle-clockwise": action(nxt)}
    if name in {"powerpnt.exe", "wpp.exe"}:
        defaults["snap"] = action(("slideshow", "从头开始放映", "F5"))
    items = {item["id"]: item for item in [*defaults.values(), *map(action, common)]}
    return [{**item, "preset": True, "available": None} for item in items.values()], {"regular": defaults}


_VK = {"Win": 0x5b, "Cmd": 0x5b, "Ctrl": 0x11, "Alt": 0x12, "Shift": 0x10,
       "Return": 13, "KeypadEnter": 13, "Space": 32, "Tab": 9, "Escape": 27, "Backspace": 8,
       "PageUp": 33, "PageDown": 34, "End": 35, "Home": 36, "Left": 37, "Up": 38, "Right": 39,
       "Down": 40, "Delete": 46, "Clear": 12, "Help": 47,
       **{f"F{i}": 0x6f+i for i in range(1, 21)}}


class WindowsAppShortcuts:
    def capture(self, *, plain_enter=False, menu_action=False, scene=False, scene_observation=False,
                focus_sensitive=True, passive_undo=False):
        user = api()[0]
        hwnd = user.GetForegroundWindow()
        info = window_info(hwnd)
        if not info:
            return None
        app, pid, thread = info
        gui = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        if not user.GetGUIThreadInfo(thread, ctypes.byref(gui)) or hwnd != user.GetForegroundWindow():
            return None
        context = "text" if gui.caret else "unknown"
        if gui.focus and not gui.caret:
            klass = ctypes.create_unicode_buffer(128)
            user.GetClassNameW(w.HWND(gui.focus), klass, len(klass))
            if klass.value.casefold() in {"syslistview32", "systreeview32", "button", "progman", "workerw"}:
                context = "nontext"
        return ShortcutTarget(f"win32:{pid}" if plain_enter else app["value"], pid, "",
                              window=hwnd, focus=gui.focus, plain_enter=plain_enter, menu_action=menu_action,
                              blocked=bool(gui.flags & 0x1e), input_context=context, focus_sensitive=focus_sensitive)

    def same_target(self, target, *, require_focus=False):
        if not target or target.blocked:
            return False
        current = self.capture(plain_enter=target.plain_enter)
        return bool(current and not current.blocked and current.pid == target.pid and current.bundle == target.bundle
                    and current.window == target.window and (not require_focus or
                        current.focus is not None and current.focus == target.focus))

    def post(self, target, shortcut, *, require_focus=False, system_wide=False):
        self._send_chord(shortcut, lambda: self.same_target(target, require_focus=require_focus))

    def switch_desktop(self, direction):
        # This shell shortcut also works on a desktop with no foreground HWND.
        self._send_chord("Win+Ctrl+" + ("Right" if direction > 0 else "Left"))

    @staticmethod
    def _send_chord(shortcut, same_target=None):
        user = api()[0]
        keys = []
        for name in normalize_shortcut(shortcut).split("+"):
            vk = _VK.get(name)
            if vk is None and len(name) == 1:
                vk = ord(name) if name.isascii() and name.isalnum() else user.VkKeyScanW(ord(name)) & 0xffff
            if vk is None or vk == 0xffff:
                raise ValueError("Windows 不支持此组合键")
            keys.append((vk & 0xff, name in {"Win", "Cmd", "KeypadEnter", "PageUp", "PageDown", "Home", "End",
                                            "Left", "Right", "Up", "Down", "Delete"}))
        if any(user.GetAsyncKeyState(vk) & 0x8000 for vk in (16, 17, 18, 0x5b, 0x5c)):
            raise RuntimeError("请先松开键盘修饰键")
        def event(key, up):
            item = _Input(type=1)
            item.ki = _KeyboardInput(wVk=key[0], dwFlags=int(key[1]) | (2 if up else 0))
            return item
        events = [event(key, False) for key in keys] + [event(key, True) for key in reversed(keys)]
        if same_target is not None and not same_target():
            raise RuntimeError("前台窗口已变化")
        count = user.SendInput(len(events), (_Input * len(events))(*events), ctypes.sizeof(_Input))
        if count != len(events):
            held = min(count, len(events)-count)
            if held:
                releases = [event(key, True) for key in reversed(keys[:held])]
                user.SendInput(held, (_Input * held)(*releases), ctypes.sizeof(_Input))
            raise RuntimeError("Windows 未允许向当前窗口发送快捷键")
