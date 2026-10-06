"""Regressions for focus boundaries, ambiguous evidence and document races."""
from dataclasses import replace
import sys

import pytest

from scene_test_helpers import activity_context
from proximic_ring.gesture_scenes import PRESENTATION
from scene_test_helpers import presentation_context
from proximic_ring.scene_capabilities import PDF, IMAGE, VIDEO, MUSIC
from proximic_ring.scene_recognition.browser import detect_browser as browser_media_context
from test_activity_scenes import content, metadata
from test_gesture_scenes import window_tree, wps_window_tree
from test_presentation_portability import native_backend, POWERPOINT, WPS
from test_app_shortcuts import desktop


class Node:
    def __init__(self, role, parent=None, **attrs):
        self.attrs = dict(AXRole=role, AXParent=parent, **attrs)
        if parent is not None:
            parent.attrs.setdefault("AXChildren", []).append(self)


def page():
    window = Node("AXWindow")
    web = Node("AXWebArea", window, AXURL="https://www.bilibili.com/video/BVtest/?p=1", AXValueSettable=False)
    wrapper = Node("AXGroup", web, AXDOMClassList=["bpx-player-primary-area"])
    video = Node("AXGroup", wrapper, AXSubrole="AXVideo", AXEnabled=True)
    Node("AXButton", wrapper, AXDescription="Pause", AXEnabled=True)
    Node("AXSlider", wrapper, AXDescription="Playback progress", AXEnabled=True)
    return window, web, wrapper, video


def read(node, key):
    assert key not in {"AXValue", "AXSelectedText", "AXTitle"}
    return node.attrs.get(key)


@pytest.mark.parametrize('scene', [PRESENTATION, PDF, IMAGE, VIDEO, MUSIC])
@pytest.mark.parametrize('state', ['hidden_focus', 'hidden_parent', 'disabled', 'stale', 'menu', 'dialog', 'foreign_text'])
def test_invalid_focus_never_claims_a_scene_gesture(scene, state):
    window, focus = window_tree() if scene == PRESENTATION else content(scene)
    if state == 'hidden_focus': focus['AXHidden'] = True
    if state == 'hidden_parent': focus['AXParent'] = dict(AXRole='AXGroup', AXHidden=True, AXParent=window)
    if state == 'disabled': focus['AXEnabled'] = False
    if state == 'stale': focus['AXFocused'] = False
    if state == 'menu': focus['AXParent'] = dict(AXRole='AXMenu', AXParent=window)
    if state == 'dialog': focus['AXParent'] = dict(AXRole='AXGroup', AXSubrole='AXDialog', AXParent=window)
    if state == 'foreign_text': focus = dict(AXRole='AXTextField', AXParent=dict(AXRole='AXWindow', AXTitle='other'))
    assert activity_context(POWERPOINT, {scene: 'generic'}, window, focus, metadata)[1] == 'unknown'


@pytest.mark.parametrize('scene', [PDF, IMAGE, VIDEO, MUSIC])
def test_native_canvas_can_report_window_without_parent(scene):
    window, focus = content(scene)
    focus.pop('AXParent'); focus['AXWindow'] = window
    assert activity_context('test.viewer', {scene: 'generic'}, window, focus, metadata) == (scene, 'nontext')


@pytest.mark.parametrize('state', ['background_pdf', 'sidebar_pdf', 'iframe_pdf'])
def test_browser_only_uses_focused_document_evidence(state):
    window = dict(AXRole='AXWindow')
    web = dict(AXRole='AXWebArea', AXParent=window, AXEditable=False, AXURL='https://example.test/home')
    other = dict(AXRole='AXWebArea', AXParent=window, AXEditable=False, AXURL='https://example.test/other.pdf')
    window['AXChildren'] = [web, other]
    focus = web
    if state == 'sidebar_pdf': focus = dict(AXRole='AXButton', AXParent=window)
    if state == 'iframe_pdf':
        window['AXChildren'] = [web]; web['AXChildren'] = [other]; other['AXParent'] = web
    assert activity_context('com.apple.Safari', {PDF: 'generic'}, window, focus, metadata)[0] == ''


def test_video_audio_controls_do_not_turn_a_video_into_music():
    window, focus = content(VIDEO)
    focus['AXChildren'].append(dict(AXRole='AXGroup', AXDescription='Audio controls'))
    assert activity_context('test.viewer', {VIDEO: 'generic', MUSIC: 'generic'}, window, focus, metadata) == (VIDEO, 'nontext')


