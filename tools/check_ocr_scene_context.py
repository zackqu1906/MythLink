#!/usr/bin/env python3
"""Replay frozen historical OCR through the production scene-context provider.

The manifest contains original capture metadata/OCR plus predeclared expectations.
Optional name enrichment is a separate condition, never a rewrite of history.
Writes full requests/responses without authentication headers. Does not capture
the desktop, change settings, or write to the voice-history dataset.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from proximic_ring.text_processing.context_preparation import (
    CONTEXT_PROMPT, CONTEXT_PROMPT_VERSION, ContextPreparationProvider, organize_ocr)
from proximic_ring.text_processing import context_preparation
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import LLMSettings
from proximic_ring.text_processing.screen_context import ScreenContext
from check_vision_screen_context import configured_settings


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, help="Archived comparison prompt; overrides only this process")
    parser.add_argument("--prompt-version", help="Required with --prompt-file")
    args = parser.parse_args()
    if bool(args.prompt_file) != bool(args.prompt_version):
        parser.error("--prompt-file and --prompt-version must be supplied together")
    prompt = args.prompt_file.read_text() if args.prompt_file else CONTEXT_PROMPT
    prompt_version = args.prompt_version or CONTEXT_PROMPT_VERSION
    context_preparation.CONTEXT_PROMPT = prompt
    context_preparation.CONTEXT_PROMPT_VERSION = prompt_version
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    cases = manifest["cases"]
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    for case in cases:
        if (not case["expected_scene"] or not case["expected_result"]
                or digest(case["raw_capture"]) != case["raw_capture_sha256"]):
            raise ValueError("Missing expectation or changed capture: " + case["id"])
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "raw.jsonl"
    if raw_path.exists():
        raise ValueError("Use a new output directory; raw.jsonl already exists")
    config = configured_settings()
    settings = LLMSettings(enabled=True, provider="volcengine",
        base_url=config["endpoint"].removesuffix("/responses"), model=config["model"],
        api_key=config["key"], api_key_env="", timeout_s=10)
    metadata = {
        "started_at": datetime.now(timezone.utc).isoformat(), "model": settings.model,
        "endpoint": config["endpoint"], "prompt_version": prompt_version,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt_override": str(args.prompt_file.resolve()) if args.prompt_file else None,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "conditions": manifest["conditions"], "repetitions_per_condition": 1,
        "entrypoint": "ContextPreparationProvider.prepare -> OpenAICompatibleTextProcessor._request_chat",
        "cache": "No cache clearing or cold-start claim; actual usage saved per response",
        "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__), ROOT / "src/proximic_ring/text_processing/context_preparation.py",
                      ROOT / "src/proximic_ring/text_processing/screen_context.py",
                      ROOT / "src/proximic_ring/text_processing/llm.py"]},
    }
    (output / "prompt.txt").write_text(prompt)
    (output / "manifest.json").write_bytes(manifest_bytes)
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    # Alternate the condition order across cases so neither is always first.
    with raw_path.open("x") as stream:
        for index, case in enumerate(cases):
            conditions = list(manifest["conditions"])
            if index % 2:
                conditions.reverse()
            for condition in conditions:
                capture = json.loads(json.dumps(case["raw_capture"]))
                raw = ScreenContext(capture["context"], evidence=capture["evidence"])
                if condition == "application_name":
                    name = case.get("application_name_evidence", {}).get("name")
                    if not name:
                        raise ValueError("No verified application name: " + case["id"])
                    raw["application_name"] = name
                wire = {}

                def recording_urlopen(req, timeout):
                    wire["request"] = json.loads(req.data)
                    wire["timeout_s"] = timeout
                    with request.urlopen(req, timeout=timeout) as response:
                        body = response.read()
                        wire["http_status"] = response.status
                    wire["response"] = json.loads(body)
                    return io.BytesIO(body)

                provider = ContextPreparationProvider(OpenAICompatibleTextProcessor(urlopen=recording_urlopen))
                started = time.perf_counter()
                try:
                    result = provider.prepare(raw, settings)
                finally:
                    provider.close()
                trace = result.evidence["context_preparation"]
                row = {"id": case["id"] + "--" + condition, "case_id": case["id"], "prompt_version": prompt_version,
                    "condition": condition, "source_record": case["source_record"],
                    "expected_scene": case["expected_scene"], "expected_result": case["expected_result"],
                    "input_sha256": digest(trace["input"]), "capture_sha256": case["raw_capture_sha256"],
                    "latency_s": round(time.perf_counter() - started, 6),
                    "prepared_context": dict(result), "trace": trace, "wire": wire}
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(json.dumps({k: row[k] for k in ("id", "latency_s")} | {
                    "status": trace["status"], "scene": result.get("scene"),
                    "error": trace.get("error", "")}, ensure_ascii=False), flush=True)
    metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
    recorded_rows = [json.loads(line) for line in raw_path.read_text().splitlines()]
    metadata["cases_processed"] = len(recorded_rows)
    metadata["requests"] = sum(bool(row["wire"].get("request")) for row in recorded_rows)
    metadata["locally_rejected"] = sum(not row["wire"].get("request") for row in recorded_rows)
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
