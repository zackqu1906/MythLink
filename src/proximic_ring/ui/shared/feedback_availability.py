"""Shared pause reasons for passive feedback; independent of gesture routing."""


def feedback_block_reason(controller, *, suggestion=None):
    setup = controller.permissionSetup
    # Legacy builds have a visible wizard for the entire active interval.
    if getattr(setup, "blocksFeedback", setup.active):
        return "permission_prompt"
    if controller.ringGestures.speech_busy():
        return "voice_interaction"
    if controller.ringGestures.windowSelector.phase != "closed":
        return "window_selector"
    if controller.appGestures.recording:
        return "shortcut_recording"
    if suggestion is not None and suggestion.visible:
        return "application_suggestion"
    return ""
