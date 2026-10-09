"""Native PDF/image/media keys; capabilities are kept in a separate module."""
from ..models import PDF, IMAGE, VIDEO, MUSIC
from ..registry import SCENE_LABELS
from ..capabilities import application_scene_profiles

# Apple Support: Preview cpprvw0003, Music mus1019, QuickTime qtpa4808515d.
# Other applications use their discovered menus or a user-recorded shortcut.
PRESETS = {
    ("preview", PDF): [("previous", "上一页", "Alt+Up"), ("next", "下一页", "Alt+Down"),
                         ("fullscreen", "进入 / 退出全屏", "Cmd+Ctrl+F")],
    ("preview", IMAGE): [("previous", "上一张 / 向上翻页", "PageUp"), ("next", "下一张 / 向下翻页", "PageDown"),
                           ("actual", "实际大小", "Cmd+Alt+0"), ("fit", "适合窗口", "Cmd+Alt+9")],
    ("music", MUSIC): [("play", "播放 / 暂停", "Space"), ("previous", "上一首", "Left"), ("next", "下一首", "Right"),
                         ("volume-up", "提高音量", "Cmd+Up"), ("volume-down", "降低音量", "Cmd+Down")],
    ("quicktime", VIDEO): [("play", "播放 / 暂停", "Space"), ("backward", "提高倒放速度", "Cmd+Left"),
                            ("forward", "提高快进速度", "Cmd+Right"), ("fullscreen", "进入 / 退出全屏", "Cmd+F")],
    ("quicktime", MUSIC): [("play", "播放 / 暂停", "Space"), ("backward", "提高倒放速度", "Cmd+Left"),
                            ("forward", "提高快进速度", "Cmd+Right")],
}


def activity_actions(bundle, scene, profile=""):
    profile = application_scene_profiles(bundle).get(scene, profile)
    return [dict(id=f"{profile}:{scene}:{key}", label=label, shortcut=shortcut,
                 path=SCENE_LABELS[scene] + " 快捷键", available=None, preset=True)
            for key, label, shortcut in PRESETS.get((profile, scene), [])]

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
