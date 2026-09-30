"""Bounded, metadata-only detection of document and playback contexts."""
from __future__ import annotations

from collections import deque
import re
import time

from ..scene_capabilities import BROWSERS
from .models import PDF, VIDEO, IMAGE, MUSIC, SceneResult
from .focus import inspect_focus
from .documents import document_kind


def detect_content(bundle, profiles, window, focus, attr, *, budget=.30):
    if window is None or not profiles:
        return SceneResult()
    deadline = time.monotonic() + budget
    def read(node, key):
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return attr(node, key) if node is not None else None
    try:
        if (read(window, "AXRole") != "AXWindow" or read(window, "AXModal") or read(window, "AXMinimized")
                or read(window, "AXSheets") or read(window, "AXSubrole") in {"AXDialog", "AXSystemDialog"}):
            return SceneResult()
        snapshot = inspect_focus(window, focus, read)
        context = snapshot.context
        # Browsers may expose multiple tabs/panes and embedded PDFs in one AX
        # window. Only the page owning the current focus supplies evidence.
        web = next((node for node in reversed(snapshot.ancestors) if read(node, "AXRole") == "AXWebArea"), None)
        browser = bundle.casefold() in BROWSERS
        if browser and web is None:
            return SceneResult()
        document = document_kind(read(web, "AXURL") if browser else read(window, "AXDocument"))
        # A title alone in a browser/office app is not proof of an open file.
        # Preview does not always expose AXDocument for an image.
        if not document and bundle.casefold() == "com.apple.preview":
            document = document_kind(read(window, "AXTitle"))
        if document in {PDF, IMAGE} and document in profiles:
            return SceneResult(document, context)

        # Only visit structural UI and controls. Never descend into document
        # text, image content, table rows or read AXValue/selected text.
        queue = deque([(web if browser else window, 0)])
        play = seek = video = audio = pdf = picture = False
        visited = 0
        while queue and visited < 96:
            node, depth = queue.popleft(); visited += 1
            if read(node, "AXHidden") or read(node, "AXEnabled") is False:
                continue
            role = read(node, "AXRole")
            if role == "AXWebArea" and node != web:
                continue
            labels = " ".join(str(read(node, key) or "") for key in ("AXIdentifier", "AXDescription", "AXSubrole")).casefold()
            if role in {"AXButton", "AXCheckBox", "AXSlider", "AXValueIndicator", "AXMenuButton"}:
                labels += " " + str(read(node, "AXTitle") or "").casefold()
                if read(node, "AXEnabled") is True:
                    # QuickTime exposes play/pause as AXCheckBox / AXToggle.
                    play |= role in {"AXButton", "AXCheckBox"} and bool(re.search(r"\b(?:play|pause)\b|播放|暂停|暫停", labels))
                    seek |= role in {"AXSlider", "AXValueIndicator"} and bool(re.search(r"\b(?:seek|scrub|timeline|playback|progress|time)\b|进度|進度|时间|時間", labels))
            if role in {"AXGroup", "AXScrollArea", "AXLayoutArea", "AXWebArea", "AXImage"}:
                video |= bool(re.search(r"\b(?:video|movie)(?:\b|view)|视频|影片|視頻", labels))
                audio |= bool(re.search(r"\baudio(?:\b|player)|音频|音訊", labels))
                pdf |= bool(re.search(r"\bpdf(?:\b|view)|pdf文档", labels))
                picture |= labels.strip() in {"image preview", "photo viewer", "图片预览", "照片查看器"}
            if role in {"AXWindow", "AXGroup", "AXToolbar", "AXSplitGroup", "AXScrollArea", "AXWebArea", "AXLayoutArea"} and depth < 7:
                queue.extend((child, depth + 1) for child in list(read(node, "AXChildren") or [])[:max(0, 96-visited-len(queue))])
        if (document == PDF or (not document and pdf)) and PDF in profiles:
            return SceneResult(PDF, context)
        if (document == IMAGE or (not document and picture)) and IMAGE in profiles:
            return SceneResult(IMAGE, context)
        # Playback includes paused content, allowing the same gesture to resume.
        # A file name or fullscreen flag alone is insufficient for playback.
        if play and (seek or video or audio):
            # A loaded file is stronger evidence than an "audio controls" label
            # inside a video player. Do not combine contradictory scene hints.
            if document in {VIDEO, MUSIC}:
                return SceneResult(document, context) if document in profiles else SceneResult()
            if not document:
                if VIDEO in profiles and video:
                    return SceneResult(VIDEO, context)
                if MUSIC in profiles and (audio or (set(profiles) == {MUSIC} and seek)) and not video:
                    return SceneResult(MUSIC, context)
            # Generic players must expose which media kind is loaded.
        return SceneResult()
    except (TimeoutError, TypeError, ValueError):
        return SceneResult()
