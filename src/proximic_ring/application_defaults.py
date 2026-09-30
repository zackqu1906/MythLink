"""Editable app defaults, installed only on add or an explicit reset.

Extend DEFAULT_MAPPINGS with scene/gesture/action IDs and supply verified actions
in the existing scene catalog or CHAT_ACTIONS. Runtime dispatch has no preset path:
these are copied into the same records as user-created mappings.
"""
from __future__ import annotations

from .app_gestures import APP_LABELS, default_profiles, profile_for_application
from .gesture_scenes import PRESENTATION, presentation_profile, scene_actions

DEFAULT_MAPPINGS = {
    "wps": {
        "regular": {"snap": "wps:start-current"},
        PRESENTATION: {"swipe-left": "wps:previous", "swipe-right": "wps:next"},
    },
    "codex": {"regular": {"circle-clockwise": "codex:next", "circle-counterclockwise": "codex:previous"}},
    "workbuddy": {"regular": {"circle-clockwise": "workbuddy:next", "circle-counterclockwise": "workbuddy:previous"}},
}
CHAT_ACTIONS = {"next": "下一个聊天", "previous": "上一个聊天"}


def default_profile(bundle: str, label: str = "") -> str:
    if presentation_profile(bundle) == "wps":
        return "wps"
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


def default_mappings(bundle: str, label: str = "") -> dict[str, dict]:
    """Fresh, serializable records; callers cannot mutate the shared template."""
    profile = default_profile(bundle, label)
    result = {}
    for scene, gestures in DEFAULT_MAPPINGS.get(profile, {}).items():
        actions = {item["id"]: item for item in
                   scene_actions(bundle, scene) + chat_actions(bundle, label, scene)}
        result[scene] = {gesture: {key: actions[action][key] for key in ("id", "label", "path", "shortcut")}
                         for gesture, action in gestures.items()}
    return result
