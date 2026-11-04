"""Readable joint and archived two-stage results, including every apply/skip probability."""
from html import escape
import json
from pathlib import Path

from proximic_ring.text_processing.task_routing import TASK_LABELS, EDIT_TASK_LABELS, LEGACY_EDIT_TASK_LABELS, LEGACY_EDIT_HINT_LABELS


def write_routing_html(report, path):
    labels = {**EDIT_TASK_LABELS, **LEGACY_EDIT_TASK_LABELS, **LEGACY_EDIT_HINT_LABELS}
    def text(value):
        return escape(str(value))
    def percent(value):
        return "—" if value is None else f"{value * 100:.2f}%"
    def seconds(value):
        return "—" if value is None else f"{value:.3f}"
    def plan(value):
        if not value:
            return "无有效结果"
        if value.get("task") == "dictation":
            return "听写"
        names = [labels.get(k, k) for k in value.get("tasks", [])]
        names += ["【" + labels.get(k, k) + "】" for k in value.get("hints", [])]
        return "编辑：" + ("＋".join(names) or "无有效计划")
    def table(headers, rows):
        return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{text(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
    def row(values, correct=None, warning=False):
        css = ' class="warn"' if warning else "" if correct is None else (' class="ok"' if correct else ' class="bad"')
        return '<tr' + css + '>' + ''.join(f'<td>{text(v)}</td>' for v in values) + '</tr>'

    summary, details = [], []
    for index, case in enumerate(report["cases"], 1):
        trace = case.get("trace", {})
        stages = {s["stage"]: s for s in trace.get("stages", [])}
        joint = stages.get("joint")
        primary = joint or stages.get("intent", {})
        secondary = joint or stages.get("edit_details")
        probabilities = primary.get("answers", {}).get("voice_intent", {}).get("probabilities", {})
        summary.append(row([index, case["id"], case["utterance"], plan(case["expected"]),
                            plan(case.get("actual")), "完全匹配" if case["exact_match"] else "不匹配",
                            percent(probabilities.get("dictation")), percent(probabilities.get("edit")),
                            seconds(primary.get("latency_s")), seconds(None if joint else (secondary or {}).get("latency_s")),
                            seconds(trace.get("latency_s"))], case["exact_match"], bool(trace.get('warning'))))
        context = '\n'.join(f"{m['speaker']}: {m['text']}" for m in case.get("context", {}).get("messages", [])) or "无"
        block = f'<section><h2>{index}. {text(case["id"])}</h2>'
        block += f'<p><b>口述：</b>{text(case["utterance"])}<br><b>可编辑原文：</b>{text(case["target"] or "空")}<br><b>OCR：</b>{text(context)}</p>'
        block += f'<p>预期：{text(plan(case["expected"]))}<br>实际：{text(plan(case.get("actual")))}</p>'
        if joint:
            raw_tasks = [labels.get(k, k) for k, a in joint.get('answers', {}).items() if k != 'voice_intent' and a.get('choice') == 'apply']
            raw_task = joint.get('answers', {}).get('voice_intent', {}).get('choice')
            block += '<p>原始模型意图：' + text(TASK_LABELS.get(raw_task, raw_task)) + '；原始 apply 任务：' + text('、'.join(raw_tasks) or '无') + '。</p>'
        if trace.get('warning'):
            block += '<p class="warn"><b>校验提示（标黄）：</b>' + text(trace['warning']) + '</p>'
        block += f'<p>总体意图：听写 {percent(probabilities.get("dictation"))}；编辑 {percent(probabilities.get("edit"))}。</p>'
        if "final_text" in case:
            block += f'<p><b>生成正文：</b></p><pre>{text(case["final_text"])}</pre>'
            block += f'<p>生成耗时 {seconds(case.get("generation_latency_s"))} 秒；分类＋生成总耗时 {seconds(case.get("total_latency_s"))} 秒。下方如有自动检查，只验证列出的文本约束；生成文本的完整语义仍需逐条审阅。</p>'
        checks = case.get("generation_checks", {}).get("checks", [])
        if checks:
            block += table(["生成检查", "预期", "结果"], [row([c["kind"], c["expected"], "通过" if c["passed"] else "未通过"], c["passed"]) for c in checks])
        if case.get("review_notes"):
            block += '<p class="bad"><b>语义审阅：</b>' + text('；'.join(case['review_notes'])) + '</p>'
        if case.get("generation_requests"):
            block += '<details><summary>实际文本生成请求（提示词、输入与工具 schema）</summary><pre>' + text(json.dumps(case['generation_requests'], ensure_ascii=False, indent=2)) + '</pre></details>'
        if case.get("error"):
            block += f'<p class="bad">错误：{text(case["error"])}</p>'
        if secondary is None:
            reason = "一级选择听写，按项目流程跳过二级；没有二级概率，不能记为 0。" if trace.get("task") == "dictation" else "未获得有效二级响应；概率缺失，不能记为 0。"
            block += f'<p>{reason}</p>'
        else:
            chosen = set(case["expected"].get("tasks", [])) | set(case["expected"].get("hints", []))
            expected_edit = case["expected"]["task"] == "edit"
            rows = []
            for name in secondary.get("answers", {}):
                if name == "voice_intent":
                    continue
                label = labels.get(name, name)
                answer = secondary.get("answers", {}).get(name, {})
                p = answer.get("probabilities", {})
                expected = ("apply" if name in chosen else "skip") if expected_edit or joint else "不应调用二级"
                actual = answer.get("choice", "无结果")
                unscored_hint = name in LEGACY_EDIT_HINT_LABELS and "hints" not in case["expected"]
                if unscored_hint:
                    expected = "旧标签，仅展示不计分"
                aliases = {"precise_edit": "modify", "verbatim_insert": "insert", "expand": "polish", "condense": "polish", "structure": "format"}
                # Archived reports use their own expected labels; current comparisons can rename
                # old fields, but cannot reconstruct polish/format scores from legacy organize.
                renamed = aliases.get(name)
                if renamed in chosen:
                    expected = "apply"
                folded_legacy = name == "organize" and "organize" not in chosen
                if folded_legacy:
                    expected = "旧合并字段，不能自动拆分为新任务"
                correct = actual == expected if (expected_edit or joint) and not unscored_hint and not folded_legacy else None
                rows.append(row([label, name, expected, actual, percent(p.get("apply")), percent(p.get("skip")),
                                 "—" if correct is None else ("正确" if correct else "错误")], correct))
            block += table(["任务／辅助标签", "字段", "预期", "实际", "apply 概率", "skip 概率", "结果"], rows)
        block += f'<details><summary>实际分类输入 state</summary><pre>{text(trace.get("input_state", "未记录"))}</pre></details></section>'
        details.append(block)

    m = report["metrics"]
    content = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Clef 分类报告</title>
<style>body{{font:15px/1.6 system-ui,sans-serif;margin:28px;color:#182128;background:#fafbfc}}h1{{font-size:25px}}h2{{font-size:19px}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;background:white;width:100%}}th,td{{border:1px solid #dce2e7;padding:8px;vertical-align:top;white-space:pre-wrap;min-width:70px}}th{{background:#eaf0f5}}.bad{{background:#fff0ed}}.warn{{background:#fff8db;color:#76520b}}p.warn{{padding:12px;border:1px solid #e5be54}}section{{margin-top:30px;border-top:2px solid #c9d5df;padding-top:12px}}pre{{white-space:pre-wrap;background:#eef2f6;padding:12px}}summary{{cursor:pointer}}</style>
<h1>Clef 分类报告</h1><p>生成时间：{text(report.get('created_at', ''))}；提示词版本：{text(report.get('prompt_version', ''))}</p><p>{text(report.get("note", ""))}</p>
<p>原始总体意图正确 {m['root_correct']}/{m['samples']}；原始完整分类匹配 {m.get('raw_exact_plan_correct', m['exact_plan_correct'])}/{m['samples']}；校验后最终处理计划匹配 {m['exact_plan_correct']}/{m['samples']}；
编辑样例任务完整匹配 {m.get('edit_plan_correct', m.get('secondary_plan_correct', 0))}/{m['edit_samples']}；有效请求返回 {m.get('requests', m.get('secondary_called', 0))} 次；原始联合矛盾 {m.get('contradictions', 0)} 次；标黄提示 {m.get('warnings', 0)} 次；错误 {m['errors']}。</p>
<p>新校验策略中听写优先；分类不明确回退听写。标黄结果保留原始编辑 apply 概率，但不执行这些任务。校验后匹配的提升不是模型原始分类准确率提升；旧报告按其当时策略展示。</p>
<p>分类总耗时中位数 {seconds(m['latency_median_s'])} 秒，P95 {seconds(m['latency_p95_s'])} 秒。
各阶段耗时都包含 HTTP/SSH 网络及服务端处理，不含 ASR、OCR 采集、文本生成和写回。</p>
<p>各版本的问题字段以本次实际响应为准；当前为总体意图和 {len(EDIT_TASK_LABELS)} 个任务，旧报告保留原分类及辅助标签。每题 apply/skip 互斥，不同题可以组合，概率不能跨题相加。
所有概率按服务端返回值展示，尚未校准为真实正确率。这是合成样例评测，不代表真实用户准确率。</p>
{table(['#', '编号', '口述', '预期', '最终处理', '最终匹配', '听写概率', '编辑概率', '联合或一级/秒', '任务响应/秒', '总计/秒'], summary)}
{''.join(details)}
<details><summary>本次完整提示词与问题 schema</summary><pre>{text(json.dumps(report.get('prompts', {}), ensure_ascii=False, indent=2))}</pre></details></html>'''
    Path(path).write_text(content, encoding="utf-8")
