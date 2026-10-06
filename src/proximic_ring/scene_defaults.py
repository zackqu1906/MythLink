"""Scene intent -> application shortcut, independent of UI and runtime routing.

Templates contain meanings, never application IDs or key combinations. Adapters
resolve those meanings using the existing catalog, verified keys, or real menu
metadata. Unresolved intentions are persisted separately and cannot dispatch.
"""
from __future__ import annotations

import re

from .app_gestures import normalize_shortcut
from .browser_shortcuts import NAVIGATION
from .scene_capabilities import PDF, IMAGE, VIDEO, MUSIC, BROWSERS, SCENE_LABELS, activity_actions

SCENE_DEFAULTS_VERSION = 2
LEGACY_MUSIC_VOLUME = {"circle-clockwise": "volume-up", "circle-counterclockwise": "volume-down"}
TEMPLATES = {
    PDF: {"swipe-left": "previous", "swipe-right": "next"},
    IMAGE: {"swipe-left": "previous", "swipe-right": "next"},
    VIDEO: {"tap": "play", "swipe-left": "backward", "swipe-right": "forward",
            "circle-clockwise": "volume-up", "circle-counterclockwise": "volume-down"},
    MUSIC: {"tap": "play", "swipe-left": "previous", "swipe-right": "next",
            "swipe-up": "volume-up", "swipe-down": "volume-down"},
}
LABELS = {
    PDF: {"previous": "上一页", "next": "下一页"},
    IMAGE: {"previous": "上一张", "next": "下一张"},
    MUSIC: {"previous": "上一首", "next": "下一首"},
    VIDEO: {"backward": "后退", "forward": "快进"},
}
COMMON_LABELS = {"play": "播放 / 暂停", "volume-up": "提高音量", "volume-down": "降低音量"}

# Verified native Mac adapters; sources are recorded in docs/SCENE_DEFAULTS.md.
ADAPTERS = {
    "org.videolan.vlc": {
        VIDEO: {"play": "Space", "backward": "Cmd+Alt+Left", "forward": "Cmd+Alt+Right",
                "volume-up": "Cmd+Up", "volume-down": "Cmd+Down"},
        MUSIC: {"play": "Space", "previous": "Cmd+Left", "next": "Cmd+Right",
                "volume-up": "Cmd+Up", "volume-down": "Cmd+Down"},
    },
    "com.colliderli.iina": {
        VIDEO: {"play": "Space", "backward": "Left", "forward": "Right", "volume-up": "Up", "volume-down": "Down"},
        MUSIC: {"play": "Space", "previous": "Cmd+Left", "next": "Cmd+Right", "volume-up": "Up", "volume-down": "Down"},
    },
    "com.spotify.client": {MUSIC: {"play": "Space"}},
    "com.apple.quicktimeplayerx": {
        VIDEO: {"volume-up": "Up", "volume-down": "Down"},
        MUSIC: {"volume-up": "Up", "volume-down": "Down"},
    },
}


def action_label(scene, action):
    return LABELS.get(scene, {}).get(action, COMMON_LABELS.get(action, action))


def _label(value):
    return re.sub(r"\s+", " ", re.sub(r"[&…]", "", str(value))).strip().rstrip(".").casefold()


def menu_action(scene, action, menus):
    """Conservative semantic matching: never confuse next track/page/tab/slide.

    Conflicting keys for the same meaning are ambiguous and remain unresolved.
    Enabled state can change with playback; it is not a permanent capability.
    """
    if scene not in TEMPLATES or action not in TEMPLATES[scene].values():
        return None
    patterns = {
        "play": r"play\s*[/／]\s*pause|play|pause|播放\s*[/／]\s*暂停|播放|暂停|播放\s*[/／]\s*暫停|暫停",
        "volume-up": r"(?:increase|raise) volume|volume up|提高音量|增大音量|增加音量|调高音量|調高音量",
        "volume-down": r"(?:decrease|lower|reduce) volume|volume down|降低音量|减小音量|减少音量|调低音量|調低音量",
        "backward": r"(?:skip|jump|seek) back(?:ward)?(?: \d+ seconds?)?|rewind|快退|后退(?:\s*\d+\s*秒)?|倒退(?:\s*\d+\s*秒)?",
        "forward": r"(?:skip|jump|seek) forward(?: \d+ seconds?)?|fast forward|快进(?:\s*\d+\s*秒)?|快進(?:\s*\d+\s*秒)?",
    }
    units = {PDF: ("page", "页|頁"), IMAGE: ("image|photo|picture", "张|張|图片|圖片|照片"), MUSIC: ("track|song", "首|曲")}
    if action in {"previous", "next"}:
        unit, chinese = units[scene]
        direction, zh = ("previous", "上") if action == "previous" else ("next", "下")
        patterns[action] = rf"(?:go to |play )?{direction} (?:{unit})|{zh}一(?:{chinese})"
    found = []
    for item in menus:
        label = _label(item.get("label", ""))
        if not re.fullmatch(patterns[action], label):
            continue
        path = _label(item.get("path", ""))
        # A suite can expose all of these in one menu tree. Reject other contexts.
        forbidden = {PDF: r"slide show|presentation|幻灯片|放映", IMAGE: r"slide show|presentation|幻灯片|放映",
                     MUSIC: r"video track|subtitle|audio track|视频轨|音轨|字幕",
                     VIDEO: r"slideshow|presentation|幻灯片|放映"}[scene]
        if re.search(forbidden, path):
            continue
        try:
            record = {key: str(item[key]) for key in ("id", "label", "path")}
            record["shortcut"] = normalize_shortcut(item.get("shortcut", ""))
        except (ValueError, TypeError, KeyError):
            continue
        found.append(record)
    return found[0] if found and len({item["shortcut"] for item in found}) == 1 else None


