from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from proximic_ring.mac_permissions import MacPermissionError, PermissionState
from proximic_ring.native_access import NativeAccessChannel


def test_process_cached_denial_recovers_without_restarting_host(tmp_path, monkeypatch):
    """A real child caches the initial grant, just as affected CG builds do."""
    permission = tmp_path / "permission"
    permission.write_text("0")
    actions = tmp_path / "actions"
    worker = tmp_path / "cached_worker.py"
    worker.write_text('''import sys, json, os
from pathlib import Path
permission, actions = map(Path, sys.argv[1:])
cached_post = permission.read_text() == "1"
for line in sys.stdin:
    m = json.loads(line)
    ax = permission.read_text() == "1"
    if m["operation"] == "status" or not (ax and cached_post):
        r = {"permissions": {"accessibility": ax, "post_events": cached_post,
                             "control_pid": os.getpid(), "control_channel": "worker"}}
        if m["operation"] != "status": r["error"] = "denied"
    else:
        with actions.open("a") as f: f.write("action\\n")
        r = {"result": os.getpid()}
    print(json.dumps({"id": m["id"], **r}), flush=True)
''')
    channel = NativeAccessChannel()
    starts = []
    def start():
        if channel._process is None:
            channel._process = subprocess.Popen([sys.executable, str(worker), str(permission), str(actions)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            starts.append(channel._process.pid)
    monkeypatch.setattr(channel, "_start", start)
    host_pid = os.getpid()
    try:
        assert not channel.permissions().ready
        assert channel._process is None
        permission.write_text("1")
        ready = channel.permissions()
        assert ready.ready and ready.control_pid != starts[0]
        assert not actions.exists()  # Grant itself does not replay any key.
        worker_pid = channel.call("sentence_key")
        assert worker_pid == ready.control_pid
        assert channel.call("sentence_key") == worker_pid  # warm channel reused
        assert len(starts) == 2 and os.getpid() == host_pid
        permission.write_text("0")
        with pytest.raises(MacPermissionError):
            channel.call("sentence_key")
        assert actions.read_text() == "action\naction\n"
        assert channel._process is None
        permission.write_text("1")
        assert channel.call("sentence_key") not in starts[:2]
        assert os.getpid() == host_pid
    finally:
        channel.close()


def test_lost_reply_is_not_replayed(tmp_path, monkeypatch):
    marker = tmp_path / "posted"
    channel = NativeAccessChannel()
    starts = []
    def start():
        process = subprocess.Popen([sys.executable, "-c",
            "import sys,pathlib;sys.stdin.readline();pathlib.Path(sys.argv[1]).write_text('posted')", str(marker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        starts.append(process.pid)
        channel._process = process
    monkeypatch.setattr(channel, "_start", start)
    try:
        with pytest.raises(RuntimeError, match="断开"):
            channel.call("shortcut")
        assert marker.read_text() == "posted"
        assert len(starts) == 1 and channel._process is None
    finally:
        channel.close()


def test_large_slow_menu_reply_uses_metadata_budget(tmp_path, monkeypatch):
    worker = tmp_path / "large_menu.py"
    worker.write_text('''import sys, json, time
m = json.loads(sys.stdin.readline())
time.sleep(2.2)
actions = [{"label": "Long application menu item " * 15, "shortcut": "Cmd+N"} for _ in range(800)]
print(json.dumps({"id": m["id"], "result": {"actions": actions}}), flush=True)
''')
    channel = NativeAccessChannel()
    def start():
        channel._process = subprocess.Popen([sys.executable, str(worker)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    monkeypatch.setattr(channel, "_start", start)
    try:
        result = channel.call("application_menu", bundle="com.example.LargeMenu")
        assert len(result["actions"]) == 800
        assert len(json.dumps(result)) > 262144
    finally:
        channel.close()


def test_frozen_channel_launches_same_executable_without_source_python(monkeypatch):
    import io
    import proximic_ring.native_access as module
    calls = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Mythlink.app/Contents/MacOS/Mythlink")
    monkeypatch.setattr(module.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(stderr=io.BytesIO(), pid=42))
    channel = NativeAccessChannel()
    channel._start()
    assert calls[0][0] == [sys.executable, "--native-access-worker"]
    assert calls[0][1]["stdin"] == subprocess.PIPE


def test_worker_keeps_ax_targets_local_and_rejects_handles_after_restart(monkeypatch):
    import proximic_ring.native_access_worker as worker
    from proximic_ring.app_shortcuts import ShortcutTarget
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, True))
    target = ShortcutTarget("com.openai.codex", 42, "codex", object(), object(), "AXTextArea")
    posted = []
    first = worker.Dispatcher()
    first.shortcuts = SimpleNamespace(capture=lambda **kw: target,
        same_target=lambda t, **kw: t is target,
        post=lambda t, shortcut, **kw: posted.append((t, shortcut)))
    captured = first.handle({"operation": "capture"})["result"]
    assert "window" not in captured and "focus" not in captured
    json.dumps(captured)
    command = {"operation": "shortcut", "target": captured["remote_id"], "shortcut": "Enter", "require_focus": True}
    second = worker.Dispatcher()
    with pytest.raises(RuntimeError, match="失效") as error:
        second.handle(command)
    assert error.value.reason == "target_expired"
    assert not posted
    first.handle(command)
    assert posted == [(target, "Enter")]


def test_worker_event_permission_denial_prevents_writes(monkeypatch):
    import proximic_ring.native_access_worker as worker
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    dispatcher = worker.Dispatcher()
    for operation in ["shortcut", "sentence_key", "scroll_apply"]:
        with pytest.raises(MacPermissionError):
            dispatcher.handle({"operation": operation})
    assert dispatcher.handle({"operation": "status"})["permissions"]["post_events"] is False


@pytest.mark.parametrize('post_events', [False, None])
def test_readonly_shortcut_and_scroll_discovery_need_no_event_posting_permission(monkeypatch, post_events):
    import proximic_ring.native_access_worker as worker
    from proximic_ring.app_shortcuts import ShortcutTarget
    monkeypatch.setattr(worker, 'read_permission_state', lambda: PermissionState(True, post_events))
    target = ShortcutTarget('example.app', 42, 'example.app')
    dispatcher = worker.Dispatcher()
    calls = []
    dispatcher.shortcuts = SimpleNamespace(capture=lambda **kw: target,
        same_target=lambda t, **kw: t is target,
        post=lambda *args, **kw: calls.append('post'))
    dispatcher.page_scroll = SimpleNamespace(handle=lambda operation, **kw: calls.append(operation) or {'status': 'ready'})
    captured = dispatcher.handle({'operation': 'capture', 'menu_action': True})['result']
    assert dispatcher.handle({'operation': 'same_target', 'target': captured['remote_id'], 'require_focus': False})['result']
    assert dispatcher.handle({'operation': 'scroll_plan'})['result'] == {'status': 'ready'}
    for command in [dict(operation='shortcut', target=captured['remote_id'], shortcut='Cmd+N', require_focus=False),
                    dict(operation='scroll_apply')]:
        with pytest.raises(MacPermissionError):
            dispatcher.handle(command)
    assert calls == ['scroll_plan']


@pytest.mark.parametrize('accessibility', [False, None])
@pytest.mark.parametrize('operation', ['capture', 'same_target', 'scroll_plan', 'shortcut', 'scroll_apply'])
def test_ax_permission_is_still_required_for_target_reads_and_writes(monkeypatch, accessibility, operation):
    import proximic_ring.native_access_worker as worker
    monkeypatch.setattr(worker, 'read_permission_state', lambda: PermissionState(accessibility, True))
    with pytest.raises(MacPermissionError):
        worker.Dispatcher().handle({'operation': operation})


def test_window_selector_uses_only_ax_permission_and_never_key_posting(monkeypatch):
    import proximic_ring.native_access_worker as worker
    from test_window_selector import Desktop
    d = Desktop()
    dispatcher = worker.Dispatcher()
    dispatcher.window_selector = d.session
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    result = dispatcher.handle({"operation": "selector_list"})["result"]
    assert result["status"] == "ready"
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(False, True))
    with pytest.raises(MacPermissionError):
        dispatcher.handle({"operation": "selector_activate", "token": result["token"], "target": result["cards"][2]["id"]})
    assert not d.activated
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    assert dispatcher.handle({"operation": "selector_activate", "token": result["token"],
                              "target": result["cards"][2]["id"]})["result"] == {"status": "activated"}
    assert d.activated == [(10, d.b)]


def test_windowless_selector_diagnostics_cross_worker_boundary_without_window_content(monkeypatch):
    import proximic_ring.native_access_worker as worker
    from test_window_selector import Desktop
    d = Desktop()
    d.stamp = {"pid": 0, "window": 0}
    dispatcher = worker.Dispatcher()
    dispatcher.window_selector = d.session
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    reply = dispatcher.handle({"operation": "selector_list"})
    assert reply["result"]["status"] == "ready"
    diagnostic = reply["diagnostic"]
    assert diagnostic["origin_visible"] is False and diagnostic["screen_source"] == "pointer"
    assert diagnostic["card_count"] == 3
    assert "Alpha" not in json.dumps(diagnostic) and "title" not in diagnostic


def test_focus_uses_ax_permission_and_keeps_plans_inside_worker(monkeypatch):
    import proximic_ring.native_access_worker as worker
    from test_text_focus import Desktop
    d = Desktop()
    dispatcher = worker.Dispatcher()
    dispatcher.text_focus = d.session
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    result = dispatcher.handle({"operation": "focus_plan", "action": "next"})["result"]
    assert result["count"] == 2 and isinstance(result["plan"], str)
    json.dumps(result)  # No AX nodes or text are passed across the pipe.
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(False, False))
    with pytest.raises(MacPermissionError):
        dispatcher.handle({"operation": "focus_apply", "plan": result["plan"]})
    assert not d.writes


def test_scroll_worker_keeps_targets_private_and_checks_event_posting_permission(monkeypatch):
    import proximic_ring.native_access_worker as worker
    from test_page_scroll import ScrollDesktop
    d = ScrollDesktop()
    dispatcher = worker.Dispatcher()
    dispatcher.page_scroll = d.scroller
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, True))
    result = dispatcher.handle({"operation": "scroll_plan", "direction": "down", "expected": d.stamp})["result"]
    assert result["status"] == "ready" and isinstance(result["plan"], str)
    json.dumps(result)
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    with pytest.raises(MacPermissionError):
        dispatcher.handle({"operation": "scroll_apply", "plan": result["plan"]})
    assert not d.scrolls
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, True))
    assert dispatcher.handle({"operation": "scroll_apply", "plan": result["plan"]})["result"]["status"] == "posted"
    assert len(d.scrolls) == 1


