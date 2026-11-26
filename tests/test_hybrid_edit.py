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


@pytest.mark.parametrize("utterance,replies,expected_models,route_fragment", [
    ("删除最后一句", [], [], "规则直接处理"),
    ("把交图改成审图算了别改了", [], [], "句尾被明确撤回"),
    ("把交图改成审图", [{"decision": "keep", "modified_text": ""}], ["复核替换初稿"], "采用替换初稿"),
    ("改成审图", [{"index": 1}, {"decision": "keep", "modified_text": ""}],
     ["选择候选位置", "复核替换初稿"], "规则筛选位置 → 模型选择候选位置 → 规则生成初稿"),
    ("改成审图", [{"index": 0}], ["选择候选位置"], "保留原文"),
    ("把交图改成审图然后分两行",
     [{"decision": "keep", "modified_text": ""}, {"modified_text": "先审图。\n再核对地图。"}],
     ["复核替换初稿", "补做剩余要求"], "模型补做剩余要求"),
    ("把这句话说得客气一点", [{"modified_text": "烦请先交图，再核对地图。"}], ["生成编辑正文"], "模型生成编辑正文"),
    ("把第二项改成审图", [{"decision": "keep", "old": "", "new": "", "occurrence": 0, "tail": ""}],
     ["定位指定次序的原词"], "保留原文"),
    ("帮我回复对方", [{"modified_text": ""}, {"modified_text": "收到，我会先交图。"}],
     ["拟写回复", "按校验反馈重做"], "模型拟写回复 → 模型按校验反馈重做"),
    ("改成审图", [{"index": "1"}], ["选择候选位置"], "模型选择候选位置"),
])
def test_saved_pipeline_matches_executed_steps(tmp_path, monkeypatch, utterance, replies, expected_models, route_fragment):
    from itertools import count
    from proximic_ring.text_processing.hybrid_edit import trace

    # Equal clock ticks at real boundaries expose overlaps or missing intervals.
    monkeypatch.setattr(trace, "perf_counter_ns", count(0, 1_250_000).__next__)
    processor, calls = service(*replies)
    request = TextProcessingRequest(20, 7, "edit", utterance, settings(), "先交图。再核对地图。",
                                    routing_mode=INPUT_ROUTING_ROUTED_EDIT)
    history = ModificationDatasetCollector(tmp_path, "test")
    history.record_text_request(request)
    done = threading.Event()

    def save(result):
        history.record_llm_result(result.request_id, result)
        done.set()

    worker = TextProcessingWorker(processor=processor, on_result=save)
    try:
        worker.submit(request)
        assert done.wait(5)
    finally:
        worker.close(wait=True)

    interaction = history.interaction_id_for_session(7)
    history = ModificationDatasetCollector(tmp_path, "test")
    saved = history._interaction_data(interaction)["llm"]["requests"][0]
    steps = saved["edit_pipeline"]["steps"]
    assert len(calls) == len(expected_models)
    assert [s["label"] for s in steps if s["kind"] == "model"] == expected_models
    assert all(s["duration_ms"] == 1.25 for s in steps)
    assert all(s["status"] == "completed" for s in steps[:-1])
    invalid = replies == [{"index": "1"}]
    assert steps[-1]["status"] == ("failed" if invalid else "completed")
    assert steps[0]["kind"] == "context" and steps[-1]["kind"] == "rule"
    decision = history.load_details(interaction)["sections"][0]["items"][0]
    assert route_fragment in decision["text"]
    assert len(decision["pipelineSteps"]) == len(steps)
    assert all("1.25 毫秒" in row["time"] for row in decision["pipelineSteps"])
    assert ("失败" in decision["pipelineSteps"][-1]["time"]) == invalid


def test_cancelled_model_wait_keeps_only_elapsed_steps(tmp_path, monkeypatch):
    from proximic_ring.text_processing.hybrid_edit import trace

    now = [0]
    monkeypatch.setattr(trace, "perf_counter_ns", lambda: now[0])
    started, release, delivered = threading.Event(), threading.Event(), threading.Event()

    def pause():
        started.set()
        assert release.wait(5)

    history = ModificationDatasetCollector(tmp_path, "test")
    processor, calls = service({"index": 1}, after_call=pause)
    request = TextProcessingRequest(5, 7, "edit", "改成审图", settings(), "先交图。",
                                    routing_mode=INPUT_ROUTING_ROUTED_EDIT)
    history.record_text_request(request)
    worker = TextProcessingWorker(processor=processor, on_result=lambda _: delivered.set())
    try:
        worker.submit(request)
        assert started.wait(5)
        now[0] = 2_500_000_000
        worker.cancel_request(5)
        history.finish_pending_llm_request(5, reason="手动取消")
        now[0] += 5_000_000_000
    finally:
        release.set()
        worker.close(wait=True)
    assert not delivered.is_set() and len(calls) == 1
    interaction = history.interaction_id_for_session(7)
    saved = history._interaction_data(interaction)["llm"]["requests"][0]
    assert saved["status"] == "cancelled"
    step = saved["edit_pipeline"]["steps"][-1]
    assert step == {"kind": "model", "label": "选择候选位置", "duration_ms": 2500, "status": "cancelled"}
    assert request.edit_pipeline.snapshot() == saved["edit_pipeline"]
    row = history.load_details(interaction)["sections"][0]["items"][0]
    assert row["pipelineSteps"][-1]["time"] == "2.50 秒 · 已中止"
