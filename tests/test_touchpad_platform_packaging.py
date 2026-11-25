"""Exercise platform branches in the real packaging recipe without building ASR."""
from importlib.machinery import ModuleSpec
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import tomllib

import pytest
from packaging.requirements import Requirement


@pytest.mark.parametrize('system', ['Darwin', 'Windows'])
def test_touchpad_libraries_match_platform_and_ring_sdks(system, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    hooks = ModuleType('PyInstaller.utils.hooks')
    calls = []
    hooks.collect_data_files = lambda name, includes=(): [(item, name) for item in includes]
    hooks.collect_submodules = lambda name, **kwargs: []
    hooks.copy_metadata = lambda *args, **kwargs: []
    def dynamic(package):
        calls.append(package)
        return [(package + '-native', package)]
    hooks.collect_dynamic_libs = dynamic
    monkeypatch.setitem(sys.modules, 'PyInstaller.utils.hooks', hooks)
    monkeypatch.setitem(sys.modules, 'platform', SimpleNamespace(system=lambda: system))
    r1 = ModuleType('ring_python_sdk_r1')
    r1.__spec__ = ModuleSpec(r1.__name__, loader=None)
    monkeypatch.setitem(sys.modules, r1.__name__, r1)
    tsf_dll = root / '.build/windows-ime/MythlinkTsf.dll'
    is_file = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda path: path == tsf_dll or is_file(path))
    # Run all import/data/native-library configuration preceding Analysis.
    source = (root/'packaging/mythlink.spec').read_text(encoding='utf8').split('a = Analysis(', 1)[0]
    namespace = dict(SPECPATH=str(root/'packaging'))
    exec(compile(source, 'mythlink.spec', 'exec'), namespace)
    assert namespace['hiddenimports'].count('MNN') == 1
    assert namespace['hiddenimports'].count('_mnncengine') == 1
    # Windows also bundles the delivered Raw2 SDK and its ONNX Runtime.
    expected = (['unicorn', 'MNN', 'onnxruntime', 'ring_windows_sdk'] if system == 'Windows'
                else ['ring_mac_sdk', 'onnxruntime', 'unicorn'])
    provider = 'ring_windows_sdk' if system == 'Windows' else 'ring_mac_sdk'
    assert ('assets/*', provider) in namespace['datas']
    assert ('assets/*', 'ring_python_sdk_r1') in namespace['datas']
    assert calls == expected
    for name in expected:
        assert (name+'-native', name) in namespace['binaries']
    assert namespace['hiddenimports'].count('onnxruntime') == 1
    assert ('ring_windows_sdk.model' in namespace['hiddenimports']) is (system == 'Windows')
    assert ((str(tsf_dll), 'windows-ime') in namespace['binaries']) is (system == 'Windows')
    script = root / 'scripts/windows-ime-prototype.ps1'
    assert ((str(script), 'windows-ime') in namespace['datas']) is (system == 'Windows')


def test_r1_wheel_is_selected_from_the_copied_project(monkeypatch, tmp_path):
    from tools import install_r1_sdk

    project = tmp_path / 'source copy'
    wheel_dir = project / 'third_party' / 'r1-sdk'
    wheel_dir.mkdir(parents=True)
    monkeypatch.setattr(install_r1_sdk, '__file__', str(project / 'tools' / 'install_r1_sdk.py'))
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match='Keep exactly one'):
        install_r1_sdk.bundled_wheel()

    original = wheel_dir / 'ring_python_sdk_r1-0.4.1-py3-none-any.whl'
    original.touch()
    assert install_r1_sdk.bundled_wheel() == original.resolve()

    replacement = original.rename(wheel_dir / 'ring_python_sdk_r1-0.5.0-py3-none-any.whl')
    assert install_r1_sdk.bundled_wheel() == replacement.resolve()

    original.touch()
    with pytest.raises(SystemExit, match='Keep exactly one'):
        install_r1_sdk.bundled_wheel()


def test_mac_touchpad_dependencies_keep_existing_versions():
    root = Path(__file__).resolve().parents[1]
    with (root/'pyproject.toml').open('rb') as stream:
        extras = tomllib.load(stream)['project']['optional-dependencies']
    requirements = [Requirement(value) for value in extras['touchpad']]
    active = {row.name: str(row.specifier) for row in requirements
              if row.marker is None or row.marker.evaluate(dict(platform_system='Darwin', platform_machine='arm64'))}
    assert active == {'MNN': '==3.6.1', 'pyobjc-framework-Quartz': '>=12.2.2',
                      'pyobjc-framework-ApplicationServices': '>=12.2.2', 'onnxruntime': '==1.21.0'}
