#!/usr/bin/env python3
"""Organize the saved 150 edit cases and source excerpts; never call a model."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from tools.check_llm_edit_router import metrics
from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS
from markdown_it import MarkdownIt

OUT = ROOT / "docs/reports"
STEM = "llm-edit-150-review-2026-11-09"
FLOW = "manual-edit-routing-flow-code-2026-11-09"
SOURCE = OUT / "llm-edit-natural-full-2026-11-09.json"
FOLLOWUP = OUT / "llm-edit-natural-error-generation-2026-11-09.json"
e = html.escape

CSS = """
:root{--ink:#192738;--muted:#59687a;--line:#dce4ec;--bg:#f2f5f9;--teal:#087c75;--red:#a43d31}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif}
main{max-width:1240px;margin:auto;padding:36px 30px 80px}header{background:#132d42;color:#fff;padding:32px;border-radius:18px;margin-bottom:24px}
h1{font-size:29px;line-height:1.4;margin:8px 0 12px}h2{font-size:23px;margin:36px 0 16px}h3{font-size:18px;margin:8px 0 14px}p{margin:9px 0 16px}
a{color:#08766e;text-underline-offset:3px}header a{color:#b9eee7}.eyebrow{letter-spacing:.1em;font-size:12px;color:#b9d5e8}.muted{color:var(--muted)}
nav{display:flex;gap:20px;flex-wrap:wrap}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.kpi{background:white;border:1px solid var(--line);padding:18px;border-radius:12px}.kpi b{display:block;font-size:28px;color:var(--teal)}
.panel,.case{background:#fff;border:1px solid var(--line);border-radius:12px;padding:22px;margin-bottom:18px}.case{scroll-margin-top:150px}.case.bad{border-left:5px solid #c27143}
.case-head{display:flex;align-items:start;justify-content:space-between;gap:20px}.case-head h3{overflow-wrap:anywhere}.badge{display:inline-block;border-radius:7px;padding:2px 10px;background:#e3f3ef;color:#116b5c;white-space:nowrap;font-size:13px}.badge.bad{background:#fff0e7;color:#9a4c22}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.field{background:#f6f8fb;border:1px solid #e4eaf0;padding:14px;border-radius:8px;min-width:0}.label{font-size:12px;color:var(--muted);margin-bottom:6px;letter-spacing:.03em}.mono,code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
pre{font-size:13px;line-height:1.75;white-space:pre-wrap;overflow-wrap:anywhere;margin:5px 0}.text{white-space:pre-wrap;overflow-wrap:anywhere}code{font-size:.9em}pre code{font-size:13px}
.callout{background:#fff7e8;border-left:4px solid #ce9650;padding:16px 20px;border-radius:5px;margin:20px 0}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:12px;text-align:left;vertical-align:top}th{background:#edf2f6;white-space:nowrap}td{overflow-wrap:anywhere}
details{border-top:1px solid var(--line);margin-top:14px;padding-top:12px}summary{cursor:pointer;color:#08766e;font-weight:600}.raw pre{max-height:650px;overflow:auto;background:#f6f8fb;padding:16px}
.filters{position:sticky;top:0;z-index:5;background:#eaf0f6f5;padding:14px;border:1px solid var(--line);border-radius:10px;display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px;backdrop-filter:blur(10px)}
input,select,button{font:inherit;padding:9px 12px;border:1px solid #bdcad6;border-radius:7px;background:white;color:var(--ink)}input{flex:1;min-width:220px}button{cursor:pointer}#shown{align-self:center;font-size:13px}[hidden]{display:none!important}.gen{margin-top:16px;border-left:3px solid #9cc8c0;padding-left:16px}.gen.bad{border-left-color:#c98563}.flowdoc pre{background:#eaf0f6;padding:18px;border-radius:8px}.flowdoc h2{border-top:1px solid var(--line);padding-top:24px}
@media(max-width:760px){main{padding:16px 12px 40px}header{padding:22px}h1{font-size:23px}.kpis{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}.case{padding:16px}.case-head{display:block}.filters{position:static}}
@media print{body{background:white;font-size:11px}main{padding:0;max-width:none}header{background:white;color:#192738;border-bottom:2px solid #192738;padding:0}.eyebrow{color:#192738}.filters,nav,.raw{display:none}.case,.panel{break-inside:avoid;box-shadow:none}.case[hidden]{display:none!important}.kpi b{font-size:22px}h2{break-after:avoid}}
"""


def labels(values):
    return "、".join(EDIT_TASK_LABELS[t] for t in values) or "无可执行编辑"


def pct(value):
    return f"{value * 100:.1f}%" if value is not None else "—"


def sec(value):
    return f"{value:.3f} 秒" if value is not None else "未测试"


def fenced(text, language="text"):
    # Input text is data, so pick a fence it cannot close.
    fence = "````" if "```" in text else "```"
    return f"\n{fence}{language}\n{text}\n{fence}\n"


def table(headers, rows):
    return '<div class="tablewrap"><table><thead><tr>' + ''.join('<th>'+e(str(x))+'</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+e(str(x))+'</td>' for x in row)+'</tr>' for row in rows) + '</tbody></table></div>'


def mdtable(headers, rows):
    def cell(x):
        return str(x).replace('|', r'\|').replace('\n', '<br>')
    return '\n|'+'|'.join(map(cell, headers))+'|\n|'+'|'.join('---' for _ in headers)+'|\n' + ''.join('|'+'|'.join(map(cell, row))+'|\n' for row in rows)


def code_excerpt(path, start, end):
    lines = (ROOT/path).read_text().splitlines()
    link = f"[{path}:{start}]({ROOT/path}:{start})"
    return link + '\n' + fenced('\n'.join(f'{i:>4}  {lines[i-1]}' for i in range(start, end+1)), '')


def flow_document():
    d = ["# 手动＋编辑路由：整体流程与代码标注\n",
         "截至 2026-11-09，已搭建并实测的是 `tools/check_llm_edit_router.py` 评测原型。应用仍只有手动与自动两个可选路由模式；第三模式的设置、tap／右滑分流、异步会话和写回连接尚未实现。下面把目标流程与已经运行的代码区分清楚。\n",
         "## 1. 你要求的完整应用流程\n",
         fenced("选择「手动＋编辑路由」【模式入口待接入】\n  ↓\n录音，获取 ASR 最终口述及当次可编辑原文／OCR【沿用现有采集能力，第三模式连接待接入】\n  ├─ tap 结束 → 手动听写处理 → 原有目标／光标校验与写回【待接入】\n  └─ 右滑结束 → 明确进入编辑，不调用听写／编辑分类【待接入】\n         ↓\n     六类定义＋选择／排除条件＋完整口述＋原文片段＋OCR 片段【已在原型实现】\n         ↓\n     火山 DeepSeek 第一次请求：直接输出中文任务类别【已实测】\n         ↓\n     类别白名单校验 → EditPlan【已实现】\n         ├─ 无可执行编辑 → 不生成、不回退成听写【原型行为】\n         ├─ 非法／重复／不可用类别 → 记录错误、停止该次处理【原型行为】\n         └─ 有效任务 → 自动编辑共用提示词＋选中任务模块【已复用】\n                           ↓\n                     火山 DeepSeek 第二次请求：生成最终全文【已实测】\n                           ↓\n                     校验 submit_text_edit 工具结果【已复用】\n                           ↓\n                     本轮保存正文和耗时到报告【已实现】\n                     实际输入法目标／光标／范围校验与写回【第三模式待接入】"),
         "这轮没有使用 Clef/Jev，也没有先发一次听写／编辑判断。路由是六类多标签选择，一句可同时得到多个类别。测试只从“手动已选编辑”的位置进入。\n",
         "## 2. 实现范围与代码入口\n",
         mdtable(['位置','状态','职责'], [
             ('tools/check_llm_edit_router.py','本轮新增评测原型','构造 LLM 路由提示词、读取设置、调用、解析、计时和评分'),
             ('tools/data/llm_edit_routing_natural_cases.json','本轮新增合成样例','24 条有效编辑＋6 条边界；预期标签只用于评分'),
             ('text_processing/router_prompts.py','复用现有代码','六类定义与选择／排除边界'),
             ('text_processing/task_routing.py','复用现有代码','类别、任务可用性、EditPlan、路由输入裁剪'),
             ('text_processing/llm.py','复用现有代码','Ark Responses 网络调用与正文工具返回校验'),
             ('text_processing/automatic_processing.py','复用现有代码','按实际 EditPlan 执行一次自动编辑生成'),
             ('text_processing/automatic_prompts.py','复用现有代码','共用目标／组合规则＋选中的任务模块＋OCR 边界'),
             ('模式选择、tap／右滑、会话回调、输入法写回','尚未接入第三模式','后续应用集成，不是此次评测脚本的已有能力'),
         ]),
         "下面的代码片段直接从当前文件提取，左侧数字是原文件行号，不是可直接复制执行的代码内容。除评测脚本外的路径均位于 `src/proximic_ring/`。\n",
         "## 3. 现有应用仍只有两个模式\n",
         code_excerpt('src/proximic_ring/text_processing/model.py', 15, 18),
         "这里没有新增第三个模式常量。不能把下面的评测入口描述为已经能在应用界面选择的新模式。\n",
         "## 4. 类别来自自动模式，空原文排除修改\n",
         code_excerpt('src/proximic_ring/text_processing/task_routing.py', 11, 18),
         code_excerpt('src/proximic_ring/text_processing/task_routing.py', 30, 32),
         "六类分别为修改、插入、润色、格式整理、翻译、按目标创作。任务集合可包含多类，排列不代表操作执行顺序。当前原文为空时只排除修改，与自动模式规则一致；原型使用的是已知源文本样例，尚未模拟原生输入上下文不可用的状态。\n",
         "## 5. 把现有类别规则包装成 LLM 提示词\n",
         code_excerpt('tools/check_llm_edit_router.py', 49, 69),
         "标注：50 行读取可用任务；53 行保留现有多任务组合规则，去掉联合总体意图的前置要求；55–56 行明确手动已经选择编辑；63–66 行直接复用六类定义及 apply/skip 条件；67–68 行只让模型输出中文类别。没有另写一套不同的六类分类标准。\n",
         f"原始类别标准见 [router_prompts.py:86]({ROOT/'src/proximic_ring/text_processing/router_prompts.py'}:86) 和 [选择／排除条件:97]({ROOT/'src/proximic_ring/text_processing/router_prompts.py'}:97)。\n",
         "## 6. 第一次请求只做编辑任务路由\n",
         code_excerpt('tools/check_llm_edit_router.py', 165, 177),
         "168–172 行调用火山文本模型；`edit_tool=None` 表示这次不要正文编辑工具调用；最多输出 128 tokens。173 行保留模型原始中文返回，174 行再解析，176 行仅在客户端拿预期标签评分，预期标签没有发送给模型。\n",
         code_excerpt('src/proximic_ring/text_processing/task_routing.py', 141, 148),
         "口述最长 2000 字符且保持完整；原文使用末 400 字符；OCR 取最后 4 条消息拼接后的末 360 字符。正文生成阶段则接收完整原文和完整给定上下文。这与当前自动路由的输入范围一致。\n",
         "## 7. 实际火山请求使用 Responses 接口\n",
         code_excerpt('src/proximic_ring/text_processing/llm.py', 258, 282),
         "此处仍需用 JSON 封装 HTTP 协议，这是 API 传输格式；模型的分类正文是普通中文文本，不是 JSON。路由请求不带 `questions`、`response_format` 或 `tools`，不调用 SystemOne。`stream=False`，明确关闭深度思考。虽然上层函数传入 `temperature=0.0`，当前 Ark 分支并未把 temperature 写入请求体，不能称为已强制 temperature=0。配置读取见下面代码，不展示密钥内容。\n",
         code_excerpt('tools/check_llm_edit_router.py', 114, 128),
         "## 8. 中文类别转换成现有 EditPlan\n",
         code_excerpt('tools/check_llm_edit_router.py', 72, 85),
         "`修改、润色` 会映射成 `EditPlan((\"modify\", \"polish\"))`。这里只做格式、白名单和任务可用性校验，不按关键词重判用户意图。支持顿号、中文／英文逗号或换行分隔；未知类别、附加解释会被拒绝，重复任务由 EditPlan 的校验拒绝。“无可执行编辑”返回 None。没有编造分类概率。\n",
         "## 9. 第二次请求复用自动模式的正文处理\n",
         code_excerpt('tools/check_llm_edit_router.py', 178, 191),
         "`generate` 是评测开关，不是用户产品设置。150 条均测路由，其中 24 条这个开关开启；4 条错误路由后来冻结实际计划单独补测生成。`edit_plan=plan` 使用真实预测，绝不替换成预期答案。未开启生成或没有计划的样例不伪造正文。\n",
         code_excerpt('src/proximic_ring/text_processing/automatic_processing.py', 75, 88),
         "78 行按实际计划组装自动提示词，83–84 行传入完整原文和上下文，85–87 行调用原有全文编辑工具，`max_attempts=1`。分类用自然语言；正文继续使用既有 `submit_text_edit` / `modified_text` 协议，以保持后续处理一致。\n",
         code_excerpt('src/proximic_ring/text_processing/automatic_prompts.py', 133, 149),
         "提示词组成是：共用编辑要求＋通用多步骤组合规则＋选中类别模块＋只读 OCR 边界。没有把六类所有模块都塞给正文模型；也不按类别排列强制执行顺序。\n",
         "## 10. 一条真实记录走完这条链\n",
         fenced('原文：周六见面。带好证件。\n口述：把周六改为周日，末尾加原话：请准时。然后把全文整理成编号列表\nDeepSeek 路由原始输出：修改、插入、格式整理\n内部计划：modify + insert + format\n正文：\n1. 周日见面。\n2. 带好证件。\n3. 请准时。\n路由耗时：0.911 秒\n路由＋正文：2.013 秒'),
         "这条样例在完整结果中的编号是 146，ID 为 `fresh-three-tasks`。完整请求正文、原始响应、token、模型输出和语义复核都保存在结果报告。\n",
         "## 11. 本轮评测边界\n",
         "150 条有效编辑任务集合匹配 145 条；24 条新样例的路由全部正确，但其正文语义复核为 20 条通过、4 条不通过。6 条无有效需求边界单列。没有采集新的音频或屏幕，没有运行输入法写回，没有 JSON 输出或同条件 Clef 对照。当前请求多数命中前缀缓存。\n",
         "后续真正接入第三模式时，还需要把本轮路由能力接到手动结束动作、保存会话模式、异步请求取消／迟到回调处理、不可用源上下文判断和原有写回校验；这些不能仅凭离线请求成功宣称已经完成。\n",
         f"[150 条完整结果]({STEM}.html) · [150 条可读文本]({STEM}.md)\n"]
    return '\n'.join(d)


def main():
    original = json.loads(SOURCE.read_text())
    supplement = json.loads(FOLLOWUP.read_text())
    cases = [c for c in original['cases'] if c['cohort'] != 'guard']
    guards = [c for c in original['cases'] if c['cohort'] == 'guard']
    follow = {c['id']: c for c in supplement['cases']}
    assert len(cases) == 150 and len({c['id'] for c in cases}) == 150
    assert len(guards) == 6 and len(follow) == 4
    assert set(follow) <= {c['id'] for c in cases}
    aggregate = metrics(cases)
    total = aggregate['all']
    assert total['exact'] == 145 and aggregate['generation']['semantic_reviews'] == {'pass':20, 'fail':4}
    numbered = {c['id']: i for i,c in enumerate(cases,1)}
    assert numbered['fresh-three-tasks'] == 146
    for c in cases:
        if c['id'] in follow:
            assert set(c['actual_tasks']) == set(follow[c['id']]['edit_plan']['tasks'])
    usage = [c['calls'][0]['response']['usage'] for c in cases]
    created = datetime.fromisoformat(original['created_at']).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    dataset = {
        'title':'手动＋编辑路由：150 条完整测试结果',
        'measured_at_beijing':created, 'model':original['model'],
        'source_report':str(SOURCE), 'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'followup_report':str(FOLLOWUP), 'followup_sha256':hashlib.sha256(FOLLOWUP.read_bytes()).hexdigest(),
        'scope':'只整理已有结果，不重跑模型。主统计为150条有效编辑；6条边界独立附录。',
        'metrics_150':aggregate,
        'coverage':{'routing':150,'main_route_and_generation':24,'followup_generation':4,'generation_not_tested':122},
        'routing_cache':{'hit_requests':sum(u.get('input_tokens_details',{}).get('cached_tokens',0)>0 for u in usage),'requests':150},
        'metadata':{k:v for k,v in original.items() if k not in {'cases','metrics','latency_context'}},
        'cases':[{**c,'number':numbered[c['id']], 'supplemental_generation':follow.get(c['id'])} for c in cases],
        'excluded_boundary_cases':guards,
    }
    (OUT/f'{STEM}.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2)+'\n')
    introduction = f"实测开始时间：{created}（北京时间）。模型：{original['model']}。本报告重新整理已保存的真实响应，不重新调用模型，不调整预期标签。"
    methodology = "所有 150 条均已明确属于编辑；一次输出可包含多个任务。按完整任务集合评分，多一个或少一个都算不匹配。126 条旧合成开发回归＋24 条本轮新增合成样例；不代表真实用户独立接受率。模型直接返回中文类别，未返回分类概率。"
    timing_note = "计时包含网络及完整响应校验，不包含 ASR、OCR 采集或输入法写回。请求串行，无自动重试。P50/P95 使用 nearest-rank。150 条中 149 条路由命中服务端前缀缓存。本表对150条重新计算，未沿用156条的P50。"
    coverage_note = "150 条全部测试了路由；24 条同轮继续生成，语义复核20通过／4不通过；4条多选类别另行冻结实际预测补测生成，3通过／1不通过。其余122条未测试正文，不计入生成通过率。补测不是一次连续链路，不与原路由耗时相加。"
    summary_rows = [
        ('完整任务集合匹配','145 / 150',pct(145/150)),('旧回归样例','121 / 126',pct(121/126)),
        ('新增样例','24 / 24','100.0%'),('路由请求／解析成功','150 / 150','100.0%'),
        ('路由 P50 / P95',sec(total['routing_latency']['p50_s']),sec(total['routing_latency']['p95_s'])),
        ('路由均值 / 最大',sec(total['routing_latency']['mean_s']),sec(total['routing_latency']['max_s'])),
        ('同轮正文生成 P50 / P95（24条）',sec(total['generation_latency']['p50_s']),sec(total['generation_latency']['p95_s'])),
        ('路由＋正文 P50 / P95（24条）',sec(total['route_plus_generation_latency']['p50_s']),sec(total['route_plus_generation_latency']['p95_s'])),
        ('同轮正文语义复核','20 / 24 通过','4 条问题'),('另行补测正文','3 / 4 通过','1 条问题'),
    ]
    task_rows = []
    for task,stat in total['per_task'].items():
        task_rows.append((EDIT_TASK_LABELS[task],stat['tp']+stat['fn'],stat['tp'],stat['fp'],stat['fn'],pct(stat['precision']),pct(stat['recall'])))
    error_rows = [(f"{numbered[c['id']]:03}",c['id'],c['utterance'],labels(c['expected_tasks']),c['raw_labels'],c.get('routing_review',{}).get('note','')) for c in cases if not c['route_exact']]
    md = ['# 手动＋编辑路由：150 条完整测试结果\n',introduction,methodology,'## 汇总',mdtable(['指标','结果 A','结果 B'],summary_rows),timing_note,coverage_note,
          '## 六类指标（可多选）','TP＝选对；FP＝多选；FN＝漏选。类别样本数可以重叠，不能相加当作150条。',mdtable(['类别','预期包含','TP','FP','FN','精确率','召回率'],task_rows),
          '## 5 条路由不匹配',mdtable(['编号','ID','口述','预期','实际','复核说明'],error_rows),
          '## 150 条逐条明细','每条保留完整口述、原文、给定 OCR、真实路由输入、原始类别输出、实际计划和耗时。未生成的结果明确标注；完整 API 请求／响应见同名 HTML 的逐条原始证据及同名 JSON。']
    parts = [f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>150 条编辑路由完整结果</title><style>{CSS}</style><main>',
             '<header><div class="eyebrow">MYTHLINK · DEEPSEEK ROUTING REVIEW · 2026.11.09</div><h1>150 条编辑路由，逐条可核对</h1><p>'+e(introduction)+'</p>',
             f'<nav><a href="#summary">结果汇总</a><a href="#errors">5 条分类差异</a><a href="#cases">全部 150 条</a><a href="#guards">边界附录</a><a href="{FLOW}.html">流程与代码标注</a><a href="{STEM}.md">完整 Markdown</a><a href="{STEM}.json">原始数据</a></nav></header>',
             '<div class="kpis"><div class="kpi"><span>任务集合匹配</span><b>96.7%</b><span>145 / 150</span></div><div class="kpi"><span>路由 P50</span><b>0.765 s</b><span>仅150条有效编辑</span></div><div class="kpi"><span>路由 P95</span><b>1.129 s</b><span>包含网络及校验</span></div><div class="kpi"><span>同轮正文复核</span><b>20 / 24</b><span>分类与正文分别评分</span></div></div>',
             '<h2 id="summary">汇总与统计范围</h2><div class="panel"><p>'+e(methodology)+'</p>'+table(['指标','结果 A','结果 B'],summary_rows)+'<p class="muted">'+e(timing_note)+'</p></div>',
             '<div class="callout">'+e(coverage_note)+'</div><h2>六类表现</h2><div class="panel"><p>可多选：TP＝选对，FP＝多选，FN＝漏选。类别样本重叠，不能相加当作150条。</p>'+table(['类别','预期包含','TP','FP','FN','精确率','召回率'],task_rows)+'</div>',
             '<h2 id="errors">5 条路由差异</h2><div class="panel">'+table(['编号','ID','口述','预期','实际','复核说明'],error_rows)+'</div>',
             '<h2 id="cases">全部 150 条完整明细</h2><p>输入、原文和输出均保留完整文本。可筛选分类差异、正文问题或任务类别；每条底部可展开真实 API 证据。</p>',
             '<div class="filters"><input id="search" aria-label="搜索样例" placeholder="搜索口述、原文、OCR、样例 ID…"><select id="result" aria-label="结果筛选"><option value="all">全部结果</option><option value="mismatch">仅路由不匹配（5）</option><option value="match">仅路由匹配（145）</option><option value="genfail">正文复核问题（含补测，5）</option><option value="notgenerated">未测试正文（122）</option></select><select id="cohort" aria-label="样例组"><option value="all">全部样例组</option><option value="regression">旧回归（126）</option><option value="fresh">新增（24）</option></select><select id="task" aria-label="任务筛选"><option value="all">全部任务（预期或实际）</option>'+''.join('<option value="'+k+'">'+e(v)+'</option>' for k,v in EDIT_TASK_LABELS.items())+'</select><button id="reset">重置</button><span id="shown">显示 150 / 150</span></div>']
    for c in cases:
        n=numbered[c['id']]
        supplement_case=follow.get(c['id'])
        generation=c if 'final_text' in c else supplement_case
        gen_review=(c.get('generation_review') if 'final_text' in c else supplement_case.get('review') if supplement_case else None)
        genfail=bool(gen_review and gen_review['status']=='fail')
        cohort='新增合成样例' if c['cohort']=='fresh' else '既有回归样例'
        route_status='匹配' if c['route_exact'] else '不匹配'
        usage=c['calls'][0]['response']['usage']
        ocr='\n'.join(f"{m.get('speaker','unknown')}：{m.get('text','')}" for m in c['context'].get('messages',[])) or '无 OCR 参考'
        actual_input=c['calls'][0]['request']['input'][1]['content'][0]['text']
        rawdata={'main_case':c,'supplemental_generation':supplement_case}
        search=json.dumps([c['id'],c['utterance'],c['target'],c['context'],c['raw_labels'],generation.get('final_text','') if generation else ''],ensure_ascii=False)
        parts.append(f'<article class="case {"bad" if not c["route_exact"] else ""}" id="case-{n:03}" data-search="{e(search,quote=True)}" data-result="{"match" if c["route_exact"] else "mismatch"}" data-cohort="{c["cohort"]}" data-tasks="{e(" ".join(set(c["expected_tasks"])|set(c["actual_tasks"])))}" data-genfail="{str(genfail).lower()}" data-generated="{str(bool(generation)).lower()}">')
        parts.append(f'<div class="case-head"><div><h3>{n:03} · {e(c["id"])}</h3><div class="muted">{cohort} · 路由 {sec(c["routing_latency_s"])}</div></div><span class="badge {"bad" if not c["route_exact"] else ""}">任务集合{route_status}</span></div>')
        parts.append('<div class="grid">'+''.join(f'<div class="field"><div class="label">{label}</div><div class="text">{e(value)}</div></div>' for label,value in [('当前口述（完整）',c['utterance']),('可编辑原文（完整）',c['target'] or '【空原文】'),('预期类别',labels(c['expected_tasks'])),('模型原始中文输出',c['raw_labels'])])+'</div>')
        parts.append('<div class="field" style="margin-top:14px"><div class="label">给定 OCR 参考（完整）</div><div class="text">'+e(ocr)+'</div></div>')
        parts.append('<p class="muted">解析后实际任务：'+e(labels(c['actual_tasks']))+'；输入 '+str(usage['input_tokens'])+' tokens（缓存 '+str(usage.get('input_tokens_details',{}).get('cached_tokens',0))+'），输出 '+str(usage['output_tokens'])+' tokens。分类概率：模型未返回。</p>')
        if c.get('routing_review'):parts.append('<p><strong>路由复核：</strong>'+e(c['routing_review']['note'])+'</p>')
        md.extend([f"### {n:03} · {c['id']}",f"样例组：{cohort}。路由任务集合：**{route_status}**。路由耗时：**{sec(c['routing_latency_s'])}**。",
                   '**当前口述**',fenced(c['utterance']),'**可编辑原文**',fenced(c['target'] or '【空原文】'),'**给定 OCR 参考**',fenced(ocr),
                   f"预期类别：{labels(c['expected_tasks'])}。模型原始输出：**{c['raw_labels']}**。解析后任务：{labels(c['actual_tasks'])}。",
                   f"路由输入 {usage['input_tokens']} tokens（缓存 {usage.get('input_tokens_details',{}).get('cached_tokens',0)}），输出 {usage['output_tokens']} tokens。未返回分类概率。"])
        if c.get('routing_review'):md.append('路由复核：'+c['routing_review']['note'])
        if generation:
            same='同轮路由后的正文生成' if 'final_text' in c else '冻结实际错误计划后的另行补测（未重新路由）'
            gtime=c.get('generation_latency_s') if 'final_text' in c else generation['latency_s']
            gnote='；连续路由＋正文 '+sec(c['total_latency_s']) if 'final_text' in c else '；与原路由不在同一次连续调用，不合算端到端耗时'
            checked='通过' if generation.get('constraint_passed') is True else '不通过' if generation.get('constraint_passed') is False else '未配置机械约束'
            judgement='通过' if gen_review['status']=='pass' else '不通过'
            parts.append('<div class="gen '+('bad' if genfail else '')+'"><h3>'+same+'</h3><p>生成耗时 '+sec(gtime)+gnote+'。</p><pre>'+e(generation['final_text'])+'</pre><p><strong>语义复核：'+judgement+'</strong>；机械约束：'+checked+'。</p><p>'+e(gen_review['note'])+'</p></div>')
            md.extend(['**'+same+'**',f'生成耗时：{sec(gtime)}{gnote}。',fenced(generation['final_text']),f"语义复核：**{judgement}**；机械约束：{checked}。{gen_review['note']}"])
        else:
            parts.append('<div class="gen"><strong>正文生成：未测试</strong><p class="muted">该条只测试路由，没有生成正文；不计入生成质量或端到端耗时统计。</p></div>')
            md.append('**正文生成：未测试。** 本条只测试路由，不计入正文质量或端到端耗时统计。')
        parts.append('<details><summary>查看路由模型实际收到的输入</summary><pre>'+e(actual_input)+'</pre></details><details class="raw"><summary>查看完整上下文、预期、真实 API 请求／响应及正文证据</summary><pre>'+e(json.dumps(rawdata,ensure_ascii=False,indent=2))+'</pre></details></article>')
        md.extend(['**路由模型实际收到的输入**',fenced(actual_input),f"数据来源：`{c.get('source_file','llm_edit_routing_natural_cases.json')}`。完整请求与响应见 [HTML 第 {n:03} 条]({STEM}.html#case-{n:03})。\n"])
    guardrows=[(c['id'],c['utterance'],c['target'] or '【空】',c['raw_labels'],'匹配' if c['route_exact'] else '不匹配',sec(c['routing_latency_s'])) for c in guards]
    parts.append('<h2 id="guards">独立附录：6 条无有效任务边界</h2><div class="panel"><p>不计入前述150条。预期均为“无可执行编辑”，实际5/6匹配；没有运行正文生成。</p>'+table(['ID','口述','原文','实际输出','结果','路由耗时'],guardrows)+'</div>')
    md.extend(['## 独立附录：6 条无有效任务边界','不计入150条。预期均为无可执行编辑，实际5/6匹配；没有正文生成。',mdtable(['ID','口述','原文','实际输出','结果','路由耗时'],guardrows)])
    footer='没有 JSON 输出格式对照或本轮同条件 Clef 对照，不能据此断言中文分类优于其他方案。源码哈希、完整请求、原始响应及复核均来自已有报告；不展示鉴权请求头。'
    parts.append('<h2>复现与来源</h2><div class="panel"><p>'+e(footer)+'</p><p>原始主报告 SHA256：<code>'+dataset['source_sha256']+'</code></p><p>原始补测报告 SHA256：<code>'+dataset['followup_sha256']+'</code></p><p><a href="'+FLOW+'.html">查看整体流程与逐段代码标注</a></p></div>')
    md.extend(['## 来源与复现',footer,f"原始主报告：`{SOURCE.name}`，SHA256：`{dataset['source_sha256']}`。",f"原始补测报告：`{FOLLOWUP.name}`，SHA256：`{dataset['followup_sha256']}`。",f"[整体流程与代码标注]({FLOW}.md)"])
    parts.append("""<script>
const cards=[...document.querySelectorAll('.case')];const search=document.getElementById('search');const result=document.getElementById('result');const cohort=document.getElementById('cohort');const task=document.getElementById('task');
function filter(){const q=search.value.toLocaleLowerCase().trim();let n=0;for(const c of cards){const d=c.dataset;const matchesResult=result.value==='all'||(result.value==='genfail'?d.genfail==='true':result.value==='notgenerated'?d.generated==='false':d.result===result.value);const show=(!q||d.search.toLocaleLowerCase().includes(q))&&matchesResult&&(cohort.value==='all'||d.cohort===cohort.value)&&(task.value==='all'||d.tasks.split(' ').includes(task.value));c.hidden=!show;if(show)n++;}document.getElementById('shown').textContent=`显示 ${n} / 150`;}
for(const x of [search,result,cohort,task])x.addEventListener('input',filter);document.getElementById('reset').addEventListener('click',()=>{search.value='';result.value='all';cohort.value='all';task.value='all';filter();});
</script></main></html>""")
    (OUT/f'{STEM}.html').write_text(''.join(parts))
    (OUT/f'{STEM}.md').write_text('\n\n'.join(md)+'\n')
    flow=flow_document()
    (OUT/f'{FLOW}.md').write_text(flow)
    rendered=MarkdownIt('commonmark',{'html':False}).enable('table').render(flow)
    (OUT/f'{FLOW}.html').write_text(f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>手动＋编辑路由流程与代码</title><style>{CSS}</style><main class="flowdoc"><nav><a href="{STEM}.html">返回150条报告</a><a href="{FLOW}.md">Markdown版本</a></nav>{rendered}</main></html>')
    with zipfile.ZipFile(OUT/f'{STEM}-bundle.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for stem,suffixes in ((STEM,('.html','.md','.json')),(FLOW,('.html','.md'))):
            for suffix in suffixes:archive.write(OUT/(stem+suffix),stem+suffix)
    print(json.dumps({'cases':len(cases),'exact':total['exact'],'generation_same_run':24,'generation_supplement':len(follow),'generation_not_tested':122,'report':str(OUT/f'{STEM}.html'),'flow':str(OUT/f'{FLOW}.html')},ensure_ascii=False))


if __name__=='__main__':
    main()
