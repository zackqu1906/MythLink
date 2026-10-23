"""Real broker exchanges, framing, authorization and extension-only recognition."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from proximic_ring.browser_bridge.protocol import clean_snapshot, identity, media_action
from proximic_ring.browser_bridge.server import Broker, BridgeServer
from proximic_ring.browser_bridge.client import post
from proximic_ring.browser_bridge.install import install_chrome, chrome_id, export_extension
from proximic_ring.scenes.recognition.browser import detect_browser
from proximic_ring.scenes.recognition.engine import detect_scene


def snapshot(**changes):
    return dict(session='session-123',window=1,tab=2,page='document:route',frame=0,
                document='document',media='player',source='source',focused=True,authorized=True,
                playing=False,scene='video',input_context='nontext',reason='recognized',**{}) | changes


@pytest.mark.parametrize('bundle', ['com.apple.Safari','com.google.Chrome'])
@pytest.mark.parametrize('scene', ['video','music'])
@pytest.mark.parametrize('context', ['text','nontext','unknown'])
def test_extension_scene_retains_pause_focus_and_opaque_identity(bundle,scene,context):
    result=detect_browser(bundle,profiles={scene:'browser'},snapshot=snapshot(scene=scene,input_context=context))
    assert (result.scene,result.input_context)==(scene,context)
    assert result.page_key and result.player and result.browser_target
    assert result.diagnostic['source']=='extension'


@pytest.mark.parametrize('change', [dict(authorized=False),dict(focused=False),dict(scene=''),dict(error='browser_disconnected')])
def test_unavailable_never_falls_back_to_ax(change):
    result=detect_browser('com.apple.Safari',profiles={'video':'browser'},snapshot=snapshot(**change))
    assert not result.scene


def test_engine_never_reads_browser_ax_contents(monkeypatch):
    import proximic_ring.scenes.recognition.browser as module
    monkeypatch.setattr(module,'request',lambda bundle:snapshot())
    def forbidden(*args): raise AssertionError('browser AX content read')
    result=detect_scene('com.google.Chrome',{'video':'browser'},object(),object(),forbidden)
    assert result.scene=='video'
    assert not detect_browser('org.mozilla.firefox',profiles={'video':'browser'},snapshot=snapshot()).scene
    assert not detect_browser('com.apple.Safari',profiles={'pdf':'browser'},snapshot=snapshot()).scene


def test_protocol_drops_content_and_rejects_bad_identities():
    clean=clean_snapshot(snapshot(url='https://secret',text='private',title='private'))
    assert not {'url','text','title'} & clean.keys()
    for values in [dict(tab=True),dict(media='x'*161),dict(document=''),dict(focused='yes')]:
        with pytest.raises(ValueError): clean_snapshot(snapshot(**values))


@pytest.mark.parametrize('field', ['session','window','tab','page','frame','document','media','source'])
def test_every_identity_dimension_invalidates_a_command(field):
    old=snapshot(); new=snapshot(**{field:3 if field in {'window','tab','frame'} else 'changed'})
    assert identity(old)!=identity(new)
    broker=Broker(); token=broker.connect('chrome','session-123')['token']
    broker.exchange(token,{'snapshot':old})
    assert broker.request('com.google.Chrome','action',new,'play')['error']=='page_changed'


def test_live_request_does_not_return_cached_snapshot_and_action_needs_ack():
    broker=Broker(); token=broker.connect('chrome','session-123')['token']
    broker.exchange(token,{'snapshot':snapshot()})
    with ThreadPoolExecutor() as pool:
        read=pool.submit(broker.request,'com.google.Chrome','snapshot')
        for _ in range(100):
            commands=broker.exchange(token,{})['commands']
            if commands:break
            time.sleep(.005)
        assert not read.done()
        broker.exchange(token,{'replies':[{'id':commands[0]['id'],'snapshot':snapshot(input_context='text')}]})
        assert read.result()['input_context']=='text'
        action=pool.submit(broker.request,'com.google.Chrome','action',snapshot(),'play')
        for _ in range(100):
            commands=broker.exchange(token,{})['commands']
            if commands:break
            time.sleep(.005)
        broker.exchange(token,{'replies':[{'id':commands[0]['id'],'ok':True}]})
        assert action.result()=={'ok':True,'reason':'browser_action_applied'}
        for reply_reason, expected in [('browser_playback_permission_required','browser_playback_permission_required'),
                                       ('private page error','browser_action_failed')]:
            blocked=pool.submit(broker.request,'com.google.Chrome','action',snapshot(),'play')
            for _ in range(100):
                commands=broker.exchange(token,{})['commands']
                if commands:break
                time.sleep(.005)
            broker.exchange(token,{'replies':[{'id':commands[0]['id'],'ok':False,'reason':reply_reason}]})
            assert blocked.result()=={'ok':False,'reason':expected}


def test_timeout_disconnect_and_multi_profile_fail_closed():
    now=[0.]
    broker=Broker(clock=lambda:now[0]); token=broker.connect('chrome','session-123')['token']
    broker.exchange(token,{'snapshot':snapshot()})
    assert broker.request('com.google.Chrome','snapshot',timeout=0)['error']=='browser_timeout'
    assert broker.exchange(token,{})['commands']==[]
    other=broker.connect('chrome','other-session')['token']
    broker.exchange(other,{'snapshot':snapshot(session='other-session')})
    assert broker.request('com.google.Chrome','snapshot')['error']=='browser_ambiguous_window'
    now[0]=4
    assert broker.request('com.google.Chrome','snapshot')['error']=='browser_disconnected'
    with pytest.raises(PermissionError):broker.exchange('invalid',{})


def test_waiting_exchange_wakes_for_commands_and_ack_does_not_wait_for_heartbeat():
    broker = Broker()
    token = broker.connect('chrome', 'session-123')['token']
    with ThreadPoolExecutor() as pool:
        try:
            poll = pool.submit(broker.exchange, token, {'snapshot': snapshot(), 'wait_ms': 1000})
            with broker.condition:
                assert broker.condition.wait_for(lambda: bool(broker.clients[token]['snapshot']), timeout=1)
            read = pool.submit(broker.request, 'com.google.Chrome', 'snapshot')
            commands = poll.result(timeout=.25)['commands']
            assert len(commands) == 1 and not read.done()  # Never serve a cached snapshot.
            ack = pool.submit(broker.exchange, token, {'wait_ms': 1000, 'replies': [
                {'id': commands[0]['id'], 'snapshot': snapshot(input_context='text')}]})
            assert read.result(timeout=.25)['input_context'] == 'text'
            assert not ack.done()  # ACK has reached desktop while the next poll is held.
        finally:
            with broker.condition:
                broker.closed = True
                broker.condition.notify_all()
        assert ack.result(timeout=.25)['commands'] == []


@pytest.mark.parametrize('wait', [-1, 1001, True, 1.5, '300', None])
def test_exchange_wait_is_bounded(wait):
    broker = Broker()
    token = broker.connect('chrome', 'session-123')['token']
    with pytest.raises(ValueError, match='invalid_wait'):
        broker.exchange(token, {'wait_ms': wait})


def test_loopback_rejects_web_requests_and_allows_native_framing():
    server=BridgeServer(port=0);server.start();port=server.http.server_port
    try:
        data=dict(browser='chrome',session='session-123')
        response=post('/connect',data,port=port)
        assert response['token']
        body=json.dumps(dict(version=1,**data)).encode()
        for extra in [{'Origin':'https://example.test'},{'Sec-Fetch-Site':'cross-site'},{'Host':'evil.test'}]:
            headers={'Content-Type':'application/json','X-Mythlink-Native':'1',**extra}
            with pytest.raises(HTTPError) as error:
                urlopen(Request(f'http://127.0.0.1:{port}/connect',body,headers),timeout=2)
            assert error.value.code==403
    finally:server.close()


def test_chrome_host_framing_survives_fragmented_input(monkeypatch):
    from proximic_ring.browser_bridge import native_host
    message=json.dumps({'path':'/connect','version':1,'browser':'chrome','session':'session-123'}).encode()
    class Fragmented(io.BytesIO):
        def read(self,n):return super().read(min(n,2))
    output=io.BytesIO()
    monkeypatch.setattr(native_host.sys,'stdin',SimpleNamespace(buffer=Fragmented(struct.pack('=I',len(message))+message)))
    monkeypatch.setattr(native_host.sys,'stdout',SimpleNamespace(buffer=output))
    monkeypatch.setattr(native_host,'post',lambda *args:{'token':'abc'})
    assert native_host.main()==0
    raw=output.getvalue();assert struct.unpack('=I',raw[:4])[0]==len(raw)-4
    assert json.loads(raw[4:])=={'token':'abc'}


def test_install_is_repeatable_uses_exact_origin_and_exports_no_private_key(tmp_path):
    path=install_chrome(tmp_path)
    assert install_chrome(tmp_path)==path
    manifest=json.loads((path/'manifest.json').read_text())
    host=json.loads((tmp_path/'Library/Application Support/Google/Chrome/NativeMessagingHosts/com.proximic.voice.browser.json').read_text())
    assert host['allowed_origins']==[f'chrome-extension://{chrome_id()}/']
    assert 'key' in manifest and len(chrome_id())==32
    store=export_extension(tmp_path/'store','chrome',store=True)
    assert 'key' not in json.loads((store/'manifest.json').read_text())
    assert not list(tmp_path.rglob('*.pem'))


def test_only_builtin_media_ids_route_to_dom():
    assert media_action('web-video:play')=='play'
    assert media_action('scene-default:music:volume-up')=='volume-up'
    assert media_action('custom:Space')==''
    assert media_action('browser:next')==''


def test_frozen_install_uses_the_other_users_app_path_without_source_checkout(tmp_path,monkeypatch):
    import shlex
    from proximic_ring.browser_bridge import install
    executable=tmp_path/'应用程序/Mythlink.app/Contents/MacOS/Mythlink'
    monkeypatch.setattr(install.sys,'frozen',True,raising=False)
    monkeypatch.setattr(install.sys,'executable',str(executable))
    home=tmp_path/'另一个用户'
    extension=install.install_chrome(home)
    host=json.loads((home/'Library/Application Support/Google/Chrome/NativeMessagingHosts/com.proximic.voice.browser.json').read_text())
    launcher=Path(host['path']).read_text()
    assert shlex.split(launcher.splitlines()[1])==['exec',str(executable),'--browser-native-host']
    assert 'PYTHONPATH' not in launcher
    assert (extension/'content.js').is_file()
    assert install.safari_helper()==executable.parents[1]/'Helpers/MythlinkBrowser.app'


@pytest.fixture
def safari_component(tmp_path, monkeypatch):
    import plistlib
    import proximic_ring.browser_bridge.install as install
    helper = tmp_path / 'MythlinkBrowser.app'
    for relative in ('Contents/MacOS/MythlinkBrowser',
                     'Contents/PlugIns/MythlinkBrowserExtension.appex/Contents/MacOS/MythlinkBrowserExtension',
                     'Contents/PlugIns/MythlinkBrowserExtension.appex/Contents/Resources/manifest.json'):
        path = helper / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('test component')
    (helper / 'Contents/Info.plist').write_bytes(plistlib.dumps(
        {'MythlinkSupportsExtensionStatus': True, 'LSUIElement': True}))
    monkeypatch.setattr(install, 'safari_helper', lambda: helper)
    return install, helper


def test_safari_missing_embedded_extension_is_not_reported_as_installed(safari_component, monkeypatch):
    install, helper = safari_component
    (helper / 'Contents/PlugIns/MythlinkBrowserExtension.appex/Contents/Resources/manifest.json').unlink()
    monkeypatch.setattr(install.subprocess, 'run', lambda *a, **k: pytest.fail('incomplete helper launched'))
    assert not install.safari_helper_available()
    assert install.safari_extension_state() == 'component_missing'


def test_safari_legacy_helper_never_opens_ui_during_status_poll(safari_component, monkeypatch):
    import plistlib
    install, helper = safari_component
    (helper / 'Contents/Info.plist').write_bytes(plistlib.dumps({}))
    monkeypatch.setattr(install.subprocess, 'run', lambda *a, **k: pytest.fail('legacy helper launched'))
    assert install.safari_extension_state() == 'unknown'


@pytest.mark.parametrize('agent_flag', [None, False])
def test_safari_old_foreground_helper_is_not_started_by_idle_poll(safari_component, monkeypatch, agent_flag):
    import plistlib
    install, helper = safari_component
    info = {'MythlinkSupportsExtensionStatus': True}
    if agent_flag is not None: info['LSUIElement'] = agent_flag
    (helper / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    monkeypatch.setattr(install.subprocess, 'run', lambda *a, **k: pytest.fail('Dock-visible helper launched'))
    assert install.safari_helper_available()
    assert install.safari_extension_state() == 'unknown'


@pytest.mark.parametrize('stdout,expected', [('{"state":"enabled"}', 'enabled'),
    ('{"state":"disabled"}', 'disabled'), ('{"state":"unavailable"}', 'unavailable'),
    ('bad output', 'unknown'), ('[]', 'unknown'), ('{"state":"unexpected"}', 'unknown')])
def test_safari_public_state_query_validates_response(safari_component, monkeypatch, stdout, expected):
    install, helper = safari_component
    def run(argv, **kw):
        assert argv == [str(helper / 'Contents/MacOS/MythlinkBrowser'), '--extension-status']
        assert kw['timeout'] == 3
        return SimpleNamespace(stdout=stdout)
    monkeypatch.setattr(install.subprocess, 'run', run)
    assert install.safari_extension_state() == expected


def test_safari_query_timeout_is_unknown_not_missing(safari_component, monkeypatch):
    install, _ = safari_component
    def timeout(*a, **kw):
        raise install.subprocess.TimeoutExpired('helper', 3)
    monkeypatch.setattr(install.subprocess, 'run', timeout)
    assert install.safari_extension_state() == 'unknown'
