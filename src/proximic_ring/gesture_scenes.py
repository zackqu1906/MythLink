"""Opt-in application scenes. Detection reads control metadata, never slide text."""
from __future__ import annotations

import re
from pathlib import Path
import plistlib

from .presentation_detection import PRESENTATION, detect_presentation

POWERPOINT = "com.microsoft.Powerpoint"
SCENE_ANCHORS = frozenset({"index-pinch", "middle-pinch"})

# Microsoft Support: Use keyboard shortcuts to deliver PowerPoint presentations,
# macOS section. Default installation is opt-in via application_defaults.py.
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
        # Verified against the installed macOS WPS menu and slide-show UI.
        # The older online shortcut table lists F5, which this version ignores.
        "regular": [("start-current", "从当前页放映", "Cmd+Return"),
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
KNOWN_PROFILES = {
    POWERPOINT.casefold(): "powerpoint", "com.apple.iwork.keynote": "keynote",
    "com.kingsoft.wpsoffice.mac": "wps", "com.kingsoft.wpsoffice.mac.global": "wps",
    "org.libreoffice.script": "libreoffice", "org.openoffice.script": "openoffice",
    "asc.onlyoffice.onlyoffice": "onlyoffice",
}
PRESENTATION_EXTENSIONS = frozenset({"ppt", "pptx", "pptm", "pps", "ppsx", "ppsm", "odp", "key", "dps", "dpt"})
PRESENTATION_UTIS = frozenset({"com.microsoft.powerpoint.ppt", "com.microsoft.powerpoint.pps",
    "org.openxmlformats.presentationml.presentation", "org.openxmlformats.presentationml.slideshow",
    "com.microsoft.powerpoint.openxmlformats.presentationml.presentation", "org.oasis-open.opendocument.presentation",
    "com.apple.keynote.key", "com.apple.iwork.keynote.key"})


def presentation_profile(bundle, metadata=None):
    known = KNOWN_PROFILES.get(bundle.casefold(), "")
    if known:
        return known
    if not isinstance(metadata, dict) or metadata.get("CFBundleIdentifier") != bundle:
        return ""
    for item in metadata.get("CFBundleDocumentTypes", []):
        if not isinstance(item, dict) or str(item.get("CFBundleTypeRole", "")).casefold() != "editor":
            continue  # A chat app that merely accepts PPT attachments is not an editor.
        extensions = {str(ext).casefold() for ext in item.get("CFBundleTypeExtensions", [])}
        utis = {str(uti).casefold() for uti in item.get("LSItemContentTypes", [])}
        type_name = str(item.get("CFBundleTypeName", ""))
        if (extensions & PRESENTATION_EXTENSIONS or utis & PRESENTATION_UTIS
                or re.search(r"\b(?:presentation|slide\s?show)\b|演示文稿|簡報|演示稿", type_name, re.I)):
            return "generic"
    return ""


def installed_presentation_profile(bundle, path):
    known = presentation_profile(bundle)
    if known:
        return known
    app = Path(path)
    if not app.is_absolute() or app.suffix.casefold() != ".app":
        return ""
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            return presentation_profile(bundle, plistlib.load(stream))
    except (OSError, ValueError, TypeError):
        return ""


def scene_actions(bundle, scene, *, profile=""):
    profile = presentation_profile(bundle) or profile
    if profile not in PROFILES:
        return []
    app, actions = PROFILES[profile]
    return [dict(id=profile + ":" + key, label=label, shortcut=shortcut,
                 path=app + (" 放映快捷键" if scene == PRESENTATION else " 快捷键"),
                 available=None, preset=True)
            for key, label, shortcut in actions.get(scene, [])]


# Optional window-title aliases, independent of shortcut presets and detection.
# Unknown applications use their installed display name with the same detector.
TITLE_NAMES = {
    "powerpoint": ("PowerPoint", "Microsoft PowerPoint"),
    "wps": ("WPS", "WPS Office", "WPS Presentation", "WPS 演示"),
    "keynote": ("Keynote",),
    "libreoffice": ("LibreOffice", "LibreOffice Impress"),
    "openoffice": ("OpenOffice", "OpenOffice Impress"),
    "onlyoffice": ("ONLYOFFICE",),
}


def presentation_context(bundle, window, focus, attr, *, budget=0.24, profile="", screen_frames=(), application_name=""):
    """Capability boundary; every supported presenter uses the same detector."""
    profile = presentation_profile(bundle) or profile
    if not profile:
        return "", "unknown"
    return detect_presentation(window, focus, attr, budget=budget, screen_frames=screen_frames,
                               application_names=(application_name, *TITLE_NAMES.get(profile, ())))
