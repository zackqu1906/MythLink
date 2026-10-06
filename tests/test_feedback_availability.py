from types import SimpleNamespace
from proximic_ring.ui.feedback_availability import feedback_block_reason


def host():
    return SimpleNamespace(permissionSetup=SimpleNamespace(active=True,blocksFeedback=False),
        ringGestures=SimpleNamespace(speech_busy=lambda:False,windowSelector=SimpleNamespace(phase='closed')),
        appGestures=SimpleNamespace(recording=False))


def test_permission_sequence_pending_without_a_prompt_does_not_silence_scene_feedback():
    c=host()
    assert feedback_block_reason(c)==''
    c.permissionSetup.blocksFeedback=True
    assert feedback_block_reason(c)=='permission_prompt'
    c.permissionSetup.blocksFeedback=False
    c.ringGestures.speech_busy=lambda:True
    assert feedback_block_reason(c)=='voice_interaction'
    c.ringGestures.speech_busy=lambda:False
    c.ringGestures.windowSelector.phase='ready'
    assert feedback_block_reason(c)=='window_selector'
    c.ringGestures.windowSelector.phase='closed';c.appGestures.recording=True
    assert feedback_block_reason(c)=='shortcut_recording'
    c.appGestures.recording=False
    assert feedback_block_reason(c,suggestion=SimpleNamespace(visible=True))=='application_suggestion'


def test_legacy_visible_permission_wizard_remains_blocking():
    c=host();del c.permissionSetup.blocksFeedback
    assert feedback_block_reason(c)=='permission_prompt'
    c.permissionSetup.active=False
    assert feedback_block_reason(c)==''
