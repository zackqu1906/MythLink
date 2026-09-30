"""Read application/menu metadata only, on explicit configuration requests."""
from __future__ import annotations

from collections import deque
import hashlib
import json
import time

from .app_gestures import KEY_CODES, normalize_shortcut

# Carbon Menus.h glyph IDs, not Unicode code points.
MENU_GLYPHS = {2: "Tab", 3: "Tab", 4: "KeypadEnter", 9: "Space", 10: "Delete",
               11: "Return", 12: "Return", 13: "Return", 23: "Backspace",
               27: "Escape", 28: "Clear", 98: "PageUp", 100: "Left",
               101: "Right", 102: "Home", 103: "Help", 104: "Up",
               105: "End", 106: "Down", 107: "PageDown"}
MENU_GLYPHS.update({111 + i: f"F{i+1}" for i in range(12)})
MENU_GLYPHS.update({135 + i: f"F{i+13}" for i in range(3)})
MENU_GLYPHS.update({143 + i: f"F{i+16}" for i in range(4)})
CHARACTER_KEYS = {"\t": "Tab", "\r": "Return", "\n": "Return", "\x03": "KeypadEnter",
                  " ": "Space", "\x1b": "Escape", "\x08": "Backspace", "\x7f": "Backspace",
                  "←": "Left", "→": "Right", "↑": "Up", "↓": "Down", "⎋": "Escape",
                  "⇥": "Tab", "⇤": "Tab", "↩": "Return", "⌅": "KeypadEnter",
                  "⌫": "Backspace", "⌦": "Delete", "↖": "Home", "↘": "End",
                  "⇞": "PageUp", "⇟": "PageDown",
                  "\uf700": "Up", "\uf701": "Down", "\uf702": "Left", "\uf703": "Right",
                  "\uf728": "Delete", "\uf729": "Home", "\uf72b": "End",
                  "\uf72c": "PageUp", "\uf72d": "PageDown"}
CHARACTER_KEYS.update({chr(0xf704+i): f"F{i+1}" for i in range(20)})
SHIFTED_KEYS = dict(zip('~!@#$%^&*()_+{}|:"<>?', '`1234567890-=[]\\;\',./'))


def menu_shortcut(character, virtual_key, modifiers, glyph=None):
    """AX modifiers imply Command unless NoCommand is present (Apple AX API)."""
    character = str(character or "")
    if not character and not glyph and not virtual_key:
        return ""  # Some apps expose virtual key 0 even for rows with no chord.
    key = next((name for name, code in KEY_CODES.items() if code == virtual_key), "")
    if modifiers is None:
        return ""  # A missing modifier mask cannot safely imply Command.
    try:
        mask = int(modifiers or 0)
    except (ValueError, TypeError):
        return ""
    if mask & ~31:
        return ""
    if virtual_key is None:
        # Electron/Cocoa menus commonly expose CmdChar but no CmdVirtualKey.
        # Prefer physical codes when supplied (including Option-altered glyphs).
        key = MENU_GLYPHS.get(glyph, "") or CHARACTER_KEYS.get(character, "")
        if not key and len(character) == 1:
            key = character.upper()
            if character in SHIFTED_KEYS:
                key = SHIFTED_KEYS[character]
                mask |= 1
    if not key:
        return ""
    parts = ([] if mask & 8 else ["Cmd"]) + (["Ctrl"] if mask & 4 else [])
    parts += (["Alt"] if mask & 2 else []) + (["Shift"] if mask & 1 else [])
    # Current macOS menu providers use bit 4 for Fn (e.g. Window > Fill
    # exposes 28, matching Apple's documented Fn-Control-F). Older SDK
    # AXMenuItemModifiers declarations omit this bit. Preserve it on delivery.
    parts += ["Fn"] if mask & 16 else []
    try:
        return normalize_shortcut("+".join(parts + [key]))
    except ValueError:
        return ""


