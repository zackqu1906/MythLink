"""Tuple assertions retained for the established scene regression matrix."""
from proximic_ring.scenes.recognition.engine import detect_scene
from proximic_ring.gesture_scenes import PRESENTATION, presentation_profile


def activity_context(bundle, profiles, window, focus, attr, **options):
    result = detect_scene(bundle, profiles, window, focus, attr, **options)
    return result.scene, result.input_context


def presentation_context(bundle, window, focus, attr, *, profile="", **options):
    profile = presentation_profile(bundle) or profile
    result = detect_scene(bundle, {PRESENTATION: profile} if profile else {}, window, focus, attr, **options)
    return result.scene, result.input_context
