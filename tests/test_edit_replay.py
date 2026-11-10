from dataclasses import replace

import pytest

from proximic_ring.text_processing import LLMSettings, TextProcessingRequest, TextProcessingResult
from proximic_ring.text_processing.context_preparation import PreparedContext
from proximic_ring.ui.voice.edit_replay import frozen_edit_request, replay_context


@pytest.mark.parametrize("mode", ["manual", "auto", "manual_routed_edit"])
def test_replay_detaches_actual_resolved_context_and_settings(mode):
    pending = PreparedContext()
    context = {"topic": "旧上文", "messages": ["旧消息"]}
    request = TextProcessingRequest(1, 11, "edit", "扩写", LLMSettings(), "原文",
                                    screen_context=pending, routing_mode=mode)
    result = TextProcessingResult(1, 11, "edit", "扩写", "结果", .1, True,
                                  model_input={"screen_context": context})
    saved = frozen_edit_request(request, result)
    assert saved.settings == request.settings
    assert saved.raw_text == "扩写" and saved.target_text == "原文" and saved.session_id == 11
    context["messages"].append("后来消息")
    if mode == "manual":
        assert replay_context(saved) is None
    else:
        first = replay_context(saved)
        assert first.done() and first.result()["messages"] == ["旧消息"]
        first.result()["messages"].append("外部修改")
        assert replay_context(saved).result()["messages"] == ["旧消息"]
    assert frozen_edit_request(request, replace(result, error="超时")) is None


def test_replay_never_waits_for_missing_context_or_collects_fresh_context():
    request = TextProcessingRequest(1, 1, "edit", "扩写", LLMSettings(),
                                    screen_context=PreparedContext(), routing_mode="auto")
    result = TextProcessingResult(1, 1, "edit", "扩写", "结果", .1, True)
    assert frozen_edit_request(request, result) is None
