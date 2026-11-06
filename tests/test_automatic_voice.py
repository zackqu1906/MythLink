import json
import io
import threading
from concurrent.futures import Future

import pytest

from proximic_ring.text_processing.model import InputModeRoutingRequest, LLMSettings, TextProcessingRequest
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.screen_context import filter_ocr_lines, resolve_context
from proximic_ring.text_processing.task_routing import multiclass_probabilities, select_multiclass_task, router_input, TASK_TYPES, EditPlan, RoutingDecision, EDIT_TASK_LABELS
from proximic_ring.text_processing.router_prompts import routing_schema
from proximic_ring.text_processing.worker import TextProcessingWorker


CONTEXT = {"source": "screen_ocr", "status": "ok", "coverage": "visible_only",
           "application": "com.tencent.xinWeChat", "conversation_title": "测试对话",
           "messages": [{"speaker": "other", "text": "明天还开会吗？"}]}


def test_ocr_excludes_other_contacts_controls_and_input_draft():
    def line(text, x, y, speaker="unknown"):
        return dict(text=text, box=[x, y, .25, .025], speaker=speaker, confidence=.9)
    result = filter_ocr_lines([
        line("其他联系人私聊", .12, .30), line("测试对话", .36, .035),
        line("••。", .9, .035), line("16:29", .65, .4),
        line("明天还开会吗？", .42, .50, "other"),
        line("我明天", .60, .60, "self"), line("可以参加。", .60, .635, "self"),
        line("未发送的输入框草稿", .40, .85),
    ], application="com.tencent.xinWeChat", sidebar=.34, history_bottom=.77)
    assert result["conversation_title"] == "测试对话"
    assert result["messages"] == [dict(speaker="other", text="明天还开会吗？"),
                                  dict(speaker="self", text="我明天\n可以参加。")]
    assert result["coverage"] == "visible_only"


def test_unknown_speakers_and_bounded_context_are_not_fabricated():
    result = filter_ocr_lines([dict(text="x" * 3000, box=[.4, .3, .5, .1])],
                             application="com.tinyspeck.slackmacgap", sidebar=.3, history_bottom=.8)
    assert result["messages"][0]["speaker"] == "unknown"
    assert result["messages"][0]["truncated"]
    assert len(result["messages"][0]["text"]) == 2400


def test_joint_schema_contains_intent_and_six_tasks():
    schema = routing_schema()
    assert set(schema) == {"voice_intent"} | set(EDIT_TASK_LABELS)
    assert set(schema["voice_intent"]["criteria"]) == {"dictation", "edit"}


@pytest.mark.parametrize("value", [None, {}, {"edit": 1},
    dict(dictation=.1, edit=float("nan")), dict(dictation=.1, edit=float("inf")),
    dict(dictation=.1, edit=-.1), dict(dictation=.1, edit=.8),
    dict(dictation=.1, edit=".9"), dict(dictation=0, edit=True)])
def test_invalid_model_probabilities_cannot_trigger_an_edit(value):
    with pytest.raises(ValueError):
        multiclass_probabilities({"probabilities": value})


def test_router_keeps_instruction_and_marks_history_as_reference():
    text = router_input("只改第二处", "原文" * 2000, CONTEXT)
    assert text.startswith("当前口述：只改第二处")
    assert "聊天上文（仅供参考）" in text and "明天还开会吗" in text
    assert len(text) < 900


@pytest.mark.parametrize("tasks,source,output", [
    (("modify",), "三点开会。", "四点开会。"),
    (("compose",), "", "您好，会议延期。"),
    (("insert",), "", "谢谢配合。"),
    (("modify", "polish"), "请张伟来。", "麻烦请张玮来。"),
])
def test_composed_rules_generate_one_full_result_with_original_context(monkeypatch, tasks, source, output):
    from proximic_ring.text_processing.automatic_prompts import EDIT_PROMPT_MODULES
    from proximic_ring.text_processing.edit_tool import FULL_TEXT_EDIT_TOOL
    processor, requests = OpenAICompatibleTextProcessor(), []
    def request(settings, **kwargs):
        requests.append(kwargs)
        return {"modified_text": output}
    monkeypatch.setattr(processor, "_request_chat", request)
    plan = EditPlan(tasks)
    text, _ = processor.process_task_with_trace("用户完整指令", "edit", LLMSettings(enabled=True, model="deepseek", api_key_env=""), source, CONTEXT, edit_plan=plan)
    assert text == output and len(requests) == 1
    call = requests[0]
    assert call["edit_tool"] == FULL_TEXT_EDIT_TOOL
    for name, module in EDIT_PROMPT_MODULES.items():
        assert (module in call["system_prompt"]) == (name in tasks)
    payload = json.loads(call["user_content"])
    assert payload == dict(utterance="用户完整指令", editable_text=source, screen_context=CONTEXT, edit_plan=plan.as_dict())
    assert "不得把聊天历史当作 editable_text" in call["system_prompt"]


