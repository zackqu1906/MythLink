import base64
import json
from pathlib import Path
import threading
from concurrent.futures import Future

import numpy as np
import pytest

from proximic_ring.text_processing.intent_router import ClefIntentRouter
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import InputModeRoutingRequest, InputModeRoutingResult, LLMSettings, TextProcessingRequest
from proximic_ring.text_processing.screen_context import filter_ocr_lines, resolve_context
from proximic_ring.text_processing.worker import TextProcessingWorker
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from proximic_ring.voice.automatic_history import automatic_history_details


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAACXBIWXMAAA9hAAAPYQGoP6dpAAAADElEQVQImWP4//8/AAX+Av5Y8msOAAAAAElFTkSuQmCC")


def test_unavailable_modify_is_distinct_from_skip_in_history(tmp_path):
    from proximic_ring.text_processing.task_routing import task_availability
    trace = {'classification_type':'joint', 'task':'dictation', 'task_availability':task_availability(''),
             'stages':[{'stage':'joint', 'answers':{
                 'voice_intent':{'choice':'dictation','probabilities':{'dictation':.9,'edit':.1}},
                 'insert':{'choice':'skip','probabilities':{'apply':.1,'skip':.9}}}}]}
    details = automatic_history_details(tmp_path, {'mode':{'routing':'auto','router_trace':trace}})
    line = details['probabilityText'].splitlines()[0]
    assert line == '修改：不可用（无可编辑原文，未参与分类，无模型概率）'
    assert '%' not in line and not details['warningText']


def test_empty_source_history_saves_reduced_request_schema(tmp_path):
    collector = ModificationDatasetCollector(tmp_path / 'dataset', 'anonymous-1')
    request = InputModeRoutingRequest(901, 7, '我叫张伟', LLMSettings(), router_backend='clef', target_text='')
    collector.record_routing_request(request)
    record = collector._interaction_data(collector._routing_interactions[901])
    assert 'modify' not in record['mode']['router_schema']
    assert len(record['mode']['router_schema']) == 6


def context_fixture():
    context = filter_ocr_lines([
        {"text": "16:29", "box": [.5, .3, .1, .02], "confidence": .99},
        {"text": "请联系张伟", "box": [.4, .4, .3, .02], "confidence": .97, "speaker": "other"},
    ], application="com.tencent.xinWeChat", sidebar=.3, history_bottom=.76)
    context.update(capture_id="a" * 32, capture_ms=20, ocr_ms=70, layout={"sidebar": .3, "history_bottom": .76})
    context.evidence.update(screenshot_png=PNG, captured_at="2026-11-03T03:00:00+00:00", image_width=1, image_height=1)
    return context


def classify(context, *, task="edit", fail_joint=False, conflict=False):
    calls = []
    def open_request(request, timeout):
        payload = json.loads(request.data)
        calls.append(payload)
        if fail_joint:
            raise OSError("offline")
        answers = {}
        for name, question in payload["questions"].items():
            selected = task if name == "voice_intent" else "apply" if (task == "edit" or conflict) and name == "modify" else "skip"
            answers[name] = {"type": "choice", "choice": selected,
                             "probabilities": {key: .9 if key == selected else .1 for key in question["criteria"]}}
        response = {"answers": answers, "model": "clef-test"}
        return Response(response)
    router = ClefIntentRouter(endpoint="http://localhost/v1/systemone", urlopen=open_request)
    try:
        decision, trace = router.classify("王字旁的玮", "请联系张伟", context)
        return decision, trace, calls
    except RuntimeError as exc:
        return None, exc.model_output, calls


class Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, _size):
        return self.data


