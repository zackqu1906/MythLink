"""Bounded, metadata-only detection of document and playback contexts."""
from __future__ import annotations

from collections import deque
from pathlib import PurePosixPath
import re
import time
from urllib.parse import unquote, urlsplit

from .gesture_scenes import PRESENTATION, presentation_context
from .scene_capabilities import PDF, VIDEO, IMAGE, MUSIC, EXTENSIONS, MUSIC_APPS, BROWSERS


def input_context(window, focus, read):
    safe = {"AXWindow", "AXButton", "AXImage", "AXGroup", "AXLayoutArea", "AXScrollArea", "AXStaticText",
            "AXToolbar", "AXSplitGroup", "AXSlider", "AXValueIndicator", "AXTable", "AXRow", "AXList", "AXOutline"}
    node = focus
    for _ in range(14):
        if node is None:
            break
        role = read(node, "AXRole")
        editable, is_editable = read(node, "AXEditable"), read(node, "AXIsEditable")
        if role in {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"} or editable or is_editable:
            return "text"
        if role not in safe and not (role == "AXWebArea" and (editable is False or is_editable is False)):
            break
        if node == window:
            return "nontext"
        node = read(node, "AXParent")
    return "unknown"


def document_kind(value):
    # Examine only a document URL's suffix; do not expose/log names or URLs.
    try:
        path = unquote(urlsplit(str(value or "")).path)
        suffix = PurePosixPath(path).suffix.casefold().lstrip(".")
    except (TypeError, ValueError):
        return ""
    return next((scene for scene, extensions in EXTENSIONS.items() if suffix in extensions), "")


def activity_context(bundle, profiles, window, focus, attr, *, budget=.16):
    if window is None or not profiles:
        return "", "unknown"
    deadline = time.monotonic() + budget
    def read(node, key):
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return attr(node, key) if node is not None else None
    try:
        # Existing presentation recognition (including the WPS exception) wins.
        if PRESENTATION in profiles:
            scene, context = presentation_context(bundle, window, focus, read,
                budget=max(0, deadline-time.monotonic()), profile=profiles[PRESENTATION])
            if scene:
                return scene, context
        if (read(window, "AXRole") != "AXWindow" or read(window, "AXModal") or read(window, "AXMinimized")
                or read(window, "AXSheets") or read(window, "AXSubrole") in {"AXDialog", "AXSystemDialog"}):
            return "", "unknown"
        context = input_context(window, focus, read)
        document = document_kind(read(window, "AXDocument"))
        browser = bundle.casefold() in BROWSERS
        # A title alone in a browser/office app is not proof of an open file.
        # Preview does not always expose AXDocument for an image.
        if not document and bundle.casefold() == "com.apple.preview":
            document = document_kind(read(window, "AXTitle"))
        if document in {PDF, IMAGE} and document in profiles:
            return document, context

        # Only visit structural UI and controls. Never descend into document
        # text, image content, table rows or read AXValue/selected text.
        queue = deque([(window, 0)])
        play = seek = video = audio = pdf = picture = False
        visited = 0
        while queue and visited < 96:
            node, depth = queue.popleft(); visited += 1
            if read(node, "AXHidden"):
                continue
            role = read(node, "AXRole")
            labels = " ".join(str(read(node, key) or "") for key in ("AXIdentifier", "AXDescription", "AXSubrole")).casefold()
            if role in {"AXButton", "AXSlider", "AXMenuButton"}:
                labels += " " + str(read(node, "AXTitle") or "").casefold()
                if read(node, "AXEnabled") is not False:
                    play |= role == "AXButton" and bool(re.search(r"\b(?:play|pause)\b|播放|暂停|暫停", labels))
                    seek |= role in {"AXSlider", "AXValueIndicator"} and bool(re.search(r"\b(?:seek|scrub|timeline|playback|progress|time)\b|进度|進度|时间|時間", labels))
            if role in {"AXGroup", "AXScrollArea", "AXLayoutArea", "AXWebArea", "AXImage"}:
                video |= bool(re.search(r"\b(?:video|movie)(?:\b|view)|视频|影片|視頻", labels))
                audio |= bool(re.search(r"\baudio(?:\b|player)|音频|音訊", labels))
                pdf |= bool(re.search(r"\bpdf(?:\b|view)|pdf文档", labels))
                picture |= labels.strip() in {"image preview", "photo viewer", "图片预览", "照片查看器"}
            if role == "AXWebArea":
                web_document = document_kind(read(node, "AXURL"))
                if web_document in {PDF, IMAGE}:
                    document = web_document
            if role in {"AXWindow", "AXGroup", "AXToolbar", "AXSplitGroup", "AXScrollArea", "AXWebArea"} and depth < 7:
                queue.extend((child, depth + 1) for child in list(read(node, "AXChildren") or [])[:96-visited-len(queue)])
        if (document == PDF or pdf) and PDF in profiles:
            return PDF, context
        if (document == IMAGE or picture) and IMAGE in profiles:
            return IMAGE, context
        # Playback includes paused content, allowing the same gesture to resume.
        # A file name or fullscreen flag alone is insufficient for playback.
        if play and (seek or video or audio):
            if VIDEO in profiles and (document == VIDEO or video) and not (document == MUSIC or audio):
                return VIDEO, context
            if MUSIC in profiles and (document == MUSIC or audio or (bundle.casefold() in MUSIC_APPS and not video)):
                return MUSIC, context
            # Generic players must expose which media kind is loaded.
        return "", "unknown"
    except (TimeoutError, TypeError, ValueError):
        return "", "unknown"
