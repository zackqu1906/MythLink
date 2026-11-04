"""Exercise the deployed HTTP contract without requiring Ascend hardware."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from tools.serve_clef_npu import ClefServer, MAX_BODY, RequestError
from proximic_ring.text_processing.intent_router import ClefIntentRouter
from proximic_ring.text_processing.task_routing import EditPlan, RoutingDecision


class Engine:
    device = "npu:0"

    def __init__(self):
        self.calls = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.error = None

    def infer(self, body):
        self.calls.append((body, threading.get_ident()))
        self.entered.set()
        assert self.release.wait(3)
        if self.error:
            raise self.error
        answers = {}
        for name, question in body["questions"].items():
            chosen = "edit" if name == "voice_intent" else ("apply" if name == "modify" else "skip")
            answers[name] = {"type": "choice", "choice": chosen, "confidence": .9,
                             "probabilities": {k: .9 if k == chosen else .1 for k in question["criteria"]}}
        return {"model": body["model"], "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 0}}


@pytest.fixture
def service():
    engine = Engine()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with ClefServer(("127.0.0.1", 0), engine, pool, api_key="test-only") as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield server, engine, f"http://127.0.0.1:{server.server_port}"
            finally:
                engine.release.set()
                server.shutdown()
                thread.join(2)


def send(base, body, *, key="test-only", path="/v1/systemone"):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = Request(base + path, data=raw, headers={"Content-Type": "application/json",
                                                    "Authorization": f"Bearer {key}"})
    return build_opener(ProxyHandler({})).open(request, timeout=3)


def body():
    return {"model": "clef-flash", "state": "测试", "questions": {"voice_intent": {
        "type": "choice", "criteria": {"dictation": "听写", "edit": "编辑"}}}}


def test_project_client_completes_joint_request_with_one_worker(service):
    _, engine, base = service
    router = ClefIntentRouter(endpoint=base + "/v1/systemone", api_key="test-only")
    decision, trace = router.classify("把咖啡改成牛奶", "我想喝咖啡。")
    assert decision == RoutingDecision("edit", EditPlan(("modify",)))
    assert len(engine.calls) == 1 and len({t for _, t in engine.calls}) == 1
    assert len(json.loads(trace)["stages"]) == 1


@pytest.mark.parametrize("raw,status", [(b"not-json", 400), (b'{"state":NaN}', 400),
                                        (b"x" * (MAX_BODY + 1), 413), ({}, 400),
                                        ({**body(), "model": "wrong"}, 404),
                                        ({**body(), "images": ["anything"]}, 400),
                                        ({**body(), "questions": {}}, 400)],
                         ids=["not-json", "nan", "too-large", "missing-fields", "unknown-model", "images", "no-questions"])
def test_invalid_inputs_never_reach_model(service, raw, status):
    _, engine, base = service
    with pytest.raises(HTTPError) as caught:
        send(base, raw)
    assert caught.value.code == status
    caught.value.close()
    assert engine.calls == []


def test_bad_auth_never_reaches_model(service):
    _, engine, base = service
    with pytest.raises(HTTPError) as caught:
        send(base, body(), key="wrong")
    assert caught.value.code == 401 and not engine.calls
    caught.value.close()


def test_health_remains_responsive_and_busy_slot_survives_timeout(service):
    server, engine, base = service
    server.request_timeout = .03
    engine.release.clear()
    for _ in range(3):
        server.slots.acquire()
    with pytest.raises(HTTPError) as caught:
        send(base, body())
    assert caught.value.code == 504
    caught.value.close()
    assert engine.entered.is_set()
    with build_opener(ProxyHandler({})).open(base + "/health", timeout=1) as response:
        assert json.load(response)["status"] == "ready"
    with pytest.raises(HTTPError) as caught:
        send(base, body())
    assert caught.value.code == 503 and len(engine.calls) == 1
    caught.value.close()
    engine.release.set()
    for _ in range(3):
        server.slots.release()


def test_token_limit_error_is_structured(service):
    _, engine, base = service
    engine.error = RequestError(413, "input exceeds server token limit")
    with pytest.raises(HTTPError) as caught:
        send(base, body())
    assert caught.value.code == 413
    assert json.load(caught.value) == {"error": "input exceeds server token limit"}
    caught.value.close()
