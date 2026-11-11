"""Exercise platform branches in the real packaging recipe without building ASR."""
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import tomllib

import pytest
from packaging.requirements import Requirement


@pytest.mark.parametrize('system', ['Darwin', 'Windows'])
def test_mnn_native_libraries_are_collected_only_for_windows(system, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    hooks = ModuleType('PyInstaller.utils.hooks')
    calls = []
    hooks.collect_data_files = lambda *args, **kwargs: []
    hooks.collect_submodules = lambda name, **kwargs: []
    hooks.copy_metadata = lambda *args, **kwargs: []
    def dynamic(package):
        calls.append(package)
        return [(package + '-native', package)]
    hooks.collect_dynamic_libs = dynamic
    monkeypatch.setitem(sys.modules, 'PyInstaller.utils.hooks', hooks)
    monkeypatch.setitem(sys.modules, 'platform', SimpleNamespace(system=lambda: system))
    # Run all import/data/native-library configuration preceding Analysis.
    source = (root/'packaging/mythlink.spec').read_text(encoding='utf8').split('a = Analysis(', 1)[0]
    namespace = dict(SPECPATH=str(root/'packaging'))
    exec(compile(source, 'mythlink.spec', 'exec'), namespace)
    assert namespace['hiddenimports'].count('MNN') == 1
    assert namespace['hiddenimports'].count('_mnncengine') == 1
    # Windows also bundles the delivered Raw2 SDK and its ONNX Runtime.
    expected = ['MNN', 'onnxruntime', 'ring_windows_sdk'] if system == 'Windows' else ['ring_mac_sdk', 'onnxruntime']
    assert calls == expected
    for name in expected:
        assert (name+'-native', name) in namespace['binaries']
    assert namespace['hiddenimports'].count('onnxruntime') == 1
    assert ('ring_windows_sdk.model' in namespace['hiddenimports']) is (system == 'Windows')


def test_mac_touchpad_dependencies_keep_existing_versions():
    root = Path(__file__).resolve().parents[1]
    with (root/'pyproject.toml').open('rb') as stream:
        extras = tomllib.load(stream)['project']['optional-dependencies']
    requirements = [Requirement(value) for value in extras['touchpad']]
    active = {row.name: str(row.specifier) for row in requirements
              if row.marker is None or row.marker.evaluate(dict(platform_system='Darwin', platform_machine='arm64'))}
    assert active == {'MNN': '==3.6.1', 'pyobjc-framework-Quartz': '>=12.2.2',
                      'pyobjc-framework-ApplicationServices': '>=12.2.2', 'onnxruntime': '==1.21.0'}
