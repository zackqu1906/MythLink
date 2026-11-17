"""Read focused-field metadata and paste without changing the system clipboard.

Called only on the suggestion controller's serial background queue. AX never
blocks Qt, and no document body is read to make a recommendation.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ClipboardField:
    key: object
    target: object
    rect: tuple


class MacClipboardSuggestionAccess:
    def __init__(self):
        from ...desktop.app_shortcuts import LocalMacAppShortcuts
        from ...desktop.text_focus import MacAX
        self.shortcuts = LocalMacAppShortcuts()
        self.ax = MacAX()

    def capture(self):
        from ...touchpad.stroke.stroke_focus import editable_target
        # Ordinary shortcuts intentionally capture only the window. The
        # passive focus mode also resolves the actual editor without switching IMEs.
        target = self.shortcuts.capture(menu_action=True, passive_undo=True)
        if target is None or not editable_target(target, self.ax):
            return None
        # Qt password editors omit AXSecureTextField but expose its localized
        # role description. Do not mistake that missing subrole for plain text.
        from AppKit import NSAccessibilityRoleDescription
        ax = self.ax.ax
        description = str(self.ax.attr(target.focus, 'AXRoleDescription') or '').casefold()
        secure_description = str(NSAccessibilityRoleDescription('AXTextField', 'AXSecureTextField') or '').casefold()
        if (self.ax.attr(target.focus, 'AXProtectedContent') is True
                or description and description in {secure_description, 'secure text field'}):
            return None
        # Retained AX element identity distinguishes editors within one window
        # and remains comparable when that same editor regains focus.
        key = (target.pid, target.window, target.focus)
        rect = self.ax.rect(target.focus)
        if not rect:
            return None
        # Prefer the insertion caret, falling back to the focused field edge.
        selection = self.ax.attr(target.focus, 'AXSelectedTextRange')
        if selection is not None:
            error, bounds = ax.AXUIElementCopyParameterizedAttributeValue(
                target.focus, 'AXBoundsForRange', selection, None)
            if error == 0 and bounds is not None:
                ok, value = ax.AXValueGetValue(bounds, ax.kAXValueCGRectType, None)
                if ok and value.size.height > 0:
                    rect = (value.origin.x, value.origin.y, max(1, value.size.width), value.size.height)
        return ClipboardField(key, target, tuple(rect))

    def paste(self, field, revision, cancelled):
        from AppKit import NSPasteboard
        import Quartz
        from ...platforms.macos.mac_permissions import require_post_event_access
        from ...platforms.macos.mac_shortcut_events import prepare_chord, post_chord
        current = self.capture()
        if cancelled.is_set() or current is None or current.key != field.key:
            return False
        require_post_event_access()
        events, releases, modifiers = prepare_chord(Quartz, 'Cmd+V')
        # Recheck after remote AX calls and key allocation. A tap on the ring
        # cancels this flag directly on the firmware thread, before Qt delivery.
        if (cancelled.is_set() or NSPasteboard.generalPasteboard().changeCount() != revision
                or self.ax.front().get('pid') != field.target.pid):
            return False
        post_chord(Quartz, field.target.pid, events, releases, {'events_posted': 0}, system_wide=False)
        return True


def clipboard_revision():
    from AppKit import NSPasteboard
    from ...ui.shared.clipboard import INTERNAL_CLIPBOARD_TYPE, internal_clipboard_change
    board = NSPasteboard.generalPasteboard()
    revision = int(board.changeCount())
    if (internal_clipboard_change(revision)
            or board.stringForType_(INTERNAL_CLIPBOARD_TYPE)
            or not board.types()):
        return None
    return revision
