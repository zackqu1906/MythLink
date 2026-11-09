#!/usr/bin/env python3
"""Text-only SystemOne service for one Ascend NPU with configurable HTTP binding."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

LOG = logging.getLogger("clef")
MAX_BODY = 128 * 1024
MODELS = {"clef-flash", "Cloudflare/clef-flash"}


class RequestError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def reject_json_constant(value):
    raise ValueError("non-finite JSON value")


def validate_request(body):
    if not isinstance(body, dict) or "state" not in body:
        raise RequestError(400, "state and questions are required")
    if not isinstance(body.get("model"), str) or body["model"] not in MODELS:
        raise RequestError(404, "unknown model")
    if set(body) - {"model", "state", "questions"}:
        raise RequestError(400, "this service accepts text/JSON state only")
    questions = body.get("questions")
    if not isinstance(questions, dict) or not 1 <= len(questions) <= 32:
        raise RequestError(400, "expected 1 to 32 questions")
    for name, q in questions.items():
        if not isinstance(name, str) or not isinstance(q, dict):
            raise RequestError(400, "invalid question")
        kind, criteria = q.get("type"), q.get("criteria")
        valid = (
            kind == "choice" and isinstance(criteria, dict) and 1 <= len(criteria) <= 64
            or kind == "score" and isinstance(criteria, list) and 1 <= len(criteria) <= 64
            or kind == "noul" and (criteria is None or isinstance(criteria, dict))
        )
        if not valid:
            raise RequestError(400, "invalid question type or criteria")
    return body


class ClefEngine:
    def __init__(self, model_path, device, max_tokens):
        # Constructed and used on the same executor thread (NPU device context).
        import torch
        import torch_npu  # noqa: F401

        self.torch, self.device, self.max_tokens = torch, device, max_tokens
        torch.npu.set_device(device)
        sys.path.insert(0, str(model_path))
        from joint_schema_model import encode_record, load_release_model, systemone

        self.encode_record, self.systemone = encode_record, systemone
        self.model, self.processor = load_release_model(
            model_path, device=device, dtype=torch.bfloat16,
            attn_implementation="eager", local_files_only=True,
        )
        if any(p.device.type != "npu" for p in self.model.parameters()):
            raise RuntimeError("all model parameters must be on NPU")

    def infer(self, body):
        # The upstream encoder truncates state; reject overlong inputs instead.
        encoded = self.encode_record(
            self.processor.tokenizer, body, processor=self.processor, max_length=1_000_000,
        )
        if len(encoded.input_ids) > self.max_tokens:
            raise RequestError(413, "input exceeds server token limit")
        self.torch.npu.synchronize()
        started = time.perf_counter()
        result = self.systemone(self.model, self.processor, body, max_length=self.max_tokens)
        self.torch.npu.synchronize()
        LOG.info("inference questions=%d tokens=%d elapsed_s=%.3f",
                 len(body["questions"]), len(encoded.input_ids), time.perf_counter() - started)
        # Refuse non-finite scores rather than emitting non-standard JSON.
        json.dumps(result, allow_nan=False)
        return result


class ClefServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine, executor, *, api_key="", request_timeout=60):
        self.engine, self.executor = engine, executor
        self.api_key, self.request_timeout = api_key, request_timeout
        self.slots = threading.BoundedSemaphore(4)
        self.started = time.time()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # Never record request bodies, headers or arbitrary URL text.

    def reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self):
        key = self.server.api_key
        return not key or hmac.compare_digest(
            self.headers.get("Authorization", "").encode(), f"Bearer {key}".encode(),
        )

    def do_GET(self):
        if self.path != "/health":
            return self.reply(404, {"error": "not found"})
        return self.reply(200, {"status": "ready", "model": "clef-flash",
                                "device": self.server.engine.device, "pid": os.getpid(),
                                "uptime_s": round(time.time() - self.server.started, 2)})

    def do_POST(self):
        if self.path != "/v1/systemone":
            return self.reply(404, {"error": "not found"})
        if not self.authorized():
            return self.reply(401, {"error": "unauthorized"})
        future = None
        try:
            if self.headers.get("Transfer-Encoding"):
                raise RequestError(400, "chunked requests are not supported")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise RequestError(400, "invalid content length") from None
            if not 0 < length <= MAX_BODY:
                raise RequestError(413, "request body is empty or too large")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise RequestError(400, "incomplete request body")
            try:
                body = json.loads(raw, parse_constant=reject_json_constant)
            except (ValueError, UnicodeError):
                raise RequestError(400, "invalid JSON") from None
            validate_request(body)
            if not self.server.slots.acquire(blocking=False):
                raise RequestError(503, "inference queue is full")
            try:
                future = self.server.executor.submit(self.server.engine.infer, body)
            except Exception:
                self.server.slots.release()
                raise
            # Keep the slot occupied until execution finishes, even on disconnect/timeout.
            future.add_done_callback(lambda _: self.server.slots.release())
            result = future.result(timeout=self.server.request_timeout)
            self.reply(200, result)
        except RequestError as exc:
            self.reply(exc.status, {"error": exc.message})
        except TimeoutError:
            if future is not None:
                future.cancel()
                self.reply(504, {"error": "inference timeout"})
            else:
                self.reply(408, {"error": "request timeout"})
        except Exception as exc:
            LOG.error("inference failed: %s", type(exc).__name__)
            self.reply(500, {"error": "inference failed"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--device", default="npu:0")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP listen address")
    parser.add_argument("--port", type=int, default=30000)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--warmup-file", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.environ["HF_HUB_OFFLINE"] = "1"
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="clef-npu") as executor:
        LOG.info("loading Clef on %s", args.device)
        engine = executor.submit(ClefEngine, args.model_path, args.device, args.max_tokens).result()
        for item in json.loads(args.warmup_file.read_text())["requests"]:
            executor.submit(engine.infer, validate_request(item["body"])).result()
        with ClefServer((args.host, args.port), engine, executor,
                        api_key=os.environ.get("CLEF_API_KEY", "")) as server:
            LOG.info("CLEF_READY http://%s:%d/v1/systemone", args.host, args.port)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
