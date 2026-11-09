#!/usr/bin/env python3
"""Live synthetic OCR → compact context → one-call routed edit, without writeback."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from tools.check_llm_edit_router import configured_settings
from proximic_ring.text_processing.context_preparation import ContextPreparationProvider, CONTEXT_PROMPT_VERSION
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import TextProcessingRequest, INPUT_ROUTING_ROUTED_EDIT
from proximic_ring.text_processing.processing_dispatch import process_text_request
from proximic_ring.text_processing.screen_context import ScreenContext


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Do not overwrite previous evidence")
    settings = configured_settings()
    examples = [
        ("人名纠正", "联系人是张伟，请明天联系他。", "根据上文纠正人名，其他不动。", "请联系张玮。", "请联系张伟。", ["modify"]),
        ("保留未完成状态", "可以做适配，但目前还没有开始。", "根据上文写一句项目状态通知，不要添加其他信息。", "", None, ["compose"]),
        ("时间纠正", "会议改到周五下午三点，不是周四。", "根据上文纠正会议时间，其他不动。", "周四下午三点开会。", "周五下午三点开会。", ["modify"]),
    ]
    report = {"scope": "合成 OCR 坐标和文字；正式预取、等待和编辑分派；无截图采集、无实际写回",
              "model": settings.model, "prompt_version": CONTEXT_PROMPT_VERSION, "cases": []}
    for i, (name, message, speech, source, expected, tasks) in enumerate(examples, 1):
        calls = []
        def request(req, **kwargs):
            if len(calls) >= 2:
                raise AssertionError("Expected one context call and one route/body call")
            calls.append({"endpoint": req.full_url, "request": json.loads(req.data)})
            return urlopen(req, **kwargs)
        processor = OpenAICompatibleTextProcessor(urlopen=request)
        provider = ContextPreparationProvider(processor)
        raw = ScreenContext({"source": "screen_ocr", "processing": "raw", "status": "ok",
            "application": "com.tencent.xinWeChat", "layout": {"sidebar": .3, "history_bottom": .76}},
            evidence={"ocr_lines": [
                {"text": "无关联系人李四", "box": [.1, .2, .1, .02], "confidence": .99},
                {"text": message, "box": [.4, .4, .4, .03], "confidence": .99, "speaker": "other"},
                {"text": "未发送的无关草稿", "box": [.4, .85, .4, .03], "confidence": .99}]})
        row = {"name": name, "ocr_lines": raw.evidence["ocr_lines"], "calls": calls}
        started = time.perf_counter()
        try:
            future = provider.prefetch(raw, settings)
            req = TextProcessingRequest(i, i, "edit", speech, settings, source,
                routing_mode=INPUT_ROUTING_ROUTED_EDIT, screen_context=future)
            result = process_text_request(processor, req, on_trace=lambda *a: None)
            context = future.result()
            row.update(context_preparation=context.evidence["context_preparation"], context=dict(context),
                result=asdict(result), expected_text=expected,
                route_ok=list(result.edit_plan.tasks if result.edit_plan else []) == tasks,
                text_ok=result.text == expected if expected is not None else None,
                request_count=len(calls))
        except Exception as exc:
            row["error"] = str(exc)
        finally:
            row["total_s"] = round(time.perf_counter() - started, 3)
            provider.close(); processor.close()
        report["cases"].append(row)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: v for k, v in row.items() if k in {"name", "context", "route_ok", "text_ok", "request_count", "total_s", "error"}}, ensure_ascii=False), flush=True)
    return int(any(c.get("error") or not c.get("route_ok") or c.get("text_ok") is False
                   or c.get("context", {}).get("status") != "ok" for c in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
