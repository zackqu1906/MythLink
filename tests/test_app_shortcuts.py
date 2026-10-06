from dataclasses import replace
from types import SimpleNamespace
import sys

import pytest

from proximic_ring.app_shortcuts import LocalMacAppShortcuts as MacAppShortcuts, ShortcutTarget, verify_native_api


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS bridge exports")
def test_installed_macos_bridge_exports_without_desktop_access():
    # Do not mock these exports: Quartz does not contain Accessibility functions.
    verify_native_api()


@pytest.fixture
def desktop(monkeypatch):
    target = ShortcutTarget("com.openai.codex", 42, "codex", "w", "editor", "AXTextArea")
    state = SimpleNamespace(target=target, permission=True, sent=[])
    backend = MacAppShortcuts()
    monkeypatch.setattr(backend, "capture", lambda **kw: state.target)
    quartz = SimpleNamespace(
        kCGEventFlagMaskCommand=256, kCGEventFlagMaskShift=128,
        kCGEventFlagMaskControl=64, kCGEventFlagMaskAlternate=32, kCGEventFlagMaskSecondaryFn=512, kCGEventSourceUserData=99,
        CGPreflightPostEventAccess=lambda: state.permission,
        CGEventCreateKeyboardEvent=lambda source, code, down: {"code": code, "down": down},
        CGEventSetFlags=lambda event, flags: event.update(flags=flags),
        CGEventSetIntegerValueField=lambda event, field, value: event.update(tag=value),
        CGEventPostToPid=lambda pid, event: state.sent.append((pid, event)),
    )
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    return backend, state, quartz


@pytest.mark.parametrize("shortcut,code,flags", [("Enter", 36, 0), ("Cmd+Shift+[", 33, 384),
    ("Cmd+]", 30, 256), ("Cmd+N", 45, 256), ("Ctrl+Alt+Return", 36, 96),
    ("Ctrl+Fn+Left", 123, 576), ("F20", 90, 0), ("Cmd+KeypadEnter", 76, 256)])
def test_posts_one_balanced_chord_to_pinned_pid(desktop, shortcut, code, flags):
    backend, state, _ = desktop
    backend.post(state.target, shortcut)
    from proximic_ring.mac_shortcut_events import MODIFIER_KEYS
    names=shortcut.split('+')[:-1]
    device_flags=0
    for name in names: device_flags |= MODIFIER_KEYS[name][1]
    assert [(pid, e["code"], e["flags"], e["down"]) for pid, e in state.sent if e["code"] == code] == [
        (42, code, flags | device_flags, True), (42, code, flags | device_flags, False)]
    assert len(state.sent) == 2 + 2 * len(names)
    assert all(pid == 42 for pid, _ in state.sent)
    if names: assert state.sent[-1][1]["flags"] == 0


@pytest.mark.parametrize("change", ["app", "pid", "window", "focus", "modal", "missing", "permission"])
def test_stale_target_or_permission_failure_posts_nothing(desktop, change):
    backend, state, _ = desktop
    original = state.target
    if change == "permission":
        state.permission = False
    elif change == "missing":
        state.target = None
    else:
        field, value = {"app": ("bundle", "com.apple.Terminal"), "pid": ("pid", 100),
            "window": ("window", "other"), "focus": ("focus", "search"), "modal": ("blocked", True)}[change]
        state.target = replace(state.target, **{field: value})
    with pytest.raises(RuntimeError):
        backend.post(original, "Enter", require_focus=True)
    assert not state.sent


def test_both_events_must_allocate_before_keydown(desktop):
    backend, state, quartz = desktop
    quartz.CGEventCreateKeyboardEvent = lambda _, code, down: {"code": code} if down else None
    with pytest.raises(RuntimeError):
        backend.post(state.target, "Enter")
    assert not state.sent


