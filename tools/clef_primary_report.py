"""Standalone HTML report with every primary result and both probabilities."""
from html import escape
import json
from pathlib import Path


def write_primary_html(report, path):
    labels = {"dictation": "听写", "edit": "编辑"}
    metrics = report["metrics"]
    def text(value):
        return escape(str(value))
    def percent(value):
        return "—" if value is None else f"{value * 100:.2f}%"
    rows = []
    for index, case in enumerate(report["cases"], 1):
        probabilities = case.get("probabilities", {})
        old = case.get("baseline_probabilities", {})
        context = "\n".join(f"{m['speaker']}: {m['text']}" for m in case.get("context", {}).get("messages", [])) or "无"
        latency = case.get("trace", {}).get("latency_s")
        values = [index, case["id"], case["utterance"], case["target"] or "空", context,
                  labels[case["expected"]["task"]], labels.get(case.get("actual"), case.get("error", "无结果")),
                  percent(probabilities.get("dictation")), percent(probabilities.get("edit")),
                  "正确" if case["correct"] else "错误", "—" if latency is None else f"{latency:.3f}",
                  percent(old.get("dictation")), percent(old.get("edit"))]
        rows.append('<tr class="' + ("ok" if case["correct"] else "bad") + '">' +
                    "".join(f"<td>{text(value)}</td>" for value in values) + "</tr>")
    headings = ["#", "编号", "当前口述", "可编辑原文", "OCR 参考上文", "预期", "实际", "听写概率", "编辑概率", "结果", "耗时/秒", "旧版听写概率", "旧版编辑概率"]
    notes = "".join(f"<p>{text(note)}</p>" for note in report.get("report_notes", []))
    changes = report.get("prompt_changes", [])
    if changes:
        notes += '<table><thead><tr><th>修改项</th><th>修改前</th><th>修改后</th></tr></thead><tbody>'
        notes += ''.join('<tr>' + ''.join(f'<td>{text(part)}</td>' for part in change) + '</tr>' for change in changes)
        notes += '</tbody></table><p>下面所有结果均只评价一级。后续二级任务和最终文字质量不在本轮验收范围内。</p>'
    before = report.get("baseline_schema")
    before_html = "" if before is None else f'<details><summary>修改前的完整一级 prompt 与类别定义</summary><pre>{text(json.dumps(before, ensure_ascii=False, indent=2))}</pre></details>'
    content = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Clef 一级分类测试报告</title>
<style>body{{font:15px/1.6 system-ui,sans-serif;margin:28px;color:#182128;background:#fafbfc}}
h1{{font-size:25px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;background:white;width:100%}}
th,td{{border:1px solid #dce2e7;padding:9px;vertical-align:top;white-space:pre-wrap;min-width:75px}}
th{{background:#eaf0f5}}td:nth-child(3),td:nth-child(4),td:nth-child(5){{min-width:160px}}
.bad{{background:#fff0ed}}.ok td:nth-child(10){{color:#12633d}}pre{{white-space:pre-wrap;background:#eef2f6;padding:16px}}
summary{{cursor:pointer;font-weight:600;margin:16px 0}}.muted{{color:#586773}}</style>
<h1>Clef 一级分类测试报告</h1>
<p>正确 {metrics['correct']} / {metrics['samples']}；调用错误 {metrics['errors']}。
版本：{text(report['prompt_version'])}。时间：{text(report.get('created_at', ''))}</p>
<p class="muted">每行均显示听写和编辑两类概率。它们是模型分数，尚未校准为真实正确率。
耗时包含客户端 HTTP/SSH 网络和服务器处理，不含二级分类、ASR、OCR 采集、文本生成或写回。
样例为人工构造文本；未运行真实截图或麦克风。</p>{notes}
<div class="scroll"><table><thead><tr>{''.join(f'<th>{text(h)}</th>' for h in headings)}</tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
{before_html}<details><summary>本次完整一级 prompt 与类别定义</summary><pre>{text(json.dumps(report['schema'], ensure_ascii=False, indent=2))}</pre></details>
<p class="muted">schema SHA256: {text(report['schema_sha256'])}。旧版列为“—”表示未提供对应基线，不能推断该样例改前表现。</p>
</html>'''
    Path(path).write_text(content, encoding="utf-8")
