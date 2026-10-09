"""Bounded, metadata-only detection of document and playback contexts."""
from __future__ import annotations

from collections import deque
import re
import time

from ..capabilities import is_music_application
from ..models import PDF, VIDEO, IMAGE, MUSIC, SceneResult
from .focus import inspect_focus
from .playback import PlaybackControls
from .documents import document_kind
from .diagnostics import explain, window_rejection


def detect_content(bundle, profiles, window, focus, attr, *, budget=.30, application_category=""):
    music_application = is_music_application(bundle, application_category)
    details = {"music_application": music_application}
    def done(reason, scene="", context="unknown", **facts):
        return explain(SceneResult(scene, context), "content", reason, **details, **facts)
    if window is None or not profiles:
        return done("window_missing" if window is None else "no_scene_capability")
    deadline = time.monotonic() + budget
    def read(node, key):
        details["last_attribute"] = key
        details["metadata_reads"] = details.get("metadata_reads", 0) + 1
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return attr(node, key) if node is not None else None
    try:
        rejection = window_rejection(window, read)
        if rejection:
            return done(rejection)
        snapshot = inspect_focus(window, focus, read)
        context = snapshot.context
        details["focus_reason"] = snapshot.reason
        # Native Electron/WebKit apps may contain embedded web views. Only the
        # view owning current focus may contribute native-window evidence.
        web = next((node for node in reversed(snapshot.ancestors) if read(node, "AXRole") == "AXWebArea"), None)
        document = document_kind(read(window, "AXDocument"))
        # A title alone in a browser/office app is not proof of an open file.
        # Preview does not always expose AXDocument for an image.
        if not document and bundle.casefold() == "com.apple.preview":
            document = document_kind(read(window, "AXTitle"))
        details["document_kind"] = document
        if document in {PDF, IMAGE} and document in profiles:
            return done("recognized", document, context, evidence="document_type")

        # Only visit structural UI and controls. Never descend into document
        # text, image content, table rows or read AXValue/selected text.
        queue = deque([(window, 0)])
        controls = PlaybackControls()
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
            controls.observe(node, read)
            play, seek = controls.play, controls.seek
            if role in {"AXGroup", "AXScrollArea", "AXLayoutArea", "AXWebArea", "AXImage"}:
                video |= bool(re.search(r"\b(?:video|movie)(?:\b|view)|视频|影片|視頻", labels))
                audio |= bool(re.search(r"\baudio(?:\b|player)|音频|音訊", labels))
                pdf |= bool(re.search(r"\bpdf(?:\b|view)|pdf文档", labels))
                picture |= labels.strip() in {"image preview", "photo viewer", "图片预览", "照片查看器"}
            if role in {"AXWindow", "AXGroup", "AXToolbar", "AXSplitGroup", "AXScrollArea", "AXWebArea", "AXLayoutArea"} and depth < 7:
                queue.extend((child, depth + 1) for child in list(read(node, "AXChildren") or [])[:max(0, 96-visited-len(queue))])
        details.update(scanned_nodes=visited, play_control=bool(play), seek_control=bool(seek),
                       video_surface=bool(video), audio_surface=bool(audio), scan_limited=bool(queue))
        if (document == PDF or (not document and pdf)) and PDF in profiles:
            return done("recognized", PDF, context, evidence="pdf_surface")
        if (document == IMAGE or (not document and picture)) and IMAGE in profiles:
            return done("recognized", IMAGE, context, evidence="image_surface")
        # Playback includes paused content, allowing the same gesture to resume.
        # A file name or fullscreen flag alone is insufficient for playback.
        if play and (seek or video or audio):
            # A loaded file is stronger evidence than an "audio controls" label
            # inside a video player. Do not combine contradictory scene hints.
            if document in {VIDEO, MUSIC}:
                return done("recognized", document, context, evidence="document_and_player") if document in profiles else done("unsupported_media_kind")
            if not document:
                if VIDEO in profiles and video:
                    return done("recognized", VIDEO, context, evidence="video_controls")
                if MUSIC in profiles and not video:
                    if audio:
                        return done("recognized", MUSIC, context, evidence="audio_controls")
                    # Music apps can also declare movies/music videos. Their
                    # active play + progress controls still identify audio when
                    # no loaded video document or video surface contradicts it.
                    if seek and (music_application or set(profiles) == {MUSIC}):
                        return done("recognized", MUSIC, context, evidence="music_player_controls")
            # Generic players must expose which media kind is loaded.
        return done("no_playback_controls" if document in {VIDEO, MUSIC} and not play else "no_content_evidence")
    except TimeoutError:
        return done("recognition_timeout")
    except (TypeError, ValueError) as exc:
        return done("invalid_metadata", error_type=type(exc).__name__)