def test_existing_shortcut_backend_recovers_after_grant_and_stops_after_revocation(desktop):
    backend, state, _ = desktop
    state.permission = False
    with pytest.raises(RuntimeError):
        backend.post(state.target, "Enter")
    assert not state.sent
    state.permission = True
    # Grant does not replay the failed send; only a fresh action sends it.
    assert not state.sent
    backend.post(state.target, "Enter")
    assert len(state.sent) == 2
    state.permission = False
    with pytest.raises(RuntimeError):
        backend.post(state.target, "Enter")
    assert len(state.sent) == 2


def test_capture_is_bounded_metadata_only_no_text_or_clipboard(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    reads = []
    values = {("app", "AXFocusedWindow"): "window", ("app", "AXFocusedUIElement"): "focus",
              ("focus", "AXRole"): "AXTextArea", ("focus", "AXDescription"): "Message input"}
    def read(node, attribute, _):
        reads.append(attribute)
        assert attribute not in {"AXValue", "AXChildren", "AXSelectedText", "AXSelectedTextRange"}
        return 0, values.get((node, attribute))
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: "app",
        AXUIElementCopyAttributeValue=read,
        AXUIElementSetMessagingTimeout=lambda elem, timeout: None))
    app = SimpleNamespace(bundleIdentifier=lambda: "com.openai.codex", processIdentifier=lambda: 42,
                          localizedName=lambda: "Codex")
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSWorkspace=SimpleNamespace(
        sharedWorkspace=lambda: SimpleNamespace(frontmostApplication=lambda: app))))
    target = MacAppShortcuts().capture()
    assert target.profile == "codex" and target.focus == "focus" and not target.blocked
    assert len(reads) == 9
    assert set(reads) == {"AXFocusedWindow", "AXFocusedUIElement", "AXRole", "AXWindow",
                          "AXSubrole", "AXDescription", "AXModal", "AXSheets", "AXMinimized"}


@pytest.mark.parametrize("bundle,role,description", [
    ("com.apple.Safari", "AXTextField", "网址和搜索"),
    ("com.google.Chrome", "AXComboBox", "Address and search bar"),
    ("other.app", "AXSearchField", "搜索"),
    ("other.editor", "AXWebArea", "editor"),
])
def test_plain_enter_capture_all_apps_and_fields(monkeypatch, bundle, role, description):
    monkeypatch.setattr(sys, "platform", "darwin")
    values = {("app", "AXFocusedWindow"): "w", ("app", "AXFocusedUIElement"): "f",
              ("f", "AXRole"): role, ("f", "AXDescription"): description}
    reads = []
    def read(node, name, _):
        reads.append(name)
        return 0, values.get((node, name))
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: "app",
        AXUIElementCopyAttributeValue=read,
        AXUIElementSetMessagingTimeout=lambda *args: None))
    app = SimpleNamespace(bundleIdentifier=lambda: bundle, processIdentifier=lambda: 42,
                          localizedName=lambda: "Unknown")
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSWorkspace=SimpleNamespace(
        sharedWorkspace=lambda: SimpleNamespace(frontmostApplication=lambda: app))))
    backend = MacAppShortcuts()
    assert backend.capture() is None  # App-specific navigation stays restricted.
    target = backend.capture(plain_enter=True)
    assert target.bundle == bundle and target.plain_enter and not target.blocked
    assert backend.same_target(target, require_focus=True)
    values[("app", "AXFocusedUIElement")] = "different-field"
    assert not backend.same_target(target, require_focus=True)
    assert set(reads) == {"AXFocusedWindow", "AXFocusedUIElement", "AXRole", "AXWindow"}


def test_plain_enter_target_posts_no_modifiers_and_rejects_other_chords(desktop):
    backend, state, _ = desktop
    state.target = replace(state.target, bundle="com.apple.Safari", profile="", plain_enter=True)
    backend.post(state.target, "Enter", require_focus=True)
    assert [(e["code"], e["flags"], e["down"]) for _, e in state.sent] == [(36, 0, True), (36, 0, False)]
    with pytest.raises(RuntimeError, match="仅允许 Enter"):
        backend.post(state.target, "Cmd+Return", require_focus=True)
    assert len(state.sent) == 2
