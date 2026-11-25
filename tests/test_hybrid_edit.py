"""The default voice editor through the real provider transport and history."""

import io
import json
import threading
from dataclasses import replace

import pytest

from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import INPUT_ROUTING_ROUTED_EDIT, TextProcessingRequest
from proximic_ring.text_processing.worker import TextProcessingWorker
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_bailian_text_service import settings


def service(*responses, after_call=None):
    calls = []
    replies = iter(responses)

    def post(req, timeout):
        body = json.loads(req.data)
        calls.append(body)
        value = json.dumps(next(replies), ensure_ascii=False)
        if "messages" in body:
            answer = {
                "choices": [
                    {"message": {"role": "assistant", "content": value}, "finish_reason": "stop"}
                ]
            }
        else:
            answer = {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": value}]}],
            }
        if after_call:
            after_call()
        return io.BytesIO(json.dumps(answer).encode())

    return OpenAICompatibleTextProcessor(urlopen=post), calls


@pytest.mark.parametrize(
    "provider,thinking,budget",
    [
        ("bailian", False, None),
        ("bailian", True, 24),
        ("volcengine", False, None),
    ],
)
def test_default_selects_the_word_in_context_and_reviews_it(provider, thinking, budget):
    prefs = replace(
        settings(),
        provider=provider,
        qwen_thinking_enabled=thinking,
        model="deepseek-test" if provider == "volcengine" else "qwen3.8-max",
    )
    processor, calls = service({"index": 1}, {"decision": "keep", "modified_text": ""})
    body, _, _, plan = processor.process_routed_edit_with_trace(
        "改成审图", prefs, "请先交图，再核对地图。"
    )
    assert body == "请先审图，再核对地图。" and plan is None
    assert len(calls) == 2 and all(c["model"] == prefs.model for c in calls)
    if provider == "bailian":
        assert [c["response_format"]["json_schema"]["name"] for c in calls] == [
            "select_location",
            "review_edit",
        ]
        assert all(
            c["enable_thinking"] is thinking and c.get("thinking_budget") == budget for c in calls
        )
        candidate_message = calls[0]["messages"][1]["content"]
    else:
        assert all(c["thinking"] == {"type": "disabled"} for c in calls)
        assert '"index"' in calls[0]["input"][0]["content"][0]["text"]
        assert '"decision"' in calls[1]["input"][0]["content"][0]["text"]
        candidate_message = calls[0]["input"][1]["content"][0]["text"]
    assert [choice["原词"] for choice in json.loads(candidate_message)["备选"]] == ["交图", "地图"]


def test_only_the_glyph_in_the_named_word_is_changed():
    processor, calls = service({"decision": "keep", "modified_text": ""})
    body, *_ = processor.process_routed_edit_with_trace(
        "晨星的星是新年的新", settings(), "晨星和星辰都在场。"
    )
    assert body == "晨新和星辰都在场。" and len(calls) == 1


@pytest.mark.parametrize("name", ["绀羽灵枢", "Acme Studio", "C++"])
def test_enabled_name_is_one_replacement_before_a_followup(name):
    from proximic_ring.text_processing.processing_dispatch import process_routed_edit

    request = TextProcessingRequest(
        1, 7, "edit", f"把项目改成{name}然后分行", settings(), "项目下周上线。请查收。",
        routing_mode=INPUT_ROUTING_ROUTED_EDIT, edit_hotwords=(name, "无关的私有词"),
    )
    expected = f"{name}下周上线。\n请查收。"
    processor, calls = service({"decision": "keep", "modified_text": ""}, {"modified_text": expected})
    assert process_routed_edit(processor, request).text == expected
    review = json.loads(calls[0]["messages"][1]["content"])
    assert review["初稿"] == f"{name}下周上线。请查收。"
    assert review["剩余口述"] == "然后分行"
    assert "无关的私有词" not in json.dumps(calls, ensure_ascii=False)