def record_example(tmp_path, *, task="edit", fail_joint=False, conflict=False, context=None):
    collector = ModificationDatasetCollector(tmp_path, "test-user")
    collector.record_audio(7, np.zeros(1600, dtype=np.float32))
    context = context_fixture() if context is None else context
    decision, trace, calls = classify(context, task=task, fail_joint=fail_joint, conflict=conflict)
    request = InputModeRoutingRequest(10, 7, "王字旁的玮", LLMSettings(), router_backend="clef", target_text="请联系张伟")
    collector.record_routing_request(request)
    result = InputModeRoutingResult(10, 7, request.raw_text, decision.task if decision else "dictation", .15,
        task_type=decision.task if decision else "", edit_plan=decision.edit_plan if decision else None,
        error="联合分类连接失败" if fail_joint else None, model_output=trace, screen_context=context)
    collector.record_routing_result(result)
    directory = collector._interaction_dir(collector._routing_interactions[10])
    return collector, directory, result, calls


def test_disabled_cleanup_is_recorded_without_fabricated_model_inputs(tmp_path):
    collector, directory, route, _ = record_example(tmp_path, task="dictation")
    collector.record_dictation_cleanup(route.session_id, False)
    saved = json.loads((directory / "record.json").read_text())
    assert saved["mode"]["dictation_cleanup_enabled"] is False
    assert saved["llm"]["requests"] == []
    details = collector.load_entries()[0]["automaticDetails"]
    assert "听写整理已关闭" in details["generationOcrStatus"]
    assert "未调用正文模型" in details["generationOcrStatus"]
    assert "直接使用 ASR" in details["contextText"]
    assert details["routerOcrText"] == "other: 请联系张伟"
    assert details["generationOcrText"] == "未记录" and not details["modelInput"]


@pytest.mark.parametrize("task", ["dictation", "edit"])
@pytest.mark.parametrize("text_size", [20, 110])
def test_history_saves_both_exact_model_ocr_versions(tmp_path, monkeypatch, task, text_size):
    context = context_fixture()
    context["messages"] = [{"speaker": "other", "text": f"第{i}条" + "字" * text_size} for i in range(6)]
    expected_context = json.loads(json.dumps(context))
    expected_excerpt = "\n".join("other: " + m["text"] for m in context["messages"][-4:])[-360:]
    collector, directory, route, routing_calls = record_example(tmp_path, task=task, context=context)
    # Recording a pending future must not block or serialize the Future object.
    pending_context = Future()
    request = TextProcessingRequest(11, 7, task, route.raw_text,
        LLMSettings(enabled=True, model="test", api_key_env=""), target_text="请联系张伟",
        task_type=task, edit_plan=route.edit_plan, screen_context=pending_context)
    collector.record_text_request(request)
    queued = json.loads((directory / "record.json").read_text())["llm"]["requests"][0]
    if task == "dictation":
        assert set(queued["input"]) == {"utterance", "screen_context"}
        assert "editable_text" not in queued["system_prompt"] and "edit_plan" not in queued["system_prompt"]
    assert "尚未取得" in collector.load_entries()[0]["automaticDetails"]["generationOcrStatus"]
    pending_context.set_result(context)
    processor, calls, results, done = OpenAICompatibleTextProcessor(), [], [], threading.Event()

    def respond(settings, **kwargs):
        calls.append(json.loads(kwargs["user_content"]))
        # A later context change must not rewrite either saved input snapshot.
        context["messages"][0]["text"] = "后续变更，不属于这次模型输入"
        return "请联系张伟" if task == "dictation" else {"modified_text": "请联系张玮"}

    def completed(result):
        results.append(result)
        collector.record_llm_result(result.request_id, result)
        done.set()

    monkeypatch.setattr(processor, "_request_chat", respond)
    worker = TextProcessingWorker(processor=processor, on_result=completed)
    try:
        worker.submit(request)
        assert done.wait(3)
    finally:
        worker.close(wait=True)
    assert results[0].error is None
    assert len(calls) == len(routing_calls) == 1
    record = json.loads((directory / "record.json").read_text())
    assert record["mode"]["router_ocr_text"] == expected_excerpt
    assert routing_calls[0]["state"].endswith("聊天上文（仅供参考）：" + expected_excerpt)
    saved = record["llm"]["requests"][0]
    assert saved["request_input"] == saved["input"] == calls[0]
    if task == "dictation":
        assert set(saved["input"]) == {"utterance", "screen_context"}
    else:
        assert saved["input"]["editable_text"] == "请联系张伟"
        assert saved["input"]["edit_plan"] == route.edit_plan.as_dict()
    assert saved["screen_context"] == expected_context
    # A fresh reader verifies persistence, rather than only the in-memory view.
    details = ModificationDatasetCollector(tmp_path, "test-user").load_entries()[0]["automaticDetails"]
    assert details["routerOcrText"] == expected_excerpt
    assert json.loads(details["generationOcrText"]) == expected_context
    assert details["generationOcrTitle"] == ("听写整理 OCR" if task == "dictation" else "编辑生成 OCR")
    assert "screenshot_png" not in details["generationOcrText"]
    assert "后续变更" not in details["generationOcrText"]


