"""Windows window cards and one-shot activation for the shared Ring selector."""
import ctypes
from ctypes import wintypes as w
import threading
import uuid

from .windows_applications import api, running_windows, window_info


def window_cards():
    user, _, _ = api()
    foreground, shell = user.GetForegroundWindow(), user.GetShellWindow()
    cloaked = ctypes.WinDLL("dwmapi").DwmGetWindowAttribute
    cloaked.argtypes = (w.HWND, w.DWORD, ctypes.POINTER(w.DWORD), w.DWORD)
    cards = []
    for hwnd, app in running_windows():
        style = user.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        if hwnd == shell or style & 0x80 or (user.GetWindow(hwnd, 4) and not style & 0x40000):
            continue  # Tool windows and owned popups are not task-switcher targets.
        hidden = w.DWORD()
        if cloaked(hwnd, 14, ctypes.byref(hidden), ctypes.sizeof(hidden)) == 0 and hidden.value:
            continue
        title = ctypes.create_unicode_buffer(1024)
        if not user.GetWindowTextW(hwnd, title, len(title)):
            continue
        pid = w.DWORD()
        if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            continue
        cards.append(dict(id=str(hwnd), pid=pid.value, bundle=app["value"], app=app["label"],
                          title=title.value, focused=hwnd == foreground, number=0))
    return cards


class WindowsSelectorChannel:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def call(self, operation, **params):
        if operation == "selector_warmup":
            return {}
        if operation == "selector_list":
            cards = window_cards()
            if not cards:
                return {"status": "empty"}
            token = uuid.uuid4().hex
            with self._lock:
                self._sessions[token] = {c["id"]: (c["bundle"], c["pid"]) for c in cards}
            return dict(status="ready", cards=cards, token=token)
        with self._lock:
            targets = self._sessions.pop(params["token"], {})
        if operation == "selector_cancel":
            return {"status": "cancelled"}
        key = params["target"]
        if key not in targets:
            return {"status": "stale"}
        hwnd = int(key)
        info = window_info(hwnd)
        user, _, _ = api()
        if not info or (info[0]["value"], info[1]) != targets[key] or not user.IsWindowVisible(hwnd):
            return {"status": "stale"}
        if user.IsIconic(hwnd):
            user.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
        if user.GetForegroundWindow() == hwnd or user.SetForegroundWindow(hwnd):
            return {"status": "activated"}
        return {"status": "not_activated"}

    def close(self):
        with self._lock:
            self._sessions.clear()