def test_declared_music_only_player_is_not_limited_to_known_bundle_ids():
    window, focus = content(MUSIC); window.pop('AXDocument')
    assert activity_context('test.new.musicplayer', {MUSIC: 'generic'}, window, focus, metadata) == (MUSIC, 'nontext')


def test_player_controls_inside_native_layout_area_are_detected():
    window, focus = content(VIDEO); focus['AXRole'] = 'AXLayoutArea'
    assert activity_context('test.player', {VIDEO: 'generic'}, window, focus, metadata) == (VIDEO, 'nontext')


@pytest.mark.parametrize('extension', ['pdf', 'webp', 'heic', 'mkv', 'mp3', 'xlsx'])
@pytest.mark.parametrize('evidence', ['title', 'controls', 'canvas'])
def test_non_slide_document_cannot_borrow_slideshow_evidence(extension, evidence):
    window, focus = wps_window_tree() if evidence == 'canvas' else window_tree()
    window['AXDocument'] = 'file:///example.' + extension
    if evidence == 'controls':
        window['AXTitle'] = 'Example'; window['AXChildren'] = [dict(AXRole='AXButton', AXTitle='Close Preview', AXEnabled=True)]
    assert presentation_context(WPS, window, focus, metadata) == ('', 'unknown')


@pytest.mark.parametrize('label', ['Close Preview', '结束播放', '退出预览'])
def test_generic_close_preview_is_not_a_slideshow_signal(label):
    window, focus = window_tree('Preview')
    window['AXChildren'] = [dict(AXRole='AXButton', AXTitle=label, AXEnabled=True)]
    assert presentation_context(WPS, window, focus, metadata)[0] == ''


@pytest.mark.parametrize('state', ['hidden_parent', 'disabled_parent', 'menu_parent', 'stale_focus', 'dialog_parent'])
def test_browser_player_focus_rejects_inactive_ancestors(state):
    window, web, wrapper, video = page()
    focus = Node('AXButton', video)
    if state == 'hidden_parent': wrapper.attrs['AXHidden'] = True
    if state == 'disabled_parent': wrapper.attrs['AXEnabled'] = False
    if state == 'menu_parent': video.attrs['AXRole'] = 'AXMenu'
    if state == 'stale_focus': focus.attrs['AXFocused'] = False
    if state == 'dialog_parent': wrapper.attrs['AXSubrole'] = 'AXDialog'
    result = browser_media_context(window, focus, read)
    assert not result.scene or result.input_context != 'nontext'


@pytest.mark.parametrize('scene', [PDF, IMAGE, VIDEO, MUSIC])
def test_switching_document_in_same_window_rejects_old_target(monkeypatch, scene):
    window, focus = content(scene)
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle='com.apple.finder')
    target = backend.capture(menu_action=True, scene=True)
    assert target.scene == scene and backend.same_target(target)
    window['AXDocument'] = window['AXDocument'].replace('test.', 'different.')
    assert not backend.same_target(target)
    assert 'file:///' not in repr(target)


def test_mid_capture_focus_owner_change_discards_mixed_snapshot(monkeypatch):
    window, focus = window_tree(); focus['AXWindow'] = window
    other, _ = window_tree('Other.pptx')
    backend, _, _ = native_backend(monkeypatch, window, focus)
    ax = sys.modules['ApplicationServices']; original = ax.AXUIElementCopyAttributeValue
    def switch(node, key, unused):
        result = original(node, key, unused)
        if node is window and key == 'AXTitle': focus['AXWindow'] = other
        return result
    ax.AXUIElementCopyAttributeValue = switch
    assert backend.capture(menu_action=True, scene=True) is None


def test_mid_capture_document_change_discards_mixed_snapshot(monkeypatch):
    window, focus = content(VIDEO)
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle='com.apple.QuickTimePlayerX')
    ax = sys.modules['ApplicationServices']; original = ax.AXUIElementCopyAttributeValue
    def switch(node, key, unused):
        result = original(node, key, unused)
        if node is focus and key == 'AXChildren': window['AXDocument'] = 'file:///other.mp4'
        return result
    ax.AXUIElementCopyAttributeValue = switch
    assert backend.capture(menu_action=True, scene=True) is None


def test_scene_shortcut_does_not_follow_focus_to_another_button(desktop):
    backend, state, _ = desktop
    original = replace(state.target, scene=VIDEO, scene_checked=True, input_context='nontext')
    state.target = replace(original, focus='different-button')
    with pytest.raises(RuntimeError): backend.post(original, 'Space')
    assert state.sent == []


