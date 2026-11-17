"""The paste boundary validates the latest board and field before any key."""
from types import SimpleNamespace
import sys
import threading

import pytest

from proximic_ring.platforms.macos.clipboard_suggestion import ClipboardField, MacClipboardSuggestionAccess


@pytest.mark.parametrize('secure', [False, True])
def test_focus_capture_explicitly_requests_editor_identity(monkeypatch, secure):
    import proximic_ring.touchpad.stroke.stroke_focus as focus
    monkeypatch.setitem(sys.modules, 'AppKit', SimpleNamespace(NSAccessibilityRoleDescription=lambda *args: 'secure text field'))
    access = object.__new__(MacClipboardSuggestionAccess)
    target = SimpleNamespace(pid=42, window='window', focus='editor')
    def capture(**options):
        assert options == {'menu_action': True, 'passive_undo': True}
        return target
    access.shortcuts = SimpleNamespace(capture=capture)
    def attribute(node, name):
        assert node == 'editor' and name in {'AXSelectedTextRange', 'AXRoleDescription', 'AXProtectedContent'}
        if secure and name == 'AXRoleDescription': return 'secure text field'
        return None
    access.ax = SimpleNamespace(rect=lambda node: (10, 20, 30, 40), attr=attribute, ax=object())
    monkeypatch.setattr(focus, 'editable_target', lambda candidate, ax: candidate is target)
    captured = access.capture()
    if secure: assert captured is None
    else: assert captured.key == (42, 'window', 'editor') and captured.rect == (10, 20, 30, 40)


@pytest.mark.parametrize('change', ['none', 'focus', 'clipboard', 'foreground', 'tap_during_read', 'tap_during_allocation'])
def test_paste_revalidates_after_reads_and_keeps_every_clipboard_format(monkeypatch, change):
    import proximic_ring.platforms.macos.mac_shortcut_events as keys
    import proximic_ring.platforms.macos.mac_permissions as permissions
    contents = {'public.png': b'original-image', 'public.utf8-plain-text': b'original-text'}
    original = dict(contents)
    board = SimpleNamespace(changeCount=lambda: 8 if change == 'clipboard' else 7, contents=contents)
    monkeypatch.setitem(sys.modules, 'AppKit', SimpleNamespace(NSPasteboard=SimpleNamespace(generalPasteboard=lambda: board)))
    monkeypatch.setitem(sys.modules, 'Quartz', SimpleNamespace())
    monkeypatch.setattr(permissions, 'require_post_event_access', lambda: None)
    cancelled, posts = threading.Event(), []
    focused = ClipboardField('editor', SimpleNamespace(pid=42), (0, 0, 10, 10))
    access = object.__new__(MacClipboardSuggestionAccess)
    def capture():
        if change == 'tap_during_read': cancelled.set()
        return None if change == 'focus' else focused
    access.capture = capture
    access.ax = SimpleNamespace(front=lambda: {'pid': 9 if change == 'foreground' else 42})
    def prepare(quartz, shortcut):
        assert shortcut == 'Cmd+V'
        if change == 'tap_during_allocation': cancelled.set()
        return ['down', 'up'], {}, ['Cmd']
    def post(quartz, pid, events, releases, diagnostic, *, system_wide):
        assert diagnostic['events_posted'] == 0
        posts.append((pid, events, system_wide))
    monkeypatch.setattr(keys, 'prepare_chord', prepare)
    monkeypatch.setattr(keys, 'post_chord', post)
    assert access.paste(focused, 7, cancelled) == (change == 'none')
    assert posts == ([(42, ['down', 'up'], False)] if change == 'none' else [])
    assert board.contents == original
