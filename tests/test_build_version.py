"""Host version selection and persistence, without creating a release package."""
import importlib.util
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_version', ROOT / 'tools/build_version.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.mark.parametrize('current,expected', [('0.6.0', '0.6.1'), ('0.6.9', '0.6.10'),
                                             ('1.9.99', '1.9.100')])
def test_only_last_component_increments(current, expected):
    assert builder.next_version(current) == expected


@pytest.mark.parametrize('value', ['1', '1.2', 'v1.2.3', '1.2.-1', '1.2.03', '1.2.3;bad', '1.2.3.4'])
def test_invalid_version_rejected(value):
    with pytest.raises(ValueError):
        builder.validate_version(value)


def test_prompt_default_override_and_invalid_retry(monkeypatch, capsys):
    stream = io.StringIO('\ninvalid\n1.2.3\n')
    monkeypatch.setattr(stream, 'isatty', lambda: True)
    monkeypatch.setattr(builder.sys, 'stdin', stream)
    assert builder.select_version('0.6.9') == '0.6.10'
    assert builder.select_version('0.6.10') == '1.2.3'
    assert '回车使用 0.6.10' in capsys.readouterr().err


def test_noninteractive_requires_explicit_choice(monkeypatch):
    monkeypatch.setattr(builder.sys, 'stdin', io.StringIO(''))
    with pytest.raises(ValueError, match='非交互'):
        builder.select_version('0.6.0')
    assert builder.select_version('0.6.0', '2.0.0') == '2.0.0'
    assert builder.select_version('0.6.0', automatic=True) == '0.6.1'


def test_successive_builds_persist_version_without_changing_other_config(tmp_path):
    path = tmp_path / 'pyproject.toml'
    original = '# project\n[project]\nname = "mythlink"\nversion = "0.6.9" # keep comment\n[tool.other]\nversion = "7.8.9"\n'
    path.write_text(original)
    assert builder.prepare_version(tmp_path, automatic=True) == '0.6.10'
    assert builder.prepare_version(tmp_path, automatic=True) == '0.6.11'
    assert path.read_text() == original.replace('0.6.9', '0.6.11')
    assert builder.prepare_version(tmp_path, '1.0.0') == '1.0.0'
    assert tomllib.loads(path.read_text())['project']['version'] == '1.0.0'


def test_invalid_input_does_not_modify_project(tmp_path):
    path = tmp_path / 'pyproject.toml'
    original = '[project]\nversion = "0.6.0"\n'
    path.write_text(original)
    with pytest.raises(ValueError):
        builder.prepare_version(tmp_path, 'broken')
    assert path.read_text() == original


def test_running_app_prefers_its_packaged_version(tmp_path, monkeypatch):
    from proximic_ring import version
    module = tmp_path / 'proximic_ring/version.py'
    module.parent.mkdir()
    module.with_name('package-version.txt').write_text('0.6.10')
    monkeypatch.setattr(version, '__file__', str(module))
    monkeypatch.setattr(version.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(version, 'version', lambda _: '0.1.0')
    assert version.app_version() == '0.6.10'


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS Bash build entry')
@pytest.mark.parametrize('args,expected', [([], '0.6.10'), (['--auto-version'], '0.6.10'),
                                         (['--version', '1.2.3'], '1.2.3')])
def test_macos_entry_selects_version_before_packaging(tmp_path, args, expected):
    import pty
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(ROOT / 'scripts/build-macos.sh', scripts / 'build-macos.sh')
    (scripts / 'macos-signing-env.sh').write_text('APPLE_SIGNING_IDENTITY=-\n')
    (tmp_path / 'tools').mkdir()
    shutil.copyfile(ROOT / 'tools/build_version.py', tmp_path / 'tools/build_version.py')
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion = "0.6.9"\n')
    python = tmp_path / '.build/packaging-venv/bin/python'
    python.parent.mkdir(parents=True)
    # Run real version selection, then stop at the dependency-install boundary.
    python.write_text('#!/bin/bash\nif [[ "$1" == tools/build_version.py ]]; then\n'
                      f'  exec {shlex.quote(sys.executable)} "$@"\nfi\nexit 91\n')
    python.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key not in {
        'MYTHLINK_BUILD_VERSION', 'CI', 'GITHUB_REF_TYPE', 'GITHUB_REF_NAME',
        'PROXIMIC_SKIP_DEPENDENCY_INSTALL', 'PROXIMIC_REQUIRE_SIGNED_RELEASE'}}
    env['PROXIMIC_PYTHON'] = sys.executable
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(['/bin/bash', str(scripts / 'build-macos.sh'), '--app-only', *args],
                                   stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, text=True)
        os.write(master, b'\n')
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 91, (stdout, stderr)
        assert f'Building Mythlink {expected}' in stdout
        assert tomllib.loads((tmp_path / 'pyproject.toml').read_text())['project']['version'] == expected
        if not args:
            assert '回车使用 0.6.10' in stderr
    finally:
        os.close(master)
        os.close(slave)
