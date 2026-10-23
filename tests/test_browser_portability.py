"""Cold browsers, cross-browser control labels and realistic AX IPC costs."""
from types import SimpleNamespace
import sys

import pytest

from proximic_ring.desktop.browser_accessibility import BrowserAccessibility, read_structure, STRUCTURE_ATTRIBUTES
from proximic_ring.scenes.models import VIDEO, MUSIC, PDF, IMAGE
from test_browser_scene_unification import Node, page, player, scene
from test_presentation_portability import native_backend


def native_page(monkeypatch, *, bundle='com.google.Chrome', url='https://www.bilibili.com/video/test'):
    window, web = page(url)
    backend, root, _ = native_backend(monkeypatch, window, web, bundle=bundle)
    ax = sys.modules['ApplicationServices']
    ax.AXUIElementCopyAttributeValue = lambda node, key, _: (0, (node.attrs if isinstance(node, Node) else node).get(key))
    return backend, root, ax, window, web


def test_initialization_tracks_process_lifetime_and_retries_transient_failure(monkeypatch):
    import proximic_ring.desktop.browser_accessibility as access
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


def test_regular_shortcuts_do_not_initialize_browser_accessibility(monkeypatch):
    backend, root, ax, window, web = native_page(monkeypatch)
    ax.AXUIElementSetAttributeValue = lambda *_: pytest.fail('unexpected AX write')
    assert backend.capture(menu_action=True) is not None
