"""Current-Space window metadata and one-shot activation; capture is a separate service."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
import uuid

from .text_focus import MacAX, intersection
from .scene_diagnostics import exception_details

MESSAGES = {
    "permission": "窗口选择需要辅助功能权限",
    "empty": "当前屏幕没有可选择的普通窗口",
    "unavailable": "暂时无法读取窗口，请重试",
    "stale": "所选窗口已不可用，请重新握拳选择",
    "not_activated": "应用未确认进入所选窗口，请重试",
    "unsupported": "当前系统不支持窗口选择",
}


def grid_move(index, count, columns, direction):
    """No wrap; a short last row has no imaginary cards."""
    if not 0 <= index < count or columns < 1:
        return index
    _, col = divmod(index, columns)
    candidate = {"left": index - 1 if col else index,
                 "right": index + 1 if col < columns - 1 else index,
                 "up": index - columns, "down": index + columns}.get(direction, index)
    return candidate if 0 <= candidate < count else index


def same_rect(a, b):
    return bool(a and b and all(math.isfinite(float(v)) for v in (*a, *b))
                and all(abs(x - y) <= 2 for x, y in zip(a, b)))


def area(rect):
    return max(0, rect[2]) * max(0, rect[3]) if rect else 0


class MacWindowAX(MacAX):
    def on_screen(self):
        import Quartz as CG
        rows = CG.CGWindowListCopyWindowInfo(
            CG.kCGWindowListOptionOnScreenOnly | CG.kCGWindowListExcludeDesktopElements, 0) or []
        return [{"pid": int(w[CG.kCGWindowOwnerPID]), "number": int(w[CG.kCGWindowNumber]),
                 "title": str(w.get(CG.kCGWindowName, "")),
                 "frame": tuple(float(w[CG.kCGWindowBounds][key]) for key in ("X", "Y", "Width", "Height"))}
                for w in rows if w.get(CG.kCGWindowLayer) == 0 and w.get(CG.kCGWindowAlpha, 1) > 0]

    def screens(self):
        import AppKit
        import Quartz as CG
        result = []
        for screen in AppKit.NSScreen.screens():
            identifier = int(screen.deviceDescription()["NSScreenNumber"])
            bounds = CG.CGDisplayBounds(identifier)
            result.append({"id": identifier, "frame": (bounds.origin.x, bounds.origin.y,
                                                        bounds.size.width, bounds.size.height)})
        return result

    def pointer_position(self):
        # Read location in the same global coordinates as CGDisplayBounds.
        # This event is never posted and cannot move the pointer or change focus.
        import Quartz as CG
        event = CG.CGEventCreate(None)
        if event is None:
            return None
        point = CG.CGEventGetLocation(event)
        return (float(point.x), float(point.y))

    def apps(self):
        import AppKit
        from .mac_workspace import running_applications
        return {int(app.processIdentifier()): app for app in running_applications()
                if app.activationPolicy() == AppKit.NSApplicationActivationPolicyRegular and not app.isHidden()}

    def windows(self, pid):
        app = self.application(pid)
        error, count = self.ax.AXUIElementGetAttributeValueCount(app, "AXWindows", None)
        self._check(error)
        if error or count > 128:
            raise RuntimeError("window list unavailable")
        if count == 0:
            return []
        error, windows = self.ax.AXUIElementCopyAttributeValues(app, "AXWindows", 0, count, None)
        self._check(error)
        return list(windows or [])

    def app_info(self, app):
        return {"app": str(app.localizedName() or "应用"), "bundle": str(app.bundleIdentifier() or "")}

    def activate(self, app, window):
        # Retain the actual AX window, not an app name or a title lookup. Never
        # raise every window, simulate a click, or recreate a closed window.
        ax_app = self.application(int(app.processIdentifier()))
        if self.settable(ax_app, "AXFocusedWindow"):
            if self.ax.AXUIElementSetAttributeValue(ax_app, "AXFocusedWindow", window) != 0:
                return False
        elif self.settable(window, "AXMain"):
            if self.ax.AXUIElementSetAttributeValue(window, "AXMain", True) != 0:
                return False
        # Modern macOS may accept NSRunningApplication.activate while leaving
        # the other app frontmost. An authorized AX client can explicitly set
        # AXFrontmost; this is a public application attribute, not a click or
        # a process-wide activation of the Ring host.
        if self.front()["pid"] != int(app.processIdentifier()) and self.settable(ax_app, "AXFrontmost"):
            if self.ax.AXUIElementSetAttributeValue(ax_app, "AXFrontmost", True) != 0:
                return False
        if self.ax.AXUIElementPerformAction(window, "AXRaise") != 0:
            return False
        if not app.activateWithOptions_(0):
            return False
        deadline = time.monotonic() + .35
        while time.monotonic() < deadline:
            if self.front()["pid"] == int(app.processIdentifier()) and self.attr(ax_app, "AXFocusedWindow") == window:
                return True
            time.sleep(.02)
        return False


@dataclass
class WindowTarget:
    pid: int
    app: object
    window: object


class WindowSelectorSession:
    """Opaque handles stay valid only inside one short-lived selection session."""
    def __init__(self, ax=None, *, clock=time.monotonic):
        self.ax = ax or MacWindowAX()
        self.clock = clock
        self.token = ""
        self.targets = {}
        self.origin = self.origin_window = self.screen = None
        self.ambiguous = False
        self.host_pid = 0
        self.host_window = {}
        self.last_diagnostic = {}

    def _choose_screen(self, screens, rows, front):
        if front:
            screen = max(screens, key=lambda s: area(intersection(s["frame"], front["frame"])))
            if area(intersection(screen["frame"], front["frame"])):
                return screen, "foreground_window"
        # Closing/minimizing the front app's last window does not close other
        # apps. Global window selection must not depend on an origin window.
        try:
            point = self.ax.pointer_position()
        except Exception:
            point = None
        if point is not None:
            for screen in screens:
                x, y, width, height = screen["frame"]
                if x <= point[0] < x + width and y <= point[1] < y + height:
                    return screen, "pointer"
        for row in rows:
            screen = max(screens, key=lambda s: area(intersection(s["frame"], row["frame"])))
            if area(intersection(screen["frame"], row["frame"])):
                return screen, "visible_window"
        return screens[0], "primary_display"

    def selectable_rows(self, rows):
        return [r for r in rows if r["pid"] != self.host_pid
                or r["number"] == self.host_window.get("number")]

    def ordinary(self, window):
        return (self.ax.attr(window, "AXRole") == "AXWindow"
                and self.ax.attr(window, "AXSubrole") == "AXStandardWindow"
                and not self.ax.attr(window, "AXMinimized")
                and not self.ax.attr(window, "AXHidden"))

    def visible_windows(self, pid, rows, deadline):
        """Public geometry matching: ambiguity across Spaces is excluded.

        Identical AX frames are safe only if *all* matching AX windows have
        corresponding on-screen WindowServer entries. No private window IDs.
        """
        windows = []
        for node in self.ax.windows(pid):
            if self.clock() > deadline:
                raise TimeoutError()
            if not self.ordinary(node):
                continue
            # The host supplies its actual main-window number and title.
            # Even standard-role helper windows must not alias its AX handle.
            if pid == self.host_pid and self.ax.attr(node, "AXTitle") != self.host_window.get("title"):
                continue
            frame = self.ax.rect(node)
            if frame and frame[2] > 150 and frame[3] > 100:
                windows.append((node, frame))
        result = []
        for node, frame in windows:
            matches = [r for r in rows if r["pid"] == pid and same_rect(r["frame"], frame)]
            ax_matches = [n for n, f in windows if same_rect(f, frame)]
            if len(matches) >= len(ax_matches) and matches:
                result.append((node, frame, min(rows.index(r) for r in matches)))
            elif matches:
                self.ambiguous = True
        return result

    @staticmethod
    def stale(reason):
        # Only fixed reason codes cross IPC/logging; no window titles/content.
        return {"status": "stale", "reason": reason}

    def handle(self, operation, **params):
        started = self.clock()
        self.last_diagnostic = {"operation": operation, "stage": "start"}
        try:
            result = self._handle(operation, **params)
            self.last_diagnostic.setdefault("reason", result.get("reason", result["status"]))
            self.last_diagnostic["status"] = result["status"]
            return result
        except Exception as exc:
            self.last_diagnostic.update(reason="native_exception", **exception_details(exc))
            raise
        finally:
            self.last_diagnostic["elapsed_ms"] = round((self.clock() - started) * 1000, 2)

    def _handle(self, operation, *, token="", target="", host_pid=0, host_window=None, expected=None):
        if operation == "selector_cancel":
            if token and token == self.token:
                self.token, self.targets = "", {}
            return {"status": "cancelled"}
        if operation == "selector_list":
            self.token, self.targets = "", {}
            self.host_pid, self.host_window = host_pid, dict(host_window or {})
            self.ambiguous = False
            self.origin_window = self.screen = None
            self.last_diagnostic["stage"] = "desktop_snapshot"
            try:
                self.origin = self.ax.front() or {"pid": 0}
            except Exception as exc:
                self.origin = {"pid": 0}
                self.last_diagnostic["origin_error"] = exception_details(exc)
            raw_rows = self.ax.on_screen()
            rows = self.selectable_rows(raw_rows)
            front = next((r for r in rows if r["pid"] == self.origin["pid"]), None)
            screens = self.ax.screens()
            self.last_diagnostic.update(origin_pid=self.origin["pid"], visible_rows=len(raw_rows),
                                        selectable_rows=len(rows), screen_count=len(screens),
                                        origin_visible=front is not None)
            if not screens:
                self.last_diagnostic["reason"] = "screens_unavailable"
                return {"status": "unavailable"}
            try:
                if front:
                    self.origin_window = self.ax.attr(self.ax.application(self.origin["pid"]), "AXFocusedWindow")
                if self.origin_window is not None:
                    focused_frame = self.ax.rect(self.origin_window)
                    focused_rows = [r for r in rows if r["pid"] == self.origin["pid"]
                                    and same_rect(r["frame"], focused_frame)]
                    if len(focused_rows) == 1:
                        front = focused_rows[0]
            except Exception as exc:
                self.origin_window = None
                self.last_diagnostic["origin_error"] = exception_details(exc)
            self.screen, source = self._choose_screen(screens, rows, front)
            self.last_diagnostic.update(screen_source=source, screen_id=self.screen["id"],
                                        stage="enumerate_windows")
            # A foreground app that does not expose AX focus must not stop a
            # global selector from showing other apps. CG geometry still
            # determines the initial display, without binding session validity.
            apps, cards, partial = self.ax.apps(), [], False
            deadline = self.clock() + 1.15
            pids = list(dict.fromkeys(r["pid"] for r in rows if r["pid"] in apps))
            self.last_diagnostic.update(candidate_apps=len(pids), app_errors=[])
            for pid in pids[:48]:
                try:
                    for node, frame, order in self.visible_windows(pid, rows, deadline):
                        if self.clock() > deadline or len(cards) >= 128:
                            partial = True
                            break
                        # A spanning window belongs to the display containing
                        # the largest part of it, so cards are not duplicated.
                        screen = max(screens, key=lambda s: area(intersection(s["frame"], frame)))
                        if screen != self.screen:
                            continue
                        title = self.ax.attr(node, "AXTitle") or "未命名窗口"
                        title = " ".join(str(title).split())[:160]
                        matches = [r for r in rows if r["pid"] == pid and same_rect(r["frame"], frame)]
                        named = [r for r in matches if " ".join(r.get("title", "").split())[:160] == title]
                        # Geometry is enough when unique. With equal-sized
                        # windows require a unique title match; never preview
                        # one window while Tap activates a different AX node.
                        preview = matches[0] if len(matches) == 1 else named[0] if len(named) == 1 else None
                        handle = uuid.uuid4().hex
                        self.targets[handle] = WindowTarget(pid, apps[pid], node)
                        info = ({"app": "Mythlink", "bundle": "com.proximic.voice"}
                                if pid == self.host_pid else self.ax.app_info(apps[pid]))
                        cards.append({"id": handle, "pid": pid, "title": title, "order": order,
                                      "number": preview["number"] if preview else 0, "frame": frame,
                                      **info,
                                      "focused": pid == self.origin["pid"] and node == self.origin_window})
                except Exception as exc:
                    partial = True
                    if len(self.last_diagnostic["app_errors"]) < 8:
                        self.last_diagnostic["app_errors"].append({"pid": pid, **exception_details(exc)})
                if self.clock() > deadline or len(cards) >= 128:
                    partial = True
                    break
            self.last_diagnostic.update(card_count=len(cards), partial=partial,
                                        ambiguous=self.ambiguous)
            if not cards:
                self.last_diagnostic["reason"] = ("window_scan_incomplete" if partial or self.ambiguous
                                                  else "no_visible_windows")
                return {"status": "unavailable" if partial or self.ambiguous else "empty"}
            self.token = uuid.uuid4().hex
            cards.sort(key=lambda c: c.pop("order"))
            selected = next((i for i, c in enumerate(cards) if c["focused"]), 0)
            return {"status": "ready", "token": self.token, "cards": cards, "selected": selected,
                    "screen": self.screen["id"], "bounds": self.screen["frame"],
                    "partial": partial or self.ambiguous or len(pids) > 48}
        # The controller owns the inactivity timer and explicitly releases
        # this bounded snapshot. No foreground/Space polling or renewal timer.
        if not token or token != self.token:
            return self.stale("session_invalid")
        if operation != "selector_activate":
            raise ValueError("未知窗口选择操作")
        chosen = self.targets.get(target)
        self.token = ""  # Consume before any mutation; never retry a lost reply.
        self.targets = {}
        if chosen is None:
            return self.stale("target_missing")
        self.last_diagnostic.update(stage="validate_target", target_pid=chosen.pid)
        apps = self.ax.apps()
        if chosen.pid not in apps:
            return self.stale("target_app_closed")
        rows = self.selectable_rows(self.ax.on_screen())
        visible = self.visible_windows(chosen.pid, rows, self.clock() + .65)
        if not any(node == chosen.window for node, _, _ in visible):
            return self.stale("target_not_visible")
        self.last_diagnostic["stage"] = "activate_target"
        return {"status": "activated" if self.ax.activate(chosen.app, chosen.window) else "not_activated"}
