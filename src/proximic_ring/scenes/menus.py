"""Semantic matching of the app's exposed menu shortcuts, without execution."""
import re
from ..app_gestures import normalize_shortcut
from .models import PDF, IMAGE, VIDEO, MUSIC
from .registry import PENDING_TEMPLATES as TEMPLATES

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