@pytest.mark.parametrize('context', ['nontext', 'text', 'unknown'])
def test_presenter_notes_allow_voice_but_unknown_slideshow_never_autofocuses(monkeypatch, context):
    from types import SimpleNamespace
    from proximic_ring import native_access_worker as worker
    from proximic_ring.app_shortcuts import ShortcutTarget
    from proximic_ring.mac_permissions import PermissionState
    monkeypatch.setattr(worker, 'read_permission_state', lambda: PermissionState(True, False))
    dispatcher = worker.Dispatcher()
    dispatcher.shortcuts = SimpleNamespace(capture=lambda **kw: ShortcutTarget(
        POWERPOINT, 42, POWERPOINT, scene=PRESENTATION, input_context=context))
    fallback = []
    dispatcher.text_focus = SimpleNamespace(plans={'old': object()},
        handle=lambda *a, **kw: fallback.append(True) or {'status': 'normal'})
    result = dispatcher.handle({'operation': 'focus_apply', 'scene_apps': {POWERPOINT: [PRESENTATION]}, 'plan': 'old'})
    if context == 'text':
        assert fallback and result['result']['status'] == 'normal'
    else:
        assert not fallback and result['result']['status'] == 'presentation'
        assert not dispatcher.text_focus.plans


def test_preview_title_only_file_change_invalidates_old_image_target(monkeypatch):
    window, focus = content(IMAGE); window.pop('AXDocument'); window['AXTitle'] = 'One.heic'
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle='com.apple.Preview')
    target = backend.capture(menu_action=True, scene=True)
    assert target.scene == IMAGE and backend.same_target(target)
    window['AXTitle'] = 'Two.heic'
    assert not backend.same_target(target)


def test_browser_readonly_web_area_uses_editability_metadata_and_not_value():
    window = dict(AXRole='AXWindow')
    focus = dict(AXRole='AXWebArea', AXParent=window, AXValueSettable=False, AXURL='https://example.test/one.pdf')
    window['AXChildren'] = [focus]
    assert activity_context('com.apple.Safari', {PDF: 'generic'}, window, focus, metadata) == (PDF, 'nontext')
    focus['AXValueSettable'] = True
    assert activity_context('com.apple.Safari', {PDF: 'generic'}, window, focus, metadata)[1] != 'nontext'


@pytest.mark.parametrize('change', ['loading', 'hidden', 'disabled'])
def test_browser_documents_with_inactive_page_do_not_claim_keys(change):
    window = dict(AXRole='AXWindow')
    focus = dict(AXRole='AXWebArea', AXParent=window, AXEditable=False, AXURL='https://example.test/one.pdf')
    focus[dict(loading='AXElementBusy', hidden='AXHidden', disabled='AXEnabled')[change]] = change != 'disabled'
    window['AXChildren'] = [focus]
    assert activity_context('com.apple.Safari', {PDF: 'generic'}, window, focus, metadata)[1] != 'nontext'


@pytest.mark.parametrize('state', ['hidden_parent', 'dialog_parent', 'foreign_window', 'stale_focus'])
def test_native_scene_snapshot_blocks_invalid_focus_before_regular_fallback(monkeypatch, state):
    window, focus = content(PDF)
    if state == 'hidden_parent': focus['AXParent'] = dict(AXRole='AXGroup', AXHidden=True, AXParent=window)
    if state == 'dialog_parent': focus['AXParent'] = dict(AXRole='AXGroup', AXSubrole='AXDialog', AXParent=window)
    if state == 'foreign_window': focus['AXParent'] = dict(AXRole='AXWindow', AXTitle='other')
    if state == 'stale_focus': focus['AXFocused'] = False
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle='com.apple.Preview')
    assert backend.capture(menu_action=True, scene=True).blocked


@pytest.mark.parametrize('change', ['page', 'during_capture'])
def test_browser_pdf_identity_is_checked_before_dispatch(monkeypatch, change):
    window, focus = content(PDF); window.pop('AXDocument')
    focus.update(AXRole='AXWebArea', AXEditable=False, AXURL='https://example.test/one.pdf')
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle='com.apple.Safari')
    if change == 'page':
        target = backend.capture(menu_action=True, scene=True)
        assert target.scene == PDF and backend.same_target(target)
        focus['AXURL'] = 'https://example.test/two.pdf'
        assert not backend.same_target(target)
    else:
        ax = sys.modules['ApplicationServices']; original = ax.AXUIElementCopyAttributeValue
        def switch(node, key, unused):
            result = original(node, key, unused)
            if node is focus and key == 'AXURL': focus['AXURL'] = 'https://example.test/two.pdf'
            return result
        ax.AXUIElementCopyAttributeValue = switch
        assert backend.capture(menu_action=True, scene=True) is None
