"""Voice functions replace their single saved gesture atomically."""
import pytest
from proximic_ring.gestures.gesture_settings import GestureBindings, GESTURE_LABELS
from proximic_ring.ui.gestures.voice_gesture_mapping import with_action, action_for, VOICE_ACTIONS, single_bindings


@pytest.mark.parametrize("action,_,targets", VOICE_ACTIONS)
def test_every_gesture_can_replace_a_function_and_reload(action, _, targets):
    bindings = GestureBindings()
    previous = None
    for gesture in GESTURE_LABELS:
        bindings = with_action(bindings, gesture, action)
        assert action_for(bindings, gesture)["voiceAction"] == action
        assert all(sum(bool(g) for g in slots) <= 1 for slots in bindings.as_dict().values())
        assert all(bindings.as_dict()[key] == [gesture, ""] for key in targets)
        if previous:
            assert action_for(bindings, previous) is None
        previous = gesture
        assert GestureBindings.from_json(bindings.to_json()) == bindings
    cleared = with_action(bindings, previous, "")
    assert all(not any(cleared.as_dict()[key]) for key in targets)


def test_reassignment_releases_old_gesture_and_keeps_other_functions():
    original = GestureBindings()
    updated = with_action(original, "tap", "undo")
    assert updated.end == ("", "") and updated.undo == ("tap", "")
    assert action_for(updated, "swipe-left") is None
    assert original.end == ("tap", "")
    assert updated.confirm == original.confirm and updated.send == original.send
    updated = with_action(updated, "double-tap", "polish")
    assert updated.confirm == ("", "") and updated.polish == ("double-tap", "")
    assert action_for(updated, "swipe-down") is None


def test_combined_start_end_can_split_and_invalid_change_is_atomic():
    original = GestureBindings()
    updated = with_action(original, "snap", "start_end")
    assert updated.confirm == updated.end == ("snap", "")
    assert action_for(updated, "tap") is action_for(updated, "double-tap") is None
    split = with_action(updated, "clench", "confirm")
    assert split.confirm == ("clench", "") and split.end == ("snap", "")
    assert action_for(split, "snap")["voiceAction"] == "end"
    cleared = with_action(updated, "snap", "")
    assert not any(cleared.confirm) and not any(cleared.end)
    with pytest.raises(ValueError):
        with_action(updated, "snap", "missing")
    with pytest.raises(ValueError):
        with_action(updated, "missing", "undo")


def test_legacy_multiple_choices_keep_first_without_reintroducing_defaults():
    original = GestureBindings(confirm=("snap", "clench"), end=("snap", "clench"),
                               undo=("", "tap", "double-tap"), send=())
    migrated = single_bindings(original)
    assert migrated.confirm == migrated.end == ("snap", "")
    assert migrated.undo == ("tap", "") and migrated.send == ("", "")
    assert single_bindings(migrated) == migrated
