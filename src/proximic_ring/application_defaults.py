"""Editable defaults and installed-app onboarding, independent of routing.

Scene meanings and application key adapters live in scene_defaults.
Presentation and chat defaults retain their existing catalog IDs. Runtime dispatch has no preset path:
these are copied into the same records as user-created mappings.
"""
from __future__ import annotations

import re

from .app_gestures import APP_LABELS, default_profiles, profile_for_application, normalize_shortcut
from .gesture_scenes import PRESENTATION, presentation_profile, scene_actions
from .scene_capabilities import application_scene_profiles
from .scene_defaults import scene_defaults

DEFAULTS_VERSION = 2
AUTO_ADD_PROFILES = frozenset({"codex", "workbuddy", "wps", "powerpoint"})
PRESENTATION_GESTURES = {"regular": {"snap": ("start-current", "start-first")},
                         PRESENTATION: {"swipe-left": ("previous",), "swipe-right": ("next",), "snap": ("end",)}}
DEFAULT_MAPPINGS = {
    "codex": {"regular": {"circle-clockwise": "codex:next", "circle-counterclockwise": "codex:previous"}},
    "workbuddy": {"regular": {"circle-clockwise": "workbuddy:next", "circle-counterclockwise": "workbuddy:previous"}},
}
CHAT_ACTIONS = {"next": "下一个聊天", "previous": "上一个聊天"}


def default_profile(bundle: str, label: str = "", *, presentation: str = "") -> str:
    presenter = presentation_profile(bundle) or presentation
    if presenter:
        return presenter
    if bundle.casefold() == "com.tencent.workbuddy.mac":
        return "workbuddy"
    profile = profile_for_application(bundle, label)
    return profile if profile in DEFAULT_MAPPINGS else ""


def chat_actions(bundle: str, label: str, scene: str) -> list[dict]:
    profile = default_profile(bundle, label)
    if scene != "regular" or profile not in {"codex", "workbuddy"}:
        return []
    # Reuse the shortcuts already supported by the legacy app adapter.
    shortcuts = default_profiles()[profile]
    return [dict(id=f"{profile}:{key}", label=title, shortcut=shortcuts[key].shortcut,
                 path=APP_LABELS[profile] + " 默认快捷键", available=None, preset=True)
            for key, title in CHAT_ACTIONS.items()]


def default_mappings(bundle: str, label: str = "", *, presentation: str = "", scene_profiles=None, menus=()) -> dict[str, dict]:
    profiles = {**(scene_profiles or {}), **application_scene_profiles(bundle)}
    result = _base_mappings(bundle, label, presentation=presentation, menus=menus)
    result.update(scene_defaults(bundle, profiles, menus)[0])
    return result


def _base_mappings(bundle: str, label: str = "", *, presentation: str = "", menus=()) -> dict[str, dict]:
    """Fresh, serializable records; callers cannot mutate the shared template."""
    profile = default_profile(bundle, label, presentation=presentation)
    presenter = presentation_profile(bundle) or presentation
    result = {}
    if presenter:
        for scene, gestures in PRESENTATION_GESTURES.items():
            actions = {item["id"].split(":")[-1]: item for item in scene_actions(bundle, scene, profile=presenter)}
            if scene == PRESENTATION:
                # Common slideshow navigation; each known app's own keys win.
                for key, title, shortcut in [("previous", "上一页", "Left"), ("next", "下一页", "Right"), ("end", "结束放映", "Escape")]:
                    actions.setdefault(key, dict(id="presentation:" + key, label=title,
                                                path="放映通用快捷键", shortcut=shortcut))
            else:
                discovered = start_action(menus)
                if discovered and "start-current" not in actions and "start-first" not in actions:
                    actions["start-current"] = discovered
            result[scene] = {}
            for gesture, choices in gestures.items():
                action = next((actions[key] for key in choices if key in actions), None)
                if action:
                    result[scene][gesture] = {key: action[key] for key in ("id", "label", "path", "shortcut")}
        return result
    for scene, gestures in DEFAULT_MAPPINGS.get(profile, {}).items():
        actions = {item["id"]: item for item in
                   scene_actions(bundle, scene) + chat_actions(bundle, label, scene)}
        result[scene] = {gesture: {key: actions[action][key] for key in ("id", "label", "path", "shortcut")}
                         for gesture, action in gestures.items()}
    return result


def start_action(menus):
    """Resolve an unknown presenter's start key from its actual menu metadata."""
    ranked = []
    for item in menus:
        label = re.sub(r"[&…]", "", str(item.get("label", ""))).strip().rstrip(".").casefold()
        current = re.fullmatch(r"(?:start |play )?(?:from )?current slide|从当前(?:页|幻灯片)(?:开始)?(?:放映)?|從目前(?:投影片)?(?:開始)?", label)
        first = re.fullmatch(r"(?:start |play )?(?:from )?(?:beginning|first slide)|从(?:头|第一张幻灯片|第一张)(?:开始)?(?:放映)?", label)
        start = re.fullmatch(r"(?:(?:start|play|begin) )?slide ?show|start presentation|开始放映|開始放映|播放幻灯片|幻灯片放映", label)
        if not (current or first or start):
            continue
        try:
            shortcut = normalize_shortcut(item.get("shortcut", ""))
            action = {key: str(item[key]) for key in ("id", "label", "path")}
        except (ValueError, TypeError, KeyError):
            continue
        ranked.append((0 if current else 1 if first else 2, {**action, "shortcut": shortcut}))
    return min(ranked, key=lambda row: row[0])[1] if ranked else None