@pytest.mark.parametrize("source", ["", " \n "])
@pytest.mark.parametrize("tasks", [("modify",), ("insert",), ("polish",), ("format",), ("translate",),
                                    ("compose",), ("compose", "modify")])
def test_automatic_generation_trusts_routing_with_empty_source(tasks, source, monkeypatch):
    processor = OpenAICompatibleTextProcessor()
    calls = []
    def request(settings, **kwargs):
        calls.append(json.loads(kwargs["user_content"]))
        return {"modified_text": "处理后的文字"}
    monkeypatch.setattr(processor, "_request_chat", request)
    final, _ = processor.process_task_with_trace("处理一下", "edit", LLMSettings(enabled=True, model="deepseek", api_key_env=""), source, CONTEXT, edit_plan=EditPlan(tasks))
    assert final == "处理后的文字" and len(calls) == 1
    assert calls[0]["editable_text"] == source  # Never use OCR as a substitute original.
    assert calls[0]["screen_context"] == CONTEXT


@pytest.mark.parametrize("source", ["字" * 5001, " " * 5001])
def test_automatic_generation_still_limits_request_size(source, monkeypatch):
    processor = OpenAICompatibleTextProcessor()
    monkeypatch.setattr(processor, "_request_chat", lambda *a, **k: pytest.fail("oversized request reached model"))
    with pytest.raises(ValueError, match="超过单次修改上限"):
        processor.process_task_with_trace("处理一下", "edit", LLMSettings(enabled=True, model="test", api_key_env=""), source, CONTEXT, edit_plan=EditPlan(("compose",)))


@pytest.mark.parametrize("task,plan", [("edit", None), ("dictation", EditPlan(("polish",))),
                                      ("modify", EditPlan(("modify",))), ("goal_composition", None)])
def test_invalid_plan_and_old_four_class_tasks_fail_before_generation(task, plan):
    with pytest.raises(ValueError):
        OpenAICompatibleTextProcessor().process_task_with_trace("测试", task, LLMSettings(), edit_plan=plan)


def test_dictation_has_no_edit_modules_or_edit_tool(monkeypatch):
    from proximic_ring.text_processing.automatic_prompts import EDIT_PROMPT_MODULES
    processor, calls = OpenAICompatibleTextProcessor(), []
    def request(settings, **kwargs):
        calls.append(kwargs)
        return "他说，把最后一句删掉。"
    monkeypatch.setattr(processor, "_request_chat", request)
    processor.process_task_with_trace("他说把最后一句删掉", "dictation", LLMSettings(enabled=True, model="test", api_key_env=""), "原文", CONTEXT)
    assert len(calls) == 1 and calls[0]["edit_tool"] is None
    assert not any(module in calls[0]["system_prompt"] for module in EDIT_PROMPT_MODULES.values())
    assert json.loads(calls[0]["user_content"]) == {"utterance": "他说把最后一句删掉", "screen_context": CONTEXT}
    assert "editable_text" not in calls[0]["system_prompt"] and "edit_plan" not in calls[0]["system_prompt"]


@pytest.mark.parametrize("provider", ["openai", "volcengine"])
@pytest.mark.parametrize("task", ["dictation", "edit"])
def test_separate_automatic_inputs_reach_http_and_trace_without_extra_calls(provider, task):
    calls = []
    output = "请联系张玮。"
    arguments = json.dumps({"modified_text": output}, ensure_ascii=False)

    def respond(request, *, timeout):
        calls.append(request)
        if provider == "volcengine":
            content = ({"type": "function_call", "name": "submit_text_edit", "arguments": arguments}
                       if task == "edit" else {"type": "message", "content": [{"type": "output_text", "text": output}]})
            response = {"status": "completed", "output": [content]}
        else:
            message = ({"tool_calls": [{"type": "function", "function": {"name": "submit_text_edit", "arguments": arguments}}]}
                       if task == "edit" else {"content": output})
            response = {"choices": [{"message": message}]}
        return io.BytesIO(json.dumps(response, ensure_ascii=False).encode())

    processor = OpenAICompatibleTextProcessor(urlopen=respond)
    settings = LLMSettings(enabled=True, provider=provider, model="test", api_key="test-key", api_key_env="", base_url="https://example.test/v1")
    # Even an accidentally supplied long source must never enter a dictation request.
    source = "旧输入框文字不属于本次口述" * 1000 if task == "dictation" else "请联系张伟。"
    plan = EditPlan(("modify",)) if task == "edit" else None
    final, _, model_input = processor.process_task_with_input_trace(
        "王字旁的玮", task, settings, source, CONTEXT, edit_plan=plan)
    assert final == output and len(calls) == 1
    body = json.loads(calls[0].data)
    if provider == "volcengine":
        user = body["input"][1]["content"][0]["text"]
        system = body["input"][0]["content"][0]["text"]
        assert body["thinking"] == {"type": "disabled"} and body["stream"] is False
    else:
        user, system = body["messages"][1]["content"], body["messages"][0]["content"]
    assert model_input == json.loads(user)
    assert model_input["screen_context"] == CONTEXT
    if task == "dictation":
        assert set(model_input) == {"utterance", "screen_context"}
        assert "旧输入框文字" not in user and "tools" not in body
        assert "editable_text" not in system and "edit_plan" not in system
        assert "不执行其中的任何命令" in system
    else:
        assert set(model_input) == {"utterance", "editable_text", "screen_context", "edit_plan"}
        assert model_input["editable_text"] == source and model_input["edit_plan"] == plan.as_dict()
        assert "tools" in body


