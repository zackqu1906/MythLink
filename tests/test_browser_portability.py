"""Cold browsers, cross-browser control labels and realistic AX IPC costs."""
from types import SimpleNamespace
import sys

import pytest

from proximic_ring.browser_accessibility import BrowserAccessibility, read_structure, STRUCTURE_ATTRIBUTES
from proximic_ring.scenes.models import VIDEO, MUSIC, PDF, IMAGE
from test_browser_scene_unification import Node, page, player, scene
from test_presentation_portability import native_backend


@pytest.mark.parametrize('bundle', ['com.apple.Safari', 'com.google.Chrome'])
@pytest.mark.parametrize('kind', [VIDEO, MUSIC])
def test_control_titles_work_inside_bilibili_player_only(bundle, kind):
    window, web = page('https://www.bilibili.com/video/test')
    media = player(web, kind, wrapper=True)
    host = media.attrs['AXParent']
    host.attrs['AXDOMClassList'] = ['bpx-player-primary-area']
    for control in host.attrs['AXChildren'][1:]:
        control.attrs['AXTitle'] = control.attrs.pop('AXDescription')
    result = scene(window, web, bundle)
    assert result.scene == kind and result.player is media
    assert 'Pause' not in repr(result.diagnostic)
    # A page title or an unrelated button cannot replace player evidence.
    host.attrs['AXChildren'] = [media]
    web.attrs['AXTitle'] = 'Pause Playback progress'
    Node('AXButton', web, AXTitle='Pause', AXEnabled=True)
    Node('AXSlider', web, AXTitle='Playback progress', AXEnabled=True)
    assert not scene(window, web, bundle).scene


def native_page(monkeypatch, *, bundle='com.google.Chrome', url='https://www.bilibili.com/video/test'):
    window, web = page(url)
    backend, root, _ = native_backend(monkeypatch, window, web, bundle=bundle)
    ax = sys.modules['ApplicationServices']
    ax.AXUIElementCopyAttributeValue = lambda node, key, _: (0, (node.attrs if isinstance(node, Node) else node).get(key))
    return backend, root, ax, window, web


@pytest.mark.parametrize('bundle', ['com.apple.Safari', 'com.google.Chrome'])
@pytest.mark.parametrize('kind', [VIDEO, MUSIC, PDF, IMAGE])
def test_cold_browser_initializes_once_then_recognizes_all_page_kinds(monkeypatch, bundle, kind):
    import proximic_ring.browser_accessibility as access
    clock, writes = [0.0], []
    monkeypatch.setattr(access.time, 'monotonic', lambda: clock[0])
    url = 'https://example.test/document.' + ('pdf' if kind == PDF else 'png') if kind in {PDF, IMAGE} else 'https://www.bilibili.com/video/test'
    backend, root, ax, window, web = native_page(monkeypatch, bundle=bundle, url=url)
    if kind in {VIDEO, MUSIC}:
        player(web, kind, wrapper=True)
    original = ax.AXUIElementCopyAttributeValue
    def cold_read(node, key, unused):
        if node is root and key == 'AXEnhancedUserInterface':
            return 0, False  # AppKit may keep reporting false after the write.
        if node is root and key == 'AXFocusedUIElement' and clock[0] < 2.2:
            return 0, None
        if node is window and key == 'AXChildren' and clock[0] < 2.2:
            return 0, []
        return original(node, key, unused)
    ax.AXUIElementCopyAttributeValue = cold_read
    ax.AXUIElementSetAttributeValue = lambda node, key, value: writes.append((node is root, key, value)) or -25208
    for tick in [0, .4, .8, 1.2, 1.6, 2.0]:
        clock[0] = tick
        target = backend.capture(scene=True, menu_action=True, scene_observation=True)
        assert not target.scene
        assert target.diagnostic['reason'] == 'browser_accessibility_initializing'
    clock[0] = 2.3
    target = backend.capture(scene=True, menu_action=True, scene_observation=True)
    assert target.scene == kind and not target.blocked
    assert writes == [(True, 'AXEnhancedUserInterface', True)]
    assert backend.same_target(target)


