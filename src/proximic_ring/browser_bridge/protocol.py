"""Small, versioned protocol shared by native hosts and the desktop broker."""
from __future__ import annotations

VERSION = 1
PORT = 47837
HOST_NAME = "com.proximic.voice.browser"
SAFARI_EXTENSION_ID = "com.proximic.voice.browserhost.extension"
BUNDLES = {"chrome": "com.google.Chrome", "safari": "com.apple.Safari"}
ACTIONS = frozenset({"play", "backward", "forward", "volume-up", "volume-down"})
MAX_MESSAGE = 256 * 1024


def media_action(action_id):
    """Route only explicit built-ins. A custom Space key remains a shortcut."""
    for prefix in ("web-video:", "scene-default:music:"):
        if action_id.startswith(prefix) and action_id[len(prefix):] in ACTIONS:
            return action_id[len(prefix):]
    return ""


def identity(snapshot):
    return tuple(snapshot.get(key) for key in
                 ("session", "window", "tab", "page", "frame", "document", "media", "source"))


def clean_snapshot(value):
    """Do not retain URLs, page titles, control text or user-entered content."""
    if not isinstance(value, dict):
        raise ValueError("invalid_snapshot")
    result = {}
    for key in ("session", "page", "document", "media", "source"):
        item = value.get(key, "")
        if not isinstance(item, str) or len(item) > 160:
            raise ValueError("invalid_snapshot")
        result[key] = item
    for key in ("window", "tab", "frame"):
        item = value.get(key, -1)
        if type(item) is not int or not -1 <= item < 2**53:
            raise ValueError("invalid_snapshot")
        result[key] = item
    for key in ("focused", "authorized", "playing"):
        if type(value.get(key, False)) is not bool:
            raise ValueError("invalid_snapshot")
        result[key] = value.get(key, False)
    result["scene"] = value.get("scene", "") if value.get("scene", "") in {"video", "music", "pdf", "image"} else ""
    result["input_context"] = value.get("input_context", "unknown") if value.get("input_context") in {"text", "nontext"} else "unknown"
    reason = value.get("reason", "no_web_player")
    result["reason"] = reason if reason in {"recognized", "no_web_player", "multiple_players", "page_loading",
        "browser_permission_required", "browser_page_unavailable", "browser_window_unfocused"} else "browser_page_unavailable"
    if result["scene"] and (not all(result[k] for k in ("page", "document", "media", "source"))
                            or result["tab"] < 0 or result["frame"] < 0):
        raise ValueError("invalid_snapshot")
    return result
