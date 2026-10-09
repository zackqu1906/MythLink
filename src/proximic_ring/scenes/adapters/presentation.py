"""Existing macOS presentation catalogs. IDs and key choices are stable."""
from ..models import PRESENTATION
from ..capabilities import POWERPOINT, presentation_profile

POWERPOINT_ACTIONS = {
    "regular": [("start-current", "从当前页放映", "Cmd+Return"),
                ("start-first", "从头放映", "Cmd+Shift+Return")],
    PRESENTATION: [("previous", "上一个动画 / 上一页", "Left"),
                   ("next", "下一个动画 / 下一页", "Right"),
                   ("end", "结束放映", "Escape"),
                   ("laser", "激光笔", "Cmd+L"),
                   ("pen", "画笔", "Cmd+P"),
                   ("arrow", "箭头指针", "Cmd+A")],
}

# App-specific defaults, never PowerPoint shortcuts copied into another app.
# Sources: Microsoft Support; help.wps.com/articles/the-shortcuts-of-wps-office-for-mac/;
# support.apple.com/guide/keynote/tanfde4a3e6d/mac; LibreOffice / ONLYOFFICE help.
NAVIGATION_ACTIONS = POWERPOINT_ACTIONS[PRESENTATION][:3]
PROFILES = {
    "powerpoint": ("PowerPoint", POWERPOINT_ACTIONS),
    "wps": ("WPS 演示", {
        # Reverified on macOS WPS: Shift+F5 starts at the selected slide.
        # Cmd+Return can INSERT a slide; it must not be borrowed from PowerPoint.
        # Cmd+Shift+Return is the installed menu's Play/from-start command.
        "regular": [("start-current", "从当前页放映", "Shift+F5"),
                    ("start-first", "从头放映", "Cmd+Shift+Return")],
        # The Mac guide lists Delete for the previous slide (physical Backspace).
        PRESENTATION: [("previous", "上一页", "Backspace"), *NAVIGATION_ACTIONS[1:],
                       ("black", "黑屏 / 恢复放映", "Cmd+B"), ("white", "白屏 / 恢复放映", "Cmd+W")],
    }),
    "keynote": ("Keynote", {
        "regular": [("start-current", "播放演示文稿", "Cmd+Alt+P")],
        PRESENTATION: [("previous", "上一页", "Left"), *NAVIGATION_ACTIONS[1:],
                       ("notes", "显示 / 隐藏演讲者备注", "Cmd+Shift+P")],
    }),
    "libreoffice": ("LibreOffice Impress", {
        "regular": [("start-first", "开始放映", "F5")], PRESENTATION: NAVIGATION_ACTIONS,
    }),
    # https://wiki.openoffice.org/wiki/Documentation/AOO4_User_Guides/Impress_Guide/Appendix_A/Function_Keys_for_Impress
    "openoffice": ("OpenOffice Impress", {
        "regular": [("start-first", "开始放映", "F5")], PRESENTATION: NAVIGATION_ACTIONS,
    }),
    "onlyoffice": ("ONLYOFFICE", {
        "regular": [("start-first", "从头放映", "Cmd+Shift+Return")], PRESENTATION: NAVIGATION_ACTIONS,
    }),
    # Other native presentation editors still get a scene, menu discovery and
    # custom shortcuts; their app-specific start shortcuts are not guessed.
    "generic": ("演示应用", {}),
}

def scene_actions(bundle, scene, *, profile=""):
    profile = presentation_profile(bundle) or profile
    if profile not in PROFILES:
        return []
    app, actions = PROFILES[profile]
    return [dict(id=profile + ":" + key, label=label, shortcut=shortcut,
                 path=app + (" 放映快捷键" if scene == PRESENTATION else " 快捷键"),
                 available=None, preset=True)
            for key, label, shortcut in actions.get(scene, [])]
