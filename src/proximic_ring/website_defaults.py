"""Website shortcut templates, separate from scene recognition."""
from .scene_recognition.websites import BILIBILI, matches_website

VIDEO_ACTIONS = (("play", "播放 / 暂停", "Space"), ("backward", "后退", "Left"),
                 ("forward", "快进", "Right"), ("volume-up", "增大音量", "Up"),
                 ("volume-down", "减小音量", "Down"))


def video_actions(website=""):
    label = "哔哩哔哩播放器" if matches_website(website, BILIBILI) else "网页播放器（需支持对应按键）"
    return [dict(id="web-video:" + key, label=title, path=label, shortcut=shortcut,
                 preset=True, available=None) for key, title, shortcut in VIDEO_ACTIONS]


def website_defaults(website):
    if website != BILIBILI:
        return {}
    return {gesture: {key: action[key] for key in ("id", "label", "path", "shortcut")}
            for gesture, action in zip(("tap", "swipe-left", "swipe-right", "swipe-up", "swipe-down"), video_actions(website))}


