"""Opt-in application scenes. Detection reads control metadata, never slide text."""
from __future__ import annotations

import re
import time
from pathlib import Path
import plistlib

POWERPOINT = "com.microsoft.Powerpoint"
PRESENTATION = "presentation"
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
    "openoffice": ("OpenOffice Impress", {PRESENTATION: NAVIGATION_ACTIONS}),
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


def presentation_context(bundle, window, focus, attr, *, budget=0.12, profile=""):
    """Return a positive scene and a three-state input context.

    A full-screen document is insufficient. Recognize a dedicated slide-show
    window or a visible End Show button in a presentation editor's controls.
    Unsupported/localized AX layouts remain unknown and retain global routing.
    """
    profile = presentation_profile(bundle) or profile
    if profile not in PROFILES or window is None:
        return "", "unknown"
    deadline = time.monotonic() + budget

    def read(node, key):
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return attr(node, key)

    def focus_context():
        # Check the actual focus and ancestors, including presenter notes. A
        # missing focus/parent or unknown control is not proof of non-editing.
        node, input_context = focus, "unknown"
        safe_roles = {"AXWindow", "AXButton", "AXImage", "AXGroup", "AXLayoutArea",
                      "AXScrollArea", "AXStaticText", "AXToolbar", "AXSplitGroup"}
        for _ in range(10):
            if node is None:
                break
            role = read(node, "AXRole")
            if role in {"AXTextArea", "AXTextField", "AXSearchField", "AXComboBox"} or (read(node, "AXEditable") or read(node, "AXIsEditable")):
                return "text"
            if role not in safe_roles:
                break
            if node == window:
                input_context = "nontext"
                break
            node = read(node, "AXParent")
        return input_context

    def wps_slide_surface():
        # WPS for Mac exposes its actual slide canvas as an untitled,
        # nonmodal AXDialog, with a focused leaf AXGroup covering the window.
        # Fullscreen alone is not evidence: normal editor windows, prompts,
        # text fields, missing focus and partially readable trees must fail.
        if (profile != "wps" or read(window, "AXTitle") != ""
                or read(window, "AXFullScreen") is not True
                or read(window, "AXModal") is not False
                or read(window, "AXMinimized") is not False
                or focus is None or read(focus, "AXRole") != "AXGroup"
                or read(focus, "AXFocused") is not True
                or read(focus, "AXEnabled") is not True
                or read(focus, "AXParent") != window
                or read(focus, "AXChildren") is None
                or read(focus, "AXChildren")):
            return False
        children = read(window, "AXChildren")
        if children is None or not 1 <= len(children) <= 2 or not any(c == focus for c in children):
            return False
        if any(c != focus and read(c, "AXRole") != "AXStaticText" for c in children):
            return False
        # LocalMacAppShortcuts converts these AXValues into numeric pairs.
        position, size = read(window, "AXPosition"), read(window, "AXSize")
        return (position is not None and size is not None and len(size) == 2
                and min(size) > 0 and position == read(focus, "AXPosition")
                and size == read(focus, "AXSize") and focus_context() == "nontext")

    try:
        if (read(window, "AXRole") != "AXWindow" or read(window, "AXModal")
                or read(window, "AXMinimized") or read(window, "AXSheets")):
            return "", "unknown"
        subrole = read(window, "AXSubrole")
        if subrole == "AXDialog" and wps_slide_surface():
            return PRESENTATION, "nontext"
        if subrole in {"AXDialog", "AXSystemDialog"}:
            return "", "unknown"
        title = str(read(window, "AXTitle") or "").strip()
        # Anchored application-generated names, not arbitrary mentions in a
        # document name. Do not treat a normal .pptx document title as a show.
        if (not re.search(r"\.(?:ppt[xm]?|pps[xm]?|odp|key|dps|dpt)\s*$", title, re.I) and re.fullmatch(
                r"(?:(?:(?:Microsoft )?PowerPoint|WPS(?: Office| Presentation| 演示)?|Keynote|(?:LibreOffice|OpenOffice)(?: Impress)?|ONLYOFFICE)\s*[-–—:]?\s*)?(?:Slide ?Show|幻灯片放映|投影片放映|幻灯片播放)(?:\s*[-–—:]\s*.+)?",
                title, re.I)):
            return PRESENTATION, focus_context()
        # Some versions expose only the document name. A visible presentation
        # toolbar's End Show button is an independent positive signal. Never
        # traverse the app menu, slide content, or read AXValue/selected text.
        queue = [(window, 0)]
        end_labels = {"end show", "end slide show", "end slideshow", "exit slideshow", "exit slide show",
                      "stop slideshow", "exit presentation", "end presentation", "close preview",
                      "结束放映", "结束幻灯片放映", "退出放映", "退出幻灯片播放", "退出幻灯片放映",
                      "结束播放", "結束放映", "結束投影片放映", "退出預覽", "退出预览"}
        visited = 0
        while queue and visited < 64:
            node, depth = queue.pop(0)
            visited += 1
            if read(node, "AXHidden"):
                continue
            role = read(node, "AXRole")
            if role == "AXButton":
                label = str(read(node, "AXTitle") or read(node, "AXDescription") or "").strip().casefold()
                if label in end_labels and read(node, "AXEnabled") and read(node, "AXHidden") is not True:
                    return PRESENTATION, focus_context()
            if role in {"AXWindow", "AXGroup", "AXToolbar", "AXSplitGroup"} and depth < 4:
                queue.extend((child, depth + 1) for child in list(read(node, "AXChildren") or [])[:64 - visited])
        return "", "unknown"
    except (TimeoutError, TypeError, ValueError):
        return "", "unknown"
