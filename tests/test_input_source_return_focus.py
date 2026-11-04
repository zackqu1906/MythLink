"""Only the active source-resume helper may borrow a local editor's focus."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("stage,owns", [
    ("selecting", True), ("selecting", False), ("returning", True),
    ("resuming", True), ("suspended", True), ("", True),
])
def test_agent_focus_waits_only_for_active_resume_helper(monkeypatch, stage, owns):
    from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
    monkeypatch.setattr("proximic_ring.agent_workflow.ui.agent_overlay.QGuiApplication.focusWindow", lambda: None)
    calls = []
    inline = SimpleNamespace(_source_activation=None, _source_return=SimpleNamespace(
        stage=stage, helper=SimpleNamespace(owns_foreground=lambda: owns)))
    overlay = SimpleNamespace(
        _closed=False, controller=SimpleNamespace(pickingAttachments=False),
        host=SimpleNamespace(_inline_input=inline),
        _focus_timer=SimpleNamespace(start=lambda duration: calls.append(("wait", duration))),
        _release_focus=lambda: calls.append(("release",)),
    )
    AgentOverlay._settle_focus(overlay)
    assert calls == ([("wait", 25)] if stage == "selecting" and owns else [("release",)])


@pytest.mark.parametrize("stage,owns", [
    ("selecting", True), ("selecting", False), ("returning", True),
    ("resuming", True), ("suspended", True), ("", True),
])
def test_recommendation_focus_waits_only_for_active_resume_helper(stage, owns):
    from proximic_ring.agent_workflow.ui.recommendation_focus import input_helper_active
    inline = SimpleNamespace(_source_activation=None, _source_return=SimpleNamespace(
        stage=stage, helper=SimpleNamespace(owns_foreground=lambda: owns)))
    owner = SimpleNamespace(_inline_input=inline, _touchpad=SimpleNamespace(strokeSession=SimpleNamespace()))
    dialog = SimpleNamespace(runtime=SimpleNamespace(agent=SimpleNamespace(owner=owner)))
    assert input_helper_active(dialog) is (stage == "selecting" and owns)


@pytest.mark.parametrize("kind", ["voice", "stroke"])
def test_recommendation_keeps_existing_startup_handoff(kind):
    from proximic_ring.agent_workflow.ui.recommendation_focus import input_helper_active
    helper = SimpleNamespace(owns_foreground=lambda: True)
    inline = SimpleNamespace(_source_activation=helper if kind == "voice" else None)
    stroke = SimpleNamespace(_source=helper if kind == "stroke" else None)
    owner = SimpleNamespace(_inline_input=inline, _touchpad=SimpleNamespace(strokeSession=stroke))
    dialog = SimpleNamespace(runtime=SimpleNamespace(agent=SimpleNamespace(owner=owner)))
    assert input_helper_active(dialog)