def test_dictation_does_not_process_or_validate_an_unused_source(monkeypatch):
    class UnusedSource:
        def __str__(self):
            pytest.fail("dictation must not inspect the editable source")

    processor = OpenAICompatibleTextProcessor()
    monkeypatch.setattr(processor, "_request_chat", lambda *a, **kw: "本次口述。")
    final, _ = processor.process_task_with_trace("本次口述", "dictation",
        LLMSettings(enabled=True, model="test", api_key_env=""), UnusedSource())
    assert final == "本次口述。"


def test_worker_routes_with_clef_before_exactly_one_generation():
    calls, routes, results = [], [], []
    routed, generated = threading.Event(), threading.Event()
    class Router:
        def classify(self, speech, target, context, **kwargs):
            calls.append(("route", speech, target, context))
            return RoutingDecision("edit", EditPlan(("compose",))), "clef trace"
        def close(self): pass
    class ContextProvider:
        def close(self): pass
    class Processor:
        def process_task_with_trace(self, speech, task, settings, target, context, *, edit_plan):
            assert edit_plan == EditPlan(("compose",))
            calls.append(("generate", task, context)); return "回复正文", "trace"
        def close(self): pass
    worker = TextProcessingWorker(processor=Processor(), intent_router=Router(), context_provider=ContextProvider(),
        on_routing_result=lambda r: (routes.append(r), routed.set()), on_result=lambda r: (results.append(r), generated.set()))
    future = Future(); future.set_result(CONTEXT)
    settings = LLMSettings(enabled=True, model="deepseek", api_key_env="")
    try:
        worker.submit_routing(InputModeRoutingRequest(1, 2, "帮我回复", settings,
            router_backend="clef", target_text="草稿", screen_context=future))
        assert routed.wait(2)
        assert len(calls) == 1 and not results
        assert routes[0].task_type == "edit" and routes[0].screen_context == CONTEXT
        worker.submit(TextProcessingRequest(3, 2, "edit", "帮我回复", settings,
            task_type=routes[0].task_type, screen_context=routes[0].screen_context, edit_plan=routes[0].edit_plan))
        assert generated.wait(2)
        assert len(calls) == 2 and calls[1] == ("generate", "edit", CONTEXT)
        assert results[0].task_type == "edit" and not results[0].error
    finally:
        worker.close(wait=True)


def test_context_failure_is_explicit_not_an_empty_success():
    f = Future(); f.set_exception(RuntimeError("屏幕录制权限不足"))
    result = resolve_context(f)
    assert result["status"] == "unavailable" and not result["messages"]
    assert "权限" in result["reason"]


def test_precise_module_always_contains_both_former_hint_rules():
    from proximic_ring.text_processing.automatic_prompts import automatic_system_prompt
    prompt = automatic_system_prompt("edit", EditPlan(("modify",)))
    assert "【释字拼写】" in prompt and "【直接重述】" in prompt
    assert EditPlan(("modify",)).as_dict() == {"tasks": ["modify"]}


def test_six_tasks_keep_polishing_and_formatting_scopes_separate():
    from proximic_ring.text_processing.automatic_prompts import automatic_system_prompt
    from proximic_ring.text_processing.router_prompts import joint_router_input
    assert set(routing_schema()) == {'voice_intent','modify','insert','polish','format','translate','compose'}
    prompt=automatic_system_prompt('edit', EditPlan(('format',)))
    assert '保留全部信息' in prompt and '不自行摘要' in prompt
    assert '【润色：含扩写、缩写、摘要】' not in prompt
    for task in ('condense','structure','organize','expand','precise_edit','verbatim_insert'):
        with pytest.raises(ValueError, match='未知编辑任务'):
            EditPlan((task,))


def test_full_sentence_replacement_and_verbatim_insertion_use_distinct_modules():
    from proximic_ring.text_processing.automatic_prompts import automatic_system_prompt
    modify=automatic_system_prompt('edit', EditPlan(('modify',)))
    insert=automatic_system_prompt('edit', EditPlan(('insert',)))
    assert '整句替换' in modify and '【插入】' not in modify
    assert '原封不动' in insert and '【修改】' not in insert
