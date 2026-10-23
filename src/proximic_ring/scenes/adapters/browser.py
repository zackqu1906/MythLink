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
    # None requests factory defaults. An explicit (possibly empty) mapping is
    # user-owned: clearing a key must let application/global fallback resolve it.
    bindings = NAVIGATION if regular is None else regular
    return {gesture: dict(bindings[gesture]) for gesture in NAVIGATION if gesture in bindings}


def video_actions():
    return media_actions("video")


def media_actions(scene):
    prefix = "web-video:" if scene == "video" else "scene-default:music:"
    return [dict(id=prefix + key, label=title, path="浏览器扩展 · 播放器控制",
                 shortcut=shortcut, preset=True, available=None)
            for key, title, shortcut in (("play", "播放 / 暂停", "Space"),
                ("backward", "后退 10 秒", "Left"), ("forward", "快进 10 秒", "Right"),
                ("volume-up", "增大音量", "Up"), ("volume-down", "减小音量", "Down"))]
