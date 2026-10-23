import types

import pytest

from proximic_ring.agent_workflow.agent import network_env


@pytest.fixture
def mac_proxy(monkeypatch):
    monkeypatch.setattr(network_env.sys, 'platform', 'darwin')
    monkeypatch.setattr(network_env, 'os', types.SimpleNamespace(environ={'PATH':'/bin'}))
    monkeypatch.setattr(network_env.urllib.request, 'getproxies', lambda: {
        'http':'http://127.0.0.1:7897', 'https':'http://127.0.0.1:7897'})
    monkeypatch.setitem(network_env.sys.modules, '_scproxy', types.SimpleNamespace(
        _get_proxy_settings=lambda:dict(exceptions=['*.local', '10.0.0.0/8', 'localhost'])))


def test_child_uses_enabled_system_proxy_and_bypasses_local_endpoints(mac_proxy):
    env = network_env.app_server_environment()
    assert env['HTTPS_PROXY'] == 'http://127.0.0.1:7897'
    assert env['HTTP_PROXY'] == env['HTTPS_PROXY']
    assert set(env['NO_PROXY'].split(',')) == {
        'localhost', '127.0.0.1', '::1', '*.local', '10.0.0.0/8'}
    assert network_env.os.environ == {'PATH':'/bin'}


@pytest.mark.parametrize('key,value', [
    ('HTTPS_PROXY', 'http://custom:1234'), ('http_proxy', ''),
    ('ALL_PROXY', 'socks5://custom:1234'), ('NO_PROXY', '*')])
def test_explicit_routing_is_not_overridden(mac_proxy, key, value):
    network_env.os.environ[key] = value
    assert network_env.app_server_environment() == dict(network_env.os.environ)


def test_no_static_proxy_does_not_invent_routing(mac_proxy, monkeypatch):
    monkeypatch.setattr(network_env.urllib.request, 'getproxies', lambda:{})
    assert network_env.app_server_environment() == {'PATH':'/bin'}


def test_unavailable_system_proxy_keeps_original_environment(mac_proxy, monkeypatch):
    def unavailable():
        raise OSError('system configuration unavailable')
    monkeypatch.setattr(network_env.urllib.request, 'getproxies', unavailable)
    assert network_env.app_server_environment() == {'PATH':'/bin'}


def test_other_platforms_keep_original_environment(mac_proxy, monkeypatch):
    monkeypatch.setattr(network_env.sys, 'platform', 'linux')
    assert network_env.app_server_environment() == {'PATH':'/bin'}
