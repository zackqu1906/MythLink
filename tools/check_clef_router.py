#!/usr/bin/env python3
"""Evaluate joint Clef routing on synthetic cases, without editing user text."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from proximic_ring.text_processing.intent_router import ClefIntentRouter
from proximic_ring.text_processing.router_prompts import ROUTER_PROMPT_VERSION, routing_schema, joint_router_input
from proximic_ring.text_processing.task_routing import router_input
from clef_cases import DEFAULT_CASES, load_cases, raw_joint_match
from clef_routing_report import write_routing_html

CASE_PATH = DEFAULT_CASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", help="Complete SystemOne endpoint; default: PROXIMIC_CLEF_URL")
    parser.add_argument("--model", help="Server model; default: PROXIMIC_CLEF_MODEL or clef-flash")
    parser.add_argument("--dump-request", action="store_true", help="Export joint schema offline")
    parser.add_argument("--cases", type=Path, default=CASE_PATH, help="Editable JSON case file")
    parser.add_argument("--case", action="append", help="Run/export selected IDs (repeatable)")
    parser.add_argument("--validate-only", action="store_true", help="Check case inputs and labels offline")
    parser.add_argument("--output", type=Path, help="Save probabilities, errors, latency and metrics as JSON plus same-name HTML")
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases, args.case, require_plan=True)
    except ValueError as exc:
        parser.error(str(exc))
    if args.validate_only:
        print(f"CASES_OK: {len(cases)} cases in {args.cases}")
        return 0
    router = ClefIntentRouter(endpoint=args.endpoint, model=args.model)
    if args.dump_request:
        requests = [case_requests(sample, router.model) for sample in cases]
        print(json.dumps(requests[0] if len(requests) == 1 else requests, ensure_ascii=False, indent=2))
        return 0
    records = []
    try:
        router.warmup()  # Configuration only; do not turn an unconfigured run into 32 errors.
        for case in cases:
            record = {**case}
            try:
                decision, raw = router.classify(case["utterance"], case["target"], case.get("context", {}))
                actual = {"task": decision.task, **(decision.edit_plan.as_dict() if decision.edit_plan else {})}
                expected = case["expected"]
                trace = json.loads(raw)
                record.update(actual=actual, trace=trace,
                              root_correct=trace.get("raw_model_task") == expected["task"],
                              exact_match=(decision.task == expected["task"] and
                                  set(actual.get("tasks", [])) == set(expected.get("tasks", []))))
            except Exception as exc:
                record.update(error=str(exc), root_correct=False, exact_match=False)
                if getattr(exc, "model_output", ""):
                    record["trace"] = json.loads(exc.model_output)
                    # A rejected joint plan can still have a correct raw intent prediction.
                    record["root_correct"] = record["trace"].get("raw_model_task") == case["expected"]["task"]
            record['raw_exact_match'] = raw_joint_match(record.get('trace', {}), case['expected'])
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(f"Clef probe failed: {exc}", file=sys.stderr)
        return 1
    finally:
        router.close()
    times = sorted(r["trace"]["latency_s"] for r in records if r.get("trace", {}).get("latency_s") is not None)
    dictation = [r for r in records if r["expected"]["task"] == "dictation"]
    edits = [r for r in records if r["expected"]["task"] == "edit"]
    metrics = {"samples": len(records), "errors": sum("error" in r for r in records),
               "root_correct": sum(r["root_correct"] for r in records),
               "exact_plan_correct": sum(r["exact_match"] for r in records),
               "raw_exact_plan_correct": sum(r["raw_exact_match"] for r in records),
               "warnings": sum(bool(r.get('trace', {}).get('warning')) for r in records),
               "edit_samples": len(edits),
               "requests": sum(len(r.get("trace", {}).get("stages", [])) for r in records),
               "contradictions": sum(bool(({r.get("trace", {}).get("error_code")} | set(r.get('trace', {}).get('warning_codes', []))) & {"dictation_with_tasks", "edit_without_tasks"}) for r in records),
               "edit_plan_correct": sum(r["exact_match"] for r in edits),
               "dictation_samples": len(dictation),
               "false_edit_count": sum(r.get("trace", {}).get("raw_model_task") == "edit" for r in dictation),
               "latency_median_s": statistics.median(times) if times else None,
               "latency_p95_s": times[max(0, (95 * len(times) + 99) // 100 - 1)] if times else None}
    metrics["latency_by_expected_intent"] = {}
    for task in ("dictation", "edit"):
        values = sorted(r["trace"]["latency_s"] for r in records if r["expected"]["task"] == task and "trace" in r)
        metrics["latency_by_expected_intent"][task] = {"n": len(values),
            "median_s": statistics.median(values) if values else None,
            "p95_s": values[max(0, (95 * len(values) + 99) // 100 - 1)] if values else None}
    report = {"kind": "synthetic-routing-evaluation", "note": "Developer-authored cases; not real-user accuracy or end-to-end editing acceptance.",
              "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "prompt_version": ROUTER_PROMPT_VERSION,
              "prompts": {"joint": routing_schema(), "shared_input_prefix": joint_router_input("")},
              "metrics": metrics, "cases_path": str(args.cases), "cases": records}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        write_routing_html(report, args.output.with_suffix(".html"))
    print(json.dumps({"metrics": metrics, "note": report["note"]}, ensure_ascii=False))
    return 1 if metrics["errors"] else (0 if metrics["exact_plan_correct"] == len(records) else 2)


def case_requests(sample, model):
    state = router_input(sample["utterance"], sample["target"], sample["context"])
    requests = [{"stage": "joint", "body": {"model": model, "state": joint_router_input(state), "questions": routing_schema()}}]
    return {"case": sample["id"], "expected": sample["expected"], "requests": requests}


if __name__ == "__main__":
    raise SystemExit(main())