def read_menu_tree(root, get, *, budget=6.0, maximum=6000, clock=time.monotonic):
    """Never traverse a window or read text values; only AXMenuBar descendants."""
    queue = deque([(root, ())])
    deadline = clock() + budget
    actions, seen = [], set()
    payload_size = 0
    count = 0
    unresolved = 0
    while queue and count < maximum and clock() < deadline:
        node, path = queue.popleft()
        count += 1
        role = get(node, "AXRole")
        if role not in {"AXMenuBar", "AXMenuBarItem", "AXMenu", "AXMenuItem"}:
            continue
        title = str(get(node, "AXTitle") or "").strip()
        current_path = path + (title,) if title and role != "AXMenu" else path
        if role == "AXMenuItem" and title:
            character = get(node, "AXMenuItemCmdChar")
            virtual_key = get(node, "AXMenuItemCmdVirtualKey")
            modifiers = get(node, "AXMenuItemCmdModifiers")
            glyph = get(node, "AXMenuItemCmdGlyph")
            shortcut = menu_shortcut(character, virtual_key, modifiers, glyph)
            if not shortcut and (character or virtual_key or glyph):
                unresolved += 1
            if shortcut:
                identity = json.dumps([current_path, shortcut], ensure_ascii=False)
                action_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
                if action_id not in seen:
                    seen.add(action_id)
                    enabled = get(node, "AXEnabled")
                    action = dict(id=action_id, label=title, path=" › ".join(current_path),
                                  shortcut=shortcut, available=None if enabled is None else bool(enabled))
                    payload_size += len(json.dumps(action, ensure_ascii=False).encode()) + 2
                    if payload_size > 768000:
                        return dict(actions=actions, partial=True, unresolved=unresolved)
                    actions.append(action)
        queue.extend((child, current_path) for child in (get(node, "AXChildren") or ()))
    return dict(actions=actions, partial=bool(queue) or clock() >= deadline, unresolved=unresolved)


def application_candidates():
    from .mac_workspace import running_applications
    apps = {}
    for app in running_applications():
        bundle = str(app.bundleIdentifier() or "")
        if bundle and int(app.activationPolicy()) == 0:
            url = app.bundleURL()
            apps[bundle] = dict(value=bundle, label=str(app.localizedName() or bundle),
                                path=str(url.path()) if url else "", running=True)
    return sorted(apps.values(), key=lambda item: item["label"].casefold())


def read_application_menu(bundle):
    import ApplicationServices as AX
    from .mac_workspace import running_applications
    matches = [app for app in running_applications() if str(app.bundleIdentifier() or "") == bundle]
    if not matches:
        raise RuntimeError("请先打开这个应用，再刷新快捷键")
    pid = int(matches[0].processIdentifier())
    deadline = time.monotonic() + 6.0
    incomplete = False
    def get(node, name, *, timeout=.05):
        nonlocal incomplete
        if time.monotonic() >= deadline:
            incomplete = True
            return None
        AX.AXUIElementSetMessagingTimeout(node, min(timeout, max(.001, deadline-time.monotonic())))
        error, value = AX.AXUIElementCopyAttributeValue(node, name, None)
        # Unsupported attributes / absent values are normal for shortcutless
        # rows. Timeouts, invalidated elements and other failures are incomplete.
        if error not in (0, -25205, -25212):
            incomplete = True
        return value if error == 0 else None
    # A background app may need a little longer to initialize its menu provider.
    root = get(AX.AXUIElementCreateApplication(pid), "AXMenuBar", timeout=.12)
    if root is None:
        raise RuntimeError("未能读取菜单。请确认辅助功能权限，并打开该应用的菜单后重试")
    result = read_menu_tree(root, get, budget=max(.001, deadline-time.monotonic()))
    result["partial"] = result["partial"] or incomplete or time.monotonic() >= deadline
    # An app that exited/relaunched during traversal cannot contribute a stale list.
    if not any(str(app.bundleIdentifier() or "") == bundle and int(app.processIdentifier()) == pid
               for app in running_applications()):
        raise RuntimeError("应用已关闭或重新启动，请刷新快捷键")
    return dict(bundle=bundle, pid=pid, **result)