@pytest.mark.parametrize("failure", ["validation", "network"])
def test_generation_failure_does_not_invent_a_sent_ocr_request(tmp_path, monkeypatch, failure):
    collector, directory, route, _ = record_example(tmp_path)
    processor, calls, results, done = OpenAICompatibleTextProcessor(), [], [], threading.Event()

    def respond(settings, **kwargs):
        calls.append(json.loads(kwargs["user_content"]))
        raise OSError("offline")

    def completed(result):
        results.append(result)
        collector.record_llm_result(result.request_id, result)
        done.set()

    monkeypatch.setattr(processor, "_request_chat", respond)
    request = TextProcessingRequest(11, 7, "edit", route.raw_text,
        LLMSettings(enabled=True, model="test", api_key_env=""),
        target_text="字" * 5001 if failure == "validation" else "请联系张伟",
        task_type="edit", edit_plan=route.edit_plan, screen_context=route.screen_context)
    collector.record_text_request(request)
    worker = TextProcessingWorker(processor=processor, on_result=completed)
    try:
        worker.submit(request)
        assert done.wait(3)
    finally:
        worker.close(wait=True)
    assert results[0].error
    details = collector.load_entries()[0]["automaticDetails"]
    saved = json.loads((directory / "record.json").read_text())["llm"]["requests"][0]
    if failure == "validation":
        assert calls == [] and saved["request_input"] is None
        assert details["generationOcrText"] == "未记录"
        assert "请求输入构造前" in details["generationOcrStatus"]
    else:
        assert saved["request_input"] == calls[0]
        assert "请联系张伟" in details["generationOcrText"]
        assert "请求失败" in details["generationOcrStatus"]


def test_empty_and_missing_ocr_and_legacy_inputs_are_distinct(tmp_path):
    collector, _, _, _ = record_example(tmp_path, context={"messages": [], "status": "unavailable"})
    details = collector.load_entries()[0]["automaticDetails"]
    assert "无 OCR 文字" in details["routerOcrText"]
    assert "未进入正文处理" in details["generationOcrStatus"]
    legacy = {"mode": {"routing": "auto", "screen_context": {"messages": [{"text": "不可据此重建"}]}},
              "llm": {"requests": [{"task_type": "dictation", "status": "completed",
                                    "input": {"screen_context": {"messages": [{"text": "旧请求 OCR"}]}}}]}}
    details = automatic_history_details(tmp_path, legacy)
    assert details["routerOcrText"] == "未记录"
    assert "旧记录" in details["generationOcrStatus"]
    assert "旧请求 OCR" in details["generationOcrText"] and "不可据此重建" not in details["generationOcrText"]


