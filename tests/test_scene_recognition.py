"""Public scene engine contract across native scenes, browsers and imports."""
import subprocess
import sys

import pytest

from proximic_ring.scenes.recognition.engine import detect_scene
from proximic_ring.scenes.models import SceneResult, PRESENTATION, PDF, IMAGE, VIDEO, MUSIC
from proximic_ring.scene_capabilities import application_scene_profiles
from test_activity_scenes import content, metadata
from test_gesture_scenes import window_tree, wps_window_tree
from test_scene_focus_audit import page, read


@pytest.mark.parametrize('scene', [PRESENTATION, PDF, IMAGE, VIDEO, MUSIC])
@pytest.mark.parametrize('context', ['nontext', 'text', 'unknown'])
def test_every_scene_uses_the_same_result_and_input_contract(scene, context):
    window, focus = window_tree() if scene == PRESENTATION else content(scene)
    if context == 'text': focus = dict(AXRole='AXTextField', AXParent=window)
    if context == 'unknown': focus = None
    result = detect_scene('com.microsoft.Powerpoint', {scene: 'generic'}, window, focus, metadata)
    assert isinstance(result, SceneResult)
    assert (result.scene, result.input_context) == (scene, context)
    assert result.page_key == '' and result.player is None


@pytest.mark.parametrize('scene', [PRESENTATION, PDF, IMAGE, VIDEO, MUSIC])
def test_every_scene_respects_modal_and_timeout_boundaries(scene):
    window, focus = window_tree() if scene == PRESENTATION else content(scene)
    profiles = {scene: 'generic'}
    assert detect_scene('test.viewer', profiles, window, focus, metadata, budget=0) == SceneResult()
    window['AXModal'] = True
    assert detect_scene('test.viewer', profiles, window, focus, metadata) == SceneResult()


def test_multipurpose_office_switches_between_pdf_and_slideshow_through_one_entry():
    profiles = application_scene_profiles('com.kingsoft.wpsoffice.mac')
    pdf_window, pdf_focus = content(PDF)
    show_window, show_focus = wps_window_tree()
    for window, focus, scene in [(pdf_window, pdf_focus, PDF), (show_window, show_focus, PRESENTATION),
                                  (pdf_window, pdf_focus, PDF)]:
        assert detect_scene('com.kingsoft.wpsoffice.mac', profiles, window, focus, metadata).scene == scene


def test_browser_video_and_pdf_both_preserve_focused_page_identity():
    window, web, wrapper, video = page()
    profiles = application_scene_profiles('com.apple.Safari')
    result = detect_scene('com.apple.Safari', profiles, window, web, read)
    assert result.scene == VIDEO and result.web_area is web and result.player is video
    assert result.page_key
    web.attrs.update(AXURL='https://example.test/paper.pdf', AXChildren=[])
    pdf = detect_scene('com.apple.Safari', profiles, window, web, read)
    assert pdf.scene == PDF and pdf.web_area is web and pdf.player is None
    assert pdf.page_key != result.page_key
    assert 'paper.pdf' not in repr(pdf)


def test_native_video_hints_in_a_webpage_never_replace_real_player_evidence():
    window, focus = content(VIDEO); window.pop('AXDocument')
    focus.update(AXRole='AXWebArea', AXEditable=False, AXURL='https://example.test/watch', AXDescription='video')
    result = detect_scene('com.apple.Safari', application_scene_profiles('com.apple.Safari'), window, focus, metadata)
    assert result.scene == '' and result.page_key


def test_recognition_imports_without_ui_shortcut_execution_or_native_bridge():
    script = '''
import sys
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ('PySide6', 'AppKit', 'Quartz', 'ApplicationServices',
                     'proximic_ring.ui', 'proximic_ring.app_shortcuts',
                     'proximic_ring.scene_defaults', 'proximic_ring.application_defaults',
                     'proximic_ring.browser_shortcuts', 'proximic_ring.scenes.adapters',
                     'proximic_ring.scenes.defaults', 'proximic_ring.scenes.configuration',
                     'proximic_ring.scenes.migrations', 'proximic_ring.scenes.resolver',
                     'proximic_ring.scenes.menus')
        if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
            raise AssertionError('Recognition imported an execution/configuration dependency: ' + fullname)
sys.meta_path.insert(0, Guard())
from proximic_ring.scenes.recognition.engine import detect_scene
from proximic_ring.scenes.models import SceneResult
assert detect_scene('', {}, None, None, lambda *args: None) == SceneResult()
'''
    result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('entry', ['scene_defaults', 'gesture_scenes', 'browser_shortcuts', 'app_shortcuts', 'scene_capabilities'])
def test_configuration_and_runtime_import_order_has_no_cycle(entry):
    result = subprocess.run([sys.executable, '-B', '-c',
        f'import proximic_ring.{entry}; from proximic_ring.scenes.recognition.engine import detect_scene'],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
