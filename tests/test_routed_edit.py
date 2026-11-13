"""Production one-call route/body contract, dispatch isolation and evidence."""
import io
import json
import threading
from types import SimpleNamespace

import pytest

from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import LLMSettings, TextProcessingRequest, INPUT_ROUTING_ROUTED_EDIT
from proximic_ring.text_processing.routed_edit_processing import parse_routed_edit_response
from proximic_ring.text_processing.routed_edit_prompts import single_pass_prompt
from proximic_ring.text_processing.worker import TextProcessingWorker
from test_shared_voice_capture import raw_capture, finish_asr


def settings():
    return LLMSettings(enabled=True, provider="volcengine", model="deepseek-test",
                       base_url="https://example.test/api/v3", api_key="test-key", api_key_env="")


def processor_returning(text, *, status="completed"):
    calls = []
    def respond(request, **kwargs):
        calls.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"status":status, "output":[{"type":"message",
            "content":[{"type":"output_text", "text":text}]}]},ensure_ascii=False).encode())
    return OpenAICompatibleTextProcessor(urlopen=respond), calls


@pytest.mark.parametrize("source,output,tasks,body", [
    ("周六见。", "分类：修改、插入\n正文：\n周日见。谢谢。", ("modify","insert"), "周日见。谢谢。"),
    ("", "分类：按目标创作\n正文：你好。", ("compose",), "你好。"),
    ("旧文", "分类：修改\n正文：", ("modify",), ""),
    (" 原文 \n", "分类：修改\n正文：\n 新文 \n", ("modify",), " 新文 \n"),
    ("旧", "分类：格式整理\n正文：\n  新\n正文：内层文字  \n", ("format",), "  新\n正文：内层文字  \n"),
])
def test_one_request_returns_plan_and_exact_body(source, output, tasks, body):
    processor,calls=processor_returning(output)
    final,raw,model_input,plan=processor.process_routed_edit_with_trace("口述",settings(),source,{"messages":[]})
    assert final==body and raw==output and (plan.tasks if plan else None)==tasks
    assert len(calls)==1 and calls[0]["thinking"]=={"type":"disabled"} and calls[0]["stream"] is False
    assert "tools" not in calls[0]
    assert calls[0]["input"][0]["content"][0]["text"] == single_pass_prompt(source)
    assert json.loads(calls[0]["input"][1]["content"][0]["text"])==model_input
    assert set(model_input)=={"utterance","editable_text","screen_context"}
    assert model_input["editable_text"]==source


@pytest.mark.parametrize("output,source", [
    ("分类：修改、释字拼写\n正文：新", "旧"),
    ("分类：修改、修改\n正文：新", "旧"),
    ("分类：修改\n正文：新", ""),
    ("分类：无可执行编辑\n正文：新", "旧"),
    ("分类：无可执行编辑\n正文：旧", "旧"),
    ("分类：无可执行编辑、润色\n正文：旧", "旧"),
    ("分类：听写\n正文：旧", "旧"),
    ("```\n分类：修改\n正文：新\n```", "旧"),
])
def test_invalid_response_never_retries_or_guesses(output,source):
    processor,calls=processor_returning(output)
    with pytest.raises(RuntimeError):
        processor.process_routed_edit_with_trace("修改",settings(),source)
    assert len(calls)==1


def test_incomplete_response_and_unconfigured_model_do_not_write_or_fallback():
    processor,calls=processor_returning("分类：修改\n正文：截断",status="incomplete")
    with pytest.raises(RuntimeError,match="未完整结束"):
        processor.process_routed_edit_with_trace("修改",settings(),"旧")
    assert len(calls)==1
    with pytest.raises(ValueError,match="火山方舟"):
        processor.process_routed_edit_with_trace("修改",LLMSettings(enabled=True,model="other"),"旧")
    assert len(calls)==1


def test_prompt_treats_manual_intent_as_confirmed_for_every_available_category():
    from proximic_ring.text_processing.routed_edit_prompts import ROUTED_EDIT_TASK_RULES
    for source in ("原文", ""):
        prompt=single_pass_prompt(source)
        for task,rules in ROUTED_EDIT_TASK_RULES.items():
            assert (rules in prompt)==(task!="modify" or bool(source))
        for abstention in ("无可执行编辑", "排除条件", "选 skip", "保留对应原文", "不执行该项操作", "目标语言缺失且无法确定时不猜测"):
            assert abstention not in prompt
        assert "从本次可用类别中选择至少一项，并完成编辑" in prompt
        assert "所有类别都按已经确认的编辑目标主动执行" in prompt
        assert "只执行用户实际要求且你选中的任务" in prompt
        assert "不得把聊天历史当作 editable_text" in prompt
        assert "submit_text_edit" not in prompt and "不要 JSON" in prompt