def test_personal_spelling_clues_are_scoped_to_the_request():
    hints = []
    for words in [(), ("岚籽", "无关的私有词"), ()]:
        processor, calls = service({"modified_text": "岚籽"})
        processor.process_routed_edit_with_trace(
            "岚子是山字旁的那个岚子", settings(), "请查收岚子的消息。", edit_hotwords=words
        )
        message = json.loads(calls[0]["messages"][1]["content"])
        hints.append(message["phonetic_candidates"].split("、"))
        assert "无关的私有词" not in json.dumps(calls, ensure_ascii=False)
    assert hints[0] == hints[2] and "岚籽" not in hints[0] and hints[1][0] == "岚籽"


def test_bare_hotword_does_not_authorize_a_local_replacement():
    from proximic_ring.text_processing.hybrid_edit.vocabulary import current_vocabulary, use_edit_hotwords

    requests = []
    for words in [(), ("Myth",)]:
        processor, calls = service({"modified_text": "Myth 绘制"})
        processor.process_routed_edit_with_trace("Myth", settings(), "miss 绘制", edit_hotwords=words)
        requests.append(calls)
    assert len(requests[0]) == 1 and requests[0] == requests[1]
    with use_edit_hotwords(("Myth",)):
        assert list(current_vocabulary().cut("Mythology和Myth")) == ["Mythology", "和", "Myth"]


def test_spoken_character_survives_a_competing_dictionary_spelling():
    from proximic_ring.text_processing.hybrid_edit.vocabulary import use_edit_hotwords
    from proximic_ring.text_processing.hybrid_edit.word_edits import pairs

    with use_edit_hotwords(("伸图", "岚籽")):
        assert pairs("伸图的伸") == ["伸"]
        assert pairs("岚紫的紫") == ["紫"]


def test_compound_uses_corrected_draft_and_continues_after_keep():
    processor, calls = service(
        {"decision": "keep", "modified_text": ""}, {"modified_text": "先审图。\n明天发货。"}
    )
    body, *_ = processor.process_routed_edit_with_trace(
        "把交图改成审图然后分两行",
        replace(settings(), qwen_thinking_enabled=True),
        "先交图。明天发货。",
        {},
        saved_context={"status": "ok", "text": "发货时间已经确认"},
    )
    assert body == "先审图。\n明天发货。"
    assert len(calls) == 2 and all(c["thinking_budget"] == 128 for c in calls)
    followup = json.loads(calls[-1]["messages"][1]["content"])
    assert followup["已改好的正文"] == "先审图。明天发货。"
    assert followup["保存资料"] == "发货时间已经确认"


@pytest.mark.parametrize("choice", [False, "1", {"index": 1}, None])
def test_invalid_location_response_aborts_before_review(choice):
    processor, calls = service({"index": choice})
    with pytest.raises(RuntimeError, match="index"):
        processor.process_routed_edit_with_trace("改成审图", settings(), "请先交图，再核对地图。")
    assert len(calls) == 1


def test_cancel_after_selection_skips_next_request():
    cancel = threading.Event()
    processor, calls = service({"index": 1}, after_call=cancel.set)
    with pytest.raises(RuntimeError, match="已取消"):
        processor.process_routed_edit_with_trace(
            "改成审图", settings(), "先交图。", cancelled=cancel.is_set
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "utterance,expected", [("删除", ""), ("把地图改成图册算了别改了", "保留地图。")]
)
def test_local_completion_through_worker_and_history(tmp_path, utterance, expected):
    processor, calls = service()
    done, results = threading.Event(), []
    worker = TextProcessingWorker(
        processor=processor, on_result=lambda r: (results.append(r), done.set())
    )
    request = TextProcessingRequest(
        1, 7, "edit", utterance, settings(), "保留地图。", routing_mode=INPUT_ROUTING_ROUTED_EDIT
    )
    history = ModificationDatasetCollector(tmp_path, "test")
    history.record_text_request(request)
    try:
        worker.submit(request)
        assert done.wait(5)
    finally:
        worker.close(wait=True)
    (result,) = results
    assert not calls and not result.error and not result.used_llm and result.final_text == expected
    history.record_llm_result(1, result)
    record = history._interaction_data(history.interaction_id_for_session(7))["llm"]["requests"][0]
    assert (
        record["candidate_text"] == expected
        and record["system_prompt"] == ""
        and record["edit_steps"] == []
    )
