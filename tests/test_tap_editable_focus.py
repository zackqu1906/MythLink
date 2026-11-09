"""An idle dictation tap may borrow a keyboard only for a verified editor."""
from types import SimpleNamespace

import pytest

from proximic_ring.desktop.app_shortcuts import MacAppShortcuts, ShortcutTarget
from proximic_ring.voice.ime import system_input_source
from test_inline_state import component


FRONT = ("test.editor", 42)


@pytest.mark.parametrize("context,expected", [
    ("text", "text"), ("nontext", "outside"), ("unknown", "unavailable"),
    ("window_changed", "unavailable"), ("app_changed", "unavailable"), ("blocked", "unavailable"),
    ("no_target", "unavailable"), ("lost_after_selection", "nontext"), ("already_ready", "text"),
])
def test_idle_tap_checks_before_switch_and_returns_unused_source(component, monkeypatch, context, expected):
    inline, _ = component
    idle = {"ready": False, "client_id": "", "application": ""}
    ready = dict(ready=True, client_id="current", epoch=inline._epoch, utterance_id="",
                 selection=[2, 0], application=FRONT[0])
    state = dict(front=FRONT, reply=ready if context == "already_ready" else idle)
    monkeypatch.setattr(inline, "_foreground_identity", lambda: state["front"])
    inline._bridge = SimpleNamespace(epoch=inline._epoch, connected=True,
                                     focus_state=lambda **_: state["reply"], close=lambda: None,
                                     send=lambda _: True)
    checks, switches, restores = [], [], []
    def passive(_, origin):
        checks.append(origin)
        if context == "app_changed":
            state["front"] = ("other.app", 43)
        if context == "no_target":
            return None
        return ShortcutTarget(FRONT[0], 43 if context == "window_changed" else 42, "",
                              input_context=context if context in {"nontext", "unknown"} else "text",
                              blocked=context == "blocked")
    def select(pid, *, remember_source, **_):
        assert remember_source and pid == 42
        switches.append(pid)
        state["reply"] = ({**ready, "ready": False, "selection": [-1, -1]}
                          if context == "lost_after_selection" else ready)
        return "original.keyboard"
    monkeypatch.setattr(MacAppShortcuts, "passive_focus", passive)
    monkeypatch.setattr(system_input_source, "activate_voice_input_source", select)
    monkeypatch.setattr(inline._gesture_source_activation, "restore_if_unused",
                        lambda front, previous, ticket: restores.append((front, previous)))
    assert inline.capture_start_focus(require_editable=True).status == expected
    assert checks == ([] if context == "already_ready" else [FRONT])
    assert switches == ([42] if context in {"text", "lost_after_selection"} else [])
    assert restores == ([(FRONT, "original.keyboard")] if context == "lost_after_selection" else [])