def test_worker_filters_ocr_once_and_history_keeps_actual_plan(tmp_path):
    from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
    processor,calls=processor_returning("分类：修改\n正文：请联系张伟")
    done=threading.Event(); results=[]
    worker=TextProcessingWorker(processor=processor,
        intent_router=SimpleNamespace(classify=lambda *a,**k:pytest.fail("Clef called"),close=lambda:None),
        context_provider=SimpleNamespace(close=lambda:None),
        on_result=lambda r:(results.append(r),done.set()))
    request=TextProcessingRequest(1,7,"edit","改成上文的人名",settings(),"请联系张玮",
        routing_mode=INPUT_ROUTING_ROUTED_EDIT,screen_context=raw_capture())
    store=ModificationDatasetCollector(tmp_path,"test")
    store.record_screen_capture(7,request.screen_context,routing=INPUT_ROUTING_ROUTED_EDIT)
    finish_asr(store);store.record_text_request(request)
    try:
        worker.submit(request); assert done.wait(5)
    finally:worker.close(wait=True)
    result=results[0]; assert not result.error and len(calls)==1
    assert result.edit_plan.tasks==("modify",) and result.routing_mode==INPUT_ROUTING_ROUTED_EDIT
    assert result.model_input["screen_context"]["messages"]==[{"speaker":"other","text":"请联系张伟"}]
    assert "screenshot_png" not in json.dumps(calls,ensure_ascii=False)
    assert "联系人" not in json.dumps(result.model_input,ensure_ascii=False)
    store.record_llm_result(1,result)
    record=store._interaction_data(store.interaction_id_for_session(7))
    assert record["mode"]["routing"]==INPUT_ROUTING_ROUTED_EDIT and record["mode"]["edit_plan"]=={"tasks":["modify"]}
    assert len(record["llm"]["requests"])==1 and "router_trace" not in record["mode"]
    assert record["llm"]["requests"][0]["request_input"]==result.model_input
    assert len(record["screen_evidence"]["ocr_lines"])==4
    assert "ocr_filter_lines" in record["screen_evidence"]
    details=store.load_details(store.interaction_id_for_session(7))
    assert "手动＋编辑路由" in details["summary"]
    assert details["sections"][0]["items"][0]["classificationText"] == "修改"


@pytest.mark.parametrize("output,status,classification,reason", [
    ("分类：修改、修改\n正文：新", "completed", "修改、修改", "编辑任务重复"),
    ("分类：润色\n正文：未完", "incomplete", "润色", "模型输出不完整"),
    ("分类：无可执行编辑\n正文：旧", "completed", "无可执行编辑", "用户已确认编辑，但模型未选择编辑类别"),
])
def test_failed_request_keeps_returned_classification_and_reason_in_history(tmp_path, output, status, classification, reason):
    from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
    processor, calls = processor_returning(output, status=status)
    done = threading.Event(); results = []
    worker = TextProcessingWorker(processor=processor,
        on_result=lambda result: (results.append(result), done.set()),
        intent_router=SimpleNamespace(close=lambda: None), context_provider=SimpleNamespace(close=lambda: None))
    request = TextProcessingRequest(1, 7, "edit", "帮我改好一点", settings(), "旧",
                                    routing_mode=INPUT_ROUTING_ROUTED_EDIT)
    store = ModificationDatasetCollector(tmp_path, "test")
    store.record_text_request(request)
    try:
        worker.submit(request)
        assert done.wait(5)
    finally:
        worker.close(wait=True)
    assert len(calls) == 1 and results[0].error
    store.record_llm_result(1, results[0])
    sections = store.load_details(store.interaction_id_for_session(7))["sections"]
    assert sections[0]["items"][0]["classificationText"] == classification
    row = sections[1]["items"][0]
    assert row["errorText"] == reason
    assert row["outputVisible"] is False  # The worker's source fallback is not a model result.


def test_cancelled_running_single_pass_does_not_publish_result():
    started,release,delivered=threading.Event(),threading.Event(),threading.Event()
    class Processor:
        def process_routed_edit_with_trace(self,*args,**kwargs):
            started.set();release.wait(5)
            from proximic_ring.text_processing.task_routing import EditPlan
            return "新文","分类：润色\n正文：新文",{},EditPlan(("polish",))
    worker=TextProcessingWorker(processor=Processor(),on_result=lambda r:delivered.set(),
        intent_router=SimpleNamespace(close=lambda:None),context_provider=SimpleNamespace(close=lambda:None))
    worker.submit(TextProcessingRequest(11,7,"edit","不用改",settings(),"原文",routing_mode=INPUT_ROUTING_ROUTED_EDIT))
    assert started.wait(5)
    worker.cancel_request(11);release.set();worker.close(wait=True)
    assert not delivered.is_set()
