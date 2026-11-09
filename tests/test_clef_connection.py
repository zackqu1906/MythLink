from dataclasses import replace
import io
import json
import time
from urllib import error, request

import pytest

from proximic_ring.text_processing.clef_connection import (
    ClefServerSettings, ClefConnection, ConnectedClefRouter,
)


@pytest.mark.parametrize('host,port,expected', [
    ('mythlink.cn', 20031, 'http://mythlink.cn:20031/v1/systemone'),
    (' https://example.com/ ', 20032, 'https://example.com:20032/v1/systemone'),
    ('127.0.0.1', 30000, 'http://127.0.0.1:30000/v1/systemone'),
    ('::1', 20031, 'http://[::1]:20031/v1/systemone'),
    ('http://[::1]', 20031, 'http://[::1]:20031/v1/systemone'),
])
def test_public_endpoint_without_ssh(host, port, expected, monkeypatch):
    monkeypatch.setattr('subprocess.Popen', lambda *a, **k: pytest.fail('must not start SSH'))
    assert ClefConnection(ClefServerSettings(host=host, port=port)).endpoint() == expected


@pytest.mark.parametrize('values', [
    dict(host=''), dict(host='ssh root@example.com'), dict(host='host;command'),
    dict(host='ssh://example.com'), dict(host='http://user:secret@example.com'),
    dict(host='example.com/path'), dict(host='example.com?x=1'),
    dict(host='example.com#fragment'), dict(host='example.com:20031'),
    dict(host='example.com:'), dict(host='http://[broken'),
    dict(host='host', port=0), dict(host='host', port=65536), dict(host='host', port=True),
])
def test_invalid_public_configuration_rejected(values):
    with pytest.raises(ValueError):
        ClefConnection(ClefServerSettings(**values)).endpoint()


def test_reconfigure_and_shutdown():
    conn = ClefConnection(ClefServerSettings(host='one.example'))
    conn.configure(replace(conn.settings, host='two.example', port=20032))
    assert conn.endpoint() == 'http://two.example:20032/v1/systemone'
    conn.shutdown()
    with pytest.raises(RuntimeError, match='已关闭'):
        conn.endpoint()
    with pytest.raises(RuntimeError, match='已关闭'):
        conn.configure(ClefServerSettings(host='other.example'))
    conn.close()


@pytest.mark.parametrize('raw', [b'', b'not json', b'[]', b'x' * 16385,
    b'{"status":"ready","model":"other"}', b'{"status":"loading","model":"clef-flash"}'])
def test_health_requires_real_clef_ready(raw, monkeypatch):
    conn = ClefConnection(ClefServerSettings(host='host'))
    monkeypatch.setattr(conn.opener, 'open', lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(RuntimeError, match='未返回已就绪'):
        conn.check()


@pytest.mark.parametrize('failure', [error.URLError('private details'),
    OSError('private details'), error.HTTPError('http://host', 502, 'private details', {}, io.BytesIO())])
def test_network_error_is_useful_without_response_secrets(failure, monkeypatch):
    conn = ClefConnection(ClefServerSettings(host='host'))
    def fail(*a, **kw):
        raise failure
    monkeypatch.setattr(conn.opener, 'open', fail)
    with pytest.raises(RuntimeError, match='公网') as caught:
        conn.check()
    assert 'private details' not in str(caught.value)


def classification_response(req):
    questions = json.loads(req.data)['questions']
    return io.BytesIO(json.dumps({'model': 'clef-flash', 'answers': {
        name: {'type': 'choice', 'choice': 'dictation' if name == 'voice_intent' else 'skip',
               'probabilities': {k: float(k in {'dictation', 'skip'}) for k in value['criteria']}}
        for name, value in questions.items()}}).encode())


def test_saved_public_address_ignores_developer_url_key_and_proxy(monkeypatch):
    monkeypatch.setenv('PROXIMIC_CLEF_URL', 'https://wrong.example/v1/systemone')
    monkeypatch.setenv('PROXIMIC_CLEF_API_KEY', 'developer-secret')
    monkeypatch.setenv('http_proxy', 'http://wrong.example:1234')
    monkeypatch.setenv('https_proxy', 'http://wrong.example:1234')
    conn = ClefConnection(ClefServerSettings(host='example.com'))
    assert not any(isinstance(h, request.ProxyHandler) and h.proxies for h in conn.opener.handlers)
    calls = []
    def respond(req, **kw):
        calls.append(req)
        # A new setting affects the next request, not the one being sent.
        conn.configure(ClefServerSettings(host='new.example', port=20032))
        return classification_response(req)
    monkeypatch.setattr(conn.opener, 'open', respond)
    router = ConnectedClefRouter(conn)
    decision, trace = router.classify('普通听写')
    assert decision.task == 'dictation' and len(calls) == 1
    assert calls[0].full_url == 'http://example.com:20031/v1/systemone'
    assert calls[0].get_header('Authorization') is None
    assert 'developer-secret' not in trace
    router.classify('下一句')
    assert calls[1].full_url == 'http://new.example:20032/v1/systemone'


def settings_controller(tmp_path):
    from PySide6.QtCore import QSettings
    from proximic_ring.ui.voice.clef_settings_controller import ClefSettingsController
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    return settings, ClefSettingsController


def wait_for_connection(ui):
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance()
    deadline = time.monotonic() + 3
    while ui.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert not ui.busy


def test_migration_reuses_only_ssh_host(tmp_path):
    settings, Controller = settings_controller(tmp_path)
    for key, value in dict(host='mythlink.cn', port=20030, user='root',
                           key_file='/never/read/key', service_port=30000).items():
        settings.setValue('clef/ssh/' + key, value)
    ui = Controller(settings)
    assert ui.config == dict(host='mythlink.cn', port=20031)
    settings.setValue('clef/http/host', 'new.example')
    settings.setValue('clef/http/port', 20032)
    restored = Controller(settings)
    assert restored.config == dict(host='new.example', port=20032)
    ui.connection.close()
    restored.connection.close()


def test_public_settings_persist_and_test_real_inference(tmp_path, monkeypatch):
    settings, Controller = settings_controller(tmp_path)
    ui = Controller(settings)
    calls = []
    def respond(req, **kw):
        calls.append(req)
        if isinstance(req, str):
            return io.BytesIO(b'{"status":"ready","model":"clef-flash"}')
        return classification_response(req)
    monkeypatch.setattr(ui.connection.opener, 'open', respond)
    ui.connectServer(dict(host='example.com', port='20032'))
    wait_for_connection(ui)
    assert '连接成功' in ui.status
    assert calls[0] == 'http://example.com:20032/health'
    assert calls[1].full_url == 'http://example.com:20032/v1/systemone'
    assert set(settings.allKeys()) == {'clef/http/host', 'clef/http/port'}
    restored = Controller(settings)
    assert restored.config == dict(host='example.com', port=20032)
    ui.connection.close()
    restored.connection.close()


def test_health_success_but_inference_failure_does_not_report_connected(tmp_path, monkeypatch):
    settings, Controller = settings_controller(tmp_path)
    ui = Controller(settings)
    monkeypatch.setattr(ui.connection, 'check', lambda: None)
    def fail(*args):
        raise RuntimeError('分类接口不可用')
    monkeypatch.setattr(ui.router, 'classify', fail)
    ui.connectServer(dict(host='example.com', port='20031'))
    wait_for_connection(ui)
    assert ui.status == '分类接口不可用'
    ui.connection.close()
