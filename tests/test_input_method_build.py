"""Build numbering and failed-build rollback, without compiling or installing an IME."""
import importlib.util
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest

pytest.importorskip('fcntl')

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ime_build', ROOT / 'tools/build_input_method.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def write_info(path, number='1', identifier='test.input'):
    path.parent.mkdir(parents=True, exist_ok=True)
    info = dict(CFBundleIdentifier=identifier, CFBundleVersion=number, CFBundleShortVersionString='1.0.0')
    path.write_bytes(plistlib.dumps(info))
    return info


@pytest.mark.parametrize('last,installed,expected', [('1', '1', '2'), ('9', '2', '10'),
                                                   ('2', '19', '20'), ('1.9', '1.10', '1.11')])
def test_next_number_uses_numeric_maximum(tmp_path, last, installed, expected):
    template = write_info(tmp_path / 'template.plist')
    write_info(tmp_path / 'last.plist', last)
    write_info(tmp_path / 'installed.plist', installed)
    write_info(tmp_path / 'unrelated.plist', '9999', 'other.app')
    assert builder.next_build_number(template, list(tmp_path.glob('*.plist'))) == expected
    assert template['CFBundleVersion'] == '1'


@pytest.fixture
def build_repo(tmp_path, monkeypatch):
    if sys.platform != 'darwin' or not shutil.which('zsh'):
        pytest.skip('macOS build uses zsh')
    root = tmp_path / 'Project with spaces'
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    for name in ('build-input-method.sh', 'macos-signing-env.sh'):
        shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
    (root / 'tools').mkdir()
    shutil.copyfile(ROOT / 'tools/build_input_method.py', root / 'tools/build_input_method.py')
    write_info(root / 'native/ProxiMicInput/Info.plist')
    (root / 'native/ProxiMicInput/Sources').mkdir()
    (root / 'native/ProxiMicInput/Sources/main.swift').write_text('fixture')
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    (fakebin / 'xcrun').write_text('''#!/bin/zsh
if [[ "$*" == "swiftc --version" || "$*" == "--show-sdk-version" ]]; then
  print "fixture-toolchain"
  exit 0
fi
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then
    if [[ "${FAIL_BUILD:-}" == "compile" || ( "${FAIL_BUILD:-}" == "helper" && "$2" == */InputMethodAdmin ) ]]; then exit 7; fi
    print "${BUILD_MARKER:-fixture}" > "$2"
    chmod +x "$2"
    exit 0
  fi
  shift
done
exit 1
''')
    (fakebin / 'codesign').write_text('''#!/bin/zsh
if [[ "${FAIL_BUILD:-}" == "sign" ]]; then exit 8; fi
exit 0
''')
    for tool in fakebin.iterdir(): tool.chmod(0o755)
    monkeypatch.setenv('PATH', str(fakebin) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('PROXIMIC_ALLOW_ADHOC_SIGNING', '1')
    monkeypatch.setenv('APPLE_SIGNING_IDENTITY', '-')
    monkeypatch.setattr(builder.Path, 'home', classmethod(lambda cls: tmp_path / 'user'))
    return root


def payload_info(root):
    return plistlib.loads((root / '.build/input-method/ProxiMicInput.app/Contents/Info.plist').read_bytes())


@pytest.mark.parametrize('failure', ['compile', 'helper', 'sign'])
def test_failed_build_preserves_last_payload_and_number(build_repo, monkeypatch, failure):
    root = build_repo
    builder.build(root)
    assert payload_info(root)['CFBundleVersion'] == '2'
    output = root / '.build/input-method'
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file() and p.name != 'build.lock'}
    (root / 'native/ProxiMicInput/Sources/main.swift').write_text('changed input method')
    monkeypatch.setenv('BUILD_MARKER', 'changed')
    monkeypatch.setenv('FAIL_BUILD', failure)
    with pytest.raises(subprocess.CalledProcessError): builder.build(root)
    assert {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file() and p.name != 'build.lock'} == before
    assert not list(output.glob('.build-*'))
    monkeypatch.delenv('FAIL_BUILD')
    builder.build(root)
    assert payload_info(root)['CFBundleVersion'] == '3'
    assert payload_info(root)['CFBundleShortVersionString'] == '1.0.0'
    assert plistlib.loads((root / 'native/ProxiMicInput/Info.plist').read_bytes())['CFBundleVersion'] == '1'


