"""Keep passive browser probes out of the Dock before native code can run."""
import importlib.util
from pathlib import Path
import plistlib

import pytest


def tool(name):
    path = Path(__file__).parents[1] / 'tools' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_declares_helper_as_agent_before_signing(tmp_path, monkeypatch):
    builder = tool('build_browser_extension')
    monkeypatch.setattr(builder.subprocess, 'check_output', lambda *a, **k: '/test/sdk')
    checked = []
    def run(argv, **kwargs):
        if argv[0] == 'codesign':
            info = plistlib.loads((tmp_path/'MythlinkBrowser.app/Contents/Info.plist').read_bytes())
            assert info['LSUIElement'] is True
            assert not info.get('LSBackgroundOnly')  # The explicit UI may still open a window.
            checked.append(argv)
    monkeypatch.setattr(builder.subprocess, 'run', run)
    builder.build(tmp_path)
    assert checked


@pytest.mark.parametrize('background', [False, True])
def test_bundle_verifier_rejects_dock_visible_browser_helper(tmp_path, monkeypatch, background):
    verifier = tool('verify_macos_bundle')
    app = tmp_path/'Mythlink.app'
    (app/'Contents/MacOS').mkdir(parents=True)
    (app/'Contents/Resources').mkdir()
    (app/'Contents/MacOS/Mythlink').touch()
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'LSUIElement': True}))
    helper = app/'Contents/Helpers/MythlinkBrowser.app/Contents/Info.plist'
    helper.parent.mkdir(parents=True)
    helper.write_bytes(plistlib.dumps({'LSUIElement': background}))
    def run(command, *args):
        if command.endswith('/file'): return 'Mach-O arm64' if str(args[0]).endswith('/Mythlink') else 'data'
        return ''
    monkeypatch.setattr(verifier, '_run', run)
    errors = verifier.verify_bundle(app, '15.0')
    assert bool(errors) is not background
    if errors: assert 'MythlinkBrowser' in errors[0]
