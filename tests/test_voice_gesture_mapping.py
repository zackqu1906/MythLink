"""Every firmware gesture can independently choose any repeatable voice action."""
import pytest
from proximic_ring.gestures.gesture_settings import GestureBindings, GESTURE_LABELS
from proximic_ring.ui.gestures.voice_gesture_mapping import with_action, action_for, VOICE_ACTIONS


@pytest.mark.parametrize("action", [row[0] for row in VOICE_ACTIONS])
def test_every_gesture_can_repeat_any_voice_operation_and_reload(action):
    bindings = GestureBindings()
    for gesture in GESTURE_LABELS:
        bindings = with_action(bindings, gesture, action)
        assert action_for(bindings, gesture)["voiceAction"] == action
    restored = GestureBindings.from_json(bindings.to_json())
    assert restored == bindings
    assert all(action_for(restored, g)["voiceAction"] == action for g in GESTURE_LABELS)
    for gesture in GESTURE_LABELS:
        bindings = with_action(bindings, gesture, "")
    assert not any(g for values in bindings.as_dict().values() for g in values)


def test_reassignment_preserves_every_other_gesture_and_can_clear_last_endpoint():
    original = GestureBindings()
    updated = with_action(original, "tap", "undo")
    assert updated.end == ("", "") and updated.undo == ("swipe-left", "tap")
    assert original.end == ("tap", "")
    updated = with_action(updated, "double-tap", "polish")
    assert updated.confirm == ("", "")
    assert updated.polish == ("swipe-down", "double-tap")
    for gesture in set(GESTURE_LABELS) - {"tap", "double-tap"}:
        assert action_for(updated, gesture) == action_for(original, gesture)


def test_combined_start_end_and_invalid_action_are_atomic():
    original = GestureBindings()
    updated = with_action(original, "snap", "start_end")
    assert updated.confirm == ("double-tap", "snap")
    assert updated.end == ("tap", "snap")
    assert with_action(updated, "snap", "") == original
    with pytest.raises(ValueError):
        with_action(updated, "snap", "missing")
    with pytest.raises(ValueError):
        with_action(updated, "missing", "undo")
