"""Private-pipe worker for the application's permission-sensitive operations."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, replace
import json
import os
import sys
import uuid

from .mac_permissions import MacPermissionError, read_permission_state


class Dispatcher:
    def __init__(self):
        from .app_shortcuts import LocalMacAppShortcuts
        self.shortcuts = LocalMacAppShortcuts()
        self.targets = OrderedDict()
        self.text_focus = None
        self.page_scroll = None
        self.window_selector = None

    def handle(self, message):
        operation = message["operation"]
        if operation == "application_candidates":
            from .application_menus import application_candidates
            return {"result": application_candidates()}
        if operation == "selector_cancel":
            # Session cleanup needs no AX access and must not request permission.
            result = (self.window_selector.handle(operation, token=message.get("token", ""))
                      if self.window_selector else {"status": "cancelled"})
            return {"result": result}
        state = replace(read_permission_state(), control_channel="worker", control_pid=os.getpid())
        if operation == "status":
            return {"permissions": asdict(state)}
        if operation == "application_menu":
            if state.accessibility is not True:
                raise MacPermissionError(state)
            from .application_menus import read_application_menu
            return {"result": read_application_menu(str(message["bundle"]))}
        if operation in {"focus_probe", "focus_plan", "focus_apply", "focus_selection"}:
            # Normal AX focus only needs Accessibility. The bounded Safari
            # click fallback checks event-posting permission at point of use.
            if state.accessibility is not True:
                raise MacPermissionError(state)
            if self.text_focus is None:
                from .text_focus import TextFocusSession
                self.text_focus = TextFocusSession()
            if message.get("scene_apps") or message.get("voice_disabled_apps"):
                target = self.shortcuts.capture(menu_action=True, scene=bool(message.get("scene_apps")))
                configured = message.get("scene_apps", {})
                disabled = target and target.bundle in message.get("voice_disabled_apps", [])
                eligible = (target and target.bundle in configured and target.scene and not target.blocked
                            and (target.scene == "presentation" or target.input_context == "nontext")
                            and (not isinstance(configured, dict) or target.scene in configured[target.bundle]))
                if eligible or disabled:
                    if operation == "focus_apply":
                        self.text_focus.plans.pop(message.get("plan", ""), None)
                    return {"result": {"status": "presentation" if eligible else "voice_overridden",
                                       "scene": target.scene, "count": 0, "index": 0}}
            return {"result": self.text_focus.handle(
                operation, ignored_pid=int(message.get("ignored_pid", 0)),
                expected=message.get("expected"), action=message.get("action", "inspect"),
                plan=message.get("plan", ""), selection=message.get("selection", ""),
                direction=message.get("direction", ""))}
        if operation in {"selector_warmup", "selector_list", "selector_activate"}:
            if state.accessibility is not True:
                raise MacPermissionError(state)
            if self.window_selector is None:
                from .window_selector import WindowSelectorSession
                self.window_selector = WindowSelectorSession()
            if operation == "selector_warmup":
                return {"result": {"status": "ready"}}  # Imports only, no window enumeration.
            return {"result": self.window_selector.handle(
                operation, token=message.get("token", ""), target=message.get("target", ""),
                host_pid=int(message.get("host_pid", 0)), host_window=message.get("host_window"),
                expected=message.get("expected"))}
        if not state.ready:
            raise MacPermissionError(state)
        if operation in {"scroll_plan", "scroll_apply"}:
            if self.page_scroll is None:
                from .page_scroll import PageScrollSession
                self.page_scroll = PageScrollSession()
            return {"result": self.page_scroll.handle(
                operation, ignored_pid=int(message.get("ignored_pid", 0)),
                expected=message.get("expected"), direction=message.get("direction", ""),
                plan=message.get("plan", ""), progress=message.get("progress", 1.0))}
        if operation == "sentence_key":
            from .wechat_native_keys import _send_key_direct
            _send_key_direct(message["command"], message["count"], message["event_tag"],
                             message["bundle"], expected_pid=message["pid"])
            return {"result": None}
        if operation == "capture":
            options = dict(plain_enter=bool(message.get("plain_enter", False)),
                           menu_action=bool(message.get("menu_action", False)))
            if message.get("scene"):
                options["scene"] = True
            target = self.shortcuts.capture(**options)
            if target is None:
                return {"result": None}
            handle = uuid.uuid4().hex
            self.targets[handle] = target
            while len(self.targets) > 64:
                self.targets.popitem(last=False)
            # AX objects stay in this process. A worker restart invalidates all
            # captured targets; never silently recapture one for an old send.
            return {"result": {"bundle": target.bundle, "pid": target.pid, "profile": target.profile,
                               "role": target.role, "blocked": target.blocked, "remote_id": handle,
                               "plain_enter": target.plain_enter, "menu_action": target.menu_action,
                               "scene": target.scene, "scene_checked": target.scene_checked,
                               "input_context": target.input_context,
                               "website": target.website, "page_key": target.page_key}}
        if operation in {"same_target", "shortcut"}:
            target = self.targets.get(message["target"])
            if target is None:
                if operation == "same_target":
                    return {"result": False}
                raise RuntimeError("按键通道或目标窗口已变化，请重新触发手势")
            if operation == "same_target":
                return {"result": self.shortcuts.same_target(target, require_focus=message["require_focus"])}
            self.shortcuts.post(target, message["shortcut"], require_focus=message["require_focus"])
            return {"result": None}
        raise ValueError("未知按键通道请求")


def main():
    import objc
    dispatcher = Dispatcher()
    for line in sys.stdin.buffer:
        message = {}
        try:
            if len(line) > 65536:
                raise ValueError("按键通道请求过长")
            message = json.loads(line)
            with objc.autorelease_pool():
                reply = dispatcher.handle(message)
        except MacPermissionError as exc:
            reply = {"error": str(exc), "permissions": asdict(replace(
                exc.state, control_channel="worker", control_pid=os.getpid()))}
        except Exception as exc:
            reply = {"error": str(exc)}
        sys.stdout.write(json.dumps({"id": message.get("id"), **reply}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
