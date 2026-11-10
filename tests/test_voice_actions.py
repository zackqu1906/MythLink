"""Keyboard cancellation and gesture-only editing/history actions."""
import itertools

import pytest

from proximic_ring.voice.voice_actions import (
    ACTION_CANCEL,
    ACTION_EDIT,
    ACTION_INPUT,
    ACTION_REDO,
    ACTION_RETRY_EDIT,
    ACTION_SWITCH_MODE,
    ACTION_UNDO,
    WindowsVoiceActionHotkeys,
    voice_action_for_gesture,
)


def test_left_swipe_and_escape_share_cancel_and_undo_priority():
    for interaction, undo in itertools.product((False, True), repeat=2):
        state = dict(interaction_active=interaction, undo_active=undo)
        expected = ACTION_UNDO if undo else ACTION_CANCEL if interaction else None
        assert voice_action_for_gesture("swipe-left", **state) == expected
        assert WindowsVoiceActionHotkeys._action_for_key(0x1B, alt_down=False, **state) == expected


@pytest.mark.parametrize("name", ["empty", "tap", "snap", "swipe-up", "swipe-down", "unknown", ""])
def test_other_gestures_do_not_trigger_actions(name):
    assert voice_action_for_gesture(
        name, interaction_active=True, correction_active=True, undo_active=True
    ) is None


@pytest.mark.parametrize("state,expected", [
    ({}, None),
    ({"correction_active": True}, ACTION_SWITCH_MODE),
    ({"redo_active": True}, ACTION_REDO),
    ({"retry_active": True}, ACTION_RETRY_EDIT),
    ({"correction_active": True, "redo_active": True}, ACTION_SWITCH_MODE),
    ({"correction_active": True, "retry_active": True}, ACTION_SWITCH_MODE),
])
def test_right_swipe_retains_edit_next_and_retry_actions(state, expected):
    assert voice_action_for_gesture("swipe-right", **state) == expected


@pytest.mark.parametrize("key", range(0x74, 0x7C))  # F5 through F12, including the former default F8.
def test_retired_conversion_keys_always_pass_through(key):
    for alt, interaction, undo in itertools.product((False, True), repeat=3):
        assert WindowsVoiceActionHotkeys._action_for_key(
            key, alt_down=alt, interaction_active=interaction, undo_active=undo
        ) is None


@pytest.mark.parametrize("key,expected", [(0x31, ACTION_INPUT), (0x32, ACTION_EDIT)])
def test_alt_input_and_edit_shortcuts_remain_available(key, expected):
    assert WindowsVoiceActionHotkeys._action_for_key(key, alt_down=True) == expected
    assert WindowsVoiceActionHotkeys._action_for_key(key, alt_down=False) is None


@pytest.mark.parametrize("key", [0x09, 0x5A])
def test_unrelated_keys_pass_through_during_voice_interaction(key):
    assert WindowsVoiceActionHotkeys._action_for_key(
        key, alt_down=False, interaction_active=True, undo_active=True
    ) is None
