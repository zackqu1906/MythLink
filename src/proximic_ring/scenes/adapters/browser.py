"""Browser-wide tab navigation and generic player keys; no site configuration."""
from ..capabilities import BROWSERS

NAVIGATION = {
    "circle-clockwise": dict(id="browser:next", label="下一个 Chat / 标签页", path="浏览器通用", shortcut="Ctrl+Tab"),
    "circle-counterclockwise": dict(id="browser:previous", label="上一个 Chat / 标签页", path="浏览器通用", shortcut="Ctrl+Shift+Tab"),
}


def shared_navigation(bundle, regular=None):
    """All scenes inherit the browser's regular navigation, including custom keys."""
    if bundle.casefold() not in BROWSERS:
        return {}
    return {gesture: dict((regular or {}).get(gesture, action)) for gesture, action in NAVIGATION.items()}


def video_actions():
    return [dict(id="web-video:" + key, label=title, path="网页播放器（需支持对应按键）",
                 shortcut=shortcut, preset=True, available=None)
            for key, title, shortcut in (("play", "播放 / 暂停", "Space"),
                ("backward", "后退", "Left"), ("forward", "快进", "Right"),
                ("volume-up", "增大音量", "Up"), ("volume-down", "减小音量", "Down"))]
