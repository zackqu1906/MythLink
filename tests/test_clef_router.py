import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError

import pytest

from proximic_ring.text_processing.intent_router import ClefIntentRouter, MAX_RESPONSE_BYTES
from proximic_ring.text_processing.model import InputModeRoutingRequest, LLMSettings
from proximic_ring.text_processing.router_prompts import routing_schema
from proximic_ring.text_processing.task_routing import multiclass_probabilities, EditPlan, RoutingDecision
from proximic_ring.text_processing.worker import TextProcessingWorker


SCORES = dict(dictation=.2, edit=.8)


def result(scores=None, choice="edit"):
    return {"model": "clef-flash", "answers": {**edit_result(() if choice == "dictation" else ("modify",))["answers"], "voice_intent": {
        "type": "choice", "choice": choice, "confidence": .4,
        "probabilities": scores if scores is not None else dict(SCORES)}},
        "usage": {"input_tokens": 200, "output_tokens": 0}}


def edit_result(tasks=("modify",)):
    selected = set(tasks)
    return {"model": "clef-flash", "answers": {
        name: {"type": "choice", "choice": "apply" if name in selected else "skip",
               "probabilities": {"apply": .9 if name in selected else .1,
                                 "skip": .1 if name in selected else .9}}
        for name in routing_schema() if name != "voice_intent"}}


def client(payload=None, details=None, **kwargs):
    response = result() if payload is None else payload
    if details is not None:
        response["answers"] = {"voice_intent": response["answers"]["voice_intent"], **details["answers"]}
    def respond(*args, **kw):
        return io.BytesIO(json.dumps(response).encode())
    return ClefIntentRouter(endpoint="http://localhost:30000/v1/systemone", urlopen=respond, **kwargs)


@pytest.fixture
def http_service():
    calls = []
    response = {"status": 200, "payload": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, dict(self.headers), payload))
            data = json.dumps(response["payload"] or (result() if "voice_intent" in payload["questions"] else edit_result())).encode()
            self.send_response(response["status"])
            self.send_header("Content-Type", "application/json")
            if response["status"] == 302:
                self.send_header("Location", "/other-service")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            calls.append((self.path, dict(self.headers), None))
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/systemone", calls, response
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_systemone_http_contract_and_environment_config(http_service, monkeypatch):
    endpoint, calls, _ = http_service
    monkeypatch.setenv("PROXIMIC_CLEF_URL", endpoint)
    monkeypatch.setenv("PROXIMIC_CLEF_MODEL", "clef-flash")
    monkeypatch.setenv("PROXIMIC_CLEF_API_KEY", "test-secret")
    monkeypatch.setenv("PROXIMIC_CLEF_TIMEOUT_S", "2")
    router = ClefIntentRouter()
    router.warmup()
    assert calls == []  # Startup does not send screen/user data or load a local model.
    task, trace_json = router.classify("改成牛奶", "我想喝咖啡", {
        "messages": [{"speaker": "other", "text": "要喝什么？"}]})
    assert task == RoutingDecision("edit", EditPlan(("modify",)))
    assert len(calls) == 1
    path, headers, payload = calls[0]
    assert path == "/v1/systemone" and headers["Authorization"] == "Bearer test-secret"
    assert set(payload) == {"model", "state", "questions"}
    assert payload["questions"] == routing_schema()
    assert "可编辑原文：我想喝咖啡" in payload["state"]
    assert "聊天上文（仅供参考）：other: 要喝什么？" in payload["state"]
    trace = json.loads(trace_json)
    assert trace["provider"] == "clef_server" and trace["model"] == "clef-flash"
    assert trace["raw_model_task"] == task.task
    assert len(trace["stages"]) == 1 and trace["edit_plan"] == task.edit_plan.as_dict()
    assert trace["probabilities"] == SCORES
    assert trace["latency_s"] >= 0
    assert "test-secret" not in trace_json and endpoint not in trace_json


def test_http_redirect_is_not_followed(http_service):
    endpoint, calls, response = http_service
    response["status"] = 302
    with pytest.raises(RuntimeError, match="HTTP 302"):
        ClefIntentRouter(endpoint=endpoint, api_key="test-secret").classify("测试")
    assert len(calls) == 1


@pytest.mark.parametrize("scores", [dict(dictation=.5, edit=.4999), dict(dictation=.5001, edit=.5)])
def test_official_four_decimal_rounding_preserves_reported_probabilities(scores):
    assert multiclass_probabilities({"probabilities": scores}) == scores