def test_installed_build_survives_clean_output(build_repo):
    installed = builder.Path.home() / 'Library/Input Methods/ProxiMicInput.app/Contents/Info.plist'
    write_info(installed, '7')
    builder.build(build_repo)
    assert payload_info(build_repo)['CFBundleVersion'] == '8'


def test_shell_entry_serializes_concurrent_builds(build_repo):
    env = {**os.environ, 'PROXIMIC_PYTHON': sys.executable}
    command = ['/bin/zsh', str(build_repo / 'scripts/build-input-method.sh')]
    first = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    results = [p.communicate(timeout=15) for p in (first, second)]
    assert first.returncode == second.returncode == 0, results
    # Identical concurrent builds publish the same component version.
    import re
    versions = sorted(int(re.search(r'已构建输入法：1.0.0 \((\d+)\)', out).group(1)) for out, _ in results)
    assert versions[1] == versions[0]
    assert payload_info(build_repo)['CFBundleVersion'] == str(versions[-1])


def test_host_only_builds_preserve_ime_version(build_repo):
    builder.build(build_repo)
    before = payload_info(build_repo)
    (build_repo / 'pyproject.toml').write_text('[project]\nversion = "0.6.10"\n')
    (build_repo / 'src').mkdir()
    (build_repo / 'src/app.py').write_text('host app change')
    builder.build(build_repo)
    assert payload_info(build_repo) == before


@pytest.mark.parametrize('change', ['source', 'resource', 'remove_resource', 'deployment'])
def test_ime_changes_increment_once(build_repo, monkeypatch, change):
    resources = build_repo / 'native/ProxiMicInput/Resources'
    resources.mkdir()
    (resources / 'icon.svg').write_text('old icon')
    builder.build(build_repo)
    if change == 'source':
        (build_repo / 'native/ProxiMicInput/Sources/main.swift').write_text('new implementation')
    elif change == 'resource':
        (resources / 'icon.svg').write_text('new icon')
    elif change == 'remove_resource':
        (resources / 'icon.svg').unlink()
    else:
        monkeypatch.setenv('MACOSX_DEPLOYMENT_TARGET', '16.0')
    builder.build(build_repo)
    assert payload_info(build_repo)['CFBundleVersion'] == '3'
    builder.build(build_repo)
    assert payload_info(build_repo)['CFBundleVersion'] == '3'


def test_clean_rebuild_reuses_installed_fingerprint(build_repo):
    builder.build(build_repo)
    before = payload_info(build_repo)
    installed = builder.Path.home() / 'Library/Input Methods/ProxiMicInput.app'
    shutil.copytree(build_repo / '.build/input-method/ProxiMicInput.app', installed)
    shutil.rmtree(build_repo / '.build/input-method')
    builder.build(build_repo)
    assert payload_info(build_repo) == before


def test_edit_during_compile_cannot_publish_wrong_fingerprint(build_repo, monkeypatch):
    builder.build(build_repo)
    before = payload_info(build_repo)
    run = builder.subprocess.run

    def changing_run(command, **kwargs):
        result = run(command, **kwargs)
        if '--prepared' in command:
            (build_repo / 'native/ProxiMicInput/Sources/main.swift').write_text('edited while compiling')
        return result

    monkeypatch.setattr(builder.subprocess, 'run', changing_run)
    with pytest.raises(RuntimeError, match='构建期间'):
        builder.build(build_repo)
    assert payload_info(build_repo) == before


def test_publish_failure_restores_both_previous_components(tmp_path, monkeypatch):
    stage, output = tmp_path / 'stage', tmp_path / 'output'
    for folder, text in [(stage, 'new'), (output, 'old')]:
        write_info(folder / 'ProxiMicInput.app/Contents/Info.plist', '2' if text == 'new' else '1')
        (folder / 'InputMethodAdmin').write_text(text)
    replace = builder.os.replace
    def fail_helper(source, target):
        if source == stage / 'InputMethodAdmin': raise OSError('fixture publication failure')
        replace(source, target)
    monkeypatch.setattr(builder.os, 'replace', fail_helper)
    with pytest.raises(OSError): builder.publish(stage, output)
    assert (output / 'InputMethodAdmin').read_text() == 'old'
    assert plistlib.loads((output / 'ProxiMicInput.app/Contents/Info.plist').read_bytes())['CFBundleVersion'] == '1'
