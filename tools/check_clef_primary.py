#!/usr/bin/env python3
"""Reproduce the frozen legacy v3 primary decision; current application uses check_clef_router.py."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from proximic_ring.text_processing.intent_router import ClefIntentRouter
BASELINE = json.loads((Path(__file__).parent / "data/clef_hierarchical_baseline_v3.json").read_text())
ROUTER_PROMPT_VERSION = BASELINE["version"]
from proximic_ring.text_processing.task_routing import router_input, select_multiclass_task
from clef_cases import DEFAULT_CASES, load_cases
from clef_primary_report import write_primary_html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", help="SystemOne URL; default: PROXIMIC_CLEF_URL")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case", action="append", help="Select an ID; repeat for multiple cases")
    parser.add_argument("--validate-only", action="store_true", help="Validate the editable case file without network calls")
    parser.add_argument("--dump-request", action="store_true", help="Show actual model inputs without network calls")
    parser.add_argument("--schema", type=Path, help="Optional candidate schema; does not change application prompts")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    schema = json.loads(args.schema.read_text()) if args.schema else BASELINE["prompts"]["intent"]
    assert set(schema) == {"voice_intent"}
    schema_json = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    try:
        cases = load_cases(args.cases, args.case)
    except ValueError as exc:
        parser.error(str(exc))
    if args.validate_only:
        print(f"CASES_OK: {len(cases)} cases in {args.cases}")
        return 0
    router = ClefIntentRouter(endpoint=args.endpoint)
    if args.dump_request:
        print(json.dumps([{"id": c["id"], "request": {"model": router.model,
              "state": router_input(c["utterance"], c["target"], c["context"]), "questions": schema}}
              for c in cases], ensure_ascii=False, indent=2))
        return 0
    records = []
    try:
        router.warmup()
        for case in cases:
            state = router_input(case["utterance"], case["target"], case.get("context", {}))
            record = {**case, "input_state": state}
            try:
                # Same HTTP transport; deliberately retain the OLD primary argmax/tie policy.
                # Deliberately skip the secondary request: this test measures the primary stage.
                result = router._classify_stage(state, schema, lambda: False)
                answer = result["answers"]["voice_intent"]
                task, _, source = select_multiclass_task(answer["probabilities"])
                record.update(actual=task, probabilities=answer["probabilities"],
                              decision_source=source, correct=task == case["expected"]["task"],
                              trace=result)
            except Exception as exc:
                record.update(error=str(exc), correct=False)
            records.append(record)
            print(json.dumps({k: record[k] for k in ("id", "actual", "probabilities", "correct", "error") if k in record}, ensure_ascii=False), flush=True)
    finally:
        router.close()
    times = sorted(r["trace"]["latency_s"] for r in records if "trace" in r)
    metrics = {"samples": len(records), "correct": sum(r["correct"] for r in records),
               "errors": sum("error" in r for r in records),
               "latency_median_s": statistics.median(times) if times else None,
               "latency_p95_s": times[max(0, (95 * len(times) + 99) // 100 - 1)] if times else None}
    report = {"kind": "synthetic-primary-routing-evaluation", "created_at": datetime.now(timezone.utc).isoformat(),
              "note": "Synthetic inputs, not live OCR/ASR or editing acceptance. Latency includes HTTP/SSH/network. Probabilities are model scores, not calibrated accuracy.",
              "prompt_version": args.schema.name if args.schema else ROUTER_PROMPT_VERSION,
              "schema": schema, "schema_sha256": hashlib.sha256(schema_json.encode()).hexdigest(),
              "cases_path": str(args.cases), "metrics": metrics, "cases": records}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        write_primary_html(report, args.output.with_suffix(".html"))
    print(json.dumps(metrics))
    return 1 if metrics["errors"] else (0 if metrics["correct"] == len(records) else 2)


if __name__ == "__main__":
    raise SystemExit(main())
