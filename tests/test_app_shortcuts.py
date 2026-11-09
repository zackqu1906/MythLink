from dataclasses import replace
from types import SimpleNamespace
import sys

import pytest

from proximic_ring.desktop.app_shortcuts import LocalMacAppShortcuts as MacAppShortcuts, ShortcutTarget, verify_native_api


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
        kCGEventFlagMaskControl=64, kCGEventFlagMaskAlternate=32, kCGEventFlagMaskSecondaryFn=512,
        kCGEventFlagMaskNumericPad=1024, kCGEventSourceUserData=99, kCGHIDEventTap=0,
        CGPreflightPostEventAccess=lambda: state.permission,
        CGEventCreateKeyboardEvent=lambda source, code, down: {"code": code, "down": down},
        CGEventGetFlags=lambda event: event.get("flags", 0),
        CGEventSetFlags=lambda event, flags: event.update(flags=flags),
        CGEventSetIntegerValueField=lambda event, field, value: event.update(tag=value),
        CGEventPostToPid=lambda pid, event: state.sent.append((pid, event)),
        CGEventPost=lambda tap, event: state.sent.append(('hid', event)),
    )
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    return backend, state, quartz


@pytest.mark.parametrize("shortcut,code,flags", [("Enter", 36, 0), ("Cmd+Shift+[", 33, 384),
    ("Cmd+]", 30, 256), ("Cmd+N", 45, 256), ("Ctrl+Alt+Return", 36, 96),
    ("Ctrl+Fn+Left", 123, 576), ("F20", 90, 0), ("Cmd+KeypadEnter", 76, 256)])
def test_posts_one_balanced_chord_to_pinned_pid(desktop, shortcut, code, flags):
    backend, state, _ = desktop
    backend.post(state.target, shortcut)
    from proximic_ring.platforms.macos.mac_shortcut_events import MODIFIER_KEYS
    names=shortcut.split('+')[:-1]
    device_flags=0
    for name in names: device_flags |= MODIFIER_KEYS[name][1]
    assert [(pid, e["code"], e["flags"], e["down"]) for pid, e in state.sent if e["code"] == code] == [
        (42, code, flags | device_flags, True), (42, code, flags | device_flags, False)]
    assert len(state.sent) == 2 + 2 * len(names)
    assert all(pid == 42 for pid, _ in state.sent)
    if names: assert state.sent[-1][1]["flags"] == 0


@pytest.mark.parametrize("change", ["app", "pid", "window", "modal", "missing", "permission"])
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


@pytest.fixture
def shortcut_capture(monkeypatch):
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
        AXUIElementIsAttributeSettable=lambda node, attribute, _: (0, values.get((node, attribute + ".writable"), False)),
        AXUIElementSetMessagingTimeout=lambda elem, timeout: None))
    app = SimpleNamespace(bundleIdentifier=lambda: "com.openai.codex", processIdentifier=lambda: 42,
                          localizedName=lambda: "Codex")
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSWorkspace=SimpleNamespace(
        sharedWorkspace=lambda: SimpleNamespace(frontmostApplication=lambda: app))))
    return MacAppShortcuts(), values, reads


def test_capture_is_bounded_metadata_only_no_text_or_clipboard(shortcut_capture):
    backend, _, reads = shortcut_capture
    target = backend.capture()
    assert target.profile == "codex" and target.focus is None and not target.blocked
    assert len(reads) == 5
    assert set(reads) == {"AXFocusedWindow", "AXSubrole", "AXModal", "AXSheets", "AXMinimized"}


def test_passive_undo_shortcut_rechecks_native_control_before_dispatch(shortcut_capture):
    backend, values, _ = shortcut_capture
    values.update({("focus", "AXRole"): "AXButton", ("focus", "AXWindow"): "window",
                   ("focus", "AXFocused"): True, ("focus", "AXEnabled"): True})
    target = backend.capture(menu_action=True, scene=True, passive_undo=True)
    assert target.input_context == "nontext" and backend.same_target(target)
    values[("focus", "AXRole")] = "AXTextArea"
    assert not backend.same_target(target)


@pytest.mark.parametrize("attributes,expected", [
    ({"AXRole": "AXTextArea", "AXValue.writable": True}, "text"),
    ({"AXRole": "AXComboBox", "AXIsEditable": True}, "text"),
    ({"AXRole": "AXComboBox", "AXValue.writable": True}, "unknown"),
    ({"AXRole": "AXTextField", "AXValue.writable": True, "AXSubrole": "AXSecureTextField"}, "unknown"),
    ({"AXRole": "AXTextField", "AXIsEditable": True, "AXEnabled": False}, "unknown"),
    ({"AXRole": "AXTextArea", "AXIsEditable": False, "AXValue.writable": True}, "unknown"),
    ({"AXRole": "AXWebArea", "AXIsEditable": False}, "unknown"),
    ({"AXRole": "AXGroup"}, "unknown"),
])
def test_passive_focus_requires_editability_not_just_a_text_role(shortcut_capture, attributes, expected):
    backend, values, reads = shortcut_capture
    values.update({("focus", name): value for name, value in attributes.items()})
    target = backend.capture(menu_action=True, scene=True, passive_undo=True)
    assert target.input_context == expected
    assert "AXValue" not in reads


def test_passive_browser_editor_fences_readonly_fields_and_editor_changes(shortcut_capture, monkeypatch):
    from proximic_ring.scenes.models import SceneResult
    backend, values, _ = shortcut_capture
    values[("focus", "AXRole")] = "AXWebArea"
    result = SceneResult(input_context="text", page_key="page", browser_target={})
    monkeypatch.setattr("proximic_ring.desktop.app_shortcuts.detect_scene", lambda *a, **k: result)
    def capture():
        return backend.capture(menu_action=True, scene=True, passive_undo=True)
    assert capture().input_context == "unknown"  # Legacy extension does not distinguish passwords.
    result = replace(result, browser_target={"editor": "current"})
    target = capture()
    assert target.input_context == "text"
    result = replace(result, browser_target={"editor": "different"})
    assert not backend.same_target(target)
    result = replace(result, browser_target={"editor": "blocked"})
    values.update({("focus", "AXRole"): "AXTextField", ("focus", "AXValue.writable"): True})
    assert capture().input_context == "unknown"


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
    assert set(reads) == {"AXFocusedWindow", "AXFocusedUIElement", "AXRole"}


def test_plain_enter_target_posts_no_modifiers_and_rejects_other_chords(desktop):
    backend, state, _ = desktop
    state.target = replace(state.target, bundle="com.apple.Safari", profile="", plain_enter=True)
    backend.post(state.target, "Enter", require_focus=True)
    assert [(e["code"], e["flags"], e["down"]) for _, e in state.sent] == [(36, 0, True), (36, 0, False)]
    with pytest.raises(RuntimeError, match="仅允许 Enter"):
        backend.post(state.target, "Cmd+Return", require_focus=True)
    assert len(state.sent) == 2