def test_sentence_key_pins_pid_in_request(monkeypatch):
    import proximic_ring.wechat_native_keys as keys
    import proximic_ring.native_access as channel
    calls = []
    app = SimpleNamespace(bundleIdentifier=lambda: "com.microsoft.VSCode", processIdentifier=lambda: 123)
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSWorkspace=SimpleNamespace(
        sharedWorkspace=lambda: SimpleNamespace(frontmostApplication=lambda: app))))
    monkeypatch.setattr(channel, "native_access", lambda: SimpleNamespace(call=lambda *a, **kw: calls.append((a, kw))))
    keys.send_input_method_key("com.microsoft.VSCode", "delete", 0, 789)
    assert calls == [(("sentence_key",), {"command": "delete", "count": 0, "event_tag": 789,
                                          "bundle": "com.microsoft.VSCode", "pid": 123})]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS worker")
def test_real_source_worker_status_uses_public_apis_without_events():
    process = subprocess.run([sys.executable, "-B", "-m", "proximic_ring.native_access_worker"],
        input='{"id":1,"operation":"status"}\n', capture_output=True, text=True, timeout=5)
    assert process.returncode == 0, process.stderr
    reply = json.loads(process.stdout)
    assert reply["id"] == 1 and not reply.get("error")
    state = PermissionState(**reply["permissions"])
    assert not state.error and state.control_pid != os.getpid()
    assert type(state.accessibility) is bool and type(state.post_events) is bool
