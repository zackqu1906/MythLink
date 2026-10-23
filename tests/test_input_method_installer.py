import json
from pathlib import Path
import plistlib
import subprocess

import pytest

from proximic_ring.voice.ime import input_method_install as module


def bundle(path, identifier, marker, version=("1.0.0", "1")):
    (path / 'Contents/MacOS').mkdir(parents=True)
    with (path / 'Contents/Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleIdentifier': identifier, 'CFBundleShortVersionString': version[0],
                      'CFBundleVersion': version[1]}, stream)
    executable = path / 'Contents/MacOS/ProxiMicInput'
    executable.write_text(marker)
    executable.chmod(0o755)
    (path / 'marker').write_text(marker)


@pytest.fixture
def installer(tmp_path, monkeypatch):
    payload = tmp_path / 'App with spaces.app/Contents/Helpers'
    payload.mkdir(parents=True)
    helper = payload / 'InputMethodAdmin'
    helper.write_text('helper')
    helper.chmod(0o755)
    bundle(payload / 'ProxiMicInput.app', module.BUNDLE_ID, 'new')
    home = tmp_path / 'user'
    home.mkdir()
    monkeypatch.setattr(module.Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    value = module.InputMethodInstaller(payload)
    value.system_destination = tmp_path / 'system/Input Methods/ProxiMicInput.app'
    state = {'selected': False, 'enabled': True, 'fail': '', 'running': '', 'ps': ''}
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        code, output, error = 0, '', ''
        if args[0] == str(helper):
            if args[1] == state['fail']:
                code, error = 1, 'test registration failure'
            elif args[1] == 'prepare-update':
                state['selected'] = False
                output = json.dumps({'update_handoff': {
                    'originalSource': module.BUNDLE_ID + '.dictation',
                    'temporarySource': 'com.apple.keylayout.ABC', 'pid': 123, 'bundle': 'test.app'}})
            elif args[1] == 'restore-after-update':
                restored = not state.get('changed_context', False)
                state['selected'] = restored
                state['restored_marker'] = (value.destination / 'marker').read_text()
                output = json.dumps({'input_source_restored': restored})
            elif args[1] == 'status':
                output = json.dumps({'installed': value.destination.exists(),
                    'sources': [{'selected': state['selected'], 'enabled': state['enabled']}]})
        elif args[0] == '/usr/bin/pgrep':
            code, output = (0, state['running']) if state['running'] else (1, '')
        elif args[0] == '/bin/ps':
            output = state['ps'] if args[-1] == 'uid=,command=' else state['running']
        elif args[0] == '/usr/bin/codesign' and state['fail'] == 'signature':
            code, error = 1, 'invalid signature'
        return subprocess.CompletedProcess(args, code, output, error)

    monkeypatch.setattr(module.subprocess, 'run', run)
    return value, state, calls


def test_unrelated_existing_bundle_is_never_overwritten(installer):
    value, _, calls = installer
    bundle(value.destination, 'other.product', 'keep')
    with pytest.raises(RuntimeError, match='不会覆盖'):
        value.install()
    assert (value.destination / 'marker').read_text() == 'keep'
    assert not any(c[-1] == 'register' for c in calls)


def test_selected_input_source_is_released_before_stop_and_restored_after_install(installer, monkeypatch):
    value, state, calls = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state['selected'] = True
    state['running'] = '42'
    state['ps'] = f'{module.os.getuid()} {value.destination}/Contents/MacOS/ProxiMicInput'
    def kill(pid, sig):
        assert not state['selected']
        assert (value.destination / 'marker').read_text() == 'previous'
        state['running'] = ''
    monkeypatch.setattr(module.os, 'kill', kill)
    result = value.install()
    assert result['input_source_restored'] and state['selected']
    assert state['restored_marker'] == 'new'
    commands = [call[1] for call in calls if call[0] == str(value.helper)]
    assert commands.index('prepare-update') < commands.index('register') < commands.index('restore-after-update')
    assert not list(value.destination.parent.glob('.ProxiMic-install-*'))


def test_temporary_switch_failure_leaves_old_bundle_and_process_untouched(installer):
    value, state, calls = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state.update(selected=True, fail='prepare-update')
    with pytest.raises(RuntimeError):
        value.install()
    assert state['selected'] and (value.destination / 'marker').read_text() == 'previous'
    assert not any(c[0] == '/usr/bin/pgrep' for c in calls)


def test_update_failure_restores_old_bundle_then_original_selection(installer):
    value, state, _ = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state.update(selected=True, fail='enable')
    with pytest.raises(RuntimeError, match='registration failure'):
        value.install()
    assert state['selected'] and state['restored_marker'] == 'previous'


@pytest.mark.parametrize('changed,fail', [(True, ''), (False, 'restore-after-update')])
def test_restore_skip_or_failure_does_not_undo_successful_update(installer, changed, fail):
    value, state, _ = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state.update(selected=True, changed_context=changed, fail=fail)
    result = value.install()
    assert result['installed'] and result['input_source_switched']
    assert not result['input_source_restored']
    assert bool(result.get('input_source_restore_error')) == bool(fail)
    assert (value.destination / 'marker').read_text() == 'new'


def test_user_reselecting_during_update_prevents_bundle_replacement(installer, monkeypatch):
    value, state, _ = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state['selected'] = True
    def reselect():
        state.update(selected=True, changed_context=True)
    monkeypatch.setattr(value, '_stop_inactive_component', reselect)
    with pytest.raises(RuntimeError, match='切回'):
        value.install()
    assert (value.destination / 'marker').read_text() == 'previous'


def test_staged_signature_failure_never_switches_input_source(installer, monkeypatch):
    value, state, calls = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state['selected'] = True
    original_run = value._run
    def run(args, **kwargs):
        if args[0] == '/usr/bin/codesign' and '.ProxiMic-install-' in args[-1]:
            raise RuntimeError('staged signature failed')
        return original_run(args, **kwargs)
    monkeypatch.setattr(value, '_run', run)
    with pytest.raises(RuntimeError, match='staged signature'):
        value.install()
    assert state['selected']
    assert (value.destination / 'marker').read_text() == 'previous'
    assert not any(c[1] == 'prepare-update' for c in calls)


@pytest.mark.parametrize('response', ['{}', 'null', '{"update_handoff": {}}', 'invalid json'])
def test_invalid_handoff_never_stops_or_replaces_the_old_component(installer, monkeypatch, response):
    value, state, calls = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state['selected'] = True
    original_run = value._run
    def run(args, **kwargs):
        if args[1] == 'prepare-update':
            return subprocess.CompletedProcess(args, 0, response, '')
        return original_run(args, **kwargs)
    monkeypatch.setattr(value, '_run', run)
    with pytest.raises((RuntimeError, ValueError)):
        value.install()
    assert (value.destination / 'marker').read_text() == 'previous'
    assert not any(c[0] == '/usr/bin/pgrep' for c in calls)


def test_stop_failure_restores_selection_without_replacing_the_bundle(installer, monkeypatch):
    value, state, _ = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state['selected'] = True
    def stop():
        raise RuntimeError('old component did not stop')
    monkeypatch.setattr(value, '_stop_inactive_component', stop)
    with pytest.raises(RuntimeError, match='did not stop'):
        value.install()
    assert state['selected'] and state['restored_marker'] == 'previous'


def test_rollback_and_restore_failure_report_both_without_losing_old_bundle(installer, monkeypatch):
    value, state, _ = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state.update(selected=True, fail='restore-after-update')
    def stop():
        raise RuntimeError('old component did not stop')
    monkeypatch.setattr(value, '_stop_inactive_component', stop)
    with pytest.raises(RuntimeError, match='did not stop；未能恢复输入法选择'):
        value.install()
    assert (value.destination / 'marker').read_text() == 'previous'


def test_uninstall_still_requires_switching_away(installer):
    value, state, calls = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state['selected'] = True
    with pytest.raises(RuntimeError, match='切回'):
        value.uninstall()
    assert not any(c[1] == 'prepare-update' for c in calls)
    assert (value.destination / 'marker').read_text() == 'previous'


def test_failed_registration_restores_previous_product(installer):
    value, state, _ = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state['fail'] = 'enable'
    with pytest.raises(RuntimeError, match='registration failure'):
        value.install()
    assert (value.destination / 'marker').read_text() == 'previous'
    assert not list(value.destination.parent.glob('.ProxiMic-install-*'))


def test_failed_fresh_install_does_not_leave_partial_bundle(installer):
    value, state, _ = installer
    state['fail'] = 'register'
    with pytest.raises(RuntimeError):
        value.install()
    assert not value.destination.exists()
    assert not list(value.destination.parent.glob('.ProxiMic-install-*'))


def test_success_installs_enables_but_never_selects(installer):
    value, _, calls = installer
    assert value.install()['installed']
    assert (value.destination / 'marker').read_text() == 'new'
    assert not list(value.destination.parent.glob('.ProxiMic-install-*'))
    commands = [call[-1] for call in calls]
    assert 'register' in commands and 'enable' in commands
    assert 'select' not in commands and 'prepare-update' not in commands
    assert not any(c[1] == 'restore-after-update' for c in calls)


def test_incomplete_payload_cannot_report_success(installer):
    value, _, calls = installer
    (value.source / 'Contents/MacOS/ProxiMicInput').unlink()
    with pytest.raises(RuntimeError, match='缺少'):
        value.install()
    assert calls == []


def test_invalid_signature_leaves_installed_bundle_untouched(installer):
    value, state, _ = installer
    bundle(value.destination, module.BUNDLE_ID, 'previous')
    state['fail'] = 'signature'
    with pytest.raises(RuntimeError, match='signature'):
        value.install()
    assert (value.destination / 'marker').read_text() == 'previous'


def test_system_must_confirm_enabled_source_or_install_rolls_back(installer):
    value, state, _ = installer
    state['enabled'] = False
    with pytest.raises(RuntimeError, match='尚未确认'):
        value.install()
    assert not value.destination.exists()


def test_only_our_inactive_current_user_process_can_be_stopped(installer, monkeypatch):
    value, state, _ = installer
    state['running'] = '42'
    state['ps'] = f'{module.os.getuid()} {value.destination}/Contents/MacOS/ProxiMicInput'
    stopped = []
    def kill(pid, sig):
        stopped.append(pid)
        state['running'] = ''
    monkeypatch.setattr(module.os, 'kill', kill)
    value.install()
    assert stopped == [42]


def test_different_executable_is_not_stopped(installer, monkeypatch):
    value, state, _ = installer
    state['running'] = '42'
    state['ps'] = f'{module.os.getuid()} /another/ProxiMicInput'
    monkeypatch.setattr(module.os, 'kill', lambda *_: pytest.fail('must not stop a different app'))
    with pytest.raises(RuntimeError, match='另一份'):
        value.install()
    assert not value.destination.exists()


def test_frozen_payload_is_located_inside_relocated_app_without_source_tree(tmp_path, monkeypatch):
    app = tmp_path / 'Another Mac/Mythlink.app'
    monkeypatch.setattr(module.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(module.sys, 'executable', str(app / 'Contents/MacOS/Mythlink'))
    assert module.payload_directory() == app / 'Contents/Helpers'


def test_source_entrypoint_uses_shared_installer(monkeypatch):
    monkeypatch.setattr(module.InputMethodInstaller, 'verify_payload', lambda _: None)
    assert module.main(['verify']) == 0


@pytest.mark.parametrize("installed,bundled,expected", [
    (("1.9.0", "20"), ("1.10.0", "1"), "outdated"),
    (("1.0.0", "9"), ("1.0.0", "10"), "outdated"),
    (("1.0.0", "1"), ("1.0.0", "1"), "current"),
    (("1.0", "1.0"), ("1.0.0", "1"), "current"),
    (("2.0.0", "1"), ("1.10.0", "20"), "newer"),
    (("1.0.0", "10"), ("1.0.0", "9"), "newer"),
    (("bad", "1"), ("1.0.0", "1"), "unknown"),
    (("1.0.0", "1"), ("1.0.0", ""), "unknown"),
])
def test_version_status_compares_numeric_release_and_build_without_native_calls(installer, installed, bundled, expected):
    value, _, calls = installer
    bundle(value.destination, module.BUNDLE_ID, 'installed', installed)
    source_plist = value.source / 'Contents/Info.plist'
    info = plistlib.loads(source_plist.read_bytes())
    info.update(CFBundleShortVersionString=bundled[0], CFBundleVersion=bundled[1])
    source_plist.write_bytes(plistlib.dumps(info))
    result = value.version_status()
    assert result['state'] == expected
    assert result['current'] == (expected in {'current', 'newer'})
    assert calls == []


def test_missing_or_unreadable_version_never_claims_current(installer):
    value, _, calls = installer
    assert value.version_status()['state'] == 'not_installed'
    bundle(value.destination, module.BUNDLE_ID, 'installed')
    plist = value.destination / 'Contents/Info.plist'
    for content in (b'not a plist', b'<?xml version="1.0"?><plist><dict>',
                    plistlib.dumps({'CFBundleIdentifier': module.BUNDLE_ID})):
        plist.write_bytes(content)
        assert value.version_status()['state'] == 'unknown'
        assert not value.version_status()['current']
    assert calls == []


def test_version_check_uses_user_bundle_before_system_fallback(installer):
    value, _, calls = installer
    bundle(value.system_destination, module.BUNDLE_ID, 'system', ('2.0.0', '1'))
    assert value.version_status()['state'] == 'newer'
    bundle(value.destination, module.BUNDLE_ID, 'user', ('0.9.0', '1'))
    assert value.version_status()['state'] == 'outdated'
    assert calls == []


def test_missing_payload_or_executable_cannot_report_current(installer):
    value, _, calls = installer
    bundle(value.destination, module.BUNDLE_ID, 'installed')
    (value.source / 'Contents/Info.plist').unlink()
    assert value.version_status()['state'] == 'unknown'
    (value.destination / 'Contents/MacOS/ProxiMicInput').unlink()
    assert value.version_status()['state'] == 'unknown'
    assert calls == []
