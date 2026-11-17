#!/usr/bin/env python3
"""Evaluate the application's current routed-edit entry point, one request per case.

Only local synthetic fixtures are sent. No microphone, live OCR or writeback.
The frozen historical runner and its old prompt are deliberately not invoked.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tools.check_llm_edit_router import configured_settings, load_cases, latency_stats, REGRESSION_FILES
from tools.check_automatic_edit_generation import check_output, RecordedResponse
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.routed_edit_prompts import ROUTED_EDIT_PROMPT_VERSION, single_pass_prompt
from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS

NATURAL = ROOT / "tools/data/llm_edit_routing_natural_cases.json"
AMBIGUITY = ROOT / "tools/data/manual_routed_edit_ambiguity_cases.json"
BASELINE = ROOT / "docs/reports/llm-edit-single-pass-reviewed-2026-11-09.json"


def expectations(case):
    if case["cohort"] == "guard":
        return {"modified_text": case["target"]}
    result = dict(case.get("generation_expected", {}))
    original = case.get("expected", {})
    for key in ("modified_text", "contains", "contains_ci", "excludes", "max_chars"):
        if key in original:
            result[key] = original[key]
    if original.get("preserve_text"):
        result["contains"] = [*result.get("contains", []), *original["preserve_text"]]
    return result


def run_case(case, settings):
    record = {**case, "calls": [], "generation_expected": expectations(case)}

    def capture(request, **kwargs):
        if record["calls"]:
            raise RuntimeError("Evaluation forbids a second HTTP request")
        payload = json.loads(request.data.decode("utf-8"))
        messages = payload["input"]
        assert messages[0]["content"][0]["text"] == single_pass_prompt(case["target"])
        actual_input = json.loads(messages[1]["content"][0]["text"])
        assert actual_input == {"utterance": case["utterance"].strip(), "editable_text": case["target"],
                                "screen_context": case["context"]}
        # Deliberately never record authentication headers or settings.api_key.
        call = {"endpoint": request.full_url, "request": payload}
        record["calls"].append(call)
        started = time.perf_counter()
        try:
            response = urlopen(request, **kwargs)
            call["http_status"] = response.status
            return RecordedResponse(response, call, started)
        except Exception as exc:
            call.update(http_latency_s=time.perf_counter() - started, error_type=type(exc).__name__)
            raise

    processor = OpenAICompatibleTextProcessor(urlopen=capture)
    started = time.perf_counter()
    try:
        final, raw, model_input, plan = processor.process_routed_edit_with_trace(
            case["utterance"], settings, case["target"], case["context"])
        record.update(final_text=final, raw_response=raw, model_input=model_input,
                      raw_labels=raw.splitlines()[0].removeprefix("分类："), actual_tasks=list(plan.tasks))
        record["route_exact"] = (set(plan.tasks) == set(case["expected_tasks"])) if case["cohort"] != "guard" else None
        record["checks"] = check_output(record["generation_expected"], final)
        record["constraint_passed"] = all(c["passed"] for c in record["checks"]) if record["checks"] else None
        record["unchanged"] = final == case["target"]
        record["generation_review"] = {"status": "pending"}
    except Exception as exc:
        record.update(error=str(exc), error_type=type(exc).__name__,
                      raw_response=str(getattr(exc, "model_output", "")),
                      model_input=getattr(exc, "model_input", None),
                      route_exact=False if case["cohort"] != "guard" else None)
    finally:
        record["latency_s"] = time.perf_counter() - started
        processor.close()
    return record


def score(cases):
    return {"n": len(cases), "valid_responses": sum(not c.get("error") for c in cases),
            "route_exact": sum(c.get("route_exact") is True for c in cases),
            "route_scored": sum(c.get("route_exact") is not None for c in cases),
            "errors": sum(bool(c.get("error")) for c in cases),
            "unchanged": sum(c.get("unchanged", False) for c in cases),
            "constraint_passed": sum(c.get("constraint_passed") is True for c in cases),
            "constraint_failed": sum(c.get("constraint_passed") is False for c in cases),
            "constraint_unchecked": sum(c.get("constraint_passed") is None for c in cases),
            "semantic_reviews": dict(Counter(c.get("generation_review", {}).get("status", "unavailable") for c in cases)),
            "route_and_body_passed": sum(c.get("route_exact") is True and c.get("generation_review", {}).get("status") == "pass" for c in cases),
            "latency": latency_stats([c["latency_s"] for c in cases if not c.get("error")])}


def write_report(report, path):
    cases = report["cases"]
    report["metrics"] = {"existing_edit_150": score([c for c in cases if c["cohort"] in ("regression", "fresh")]),
                         **{g: score([c for c in cases if c["cohort"] == g]) for g in ("regression", "fresh", "ambiguity", "guard")}}
    usages = [call.get("response", {}).get("usage", {}) for c in cases for call in c["calls"]]
    report["usage"] = {"requests": sum(len(c["calls"]) for c in cases),
                       "input_tokens": sum(u.get("input_tokens", 0) for u in usages),
                       "output_tokens": sum(u.get("output_tokens", 0) for u in usages),
                       "cached_requests": sum(u.get("input_tokens_details", {}).get("cached_tokens", 0) > 0 for u in usages),
                       "cached_tokens": sum(u.get("input_tokens_details", {}).get("cached_tokens", 0) for u in usages)}
    if BASELINE.exists():
        old = json.loads(BASELINE.read_text())
        prior = {c["id"]: c for c in old["cases"]}
        paired = [c for c in cases if c["cohort"] in ("regression", "fresh") and c["id"] in prior]
        report["comparison"] = {"source": str(BASELINE.relative_to(ROOT)), "model": old["model"],
            "note": "历史单次请求 v1 对照，非同期随机 A/B；语义复核为 Codex 离线判断，非独立盲评。旧取消/含糊指令限制已改变，历史语义分数不能全部直接归因比较。",
            "paired_n": len(paired), "old_route_exact": sum(prior[c["id"]]["route_exact"] for c in paired),
            "new_route_exact": sum(c["route_exact"] for c in paired),
            "old_metrics": old["metrics"]["effective_edit_150"],
            "route_changed": [{"id":c["id"], "expected":c["expected_tasks"], "before":prior[c["id"]].get("actual_tasks"), "after":c.get("actual_tasks")} for c in paired if set(c.get("actual_tasks", [])) != set(prior[c["id"]].get("actual_tasks", []))]}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    e = lambda value: html.escape(str(value))
    body = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>手动编辑 v4 实测</title>',
            '<style>body{font:15px/1.65 system-ui;max-width:1180px;margin:32px auto;padding:0 22px;color:#172438}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f9;padding:14px;border-radius:8px}article{border:1px solid #ccd4e0;border-radius:10px;margin:20px 0;padding:20px}.bad{border-left:5px solid #d65742}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}h3{margin-top:0}details{margin-top:12px}summary{cursor:pointer}a{color:#2456aa}</style>',
            '<h1>当前手动＋编辑路由提示词实测</h1><p>' + e(report["scope"]) + '</p><p>模型：' + e(report["model"]) + '；版本：' + e(report["prompt_version"]) + '</p>',
            '<p>每例严格一次请求，无重试。延迟为含网络的完整响应及解析耗时；nearest-rank P50/P95。分类按预先冻结标签计分；机械约束与语义复核分开。六条旧边界单独观察。</p>',
            '<p>' + e(report.get("review_policy", "语义复核待完成。")) + '</p>',
            '<p><a href="' + e(path.name) + '">下载完整请求／响应 JSON</a></p><h2>指标</h2><pre>' + e(json.dumps(report["metrics"], ensure_ascii=False, indent=2)) + '</pre>']
    for i, c in enumerate(cases, 1):
        review = c.get("generation_review", {"status":"unavailable"})
        bad = c.get("error") or c.get("route_exact") is False or review["status"] == "fail"
        body.append('<article id="' + e(c["id"]) + '" class="' + ('bad' if bad else '') + '"><h3>' + e(f'{i:03} · {c["id"]}') + '</h3><p>' + e(f'{c["cohort"]} · {c["latency_s"]:.3f} 秒') + '</p><div class="grid">')
        for label, value in (("口述", c["utterance"]), ("原文", c["target"] or "【空原文】"), ("预期分类", "、".join(EDIT_TASK_LABELS[t] for t in c["expected_tasks"]) or "旧边界：不计分类准确率"), ("实际分类", c.get("raw_labels", "未解析成功"))):
            body.append('<div><strong>' + label + '</strong><pre>' + e(value) + '</pre></div>')
        body.append('</div><strong>最终正文</strong><pre>' + e(c.get("final_text", "【未取得有效正文】")) + '</pre><p>语义复核：' + e(review["status"]) + '；机械约束：' + e(c.get("constraint_passed")) + '</p><p>' + e(review.get("note", "")) + '</p>')
        if c.get("error"):
            body.append('<p><strong>失败原因：</strong>' + e(c["error"]) + '</p>')
        body.append('<details><summary>完整输入、实际请求、原始返回和评分</summary><pre>' + e(json.dumps(c, ensure_ascii=False, indent=2)) + '</pre></details></article>')
    body.append('<details><summary>提示词全文、哈希、历史对照与用量</summary><pre>' + e(json.dumps({k:v for k,v in report.items() if k != "cases"}, ensure_ascii=False, indent=2)) + '</pre></details></html>')
    path.with_suffix(".html").write_text("".join(body))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    cases = load_cases(NATURAL) + load_cases(AMBIGUITY, regression=False)
    assert len({c["id"] for c in cases}) == len(cases)
    if args.case:
        if set(args.case) - {c["id"] for c in cases}:
            raise ValueError("Unknown case IDs")
        cases = [c for c in cases if c["id"] in args.case]
    for case in cases:
        single_pass_prompt(case["target"])
        expectations(case)
    if args.validate_only:
        print("CASES_AND_PRODUCTION_PROMPT_OK", len(cases), ROUTED_EDIT_PROMPT_VERSION)
        return 0
    if args.output.exists() or args.output.with_suffix(".jsonl").exists():
        raise ValueError("Refusing to overwrite an existing run")
    settings = configured_settings()
    sources = [Path(__file__), NATURAL, AMBIGUITY, *(ROOT / "tools/data" / f for f in REGRESSION_FILES),
               *sorted((ROOT / "src/proximic_ring/text_processing").glob("*.py"))]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "prompt_version": ROUTED_EDIT_PROMPT_VERSION,
              "model": settings.model, "timeout_s": settings.timeout_s,
              "scope": "150条既有编辑回归＋12条省略/含糊指令＋6条旧边界。应用真实 process_routed_edit_with_trace 入口；合成文本，不含实时 ASR、OCR 捕获或输入法写回。",
              "source_sha256": hashes, "prompts": {"nonempty": single_pass_prompt("原文"), "empty": single_pass_prompt("")}, "cases": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.with_suffix(".jsonl").open("x") as journal:
            for i, case in enumerate(cases, 1):
                result = run_case(case, settings)
                report["cases"].append(result)
                journal.write(json.dumps(result, ensure_ascii=False) + "\n")
                journal.flush()
                print(f'{i}/{len(cases)} {case["id"]} route={result.get("route_exact")} error={result.get("error_type", "none")} {result["latency_s"]:.3f}s', flush=True)
                if len(report["cases"]) >= 3 and all(c.get("error") and not any("response" in call for call in c["calls"]) for c in report["cases"][-3:]):
                    raise RuntimeError("Stopped after three consecutive transport/pre-request failures")
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["completed_all"] = len(report["cases"]) == len(cases)
        report["sources_unchanged"] = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == digest for p, digest in hashes.items())
        write_report(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