def test_initialization_tracks_process_lifetime_and_retries_transient_failure(monkeypatch):
    import proximic_ring.browser_accessibility as access
    clock, launch, writes = [0.], [1.], []
    monkeypatch.setattr(access.time, 'monotonic', lambda: clock[0])
    app = SimpleNamespace(bundleIdentifier=lambda: 'com.google.Chrome', processIdentifier=lambda: 42,
        launchDate=lambda: SimpleNamespace(timeIntervalSince1970=lambda: launch[0]))
    ax = SimpleNamespace(AXUIElementCopyAttributeValue=lambda *_: (0, False),
        AXUIElementSetAttributeValue=lambda *args: writes.append(args) or (-25204 if len(writes) == 1 else 0))
    prep, root = BrowserAccessibility(), object()
    assert prep.prepare(app, root, ax)['state'] == 'unavailable'
    clock[0] = 1
    prep.prepare(app, root, ax)
    assert len(writes) == 1
    clock[0] = 6
    assert prep.prepare(app, root, ax)['state'] == 'requested'
    clock[0] = 9
    assert not prep.prepare(app, root, ax)['warming']
    assert len(writes) == 2
    launch[0] = 2  # PID reused after a browser restart.
    prep.prepare(app, root, ax)
    assert len(writes) == 3


@pytest.mark.parametrize('error, enabled', [(0, True), (-25211, None)])
def test_existing_support_or_denied_permission_does_not_write(error, enabled):
    app = SimpleNamespace(bundleIdentifier=lambda: 'com.apple.Safari', processIdentifier=lambda: 1)
    ax = SimpleNamespace(AXUIElementCopyAttributeValue=lambda *_: (error, enabled),
        AXUIElementSetAttributeValue=lambda *_: pytest.fail('unexpected AX write'))
    assert not BrowserAccessibility().prepare(app, object(), ax)['warming']


def install_batch(ax, monkeypatch, clock, latency):
    class AXError:
        def __init__(self, code): self.code = code
    original = ax.AXUIElementCopyAttributeValue
    def single(*args):
        clock[0] += latency
        return original(*args)
    def batch(node, names, flags, unused):
        clock[0] += latency
        assert names == STRUCTURE_ATTRIBUTES and flags == 0
        return 0, [original(node, name, None)[1] if original(node, name, None)[1] is not None else AXError(-25212) for name in names]
    monkeypatch.setattr(ax, 'AXUIElementCopyAttributeValue', single)
    monkeypatch.setattr(ax, 'AXUIElementCopyMultipleAttributeValues', batch, raising=False)
    ax.AXValueRef = AXError
    ax.kAXValueAXErrorType = 5
    ax.AXValueGetType = lambda value: 5
    ax.AXValueGetValue = lambda value, kind, _: (True, value.code)


def test_batched_metadata_recognizes_bilibili_with_slow_cross_process_calls(monkeypatch):
    import proximic_ring.browser_accessibility as access
    backend, root, ax, window, web = native_page(monkeypatch)
    media = player(web, wrapper=True)
    for _ in range(550):
        Node('AXGroup', media)
    clock = [0.]
    monkeypatch.setattr(access.time, 'monotonic', lambda: clock[0])
    install_batch(ax, monkeypatch, clock, .00018)
    target = backend.capture(scene=True, menu_action=True)
    assert target.scene == VIDEO and target.input_context == 'nontext'
    assert target.diagnostic['ax_batches'] >= 550
    assert target.diagnostic['recognition']['scanned_nodes'] >= 550
    # Same tree/IPC latency on the old single-attribute path exceeds 300ms.
    monkeypatch.delattr(ax, 'AXUIElementCopyMultipleAttributeValues')
    assert backend.capture(scene=True, menu_action=True).diagnostic['reason'] == 'recognition_timeout'


def test_batch_attribute_errors_and_api_fallback_preserve_boundaries(monkeypatch):
    backend, root, ax, window, web = native_page(monkeypatch)
    media = player(web)
    install_batch(ax, monkeypatch, [0.], 0)
    result = read_structure(ax, media)
    assert result['AXHidden'] == (-25212, None)
    assert backend.capture(scene=True, menu_action=True).scene == VIDEO
    media.attrs['AXHidden'] = True
    assert not backend.capture(scene=True, menu_action=True).scene
    media.attrs['AXHidden'] = False
    web.attrs['AXEditable'] = True
    assert backend.capture(scene=True, menu_action=True).input_context == 'text'
    monkeypatch.setattr(ax, 'AXUIElementCopyMultipleAttributeValues', lambda *_: (-25205, None))
    assert read_structure(ax, media) is None
    assert backend.capture(scene=True, menu_action=True).input_context == 'text'


def test_regular_shortcuts_do_not_initialize_browser_accessibility(monkeypatch):
    backend, root, ax, window, web = native_page(monkeypatch)
    ax.AXUIElementSetAttributeValue = lambda *_: pytest.fail('unexpected AX write')
    assert backend.capture(menu_action=True) is not None
