"""Application-independent slideshow recognition from a fresh AX snapshot.

The caller verifies presentation capability. This module knows neither bundle
identifiers nor shortcut presets; it only evaluates window/control evidence.
"""
from __future__ import annotations

from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit

PRESENTATION = "presentation"


def detect_presentation(window, focus, attr, *, budget=0.24, screen_frames=(), application_names=()):
    """Return a positive scene and a three-state input context.

    A full-screen document is insufficient. Recognize a dedicated slide-show
    window or a visible End Show button in a presentation editor's controls.
    Unsupported/localized AX layouts remain unknown and retain global routing.
    """
    if window is None:
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
            parent = read(node, "AXParent")
            # Some presentation applications expose AXWindow without AXParent on
            # their slide canvas. Keep checking ancestors when available.
            if parent is None and read(node, "AXWindow") == window:
                return "nontext"
            node = parent
        return input_context

    def close_rect(position, size, other_position, other_size):
        if any(value is None or len(value) != 2 for value in (position, size, other_position, other_size)):
            return False
        if min(*size, *other_size) <= 0:
            return False
        # AX canvas bounds can differ by a border or display-scale rounding.
        return all(abs(a - b) <= max(3., extent * .01)
                   for pair in ((position, other_position), (size, other_size))
                   for a, b, extent in zip(*pair, size))

    def slide_surface():
        # Native slide surfaces vary between AXDialog/AXStandardWindow,
        # and borderless fullscreen windows often omit AXFullScreen entirely.
        # Require a complete, non-editable canvas tree covering the screen;
        # fullscreen editor ribbons, prompts and missing trees never qualify.
        if (focus is None or read(focus, "AXRole") not in {"AXGroup", "AXImage", "AXLayoutArea"}
                or read(focus, "AXFocused") is False or read(focus, "AXEnabled") is False
                or focus_context() != "nontext"):
            return False
        # Multi-purpose editors also display PDFs, images and documents fullscreen. A known
        # non-slide document must not borrow slideshow mappings. Titles may
        # otherwise be empty, omitted, localized, or just the deck's name.
        document = read(window, "AXDocument")
        document_path = unquote(urlsplit(str(document or "")).path)
        suffix = Path(document_path or str(read(window, "AXTitle") or "")).suffix.casefold()
        if suffix in {".pdf", ".doc", ".docx", ".odt", ".txt", ".xls", ".xlsx", ".csv",
                      ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mov"}:
            return False
        position, size = read(window, "AXPosition"), read(window, "AXSize")
        if not close_rect(position, size, read(focus, "AXPosition"), read(focus, "AXSize")):
            return False
        if read(window, "AXFullScreen") is not True and not any(
                close_rect(position, size, frame[:2], frame[2:]) for frame in screen_frames):
            return False
        queue, found, visited = [(window, 0)], False, 0
        while queue and visited < 24:
            node, depth = queue.pop(0)
            visited += 1
            if read(node, "AXHidden") is True:
                continue
            role = read(node, "AXRole")
            if read(node, "AXEditable") or read(node, "AXIsEditable"):
                return False
            if node == focus:
                found = True
            if node != window and role not in {"AXGroup", "AXImage", "AXLayoutArea", "AXStaticText"}:
                return False
            if role in {"AXStaticText", "AXImage"}:
                continue
            children = read(node, "AXChildren")
            if children is None or (children and depth >= 4) or len(children) + len(queue) + visited > 24:
                return False
            queue.extend((child, depth + 1) for child in children)
        return found and not queue

    try:
        if (read(window, "AXRole") != "AXWindow" or read(window, "AXModal")
                or read(window, "AXMinimized") or read(window, "AXSheets")):
            return "", "unknown"
        subrole = read(window, "AXSubrole")
        if subrole == "AXSystemDialog":
            return "", "unknown"
        title = str(read(window, "AXTitle") or "").strip()
        # Anchored application-generated names, not arbitrary mentions in a
        # document name. Do not treat a normal .pptx document title as a show.
        app_name = "|".join(re.escape(name) for name in application_names if name)
        show_name = r"(?:Slide ?Show|Presenter (?:View|Display)|幻灯片放映|幻燈片放映|投影片放映|幻灯片播放|演示者视图|演講者檢視|演講者顯示器)"
        branded = app_name and re.fullmatch("(?:" + app_name + ")" + r"\s*[-–—:]?\s*" + show_name + r"(?:\s*[-–—:]\s*.+)?", title, re.I)
        unbranded = re.fullmatch(show_name + r"(?:\s*[-–—:]\s*.+)?", title, re.I)
        # A native show title normally includes the deck's .pptx extension.
        # Only reject it as a filename when AXDocument actually says so.
        document_name = Path(unquote(urlsplit(str(read(window, "AXDocument") or "")).path)).name
        if (branded or unbranded) and title != document_name:
            return PRESENTATION, focus_context()
        if slide_surface():
            return PRESENTATION, "nontext"
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