def test_root_tie_falls_back_to_dictation_with_raw_probabilities_retained():
    scores = dict(dictation=.5, edit=.5)
    decision, raw = client(result(scores)).classify("修改", "原文")
    trace = json.loads(raw)
    assert decision == RoutingDecision("dictation")
    assert trace["raw_model_task"] == "edit"
    assert trace["warning_codes"] == ["tied_probability"] and trace["task"] == "dictation"
    assert trace["decision_source"] == "uncertain_dictation_fallback"
    assert trace["warning_level"] == "yellow" and trace["edit_plan"] is None
    assert "error" not in trace
    assert trace["probabilities"] == scores and len(trace["stages"]) == 1


@pytest.mark.parametrize("payload", [
    [], {}, {"error": "internal details"}, {"answers": []}, {"answers": {"other": {}}},
    {"answers": {"voice_intent": "edit"}},
    {"answers": {"voice_intent": {"type": "noul", "noul": 1}}},
    result({"modify": 1}),
    result(dict(dictation=.2, edit=.8003)),
    result(dict(dictation=.2, edit=float("nan"))),
    result(choice="unknown"), result(choice="dictation"),
])
def test_invalid_responses_cannot_produce_an_edit(payload):
    with pytest.raises(ValueError):
        client(payload).classify("改成牛奶", "咖啡")


@pytest.mark.parametrize("raw", [b"<html>upstream error</html>", b"\xff", b"x" * (MAX_RESPONSE_BYTES + 1)])
def test_non_json_and_oversized_responses_are_rejected(raw):
    router = ClefIntentRouter(endpoint="http://localhost/v1/systemone", urlopen=lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(ValueError):
        router.classify("测试")


@pytest.mark.parametrize("error,kind,message", [
    (HTTPError("http://localhost", 401, "secret", {}, io.BytesIO(b"private state")), RuntimeError, "HTTP 401"),
    (HTTPError("http://localhost", 503, "secret", {}, None), RuntimeError, "HTTP 503"),
    (URLError("private details"), RuntimeError, "无法连接"),
    (TimeoutError("private details"), TimeoutError, "超时"),
    (URLError(TimeoutError()), TimeoutError, "超时"),
])
def test_failed_requests_are_not_retried_or_logged_with_server_details(error, kind, message):
    calls = []
    def fail(request, **kwargs):
        calls.append(request)
        raise error
    router = ClefIntentRouter(endpoint="http://localhost/v1/systemone", urlopen=fail)
    with pytest.raises(kind, match=message) as caught:
        router.classify("测试")
    assert len(calls) == 1
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)


@pytest.mark.parametrize("settings", [
    {"endpoint": ""}, {"endpoint": "file:///tmp/server"},
    {"endpoint": "https://user:secret@localhost/v1/systemone"},
    {"endpoint": "http://localhost:bad/v1/systemone"},
    {"timeout_s": "invalid"}, {"timeout_s": "nan"}, {"timeout_s": 0}, {"model": ""},
])
def test_bad_configuration_fails_before_network(settings):
    calls = []
    config = dict(endpoint="http://localhost/v1/systemone", urlopen=lambda *a, **k: calls.append(a))
    config.update(settings)
    with pytest.raises((ValueError, RuntimeError)):
        ClefIntentRouter(**config).classify("测试")
    assert not calls


def test_close_rejects_inflight_response_and_future_requests():
    router = client()
    def respond(*args, **kwargs):
        router.close()
        return io.BytesIO(json.dumps(result()).encode())
    router._urlopen = respond
    with pytest.raises(RuntimeError, match="结果已丢弃"):
        router.classify("测试")
    with pytest.raises(RuntimeError, match="已关闭"):
        router.classify("测试")


def test_worker_cancellation_discards_pending_clef_result():
    entered, release, finished, delivered = (threading.Event() for _ in range(4))
    class Router:
        def classify(self, *args, **kwargs):
            entered.set()
            assert release.wait(2)
            return RoutingDecision("edit", EditPlan(("modify",))), "trace"
        def close(self):
            pass
    worker = TextProcessingWorker(intent_router=Router(), on_result=lambda r: None,
                                  on_routing_result=lambda r: delivered.set())
    consume = worker._consume_cancelled
    def consume_cancelled(request_id):
        value = consume(request_id)
        if value:
            finished.set()
        return value
    worker._consume_cancelled = consume_cancelled
    try:
        worker.submit_routing(InputModeRoutingRequest(1, 2, "改成牛奶", LLMSettings(),
                                                     router_backend="clef", target_text="咖啡"))
        assert entered.wait(2)
        worker.cancel_request(1)
        release.set()
        assert finished.wait(2) and not delivered.is_set()
    finally:
        release.set()
        worker.close(wait=True)


