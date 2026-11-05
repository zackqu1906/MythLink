from dataclasses import replace
import io
import json
from pathlib import Path

import pytest

from proximic_ring.text_processing.clef_connection import ClefSSHSettings, ClefSSHConnection, ConnectedClefRouter


@pytest.mark.parametrize("values", [dict(host=""), dict(host="-oProxyCommand=bad"),
    dict(host="ssh root@example.com"), dict(host="host;command"), dict(host="host", user="-x"),
    dict(host="host", port=0), dict(host="host", service_port=65536)])
def test_invalid_ssh_configuration_never_spawns(values, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: pytest.fail("invalid settings must not execute"))
    with pytest.raises(ValueError):
        ClefSSHConnection(ClefSSHSettings(**values)).endpoint()


def test_key_selection_and_no_secrets_in_command(tmp_path):
    key = tmp_path / "my key"
    key.write_text("PRIVATE TEST CONTENT")
    connection = ClefSSHConnection(ClefSSHSettings(host="example.com", key_file=str(key)), directory=tmp_path)
    connection.settings.validate()
    command = connection._command(41235)
    assert command[0] == "/usr/bin/ssh" and command[-1] == "example.com"
    assert command[command.index("-i") + 1] == str(key)
    assert command[command.index("-L") + 1] == "127.0.0.1:41235:127.0.0.1:30000"
    assert "StrictHostKeyChecking=accept-new" in command
    assert "ForwardAgent=no" in command and "BatchMode=yes" in command
    assert "PRIVATE TEST CONTENT" not in repr(command)
    public = tmp_path / "key.pub"
    public.write_text("public")
    with pytest.raises(ValueError, match="不是 .pub"):
        replace(connection.settings, key_file=str(public)).validate()


def test_tunnel_reused_reconnected_and_closed(tmp_path, monkeypatch):
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, addr): assert addr == ("127.0.0.1", 0)
        def getsockname(self): return ("127.0.0.1", 42345)
    class Process:
        dead = False
        def poll(self): return 0 if self.dead else None
        def terminate(self): self.dead = True
        def wait(self, **kwargs): return 0
    processes = []
    def spawn(command, **kwargs):
        assert kwargs["stdin"] == -3 and kwargs["start_new_session"]
        process = Process(); processes.append(process); return process
    monkeypatch.setattr("socket.socket", Socket)
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: Socket())
    monkeypatch.setattr("subprocess.Popen", spawn)
    conn = ClefSSHConnection(ClefSSHSettings(host="example.com"), directory=tmp_path)
    first = conn.endpoint()
    assert conn.endpoint() == first and len(processes) == 1
    processes[0].dead = True
    assert conn.endpoint() == first and len(processes) == 2
    conn.configure(replace(conn.settings, port=20030))
    assert processes[-1].dead
    conn.endpoint()
    conn.shutdown()
    assert processes[-1].dead
    with pytest.raises(RuntimeError, match="已关闭"): conn.endpoint()
    conn.close()
    assert conn._process is None and conn._stderr is None


def test_host_key_change_is_not_silently_accepted(tmp_path):
    conn = ClefSSHConnection(directory=tmp_path)
    conn._stderr = io.BytesIO(b"WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! secret-output")
    assert "身份" in conn._ssh_error() and "secret-output" not in conn._ssh_error()


def test_health_requires_real_clef_ready_not_just_ssh(monkeypatch):
    conn = ClefSSHConnection(ClefSSHSettings(host="host"))
    monkeypatch.setattr(conn, "endpoint", lambda: "http://127.0.0.1:4242/v1/systemone")
    monkeypatch.setattr(conn.opener, "open", lambda *a, **k: io.BytesIO(b'{"status":"ready","model":"other"}'))
    with pytest.raises(RuntimeError, match="Clef 服务未就绪"): conn.check()
    monkeypatch.setattr(conn.opener, "open", lambda *a, **k: io.BytesIO(b'{"status":"ready","model":"clef-flash"}'))
    assert conn.check().endswith("/v1/systemone")


def test_saved_ssh_ignores_developer_url_key_and_proxy(monkeypatch):
    monkeypatch.setenv("PROXIMIC_CLEF_URL", "https://wrong.example/v1/systemone")
    monkeypatch.setenv("PROXIMIC_CLEF_API_KEY", "developer-secret")
    monkeypatch.setenv("https_proxy", "http://wrong.example:1234")
    conn = ClefSSHConnection(ClefSSHSettings(host="example.com"))
    monkeypatch.setattr(conn, "endpoint", lambda: "http://127.0.0.1:4242/v1/systemone")
    calls = []
    def respond(req, **kw):
        calls.append(req)
        questions = json.loads(req.data)["questions"]
        return io.BytesIO(json.dumps({"model":"clef-flash", "answers":{
            name: {"type":"choice", "choice": "dictation" if name == "voice_intent" else "skip",
                   "probabilities": {k: float(k in {"dictation", "skip"}) for k in value["criteria"]}}
            for name, value in questions.items()}}).encode())
    monkeypatch.setattr(conn.opener, "open", respond)
    decision, trace = ConnectedClefRouter(conn).classify("普通听写")
    assert decision.task == "dictation" and len(calls) == 1
    assert calls[0].full_url.startswith("http://127.0.0.1:4242/")
    assert calls[0].get_header("Authorization") is None
    assert "developer-secret" not in trace


def test_settings_persist_without_reading_or_copying_private_key(tmp_path, monkeypatch):
    import threading
    from PySide6.QtCore import QCoreApplication, QSettings
    from proximic_ring.ui.voice.clef_settings_controller import ClefSettingsController
    app = QCoreApplication.instance() or QCoreApplication([])
    settings_path = tmp_path / 'settings.ini'
    saved = QSettings(str(settings_path), QSettings.IniFormat)
    ui = ClefSettingsController(saved)
    monkeypatch.setattr(ui.connection, 'check', lambda: None)
    monkeypatch.setattr(ui.router, 'classify', lambda *a: None)
    finished = threading.Event()
    ui._finished.connect(lambda _: finished.set())
    key = tmp_path / 'identity'; key.write_text('PRIVATE_CONTENT_NOT_A_SETTING')
    ui.connectServer(dict(host='example.com',port='20030',user='alice',key_file=str(key),service_port='30000'))
    import time
    deadline = time.monotonic()+2
    while not finished.is_set() and time.monotonic()<deadline:
        app.processEvents(); time.sleep(.005)
    assert not ui.busy and '连接成功' in ui.status
    restored = ClefSettingsController(QSettings(str(settings_path), QSettings.IniFormat))
    assert restored.config == dict(host='example.com',port=20030,user='alice',key_file=str(key),service_port=30000)
    assert 'PRIVATE_CONTENT_NOT_A_SETTING' not in settings_path.read_text()
    ui.connection.close(); restored.connection.close()
