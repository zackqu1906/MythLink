"""Private-pipe worker for the application's permission-sensitive operations."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, replace
import json
import math
import os
import sys
import time
import uuid

from .mac_permissions import MacPermissionError, read_permission_state
from ...diagnostics.scene_diagnostics import SceneActionError, safe_data, exception_details
from ...touchpad.click_diagnostics import click_record, start_click_diagnostics


TOUCHPAD_REQUEST_MAX_AGE = .250


class Dispatcher:
    def __init__(self):
        from ...desktop.app_shortcuts import LocalMacAppShortcuts
        # This worker is spawned directly by the UI over private pipes.
        self.host_pid = os.getppid()
        self.shortcuts = LocalMacAppShortcuts(host_pid=self.host_pid)
        self.targets = OrderedDict()
        self.text_focus = None
        self.page_scroll = None
        self.window_selector = None
        self.agent_focus = None
        self.touchpad_mouse = None
        self.last_touchpad_receipt = None
        self._observed_page = None
        self._observed_page_token = ''

    def _page_token(self, target):
        # Keep only one read-only identity, never an AX handle usable for sends.
        # URL hashes alone cannot distinguish two tabs showing the same URL.
        if not target.page_key or target.web_area is None:
            return ''
        identity = (target.bundle, target.pid, target.window, target.web_area, target.page_key,
                    target.player if target.page_key == 'native-pdf' else None)
        if identity != self._observed_page:
            self._observed_page = identity
            self._observed_page_token = uuid.uuid4().hex
        return self._observed_page_token

    def handle(self, message):
        operation = message["operation"]
        if operation == "application_candidates":
            from ...desktop.application_menus import application_candidates
            return {"result": application_candidates(host_pid=self.host_pid)}
        if operation == "selector_cancel":
            # Session cleanup needs no AX access and must not request permission.
            result = (self.window_selector.handle(operation, token=message.get("token", ""))
                      if self.window_selector else {"status": "cancelled"})
            return {"result": result}
        if operation in {"touchpad_enable", "touchpad_apply", "touchpad_double_click_target",
                         "touchpad_health"}:
            # Touchpad delivery uses its own background permission monitor.
            # Do not put the generic synchronous probe back on this hot path.
            try:
                return self._touchpad(message)
            except PermissionError:
                raise MacPermissionError(replace(read_permission_state(),
                    control_channel="worker", control_pid=os.getpid())) from None
        state = replace(read_permission_state(), control_channel="worker", control_pid=os.getpid())
        if operation == "status":
            return {"permissions": asdict(state)}
        if operation in {"agent_focus_capture", "agent_focus_restore"}:
            if state.accessibility is not True:
                raise MacPermissionError(state)
            if self.agent_focus is None:
                from proximic_ring.agent_workflow.agent.agent_focus import AgentFocusMemory
                self.agent_focus = AgentFocusMemory()
            result = (self.agent_focus.capture(int(message.get('host_pid', 0)))
                      if operation == 'agent_focus_capture' else self.agent_focus.restore())
            return {"result": result}
        if operation == "application_menu":
            if state.accessibility is not True:
                raise MacPermissionError(state)
            from ...desktop.application_menus import read_application_menu
            return {"result": read_application_menu(str(message["bundle"]), host_pid=self.host_pid)}
        if operation == "scene_observe":
            # Read-only scene notice probe. No target handles, activation or keys;
            # observing a mode requires AX access but not permission to post keys.
            if state.accessibility is not True:
                raise MacPermissionError(state)
            target = self.shortcuts.capture(menu_action=True, scene=True, scene_observation=True)
            if target is None:
                return {"result": None, "diagnostic": getattr(self.shortcuts, "last_diagnostic", {})}
            if (target.bundle != message.get("expected_bundle")
                    or target.pid != message.get("expected_pid")):
                return {"result": None, "diagnostic": {"reason": "foreground_changed"}}
            return {"result": dict(bundle=target.bundle, pid=target.pid, scene=target.scene,
                                    blocked=target.blocked, input_context=target.input_context,
                                    page_token=self._page_token(target),
                                    trace_id=target.trace_id, diagnostic=target.diagnostic)}
        if operation in {"focus_probe", "focus_plan", "focus_apply", "focus_selection"}:
            # Normal AX focus only needs Accessibility. The bounded Safari
            # click fallback checks event-posting permission at point of use.
            if state.accessibility is not True:
                raise MacPermissionError(state)
            if self.text_focus is None:
                from ...desktop.text_focus import TextFocusSession
                self.text_focus = TextFocusSession()
            if message.get("scene_apps") or message.get("voice_disabled_apps"):
                target = self.shortcuts.capture(menu_action=True, scene=bool(message.get("scene_apps")))
                configured = message.get("scene_apps", {})
                disabled = target and target.bundle in message.get("voice_disabled_apps", [])
                eligible = (target and target.bundle in configured and target.scene and not target.blocked
                            and (target.input_context == "nontext"
                                 or (target.scene == "presentation" and target.input_context == "unknown"))
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
                from ...desktop.window_selector import WindowSelectorSession
                self.window_selector = WindowSelectorSession()
            if operation == "selector_warmup":
                return {"result": {"status": "ready"}}  # Imports only, no window enumeration.
            result = self.window_selector.handle(
                operation, token=message.get("token", ""), target=message.get("target", ""),
                host_pid=int(message.get("host_pid", 0)), host_window=message.get("host_window"),
                expected=message.get("expected"))
            return {"result": result, "diagnostic": safe_data(self.window_selector.last_diagnostic)}
        # Metadata discovery does not send events. Check event-posting access
        # only for delivery; an unavailable write permission must not hide hints.
        read_only = operation in {"capture", "same_target", "scroll_plan", "stroke_capture", "browser_action"}
        if state.accessibility is not True or (not read_only and not state.ready):
            raise MacPermissionError(state)
        if operation in {"scroll_plan", "scroll_apply"}:
            if self.page_scroll is None:
                from ...desktop.page_scroll import PageScrollSession
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
        if operation in {"capture", "stroke_capture"}:
            options = dict(plain_enter=operation == "stroke_capture" or bool(message.get("plain_enter", False)),
                           menu_action=bool(message.get("menu_action", False)))
            if message.get("scene"):
                options["scene"] = True
            if operation == "capture" and message.get("focus_sensitive") is False:
                options["focus_sensitive"] = False
            target = self.shortcuts.capture(**options)
            if target is None:
                return {"result": None, "diagnostic": getattr(self.shortcuts, "last_diagnostic", {})}
            if operation == "stroke_capture":
                from ...touchpad.stroke.stroke_focus import editable_target, point_matches_focus
                point = message.get("point")
                if not editable_target(target) or (point and not point_matches_focus(point, target)):
                    return {"result": {"eligible": False, "bundle": target.bundle, "pid": target.pid}}
            handle = uuid.uuid4().hex
            self.targets[handle] = target
            while len(self.targets) > 64:
                self.targets.popitem(last=False)
            # AX objects stay in this process. A worker restart invalidates all
            # captured targets; never silently recapture one for an old send.
            return {"result": {**({"eligible": True} if operation == "stroke_capture" else {}),
                               "bundle": target.bundle, "pid": target.pid, "profile": target.profile,
                               "role": target.role, "blocked": target.blocked, "remote_id": handle,
                               "plain_enter": target.plain_enter, "menu_action": target.menu_action,
                               "scene": target.scene, "scene_checked": target.scene_checked,
                               "input_context": target.input_context,
                               "focus_sensitive": target.focus_sensitive,
                               "page_key": target.page_key, "browser_target": target.browser_target,
                               "trace_id": target.trace_id, "diagnostic": target.diagnostic},
                    "diagnostic": target.diagnostic}
        if operation in {"same_target", "shortcut", "browser_action"}:
            target = self.targets.get(message["target"])
            if target is None:
                if operation == "same_target":
                    return {"result": False, "diagnostic": {"reason": "target_expired"}}
                raise SceneActionError("target_expired")
            if operation == "same_target":
                valid = self.shortcuts.same_target(target, require_focus=message["require_focus"])
                return {"result": valid, "diagnostic": getattr(self.shortcuts, "last_diagnostic", {})}
            if operation == "browser_action":
                self.shortcuts.browser_action(target, message["action"])
            else:
                self.shortcuts.post(target, message["shortcut"], require_focus=message["require_focus"])
            return {"result": None, "diagnostic": getattr(self.shortcuts, "last_diagnostic", {})}
        raise ValueError("未知按键通道请求")

    def _touchpad(self, message):
        self.last_touchpad_receipt = None
        operation = message["operation"]
        if operation == "touchpad_enable":
            start_click_diagnostics()
            from .native_touchpad import WorkerTouchpadMouse
            if self.touchpad_mouse is not None:
                self.touchpad_mouse.disable()
            self.touchpad_mouse = WorkerTouchpadMouse(int(message["cancel_fd"]))
            self.touchpad_mouse.gain = float(message["gain"])
            self.touchpad_mouse.clicks_enabled = bool(message["clicks"])
            self.touchpad_mouse.invert_y = bool(message["invert_y"])
            self.touchpad_mouse.enable()  # One initial check; no input events.
            return {"result": {"pid": os.getpid(),
                               "app_nap_protected": self.touchpad_mouse.app_nap_protected}}
        if operation == "touchpad_health":
            if self.touchpad_mouse is None:
                raise RuntimeError("鼠标控制通道已结束，请重新开启触摸板")
            self.touchpad_mouse.check_health()
            return {"result": None}
        return self._touchpad_request(message)

    def _touchpad_request(self, message):
        arrived = time.monotonic()
        age = arrived - float(message["created"])
        operation = message["operation"]
        trace = message.get("click_trace")
        mouse = self.touchpad_mouse
        before = {name: getattr(mouse, name, 0) for name in
                  ('moves', 'clicks', 'click_down_posts', 'click_up_posts')}
        result = dict(ipc_age_ms=round(age*1000, 3) if math.isfinite(age) else None,
                      ipc_limit_ms=round(TOUCHPAD_REQUEST_MAX_AGE*1000))
        if trace:
            click_record({**trace, **result, 'stage': 'native_received', 'operation': operation},
                         native=True)
        target = None
        try:
            if mouse is None:
                raise RuntimeError("鼠标控制通道已结束，请重新开启触摸板")
            mouse.check_health()  # Cached result, never a synchronous OS probe.
            if operation == "touchpad_apply":
                events = message["events"]
                if not isinstance(events, list) or len(events) > 32:
                    raise ValueError("鼠标事件批次无效")
            if not math.isfinite(age):
                result['reason'] = 'ipc_invalid_timestamp'
            elif age < 0:
                result['reason'] = 'ipc_future_timestamp'
            elif age > TOUCHPAD_REQUEST_MAX_AGE:
                result['reason'] = 'ipc_expired'
            elif not mouse.enabled:
                result['reason'] = 'native_disabled'
            elif operation == "touchpad_apply":
                mouse.apply(events)
                result['reason'] = ('posted' if mouse.clicks > before['clicks'] else
                                    'clicks_disabled' if not mouse.clicks_enabled else
                                    'native_disabled' if not mouse.enabled else 'no_click_posted')
            else:
                target = mouse.double_click_target()
                result['reason'] = 'double_target_ready' if target else 'double_target_unavailable'
        except Exception as exc:
            result.update(reason='permission_denied' if isinstance(exc, PermissionError) else 'native_error',
                          error_type=type(exc).__name__)
            raise
        finally:
            result.update(moves=getattr(mouse, 'moves', 0)-before['moves'],
                          clicks_posted=getattr(mouse, 'clicks', 0)-before['clicks'],
                          down_posts=getattr(mouse, 'click_down_posts', 0)-before['click_down_posts'],
                          up_posts=getattr(mouse, 'click_up_posts', 0)-before['click_up_posts'],
                          native_ms=round((time.monotonic()-arrived)*1000, 3))
            self.last_touchpad_receipt = result
            if trace:
                click_record({**trace, **result, 'stage': 'native_result', 'operation': operation},
                             native=True)
        if operation == "touchpad_apply":
            return {"result": result}
        return {"result": {**result, 'target': target} if trace else target}


def main():
    from .mac_app_activation import configure_app_activation

    # NSWorkspace/menu reads can register this executable as an application.
    # Set the policy first, including when a denied worker is being restarted.
    configure_app_activation(background=True)
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
            reply["diagnostic"] = {"reason": "permission_denied", "accessibility": exc.state.accessibility,
                                   "post_events": exc.state.post_events}
        except SceneActionError as exc:
            reply = {"error": str(exc), "diagnostic": {**exc.diagnostic, "reason": exc.reason}}
        except Exception as exc:
            details = (getattr(dispatcher.shortcuts, "last_diagnostic", {})
                       if message.get("operation") in {"capture", "shortcut", "same_target"} else {})
            if message.get("operation", "").startswith("selector_"):
                details = getattr(dispatcher.window_selector, "last_diagnostic", {})
            reply = {"error": str(exc), "diagnostic": {**details, "reason": "native_exception", **exception_details(exc)}}
        if (message.get("operation") in {"touchpad_apply", "touchpad_double_click_target"}
                and message.get("click_trace") and dispatcher.last_touchpad_receipt is not None):
            reply.setdefault("diagnostic", {})["click_receipt"] = dispatcher.last_touchpad_receipt
        if (message.get("operation") in {"capture", "shortcut", "same_target"}
                or message.get("operation", "").startswith("selector_")):
            reply["diagnostic"] = safe_data({**reply.get("diagnostic", {}), "worker_pid": os.getpid(),
                                             "operation": message.get("operation")})
        sys.stdout.write(json.dumps({"id": message.get("id"), **reply}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    if dispatcher.touchpad_mouse is not None:
        dispatcher.touchpad_mouse.disable()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
