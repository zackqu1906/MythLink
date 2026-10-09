"""Regressions distilled from Safari's real player/PDF accessibility trees."""
from dataclasses import replace
import sys
import pytest

from proximic_ring.scenes.recognition.browser import MAX_PAGE_NODES
from proximic_ring.scenes.recognition.playback import PlaybackControls
from proximic_ring.scene_capabilities import BROWSERS, PDF, IMAGE, VIDEO, MUSIC
from test_browser_scene_unification import Node, read, page, player, scene
from test_app_shortcuts import desktop
from test_presentation_portability import native_backend


def custom_player(web, kind=VIDEO, wrapper='bpx-player-primary-area', count=0):
    host = Node('AXGroup', web, AXDOMClassList=[wrapper])
    media = Node('AXGroup', host, AXSubrole='AXVideo' if kind == VIDEO else 'AXAudio', AXEnabled=True)
    # Safari exposes caption/OCR overlays before the actual controls.
    for _ in range(count):
        Node('AXGroup', media)
    Node('AXButton', host, AXDescription='播放/暂停', AXEnabled=True)
    progress = Node('AXGroup', host, AXDOMClassList=['bpx-player-shadow-progress-schedule-wrap'])
    return media, host, progress


@pytest.mark.parametrize('bundle', sorted(BROWSERS))
@pytest.mark.parametrize('kind', [VIDEO, MUSIC])
def test_large_loaded_page_with_div_timeline_is_detected(bundle, kind):
    window, web = page('https://arbitrary.example.test/watch')
    media, _, _ = custom_player(web, kind, count=550)
    result = scene(window, web, bundle)
    assert (result.scene, result.input_context) == (kind, 'nontext')
    assert result.player is media
    assert result.diagnostic['scanned_nodes'] > 550
    assert not result.diagnostic['scan_limited']
    assert result.diagnostic['player_evidence'][0]['structural_seek']


@pytest.mark.parametrize('marker', ['timeline', 'vjs-progress-control', 'plyr__progress', 'seekbar', 'player_scrubber'])
def test_common_player_timeline_classes_and_identifiers(marker):
    window, web = page(); _, _, progress = custom_player(web, wrapper='video-js')
    progress.attrs.update(AXDOMClassList=[], AXDOMIdentifier=marker)
    assert scene(window, web).scene == VIDEO


@pytest.mark.parametrize('case', ['no_media', 'no_play', 'no_progress', 'progress_outside',
    'disabled', 'hidden', 'volume-progress', 'loading-progress', 'progressive'])
def test_structure_does_not_replace_real_media_and_matching_controls(case):
    window, web = page(); media, host, progress = custom_player(web)
    if case == 'no_media': media.attrs['AXSubrole'] = 'AXImage'
    if case == 'no_play': host.attrs['AXChildren'][1].attrs['AXEnabled'] = False
    if case == 'no_progress': host.attrs['AXChildren'].remove(progress)
    if case == 'progress_outside':
        host.attrs['AXChildren'].remove(progress)
        web.attrs['AXChildren'].append(progress); progress.attrs['AXParent'] = web
    if case == 'disabled': progress.attrs['AXEnabled'] = False
    if case == 'hidden': progress.attrs['AXHidden'] = True
    if case in {'volume-progress', 'loading-progress', 'progressive'}: progress.attrs['AXDOMClassList'] = [case]
    assert not scene(window, web).scene


def test_native_playback_does_not_use_html_container_hints():
    controls = PlaybackControls()
    controls.observe(Node('AXGroup', AXDOMClassList=['progress']), read)
    assert not controls.seek


def test_second_player_after_old_limit_cannot_be_mistaken_for_single_player():
    window, web = page(); custom_player(web, count=350); second = player(web)
    result = scene(window, web)
    assert not result.scene and result.diagnostic['reason'] == 'multiple_players'
    assert scene(window, Node('AXButton', second)).player is second


def test_real_tree_shapes_preserve_text_and_address_focus():
    window, web = page(); _, host, _ = custom_player(web, count=350)
    result = scene(window, Node('AXTextField', host))
    assert (result.scene, result.input_context) == (VIDEO, 'text')
    assert not scene(window, Node('AXTextField', window)).scene


def pdf_plugin(window=None):
    window = window or Node('AXWindow')
    split = Node('AXSplitGroup', window)
    tabs = Node('AXTabGroup', split)
    host = Node('AXGroup', tabs)
    plugin = Node('AXGroup', host, AXSubrole='AXPDFPluginSubrole')
    first = Node('AXPage', plugin)
    Node('AXStaticText', first)
    Node('AXUnknown', first, AXValueSettable=False)
    Node('AXPage', plugin)
    return window, plugin, first


@pytest.mark.parametrize('focused', ['plugin', 'page', 'text', 'window', 'missing'])
def test_safari_native_pdf_with_no_url_uses_page_object_identity(focused):
    window, plugin, first = pdf_plugin()
    focus = dict(plugin=plugin, page=first, text=first.attrs['AXChildren'][0], window=window, missing=None)[focused]
    result = scene(window, focus)
    assert (result.scene, result.input_context) == (PDF, 'nontext')
    assert result.web_area is plugin and result.player is first and result.page_key == 'native-pdf'
    assert result.diagnostic['evidence'] == 'native_pdf_pages'
    assert result.diagnostic['inferred_pdf_focus'] == (focused in {'window', 'missing'})