def test_evidence_survives_resolution_but_never_enters_model_payload(monkeypatch):
    context = context_fixture()
    assert resolve_context(context) is context
    decision, trace, calls = classify(context)
    sent = []
    processor = OpenAICompatibleTextProcessor()
    def request(settings, **kwargs):
        sent.append(kwargs)
        return {"modified_text": "请联系张玮"}
    monkeypatch.setattr(processor, "_request_chat", request)
    processor.process_task_with_trace("王字旁的玮", "edit", LLMSettings(enabled=True, model="test", api_key_env=""),
                                      "请联系张伟", context, edit_plan=decision.edit_plan)
    payload = json.loads(sent[0]["user_content"])
    assert payload["screen_context"] == dict(context)
    combined = json.dumps(calls, ensure_ascii=False) + sent[0]["user_content"]
    for private in ("screenshot_png", "ocr_lines", "confidence", "screenshot_file", "iVBOR"):
        assert private not in combined
    assert "请联系张伟" in json.loads(trace)["input_state"]


def test_record_links_exact_frame_rules_probabilities_and_composed_prompt(tmp_path):
    collector, directory, result, _ = record_example(tmp_path)
    request = TextProcessingRequest(11, 7, "edit", result.raw_text, LLMSettings(enabled=True, model="test"),
        target_text="请联系张伟", task_type="edit", edit_plan=result.edit_plan, screen_context=result.screen_context)
    collector.record_text_request(request)
    record = json.loads((directory / "record.json").read_text())
    evidence = record["screen_evidence"]
    assert (directory / evidence["screenshot_file"]).read_bytes() == PNG
    assert evidence["ocr_lines"][0]["disposition"] == "timestamp"
    assert evidence["ocr_lines"][1]["confidence"] == .97
    assert evidence["ocr_lines"][1]["box"] == [.4, .4, .3, .02]
    assert "screenshot_png" not in (directory / "record.json").read_text()
    details = collector.load_entries()[0]["automaticDetails"]
    assert "编辑" in details["level1Text"] and "90.0%" in details["level1Text"]
    assert details["level2Text"] == "修改" and details["hintText"] == ""
    assert len(details["probabilityText"].splitlines()) == 6
    assert details["screenshotUrl"] == (directory / evidence["screenshot_file"]).as_uri()
    assert "16:29" not in details["ocrText"] and "16:29" in details["rawOcrText"]
    assert details["systemPrompt"] == record["llm"]["requests"][0]["system_prompt"]
    assert json.loads(details["modelInput"])["editable_text"] == "请联系张伟"
    # Normal history deletion also removes the image; a delayed callback cannot recreate it.
    assert collector.delete_entry(directory.name)
    collector.record_routing_result(result)
    assert not directory.exists()


@pytest.mark.parametrize("task,fail_joint", [("dictation", False), ("edit", True)])
def test_joint_dictation_and_network_failure_are_distinct(tmp_path, task, fail_joint):
    collector, directory, _, calls = record_example(tmp_path, task=task, fail_joint=fail_joint)
    details = collector.load_entries()[0]["automaticDetails"]
    if fail_joint:
        assert details["level1Text"] == "未取得判断结果"
        assert details["level2Text"] == "分类未通过校验，未执行" and details["errorText"]
        assert len(calls) == 1
    else:
        assert "听写" in details["level1Text"] and "全部任务跳过" in details["level2Text"]
        assert len(calls) == 1 and len(details["probabilityText"].splitlines()) == 6
    assert details["screenshotUrl"]


def test_png_write_failure_keeps_classification_and_records_reason(tmp_path, monkeypatch):
    write = Path.write_bytes
    def fail_image(path, data):
        if path.name.endswith(".png.tmp"):
            raise OSError("disk full")
        return write(path, data)
    monkeypatch.setattr(Path, "write_bytes", fail_image)
    collector, _, _, _ = record_example(tmp_path)
    details = collector.load_entries()[0]["automaticDetails"]
    assert details["level2Text"] == "修改"
    assert not details["screenshotUrl"] and "disk full" in details["screenshotText"]