def resolve_action(bundle, scene, action, profile="", menus=()):
    discovered = menu_action(scene, action, menus)
    if discovered:
        return discovered
    for item in activity_actions(bundle, scene, profile):
        if item["id"].rsplit(":", 1)[-1] == action:
            return {key: item[key] for key in ("id", "label", "path", "shortcut")}
    shortcut = ADAPTERS.get(bundle.casefold(), {}).get(scene, {}).get(action)
    if bundle.casefold() in BROWSERS and scene == VIDEO:
        shortcut = {"play": "Space", "backward": "Left", "forward": "Right", "volume-up": "Up", "volume-down": "Down"}.get(action)
    if bundle.casefold() in BROWSERS and scene == MUSIC:
        shortcut = {"volume-up": "Up", "volume-down": "Down"}.get(action)
    if bundle.casefold() in BROWSERS and scene == PDF:
        shortcut = {"previous": "PageUp", "next": "PageDown"}.get(action)
    if not shortcut:
        return None
    identity = "web-video:" + action if bundle.casefold() in BROWSERS and scene == VIDEO else f"scene-default:{scene}:{action}"
    return dict(id=identity, label=action_label(scene, action),
                path=SCENE_LABELS[scene] + " 默认快捷键", shortcut=normalize_shortcut(shortcut))


def migrate_music_volume_defaults(bundle, app):
    """Upgrade old factory volume gestures once, preserving edits and clears.

    Resolved menu defaults retain their actual shortcut when moved. Custom
    circle bindings and occupied swipe bindings are never overwritten. Browsers
    previously omitted volume defaults because circles belong to tab navigation.
    Disabled scenes are migrated too, without enabling them.
    """
    if app.get("sceneDefaultsVersion", 0) >= 2:
        return False
    app["sceneDefaultsVersion"] = 2
    if app.get("removed") or app.get("defaultsCleared"):
        return True
    bindings = app.get("scenes", {}).get(MUSIC, {})
    pending = app.get("pendingDefaults", {}).get(MUSIC, {})
    if not bindings and not pending:
        return True  # Explicitly empty scenes stay empty.
    browser = bundle.casefold() in BROWSERS
    profile = app.get("sceneProfiles", {}).get(MUSIC, "")
    for old, meaning in LEGACY_MUSIC_VOLUME.items():
        gesture = "swipe-up" if meaning == "volume-up" else "swipe-down"
        previous = bindings.get(old)
        factory = resolve_action(bundle, MUSIC, meaning, profile)
        migrated = previous is not None and (previous == factory or (
            not previous["id"].startswith("custom:") and menu_action(MUSIC, meaning, [previous]) == previous))
        waiting = pending.get(old) == meaning
        if migrated:
            bindings.pop(old)
        if waiting:
            pending.pop(old)
        if gesture in bindings or gesture in pending or not (migrated or waiting or browser):
            continue
        record = previous if migrated else factory
        if record:
            bindings[gesture] = dict(record)
        else:
            pending[gesture] = meaning
    app.setdefault("scenes", {})[MUSIC] = bindings
    if pending:
        app.setdefault("pendingDefaults", {})[MUSIC] = pending
    else:
        app.get("pendingDefaults", {}).pop(MUSIC, None)
    return True


def scene_defaults(bundle, profiles, menus=()):
    """Return fresh resolved records and unresolved semantic intentions."""
    resolved, pending = {}, {}
    for scene, profile in profiles.items():
        if scene not in TEMPLATES:
            continue
        resolved[scene] = {}
        for gesture, action in TEMPLATES[scene].items():
            if bundle.casefold() in BROWSERS and gesture in NAVIGATION:
                continue  # Browser navigation is shared by every scene.
            item = resolve_action(bundle, scene, action, profile, menus)
            if item:
                resolved[scene][gesture] = item
            else:
                pending.setdefault(scene, {})[gesture] = action
    return resolved, pending