@pytest.mark.parametrize('backend', ['gliner', 'llm'])
def test_removed_backends_are_rejected_without_calling_a_model(backend):
    responses, done = [], threading.Event()
    class Router:
        def classify(self, *args, **kwargs):
            pytest.fail("Removed backend must not silently invoke Clef")
        def close(self):
            pass
    worker = TextProcessingWorker(intent_router=Router(), on_result=lambda r: None,
        on_routing_result=lambda r: (responses.append(r), done.set()))
    try:
        worker.submit_routing(InputModeRoutingRequest(1, 2, "测试", LLMSettings(), router_backend=backend))
        assert done.wait(2)
        assert responses[0].error and not responses[0].task_type
    finally:
        worker.close(wait=True)


def test_routing_request_defaults_to_clef_and_preserves_joint_plan():
    responses, done = [], threading.Event()
    worker = TextProcessingWorker(intent_router=client(), on_result=lambda r: None,
        on_routing_result=lambda r: (responses.append(r), done.set()))
    try:
        worker.submit_routing(InputModeRoutingRequest(1, 2, "改成牛奶", LLMSettings(), target_text="咖啡"))
        assert done.wait(2)
        assert responses[0].error is None
        assert responses[0].task_type == 'edit' and responses[0].edit_plan == EditPlan(('modify',))
        assert json.loads(responses[0].model_output)['classification_type'] == 'joint'
    finally:
        worker.close(wait=True)


def test_dictation_checks_all_tasks_in_same_request():
    calls = []
    def respond(request, **kwargs):
        calls.append(json.loads(request.data))
        return io.BytesIO(json.dumps(result(dict(dictation=.9, edit=.1), "dictation")).encode())
    decision, raw = ClefIntentRouter(endpoint="http://localhost/v1/systemone", urlopen=respond).classify("他说把最后一句删掉", "保留这句。删除那句。")
    assert decision == RoutingDecision("dictation") and len(calls) == 1
    assert len(calls[0]["questions"]) == 7
    assert json.loads(raw)["edit_plan"] is None


def test_mixed_edit_keeps_independent_probabilities_without_hint_fields():
    decision, raw = client(details=edit_result(("modify", "polish"))).classify(
        "把伟改成王字旁的玮，再客气一点", "请张伟来")
    assert decision.edit_plan == EditPlan(("modify", "polish"))
    detail = json.loads(raw)["stages"][0]["answers"]
    assert detail["modify"]["probabilities"]["apply"] == .9
    assert detail["polish"]["probabilities"]["apply"] == .9  # Not normalized across tasks.


@pytest.mark.parametrize("failure", ["missing", "extra", "bad_probability", "wrong_choice"])
def test_invalid_joint_tasks_never_return_partial_plan(failure):
    details = edit_result()
    answers = details["answers"]
    if failure == "missing": del answers["translate"]
    elif failure == "extra": answers["unknown"] = answers["polish"]
    elif failure == "bad_probability": answers["polish"]["probabilities"]["apply"] = True
    elif failure == "wrong_choice": answers["polish"]["choice"] = "apply"
    with pytest.raises(ValueError) as caught:
        client(details=details).classify("修改", "原文")
    trace = json.loads(caught.value.model_output)
    assert trace["error"] and trace["consistency"] == "rejected"
    assert "edit_plan" not in trace


def test_cancellation_discards_joint_response_without_retry():
    calls = []
    cancelled = threading.Event()
    def respond(request, **kwargs):
        calls.append(request)
        cancelled.set()
        return io.BytesIO(json.dumps(result()).encode())
    router = ClefIntentRouter(endpoint="http://localhost/v1/systemone", urlopen=respond)
    with pytest.raises(RuntimeError, match="取消"):
        router.classify("改成牛奶", "咖啡", cancelled=cancelled.is_set)
    assert len(calls) == 1


def test_dictation_wins_over_edit_tasks_with_yellow_warning_and_raw_scores():
    decision, raw = client(result(dict(dictation=.9, edit=.1), "dictation"), details=edit_result()).classify("他说改一下", "原文")
    trace = json.loads(raw)
    assert decision == RoutingDecision("dictation")
    assert trace["warning_codes"] == ["dictation_with_tasks"]
    assert trace["raw_model_task"] == "dictation" and trace["raw_tasks"] == ["modify"]
    assert trace["task"] == "dictation" and trace["edit_plan"] is None
    assert trace["warning_level"] == "yellow" and trace["ignored_tasks"] == ["modify"]
    assert trace["decision_source"] == "dictation_priority" and "error" not in trace
    assert len(trace["stages"][0]["answers"]) == 7


