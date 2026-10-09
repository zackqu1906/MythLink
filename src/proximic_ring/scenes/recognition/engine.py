"""Single runtime entry point for all scene recognition.

Application capabilities select applicable evidence. A fresh caller-owned AX
snapshot and read cache flow through every detector. Recognition returns data;
configuration, voice routing and shortcut execution remain outside this package.
"""
from dataclasses import replace
import time

from ..capabilities import presentation_profile, TITLE_NAMES
from ..capabilities import BROWSERS
from . import browser, content, presentation
from ..models import PRESENTATION, SceneResult


def detect_scene(bundle, profiles, window, focus, attr, *, budget=.48,
                 screen_frames=(), application_name="", application_category="", observation=False):
    """Return one SceneResult; preserve current evidence priority and time limits.

    Browser and native adapters return the same scene/focus/identity contract.
    Browser evidence is restricted to the current page; native slideshow evidence
    precedes reading/media in multi-purpose apps. Each adapter has at most 300 ms
    inside the total budget. No result is cached across calls.
    """
    started = time.perf_counter()
    attempts = []
    def finish(result):
        info = {**result.diagnostic, "schema": 1, "attempts": attempts,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
        return replace(result, diagnostic=info)
    if window is None or not profiles or budget <= 0:
        reason = "window_missing" if window is None else "no_scene_capability" if not profiles else "recognition_timeout"
        return finish(SceneResult(diagnostic={"reason": reason}))
    deadline = time.monotonic() + budget
    is_browser = bundle.casefold() in BROWSERS
    web = SceneResult()
    if is_browser:
        web = browser.detect_browser(window, focus, attr, profiles=profiles,
            budget=max(0, min(.30, deadline - time.monotonic())), observation=observation)
        attempts.append(web.diagnostic)
        return finish(web)  # Web content stays inside the verified current page.
    native_deadline = min(deadline, time.monotonic() + .30)
    result = SceneResult()
    if PRESENTATION in profiles:
        profile = presentation_profile(bundle) or profiles[PRESENTATION]
        if profile:
            result = presentation.detect_presentation(window, focus, attr,
                budget=max(0, min(.24 if len(profiles) > 1 else .30, native_deadline - time.monotonic())),
                screen_frames=screen_frames,
                application_names=(application_name, *TITLE_NAMES.get(profile, ())))
            attempts.append(result.diagnostic)
    presentation_diagnostic = result.diagnostic
    if not result.scene:
        result = content.detect_content(bundle, profiles, window, focus, attr,
            budget=max(0, native_deadline - time.monotonic()), application_category=application_category)
        attempts.append(result.diagnostic)
        if not result.scene and set(profiles) == {PRESENTATION} and presentation_diagnostic:
            result = replace(result, diagnostic=presentation_diagnostic)
    return finish(result)
