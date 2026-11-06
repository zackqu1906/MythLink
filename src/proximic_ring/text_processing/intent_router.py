"""Clef server client using the Jev/SystemOne decision API (not chat completions)."""
from __future__ import annotations

import json
import math
import os
import threading
import time
from urllib import error as urlerror, request as urlrequest
from urllib.parse import urlsplit

from .router_prompts import ROUTER_PROMPT_VERSION, routing_schema, joint_router_input
from .task_routing import (DEFAULT_ROUTER_MODEL, EDIT_TASK_LABELS, router_input, router_ocr_text, resolve_joint_decision,
                           multiclass_probabilities, JOINT_POLICY_VERSION, task_availability)


MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Keep utterances, context and credentials at the configured endpoint.
        return None


class ClefIntentRouter:
    def __init__(self, *, endpoint=None, model=None, api_key=None, timeout_s=None, urlopen=None):
        self.endpoint = str(endpoint if endpoint is not None else os.environ.get("PROXIMIC_CLEF_URL", "")).strip()
        self.model = str(model if model is not None else os.environ.get("PROXIMIC_CLEF_MODEL", DEFAULT_ROUTER_MODEL)).strip()
        self._api_key = api_key
        self.timeout_s = timeout_s if timeout_s is not None else os.environ.get("PROXIMIC_CLEF_TIMEOUT_S", "15")
        self._urlopen = urlopen or urlrequest.build_opener(_NoRedirect()).open
        self._closed = threading.Event()

    def warmup(self):
        """Validate configuration only; the server owns model loading/warmup."""
        if self._closed.is_set():
            raise RuntimeError("Clef 自动判断服务已关闭")
        if not self.endpoint:
            raise RuntimeError("尚未配置 Clef 服务，请设置 PROXIMIC_CLEF_URL 为完整 /v1/systemone 地址")
        try:
            url = urlsplit(self.endpoint)
            valid = (url.scheme in {"http", "https"} and url.hostname and url.port != 0
                     and not url.username and not url.password and not url.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Clef 服务地址必须是有效的 HTTP(S) URL，鉴权使用 PROXIMIC_CLEF_API_KEY")
        if not self.model:
            raise ValueError("Clef 服务模型名不能为空")
        try:
            timeout = float(self.timeout_s)
        except (ValueError, TypeError):
            timeout = 0
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("PROXIMIC_CLEF_TIMEOUT_S 必须是正数")

    def classify(self, utterance, target="", context=None, *, cancelled=lambda: False):
        self.warmup()
        ocr_text = router_ocr_text(context or {})
        state = joint_router_input(router_input(utterance, target, context or {}, ocr_text=ocr_text))
        schema = routing_schema(target=target)
        started = time.perf_counter()
        trace = {"provider": "clef_server", "requested_model": self.model,
                 "prompt_version": ROUTER_PROMPT_VERSION, "classification_type": "joint",
                 "policy_version": JOINT_POLICY_VERSION,
                 "input_state": state, "ocr_text": ocr_text,
                 "request_schema": schema, "task_availability": task_availability(target),
                 "stages": []}
        try:
            result = self._classify_stage(state, schema, cancelled)
            trace["stages"].append(dict(stage="joint", **result))
            answers = result["answers"]
            answer = answers["voice_intent"]
            trace.update(model=result["model"], raw_model_task=answer["choice"],
                         raw_tasks=[k for k in EDIT_TASK_LABELS if k in answers and answers[k]["choice"] == "apply"],
                         probabilities=answer["probabilities"], consistency="pending")
            decision, policy = resolve_joint_decision(answers, target=target)
            trace.update(policy)
            trace.update(task=decision.task,
                         edit_plan=decision.edit_plan.as_dict() if decision.edit_plan else None)
            trace["latency_s"] = time.perf_counter() - started
            return decision, json.dumps(trace, ensure_ascii=False)
        except Exception as exc:
            trace.update(error=str(exc), error_code=getattr(exc, "code", "request_or_response_error"),
                         consistency="rejected", latency_s=time.perf_counter() - started)
            exc.model_output = json.dumps(trace, ensure_ascii=False)
            raise

    def _classify_stage(self, state, schema, cancelled):
        if self._closed.is_set() or cancelled():
            raise RuntimeError("Clef 自动判断已取消")
        payload = {"model": self.model, "state": state, "questions": schema}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = self._api_key if self._api_key is not None else os.environ.get("PROXIMIC_CLEF_API_KEY", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urlrequest.Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=headers, method="POST")
        started = time.perf_counter()
        try:
            with self._urlopen(request, timeout=float(self.timeout_s)) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urlerror.HTTPError as exc:
            # Do not put server bodies (which may echo state or credentials) in logs.
            status = exc.code
            exc.close()
            raise RuntimeError(f"Clef 自动判断请求失败（HTTP {status}），请检查服务地址、鉴权和模型状态") from None
        except TimeoutError:
            raise TimeoutError("Clef 自动判断请求超时") from None
        except urlerror.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError("Clef 自动判断请求超时") from None
            raise RuntimeError("无法连接 Clef 自动判断服务，请检查服务状态和网络") from None
        except OSError:
            raise RuntimeError("Clef 自动判断连接中断") from None
        if self._closed.is_set() or cancelled():
            raise RuntimeError("Clef 自动判断服务已关闭或取消，结果已丢弃")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Clef 自动判断响应过大")
        try:
            response = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ValueError("Clef 未返回有效的 SystemOne JSON 响应") from None
        if not isinstance(response, dict) or response.get("error"):
            raise ValueError("Clef 未返回有效的分类结果")
        answers = response.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(schema):
            raise ValueError("Clef 未返回完整的分类问题结果")
        validated = {}
        for name, question in schema.items():
            answer = answers[name]
            if not isinstance(answer, dict) or answer.get("type") != "choice":
                raise ValueError(f"Clef {name} 必须返回 choice 分类结果")
            probabilities = multiclass_probabilities(answer, question["criteria"])
            choice = answer.get("choice")
            if (not isinstance(choice, str) or choice not in probabilities
                    or probabilities[choice] != max(probabilities.values())):
                raise ValueError("Clef 返回的类别与最高概率不一致")
            validated[name] = {"choice": choice, "probabilities": probabilities}
        return {"model": response.get("model") or self.model, "answers": validated,
                "latency_s": time.perf_counter() - started}

    def close(self):
        self._closed.set()