@pytest.mark.parametrize("failure", ["task_tie", "all_skip"])
def test_unclear_edit_plan_falls_back_without_executing_partial_tasks(failure):
    details = edit_result(()) if failure == "all_skip" else edit_result()
    if failure == "task_tie":
        details["answers"]["polish"]["probabilities"] = dict(apply=.5, skip=.5)
    decision, raw = client(details=details).classify("修改", "原文")
    trace = json.loads(raw)
    assert decision == RoutingDecision("dictation") and trace["edit_plan"] is None
    assert trace["decision_source"] == "uncertain_dictation_fallback"
    assert trace["warning_level"] == "yellow" and "error" not in trace
    assert trace["warning_codes"] == ["edit_without_tasks" if failure == "all_skip" else "tied_probability"]


def test_dictation_priority_is_not_blocked_by_uncertain_task_head():
    details = edit_result()
    details["answers"]["polish"]["probabilities"] = dict(apply=.5, skip=.5)
    decision, raw = client(result(dict(dictation=.9, edit=.1), "dictation"), details=details).classify("普通口述", "原文")
    assert decision == RoutingDecision("dictation")
    trace = json.loads(raw)
    assert trace["ignored_tasks"] == ["modify"] and trace["warning_level"] == "yellow"


@pytest.mark.parametrize("target", ["", " \t\n\u3000"])
@pytest.mark.parametrize("task,selected", [("dictation", ()), ("edit", ("insert",)), ("edit", ("compose",))])
def test_empty_source_removes_modify_from_request_without_inventing_probabilities(target, task, selected):
    calls = []
    def respond(request, **kwargs):
        payload = json.loads(request.data)
        calls.append(payload)
        assert set(payload["questions"]) == {"voice_intent", "insert", "polish", "format", "translate", "compose"}
        response = result(dict(dictation=.9, edit=.1) if task == "dictation" else None, task)
        response["answers"].update(edit_result(selected)["answers"])
        del response["answers"]["modify"]
        return io.BytesIO(json.dumps(response).encode())
    decision, raw = ClefIntentRouter(endpoint="http://localhost/v1/systemone", urlopen=respond).classify(
        "把张伟改成张玮", target, {"messages": [{"speaker": "self", "text": "张伟"}]})
    trace = json.loads(raw)
    assert len(calls) == 1
    assert decision == RoutingDecision(task, EditPlan(selected) if selected else None)
    assert trace["request_schema"] == calls[0]["questions"]
    assert trace["task_availability"]["excluded_tasks"] == {"modify": "no_editable_source"}
    assert "modify" not in trace["stages"][0]["answers"]
    assert "modify" not in trace["raw_tasks"] and not trace["warning"]


@pytest.mark.parametrize("violation", ["extra_modify", "missing_translate"])
def test_empty_source_response_must_match_the_reduced_schema(violation):
    response = result()
    if violation == "missing_translate":
        del response["answers"]["modify"]
        del response["answers"]["translate"]
    with pytest.raises(ValueError, match="完整的分类问题") as caught:
        client(response).classify("修改", "")
    trace = json.loads(caught.value.model_output)
    assert "edit_plan" not in trace and trace["consistency"] == "rejected"


def test_empty_source_all_skip_still_falls_back_to_dictation():
    response = result()
    response["answers"].update(edit_result(())["answers"])
    del response["answers"]["modify"]
    decision, raw = client(response).classify("删掉最后一句", "")
    assert decision == RoutingDecision("dictation")
    trace = json.loads(raw)
    assert trace["warning_codes"] == ["edit_without_tasks"]
    assert trace["warning_level"] == "yellow"


@pytest.mark.parametrize("target", ["空", " \n原文\n "])
def test_literal_empty_marker_and_nonempty_source_keep_modify_available(target):
    decision, raw = client().classify("改成新内容", target)
    assert decision.edit_plan == EditPlan(("modify",))
    assert "modify" in json.loads(raw)["request_schema"]


def test_decision_validation_cannot_accept_modify_when_source_is_empty():
    from proximic_ring.text_processing.task_routing import resolve_joint_decision
    with pytest.raises(ValueError, match="可用任务"):
        resolve_joint_decision(result()["answers"], target="")