@pytest.mark.parametrize('case', ['direct_form', 'page_form', 'editable_group', 'settable_unknown', 'unknown', 'incomplete'])
def test_pdf_without_explicit_focus_never_assumes_forms_are_nontext(case):
    window, plugin, first = pdf_plugin()
    if case == 'direct_form': Node('AXTextField', plugin)
    if case == 'page_form': Node('AXTextField', first)
    if case == 'editable_group': Node('AXGroup', first, AXEditable=True)
    if case == 'settable_unknown': Node('AXUnknown', first, AXValueSettable=True)
    if case == 'unknown': Node('AXUnknown', first)
    if case == 'incomplete':
        for _ in range(MAX_PAGE_NODES): Node('AXGroup', first)
    result = scene(window, None)
    assert (result.scene, result.input_context) == (PDF, 'unknown')
    form = Node('AXTextField', first)
    assert scene(window, form).input_context == 'text'


@pytest.mark.parametrize('case', ['address', 'toolbar', 'webpage', 'two_plugins', 'hidden', 'disabled', 'loading', 'modal', 'sheet', 'no_pages'])
def test_missing_pdf_focus_fallback_is_confined_to_current_readonly_document(case):
    window, plugin, first = pdf_plugin(); focus = None
    if case == 'address': focus = Node('AXTextField', window)
    if case == 'toolbar': focus = Node('AXButton', window)
    if case == 'webpage': focus = Node('AXWebArea', window, AXURL='https://example.test/', AXEditable=False)
    if case == 'two_plugins': pdf_plugin(window)
    if case == 'hidden': plugin.attrs['AXHidden'] = True
    if case == 'disabled': plugin.attrs['AXEnabled'] = False
    if case == 'loading': plugin.attrs['AXElementBusy'] = True
    if case == 'modal': window.attrs['AXModal'] = True
    if case == 'sheet': window.attrs['AXSheets'] = [Node('AXSheet', window)]
    if case == 'no_pages': plugin.attrs['AXChildren'] = []
    assert not scene(window, focus).scene


def test_pdf_plugin_inside_background_webpage_is_not_a_missing_focus_fallback():
    window, web = page()
    plugin = Node('AXGroup', web, AXSubrole='AXPDFPluginSubrole')
    Node('AXPage', plugin)
    assert not scene(window, None).scene


def test_explicit_pdf_focus_does_not_scan_unrelated_page_bodies():
    from proximic_ring.scenes.recognition.browser import detect_browser
    window, plugin, first = pdf_plugin()
    unrelated = Node('AXPage', plugin)
    def minimal_read(node, key):
        assert node is not unrelated or key == 'AXRole'
        return read(node, key)
    result = detect_browser(window, first, minimal_read)
    assert (result.scene, result.input_context) == (PDF, 'nontext')


@pytest.mark.parametrize('extension,kind', [('pdf', PDF), ('png', IMAGE), ('webp', IMAGE), ('jpg', IMAGE)])
def test_direct_reading_scenes_still_require_the_current_page(extension, kind):
    window, web = page('https://example.test/current.' + extension)
    result = scene(window, web)
    assert (result.scene, result.input_context) == (kind, 'nontext')
    assert not scene(window, Node('AXTextField', window)).scene


@pytest.mark.parametrize('change', ['page', 'plugin', 'text_focus', 'scene'])
def test_pdf_identity_or_context_change_cancels_queued_dispatch(desktop, change):
    backend, state, _ = desktop
    target = replace(state.target, bundle='com.apple.Safari', scene=PDF, scene_checked=True,
                     input_context='nontext', page_key='native-pdf', web_area='plugin', player='page')
    updates = dict(page=dict(player='new-page'), plugin=dict(web_area='new-plugin'),
                   text_focus=dict(input_context='text'), scene=dict(scene=VIDEO))[change]
    state.target = replace(target, **updates)
    with pytest.raises(RuntimeError): backend.post(target, 'PageDown')
    assert not state.sent


def test_native_capture_and_recheck_use_pdf_plugin_and_page_identity(monkeypatch):
    window, plugin, first = pdf_plugin()
    # The native AX fixture uses dictionaries, so convert while preserving links.
    def attrs(node):
        for key in ('AXParent',):
            if isinstance(node.attrs.get(key), Node): node.attrs[key] = node.attrs[key].attrs
        children = node.attrs.get('AXChildren', [])
        for child in children: attrs(child)
        node.attrs['AXChildren'] = [child.attrs for child in children]
    attrs(window)
    backend, _, _ = native_backend(monkeypatch, window.attrs, None, bundle='com.apple.Safari')
    monkeypatch.setattr(sys.modules['ApplicationServices'], 'AXUIElementIsAttributeSettable',
                        lambda node, key, unused: (0, node.get('AXValueSettable', False)), raising=False)
    target = backend.capture(menu_action=True, scene=True)
    assert target.scene == PDF and target.input_context == 'nontext' and backend.same_target(target)
    plugin.attrs['AXChildren'] = [dict(AXRole='AXPage', AXParent=plugin.attrs)]
    assert not backend.same_target(target)
