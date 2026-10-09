"""Module boundaries and saved-config compatibility for the unified scene package."""
import ast
import importlib
from pathlib import Path
import subprocess
import sys

import pytest

from proximic_ring.scenes import configuration
from proximic_ring.scenes.models import PRESENTATION, PDF, IMAGE, VIDEO, MUSIC
from proximic_ring.scenes.registry import SCENES
from proximic_ring.scenes.defaults import scene_defaults, entry_defaults
from proximic_ring.scenes.resolver import resolve_action


@pytest.mark.parametrize('scene', [PRESENTATION, PDF, IMAGE, VIDEO, MUSIC])
def test_every_scene_has_one_registry_definition_and_uses_the_shared_builder(scene):
    result, pending = scene_defaults('test.unknown', {scene: 'generic'})
    assert set(result) == {scene}
    assert set(result[scene]) | set(pending.get(scene, {})) == set(SCENES[scene].gestures)
    assert 'regular' not in result
    assert entry_defaults('test.unknown', scene, 'generic') == {}


def test_entry_actions_stay_outside_the_active_scene_and_preserve_app_specific_keys():
    for bundle, key in [('com.kingsoft.wpsoffice.mac', 'Shift+F5'), ('com.microsoft.Powerpoint', 'Cmd+Return')]:
        app = configuration.new_application(dict(value=bundle, label='Slides'))
        assert app['bindings']['snap']['shortcut'] == key
        assert app['scenes'][PRESENTATION]['snap']['shortcut'] == 'Escape'
        assert app['enabledScenes'] == [PRESENTATION]
        assert not app['initializedScenes'] and not app['pendingDefaults'] and not app['pendingStart']


def test_existing_resolution_priorities_are_explicit_not_changed_by_refactor():
    menus = [dict(id='menu:current', label='From Current Slide', path='Slide Show', shortcut='Ctrl+F5')]
    assert resolve_action('com.kingsoft.wpsoffice.mac', PRESENTATION, 'start-current', 'wps', menus, entry=True)['shortcut'] == 'Shift+F5'
    assert entry_defaults('test.slides', PRESENTATION, 'generic', menus)['snap']['shortcut'] == 'Ctrl+F5'
    menus = [dict(id='menu:play', label='Play/Pause', path='Playback', shortcut='Ctrl+Space')]
    assert resolve_action('com.apple.Music', MUSIC, 'play', 'music', menus)['shortcut'] == 'Ctrl+Space'


def test_legacy_pending_api_and_storage_names_remain_unchanged():
    from proximic_ring.scene_defaults import scene_defaults as legacy, resolve_action as legacy_action
    assert legacy('test.slides', {PRESENTATION: 'generic'}) == ({}, {})
    assert legacy_action('com.microsoft.Powerpoint', PRESENTATION, 'next') is None
    assert configuration.SETTINGS_KEY == 'gestures/applicationMenusV1'
    app = configuration.new_application(dict(value='test.music', label='Music', sceneProfiles={MUSIC: 'generic'}))
    assert app['pendingDefaults'][MUSIC] == {'tap': 'play', 'swipe-left': 'previous', 'swipe-right': 'next',
                                           'swipe-up': 'volume-up', 'swipe-down': 'volume-down'}
    assert not app['pendingStart'] and app['initializedScenes'] == [MUSIC]


def test_legacy_recognition_package_has_been_removed():
    import proximic_ring
    root = Path(proximic_ring.__file__).parent
    assert not (root / 'scene_recognition').exists()
    assert importlib.util.find_spec('proximic_ring.scene_recognition') is None


@pytest.mark.parametrize('entry', ['registry', 'capabilities', 'policy', 'defaults', 'configuration', 'migrations',
    'resolver', 'menus', 'adapters.presentation', 'adapters.native', 'adapters.browser', 'recognition.engine'])
def test_canonical_modules_import_in_a_clean_process_without_ui_or_native_execution(entry):
    script = '''
import sys
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PySide6','AppKit','Quartz','ApplicationServices'} or fullname.startswith('proximic_ring.ui'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Guard())
import importlib
importlib.import_module('proximic_ring.scenes.ENTRY')
from proximic_ring.scenes.recognition.engine import detect_scene
'''.replace('ENTRY', entry)
    result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_production_has_no_dependency_on_the_old_compatibility_modules():
    import proximic_ring
    root = Path(proximic_ring.__file__).parent
    aliases = {'gesture_scenes', 'scene_capabilities', 'scene_defaults', 'application_defaults',
               'application_scene_policy', 'browser_shortcuts', 'scene_recognition'}
    for path in root.rglob('*.py'):
        if path.relative_to(root).parts[0].split('.')[0] in aliases:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not set(node.module.split('.')) & aliases, str(path)
