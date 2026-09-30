"""Single runtime entry point for all scene recognition.

Application capabilities select applicable evidence. A fresh caller-owned AX
snapshot and read cache flow through every detector. Recognition returns data;
configuration, voice routing and shortcut execution remain outside this package.
"""
from dataclasses import replace
import time

from ..gesture_scenes import presentation_profile, TITLE_NAMES
from ..scene_capabilities import BROWSERS
from . import browser, content, presentation
from .models import PRESENTATION, VIDEO, SceneResult


def detect_scene(bundle, profiles, window, focus, attr, *, budget=.48,
                 screen_frames=(), application_name=""):
    """Return one SceneResult; preserve current evidence priority and time limits.

    Browser video must carry current page/player identity. Otherwise slideshow
    evidence is checked before native reading/media in multi-purpose apps.
    The existing browser (180 ms), native (300 ms) and mixed-app slideshow
    (240 ms) limits fit inside the total budget. No result is cached across calls.
    """
    if window is None or not profiles or budget <= 0:
        return SceneResult()
    deadline = time.monotonic() + budget
    is_browser = bundle.casefold() in BROWSERS
    web = SceneResult()
    if is_browser:
        web = browser.detect_browser(window, focus, attr,
            budget=max(0, min(.18, deadline - time.monotonic())))
        if web.scene:
            return web
    native_deadline = min(deadline, time.monotonic() + .30)
    result = SceneResult()
    if PRESENTATION in profiles:
        profile = presentation_profile(bundle) or profiles[PRESENTATION]
        if profile:
            result = presentation.detect_presentation(window, focus, attr,
                budget=max(0, min(.24 if len(profiles) > 1 else .30, native_deadline - time.monotonic())),
                screen_frames=screen_frames,
                application_names=(application_name, *TITLE_NAMES.get(profile, ())))
    if not result.scene:
        result = content.detect_content(bundle, profiles, window, focus, attr,
            budget=max(0, native_deadline - time.monotonic()))
    if is_browser and result.scene == VIDEO:
        # Generic native hints alone cannot authorize a webpage video shortcut.
        result = replace(result, scene="")
    return replace(result, website=web.website, page_key=web.page_key,
                   web_area=web.web_area, player=web.player)
