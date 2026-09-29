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


def test_frozen_channel_launches_same_executable_without_source_python(monkeypatch):
    import proximic_ring.native_access as module
    calls = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Proximic Voice.app/Contents/MacOS/ProximicVoice")
    monkeypatch.setattr(module.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)) or SimpleNamespace())
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
    with pytest.raises(RuntimeError, match="变化"):
        second.handle(command)
    assert not posted
    first.handle(command)
    assert posted == [(target, "Enter")]


def test_worker_denial_prevents_capture_and_writes(monkeypatch):
    import proximic_ring.native_access_worker as worker
    monkeypatch.setattr(worker, "read_permission_state", lambda: PermissionState(True, False))
    dispatcher = worker.Dispatcher()
    for operation in ["capture", "shortcut", "sentence_key", "scroll_plan", "scroll_apply"]:
        with pytest.raises(MacPermissionError):
            dispatcher.handle({"operation": operation})
    assert dispatcher.handle({"operation": "status"})["permissions"]["post_events"] is False


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
