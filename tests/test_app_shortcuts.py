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


@pytest.fixture
def wechat_capture(shortcut_capture, monkeypatch):
    backend, values, _ = shortcut_capture
    values[("app", "AXFocusedUIElement")] = None
    values.update({("editor", "AXWindow"): "window", ("editor", "AXRole"): "AXTextArea",
                   ("editor", "AXValue.writable"): True, ("editor", "AXFocused"): False})
    app = SimpleNamespace(bundleIdentifier=lambda: "com.tencent.xinWeChat", processIdentifier=lambda: 42,
                          localizedName=lambda: "WeChat")
    monkeypatch.setattr(sys.modules["AppKit"].NSWorkspace, "sharedWorkspace",
                        lambda: SimpleNamespace(frontmostApplication=lambda: app))
    children = {"window": ["editor"]}
    pids = {"editor": 42, "button": 42}
    ax = sys.modules["ApplicationServices"]
    ax.AXUIElementCreateSystemWide = lambda: "system"
    ax.AXUIElementGetPid = lambda node, _: (0, pids.get(node, 0))
    backend._wechat_ax = SimpleNamespace(ax=ax, attr=lambda node, key: values.get((node, key)),
        prepare_application=lambda pid, node: True, children=lambda node, limit: children.get(node, []))
    return backend, values, children, pids


@pytest.fixture
def wechat_titlebar(wechat_capture):
    backend, values, children, pids = wechat_capture
    values.update({("window", "AXRole"): "AXWindow", ("window", "AXSubrole"): "AXStandardWindow",
                   ("window", "AXMain"): True, ("window", "AXMinimized"): False, ("window", "AXModal"): False,
                   ("app", "AXEnhancedUserInterface"): True, ("app", "AXMainWindow"): "window",
                   ("app", "AXWindows"): ["window"]})
    children.clear()
    children.update(window=["close", "fullscreen", "minimize"], fullscreen=["group1"], group1=["group2"])
    for node, key in (("close", "AXCloseButton"), ("fullscreen", "AXFullScreenButton"),
                      ("minimize", "AXMinimizeButton")):
        values[("window", key)] = node
        values[(node, "AXRole")] = "AXButton"
        values[(node, "AXWindow")] = "window"
    for node in ("group1", "group2"):
        values[(node, "AXRole")] = "AXGroup"
    for node in ("window", "group1", "group2", *children["window"]):
        values[(node, "AXFocused")] = False
        pids[node] = 42
    ax = backend._wechat_ax.ax
    ax.AXUIElementGetAttributeValueCount = lambda node, key, _: (0, len(children.get(node, [])))
    ax.AXUIElementCopyAttributeValues = lambda node, key, start, count, _: (0, children[node][start:start+count])
    return wechat_capture


def test_only_confirmed_opaque_wechat_window_allows_temporary_ime_probe(wechat_titlebar):
    backend, values, children, _ = wechat_titlebar
    values[("app", "AXWindows")].append("background-chat")
    values[("background-chat", "AXSubrole")] = "AXStandardWindow"
    target = backend.capture(menu_action=True, scene=True, passive_undo=True)
    assert target.wechat_ime_probe and target.input_context == "unknown" and backend.same_target(target)
    children["window"].append("editor")
    assert not backend.same_target(target)
    assert not backend.capture(menu_action=True, scene=True, passive_undo=True).wechat_ime_probe


@pytest.mark.parametrize("case", ["known_focus", "unknown_control", "extra_window", "sheet", "modal",
                                  "foreign_child", "menu", "open_menu", "unreadable", "incomplete", "denied"])
def test_wechat_probe_requires_complete_unblocked_titlebar(wechat_titlebar, case):
    from proximic_ring.desktop.text_focus import FocusError
    backend, values, children, pids = wechat_titlebar
    if case == "known_focus":
        values[("app", "AXFocusedUIElement")] = "close"
    elif case == "unknown_control":
        values[("group1", "AXRole")] = "AXUnknown"
    elif case == "extra_window":
        values[("app", "AXWindows")].append("popup")
    elif case == "sheet":
        values[("window", "AXSheets")] = ["sheet"]
    elif case == "modal":
        values[("window", "AXModal")] = True
    elif case == "foreign_child":
        pids["close"] = 99
    elif case == "menu":
        children["group1"].append("menu")
        values[("menu", "AXRole")], pids["menu"] = "AXMenu", 42
    elif case == "open_menu":
        values[("app", "AXMenuBar")] = "bar"
        values[("bar", "AXSelectedChildren")] = ["menu item"]
    elif case == "unreadable":
        backend._wechat_ax.ax.AXUIElementGetAttributeValueCount = lambda *_: (-25204, 0)
    elif case == "incomplete":
        backend._wechat_ax.ax.AXUIElementCopyAttributeValues = lambda *_: (0, [])
    else:
        read = backend._wechat_ax.attr
        def denied(owner, key):
            if owner == "system" and key == "AXFocusedUIElement":
                raise FocusError("unavailable", ax_error=-25211)
            return read(owner, key)
        backend._wechat_ax.attr = denied
    target = backend.capture(menu_action=True, scene=True, passive_undo=True)
    assert target is None or not target.wechat_ime_probe


@pytest.mark.parametrize("source,placeholder", [("system", None), ("window", "app"), ("tree", "window")])
def test_wechat_missing_application_focus_rechecks_positive_editor(wechat_capture, source, placeholder):
    backend, values, _, _ = wechat_capture
    values[("app", "AXFocusedUIElement")] = placeholder
    values[("editor", "AXFocused")] = True
    if source != "tree":
        values[(source, "AXFocusedUIElement")] = "editor"
    target = backend.capture(menu_action=True, scene=True, passive_undo=True)
    assert target.input_context == "text" and target.focus == "editor"
    assert backend.same_target(target)
    values[("editor", "AXFocused")] = False
    assert not backend.same_target(target)


def test_wechat_conflicting_authoritative_focus_beats_stale_tree_node(wechat_capture):
    backend, values, _, _ = wechat_capture
    values.update({("window", "AXFocusedUIElement"): "button", ("button", "AXWindow"): "window",
                   ("button", "AXRole"): "AXButton", ("editor", "AXFocused"): True})
    assert backend.capture(menu_action=True, scene=True, passive_undo=True).input_context == "nontext"


def test_wechat_application_reported_as_window_does_not_authorize_menu_search(wechat_capture):
    backend, values, _, _ = wechat_capture
    values.update({("app", "AXFocusedWindow"): "app", ("app", "AXFocusedUIElement"): "editor",
                   ("editor", "AXFocused"): True, ("editor", "AXWindow"): "app"})
    assert backend.capture(menu_action=True, scene=True, passive_undo=True) is None


@pytest.mark.parametrize("case", ["unfocused", "other_window", "other_pid", "conflict", "secure", "readonly"])
def test_wechat_static_or_unowned_editors_do_not_grant_dictation(wechat_capture, case):
    backend, values, children, pids = wechat_capture
    values[("editor", "AXFocused")] = case != "unfocused"
    if case == "other_window":
        values[("editor", "AXWindow")] = "another"
    if case == "other_pid":
        pids["editor"] = 55
    if case == "conflict":
        children["window"].append("button")
        values.update({("button", "AXFocused"): True, ("button", "AXWindow"): "window"})
    if case == "secure":
        values[("editor", "AXSubrole")] = "AXSecureTextField"
    if case == "readonly":
        values[("editor", "AXIsEditable")] = False
    target = backend.capture(menu_action=True, scene=True, passive_undo=True)
    assert target is None or target.input_context == "unknown"


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