def test_legacy_and_unavailable_evidence_do_not_invent_image_or_classification(tmp_path):
    assert automatic_history_details(tmp_path, {"mode": {"routing": "manual"}}) == {}
    record = {"mode": {"routing": "auto", "router_output": "legacy text",
                       "screen_context": {"status": "unavailable", "reason": "需要屏幕录制权限"}},
              "screen_evidence": {"screenshot_file": "../outside.png"}}
    details = automatic_history_details(tmp_path, record)
    assert details["level1Text"] == "未取得判断结果"
    assert not details["screenshotUrl"] and "需要屏幕录制权限" in details["contextText"]


def test_rejected_joint_history_shows_raw_intent_and_all_scores(tmp_path):
    from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS
    answers={name:{'choice':'apply' if name=='polish' else 'skip',
                   'probabilities':{'apply':.8 if name=='polish' else .1,'skip':.2 if name=='polish' else .9}}
             for name in EDIT_TASK_LABELS}
    answers['voice_intent']={'choice':'dictation','probabilities':{'dictation':.7,'edit':.3}}
    trace={'classification_type':'joint','raw_model_task':'dictation','raw_tasks':['polish'],
           'consistency':'rejected','error_code':'dictation_with_tasks','error':'联合分类矛盾',
           'stages':[{'stage':'joint','answers':answers,'latency_s':.4}]}
    record={'mode':{'routing':'auto','router_trace':trace}}
    details=automatic_history_details(tmp_path,record)
    assert '听写' in details['level1Text'] and '70.0%' in details['level1Text']
    assert details['level2Text']=='分类未通过校验，未执行'
    assert details['hintText']=='' and len(details['probabilityText'].splitlines())==6
    assert '联合分类 400 ms' in details['timingText'] and details['errorText']=='联合分类矛盾'


def test_old_two_stage_history_retains_hint_evidence(tmp_path):
    trace={'classification_type':'hierarchical','task':'edit','edit_plan':{'tasks':['precise_edit'],'hints':['redictation']},
           'stages':[{'stage':'intent','answers':{'voice_intent':{'choice':'edit','probabilities':{'edit':.9,'dictation':.1}}}},
                     {'stage':'edit_details','answers':{'redictation':{'choice':'apply','probabilities':{'apply':.8,'skip':.2}}}}]}
    details=automatic_history_details(tmp_path,{'mode':{'routing':'auto','router_trace':trace}})
    assert details['hintText']=='直接重述' and '80.0%' in details['probabilityText']


def test_dictation_warning_survives_recording_without_rewriting_model_probabilities(tmp_path):
    collector, directory, result, calls = record_example(tmp_path, task='dictation', conflict=True)
    assert result.task_type == 'dictation' and result.edit_plan is None and result.error is None
    record = json.loads((directory / 'record.json').read_text())
    trace = record['mode']['router_trace']
    assert trace['raw_tasks'] == trace['ignored_tasks'] == ['modify']
    assert trace['stages'][0]['answers']['modify']['probabilities']['apply'] == .9
    details = collector.load_entries()[0]['automaticDetails']
    assert details['warningLevel'] == 'yellow' and '修改' in details['warningText']
    assert '听写优先' in details['level1Text'] and '未执行：修改' in details['level2Text']
    assert '模型选启用，实际已忽略' in details['probabilityText']
    assert not details['errorText'] and len(calls) == 1


def test_old_joint_structure_and_condense_keep_original_probabilities(tmp_path):
    answers={'voice_intent':{'choice':'edit','probabilities':{'edit':.9,'dictation':.1}},
             'structure':{'choice':'apply','probabilities':{'apply':.8,'skip':.2}},
             'condense':{'choice':'skip','probabilities':{'apply':.3,'skip':.7}}}
    trace={'classification_type':'joint','task':'edit','edit_plan':{'tasks':['structure']},
           'stages':[{'stage':'joint','answers':answers}]}
    details=automatic_history_details(tmp_path, {'mode':{'routing':'auto','router_trace':trace}})
    assert details['level2Text']=='结构格式调整'
    assert '结构格式调整：启用（启用 80.0%' in details['probabilityText']
    assert '精简摘要：不启用（启用 30.0%' in details['probabilityText']
    assert '内容整理' not in details['probabilityText']
