#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jev / Nimble 语音输入意图分类测试程序
=================================================

任务：参考已有文本，判断当前语句是「听写」还是「编辑」。

Jev 不是聊天模型：它接收 state（状态）+ questions（类型化问题），
直接返回结构化答案（choice / probabilities / confidence），不生成任何文本。
因此本程序把你的 prompt 拆成两部分：
  · 「已有文本 / 当前语句」  -> state
  · 角色、判断规则、注意事项              -> question 的 instructions
两个类别的定义放进 criteria，于是：
  Choice.probabilities = {"听写": p0, "编辑": p1}   <- 两个分类的置信度
  Choice.confidence    = 模型对本次判断的整体确信度（0~1）
另外可以用 --with-uncertainty 追加一个 noul 问题 information_sufficient，
用来实现你 prompt 里「信息不足或语义模糊，不要强行确定」那一条（默认关闭，
两个原语的实测表现见 README）。

Jev 后端只用标准库，Python >= 3.10。
本地 Nimble 使用独立环境，安装及下载步骤见 NIMBLE_TESTING.md。

常用命令
--------
  python3 jev_intent_tester.py                     # 跑全部测试用例（自动创建 test_cases.json）
  python3 jev_intent_tester.py --list              # 列出所有用例 id
  python3 jev_intent_tester.py --id edit_01        # 只跑一个用例
  python3 jev_intent_tester.py --repeat 5 --id edit_01   # 同一用例跑 5 次，看稳定性
  python3 jev_intent_tester.py --interactive       # 交互模式，随时改 document/context/utterance
  python3 jev_intent_tester.py --once -u "把明天改成周三" -d "会议定在明天" -c ""
  python3 jev_intent_tester.py --mock              # 离线跑通流程（不发网络请求）
  python3 jev_intent_tester.py --dump-prompt --id edit_01   # 看真正发给 API 的内容

API Key
-------
优先级：--api-key 参数 > 环境变量 TYPESAFE_API_KEY > 同目录 .env 文件 > 下面 DEFAULT_API_KEY
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

# =====================================================================
# 1. 配置
# =====================================================================

DEFAULT_BASE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 3
DEFAULT_WORKERS = 4

# 你给的 API Key。生产环境建议改从环境变量 TYPESAFE_API_KEY 读取，别把 key 提交到 git。
DEFAULT_API_KEY = (
    "apikey_281040cd5617f9e424680395a27e3191c8c_"
    "f44a2f2c724c87f8366001754f868803dfddaa2aa1ee8d461b26158efbf6f8b6"
)

BASE_DIR = Path(__file__).resolve().parent
CASES_FILE = BASE_DIR / "test_cases.json"
ENV_FILE = BASE_DIR / ".env"
RESULT_DIR = BASE_DIR / "results"

# 空字符串的占位符：让模型明确知道「这里确实没有内容」，而不是漏传字段
EMPTY_PLACEHOLDER = "（无）"

LABEL_DICTATE = "听写"
LABEL_EDIT = "编辑"
LABELS = (LABEL_DICTATE, LABEL_EDIT)

# =====================================================================
# 2. Prompt（改这里就行）
# =====================================================================
# 下面这段就是你给的 prompt。最后一个"已有文本："是分界线：
#   · 分界线之前的部分  -> 作为 question 的 instructions（角色 + 判断规则 + 注意事项）
#   · 分界线开始的部分  -> 作为 state（已有文本 / 当前语句）
# 所以你只需要维护这一份 prompt，改完两边自动同步。

PROMPT_TEMPLATE = """你是语音输入系统中的意图分类器。
根据已有文本和当前语句，判断用户是在“听写”还是“编辑”。

听写：
用户正在继续口述内容，希望将当前语句追加到已有文本后。

编辑：
用户希望修正或调整已有文本，包括删除、替换、改写、纠错、
修改标点或格式、撤销等。

编辑不一定包含明确的修改指令。
用户经常省略“把……改成……”“不是……是……”等表达，
只说出修正后的词语或短句。这种情况也属于编辑。

判断时比较两种解释：
1. 将当前语句接在已有文本后，是否构成自然的续写。
2. 将当前语句替换已有文本中的某个片段，是否更符合口语纠错。

以下线索支持省略式纠错：
- 当前语句与已有文本中的片段高度重合，尤其是末尾片段，
  仅有少数字词、读音、数字或名称发生变化。
- 替换后能修正明显的错字、同音误识别或不通顺表达。
- 直接追加会造成局部重复或语义不连贯，而替换解释更自然。

不要仅因当前语句很短、与原文存在相同词语，
或包含“改、删、换”等词，就判断为编辑。
如果当前语句自然补全未完成的表达，或增加新的信息，应判断为听写。
如果续写与纠错两种解释都合理，应体现不确定性，不要强行高置信度判断。

示例：
已有文本：我在咖啡店和牛奶
当前语句：喝牛奶
判断：编辑。省略式纠错，将“和牛奶”修正为“喝牛奶”。

已有文本：会议定在周三下午三点。
当前语句：下午四点
判断：编辑。只说出修正后的时间，替换原来的时间。

已有文本：我在咖啡店
当前语句：喝牛奶
判断：听写。自然补全原句，形成“我在咖啡店喝牛奶”。

已有文本：我在咖啡店喝牛奶。
当前语句：还点了一块蛋糕
判断：听写。继续补充新内容。

已有文本：
{document}
当前语句：
{utterance}"""

# Choice 两个选项的判定标准（与上面 prompt 里的定义保持一致，可独立微调）
PROMPT_CRITERIA = {
    LABEL_DICTATE: "用户希望当前说出的内容本身被写入文档：新内容、继续往下写、补充、追加、口述一段话。",
    LABEL_EDIT: (
        "用户希望系统修改已有文本，而不是把当前话语原样写入文档：删除、替换、改写、"
        "纠错、修改标点或格式、调整顺序、撤销等操作。"
        "包括省略式纠错：用户只说出修正后的词语或短句，希望替换已有文本中的对应片段，"
        "不一定包含修改动词。注意：话语里出现「改/删/换成/重新」"
        "这些词，但如果它本身就是要写进文档的内容，仍属于听写。"
    ),
}

# 附加问题：实现 prompt 里「信息不足不要强行确定」
# 注意：noul 返回的是「该陈述为真」的概率，所以这里要用正向陈述句。
# 早期版本写成「是否包含足够信息来判断？」（疑问句 + 否定倾向），模型几乎对每个用例
# 都给出 0.1 左右的低值，连「我们下周要开一次全员大会」这种毫无歧义的听写也不例外，
# 说明措辞本身把模型带偏了。改成正向陈述后与判断正确率的相关性才正常。
UNCERTAINTY_INSTRUCTIONS = (
    "当前语句的意图是明确无歧义的，不存在「听写」和「编辑」两种解释都说得通的情况。"
)
UNCERTAINTY_KEY = "information_sufficient"


def split_prompt(template: str) -> tuple[str, str]:
    """把 prompt 拆成 (instructions, state_template)。"""
    marker = "已有文本："
    idx = template.rfind(marker)
    if idx == -1:
        raise ValueError(f"prompt 里找不到分界线「{marker}」，无法拆分 instructions / state")
    return template[:idx].strip(), template[idx:].strip()


# =====================================================================
# 3. 测试用例
# =====================================================================
# expected=None 表示「这题本身就没有唯一答案」，程序只记录模型输出的不确定性，
# 不计入准确率——正好用来验证 prompt 里「不要强行确定」这条。

DEFAULT_CASES: list[dict] = [
    # ---- 明确的编辑 ----
    dict(id="edit_01", expected=LABEL_EDIT,
         document="会议定在明天下午三点，地点是 3 号会议室。",
         context="", utterance="把明天改成周三",
         note="出现「改」，且指向已有文本 -> 编辑"),
    dict(id="edit_02", expected=LABEL_EDIT,
         document="今天天气不错，我们出去走走吧。",
         context="", utterance="把最后一句删掉",
         note="删除指令"),
    dict(id="edit_03", expected=LABEL_EDIT,
         document="预算控制在十万元以内。",
         context="", utterance="不是十万，是二十万",
         note="口语纠正，不含编辑动词"),
    dict(id="edit_04", expected=LABEL_EDIT,
         document="第一、要保证质量。第二、要控制成本",
         context="", utterance="第一句后面加个句号",
         note="改标点"),
    dict(id="edit_05", expected=LABEL_EDIT,
         document="联系人是张伟。",
         context="", utterance="不对，是张伟明",
         note="纠错人名"),
    dict(id="edit_06", expected=LABEL_EDIT,
         document="我们决定取消这个项目。",
         context="", utterance="撤销，回到上一版",
         note="撤销"),
    dict(id="edit_07", expected=LABEL_EDIT,
         document="先做设计，再做需求分析。",
         context="", utterance="顺序反了，调过来",
         note="调整顺序"),
    dict(id="edit_08", expected=LABEL_EDIT,
         document="需求分析 设计 开发 测试",
         context="", utterance="改成有序列表",
         note="改格式"),
    dict(id="edit_09", expected=LABEL_EDIT,
         document="项目预计两周完成。",
         context="用户上一句：把「两周」改成「三周」",
         utterance="后面再加一句：如有变动另行通知",
         note="上下文里已经在改，这句是接着的指令"),

    # ---- 明确的听写（包含「改/删/修改」的陷阱） ----
    dict(id="dict_01", expected=LABEL_DICTATE,
         document="", context="", utterance="我们下周要开一次全员大会",
         note="最普通的听写"),
    dict(id="dict_02", expected=LABEL_DICTATE,
         document="这个方案我们不改了，",
         context="", utterance="就按原来的执行",
         note="陷阱：出现「不改了」，但它是内容本身"),
    dict(id="dict_03", expected=LABEL_DICTATE,
         document="", context="", utterance="用户可以通过语音直接修改文档内容",
         note="陷阱：出现「修改」，但整句是要写进文档的话"),
    dict(id="dict_04", expected=LABEL_DICTATE,
         document="", context="", utterance="他说：“这个功能不能删，删了整个流程就断了。”",
         note="陷阱：引号里的「删」是引述内容"),
    dict(id="dict_05", expected=LABEL_DICTATE,
         document="今天天气不错，", context="", utterance="下午我们去公园吧",
         note="接着已有文本继续写"),
    dict(id="dict_06", expected=LABEL_DICTATE,
         document="会议时间定在", context="", utterance="下午两点半",
         note="短语续写"),
    dict(id="dict_07", expected=LABEL_DICTATE,
         document="我们明天开会，", context="用户正在口述通知内容",
         utterance="请大家准时参加",
         note="上下文表明是口述"),
    # ---- 信息不足 / 语义模糊（expected=None，不计分） ----
    dict(id="amb_01", expected=None,
         document="", context="", utterance="改一下",
         note="要改什么、改成什么都没说"),
    dict(id="amb_02", expected=None,
         document="谢谢大家。", context="", utterance="嗯",
         note="单字，无法判断"),
    dict(id="amb_03", expected=None,
         document="", context="", utterance="顺便说一下",
         note="语义不完整"),
    dict(id="amb_04", expected=None,
         document="", context="", utterance="把这句话删掉",
         note="看似是删除指令，但已有文本为空、也没有上下文，无从判断该删什么"),
]


# =====================================================================
# 4. 数据结构
# =====================================================================

@dataclass
class Case:
    id: str
    document: str = ""
    context: str = ""
    utterance: str = ""
    expected: str | None = None
    note: str = ""

    def render_state(self) -> str:
        _, state_template = split_prompt(PROMPT_TEMPLATE)
        return state_template.format(
            document=self.document.strip() or EMPTY_PLACEHOLDER,
            context=self.context.strip() or EMPTY_PLACEHOLDER,
            utterance=self.utterance.strip(),
        )


@dataclass
class Result:
    case_id: str
    utterance: str
    expected: str | None
    predicted: str | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    information_sufficient: float | None = None
    latency_ms: float | None = None
    model_compute_ms: float | None = None
    model: str | None = None
    error: str | None = None
    raw: dict | None = None
    note: str = ""

    @property
    def is_correct(self) -> bool | None:
        if self.expected is None or self.predicted is None:
            return None
        return self.expected == self.predicted

    def p(self, label: str) -> float | None:
        v = self.probabilities.get(label)
        return None if v is None else float(v)


# =====================================================================
# 5. API 客户端
# =====================================================================

class JevClient:
    """只负责发请求，不关心业务。"""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
                 retries: int = DEFAULT_RETRIES, verbose: bool = False):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.retries = max(1, retries)
        self.verbose = verbose

    def build_payload(self, state: str, questions: dict) -> dict:
        return {"state": state, "model": self.model, "questions": questions}

    def call(self, state: str, questions: dict) -> tuple[dict | None, str | None]:
        payload = self.build_payload(state, questions)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "jev-intent-tester/1.0",
        }
        last_err = None
        for attempt in range(1, self.retries + 1):
            req = urlrequest.Request(self.base_url, data=body, headers=headers, method="POST")
            try:
                with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8")), None
            except urlerror.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                last_err = f"HTTP {exc.code}: {detail}"
                # 4xx 基本是配置问题（key 错 / 参数错），重试没意义
                if 400 <= exc.code < 500 and exc.code != 429:
                    return None, last_err
            except Exception as exc:  # 超时、DNS、连接中断……
                last_err = f"{type(exc).__name__}: {exc}"

            if attempt < self.retries:
                backoff = min(2 ** (attempt - 1) * 0.8, 8.0) + random.random() * 0.3
                if self.verbose:
                    print(f"    [重试 {attempt}/{self.retries - 1}] {last_err} -> {backoff:.1f}s 后重试")
                time.sleep(backoff)
        return None, last_err


# =====================================================================
# 6. 分类逻辑（把 prompt 映射到 Jev 的 questions）
# =====================================================================

def build_questions(include_uncertainty: bool = True) -> dict:
    instructions, _ = split_prompt(PROMPT_TEMPLATE)
    questions: dict = {
        "intent": {
            "type": "choice",
            "instructions": instructions,
            "criteria": PROMPT_CRITERIA,
        }
    }
    if include_uncertainty:
        questions[UNCERTAINTY_KEY] = {
            "type": "noul",
            "instructions": UNCERTAINTY_INSTRUCTIONS,
        }
    return questions


def heuristic_classify(case: Case) -> tuple[str, dict[str, float], float, float]:
    """--mock 用的离线兜底，仅用于跑通流程/演示输出格式，不是真实模型。"""
    utt = case.utterance
    doc = case.document.strip()
    ctx = case.context.strip()
    edit_words = ["改成", "改一下", "改为", "换成", "替换", "删掉", "删了", "删除", "去掉",
                  "撤销", "不要", "不对", "错了", "加个", "调过来", "调整", "顺序"]
    quote = bool(re.search(r"[\u201c\u2018\"'].+?[\u201d\u2019\"']", utt))
    hit = any(w in utt for w in edit_words)
    # 有已有文本 + 命中编辑词 + 不是引述 -> 倾向编辑
    score = 0.0
    score += 0.45 if hit else 0.0
    score += 0.3 if (doc or ctx) else 0.0
    score -= 0.6 if quote else 0.0
    if len(utt.strip()) <= 2:
        score = 0.5
    score = min(max(score, 0.02), 0.98)
    p_edit = round(score, 2)
    p_dict = round(1 - p_edit, 2)
    label = LABEL_EDIT if p_edit >= p_dict else LABEL_DICTATE
    conf = abs(p_edit - p_dict)
    info = 0.2 if len(utt.strip()) <= 2 else (0.9 if (doc or ctx or len(utt) > 8) else 0.4)
    return label, {LABEL_DICTATE: p_dict, LABEL_EDIT: p_edit}, conf, info


def evaluate_case(case: Case, client: JevClient | None, include_uncertainty: bool = True,
                  mock: bool = False) -> Result:
    result = Result(case_id=case.id, utterance=case.utterance,
                    expected=case.expected, note=case.note)
    state = case.render_state()
    started = time.perf_counter()

    if mock:
        label, probs, conf, info = heuristic_classify(case)
        result.latency_ms = (time.perf_counter() - started) * 1000
        result.predicted = label
        result.probabilities = probs
        result.confidence = conf
        result.information_sufficient = info
        result.model = "mock"
        return result

    assert client is not None
    resp, err = client.call(state, build_questions(include_uncertainty))
    result.latency_ms = (time.perf_counter() - started) * 1000
    if err or resp is None:
        result.error = err or "unknown error"
        return result

    result.raw = resp
    result.model = resp.get("model")
    compute_seconds = resp.get("nimble", {}).get("metrics", {}).get("total_seconds")
    if compute_seconds is not None:
        result.model_compute_ms = float(compute_seconds) * 1000
    answers = resp.get("answers", {})

    intent = answers.get("intent", {})
    result.predicted = intent.get("choice")
    probs = intent.get("probabilities") or {}
    result.probabilities = {k: float(v) for k, v in probs.items()}
    if intent.get("confidence") is not None:
        result.confidence = float(intent["confidence"])

    unc = answers.get(UNCERTAINTY_KEY) or {}
    if unc.get("noul") is not None:
        result.information_sufficient = float(unc["noul"])

    if result.predicted is None and not result.error:
        result.error = f"响应里没有 intent.choice：{json.dumps(resp, ensure_ascii=False)[:300]}"
    return result


# =====================================================================
# 7. 输出（终端表格 / JSON / CSV）
# =====================================================================

class C:
    """ANSI 颜色。终端不支持时自动退化为空串。"""
    _on = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    RESET = "\033[0m" if _on else ""
    DIM = "\033[2m" if _on else ""
    BOLD = "\033[1m" if _on else ""
    GREEN = "\033[32m" if _on else ""
    RED = "\033[31m" if _on else ""
    YELLOW = "\033[33m" if _on else ""
    CYAN = "\033[36m" if _on else ""


def dwidth(text: str) -> int:
    """按终端显示宽度算长度（中文算 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - dwidth(text))


def truncate(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    if dwidth(text) <= width:
        return text
    out, cur = "", 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur + w > width - 1:
            break
        out += ch
        cur += w
    return out + "…"


def fmt_p(value: float | None) -> str:
    return "  –  " if value is None else f"{value:.3f}"


HEADERS = ["ID", "预期", "预测", "判定", "P(听写)", "P(编辑)", "conf", "信息足", "耗时ms"]
WIDTHS = [14, 6, 6, 5, 8, 8, 6, 7, 8]


def print_header() -> None:
    line = "  ".join(pad(h, w) for h, w in zip(HEADERS, WIDTHS))
    print(C.BOLD + line + C.RESET)
    print(C.DIM + "─" * (sum(WIDTHS) + 2 * (len(WIDTHS) - 1)) + C.RESET)


def print_result_row(r: Result) -> None:
    if r.error:
        tail = "  " + C.RED + f"ERROR: {truncate(r.error, 90)}" + C.RESET
        print(pad(truncate(r.case_id, 14), 14) + "  " + pad(r.expected or "—", 6)
              + "  " + pad("—", 6) + "  " + pad("—", 5) + tail)
        return

    verdict = "✔" if r.is_correct is True else ("✘" if r.is_correct is False else "?")
    vcolor = {"✔": C.GREEN, "✘": C.RED, "?": C.YELLOW}[verdict]

    cols = [
        truncate(r.case_id, 14),
        r.expected or "—",
        r.predicted or "—",
        vcolor + verdict + C.RESET,
        fmt_p(r.p(LABEL_DICTATE)),
        fmt_p(r.p(LABEL_EDIT)),
        "  –  " if r.confidence is None else f"{r.confidence:.2f}",
        "  –  " if r.information_sufficient is None else f"{r.information_sufficient:.2f}",
        "  –  " if r.latency_ms is None else f"{r.latency_ms:.0f}",
    ]
    print("  ".join(pad(c, w) for c, w in zip(cols, WIDTHS)))


def summarize(results: list[Result]) -> dict:
    scored = [r for r in results if r.expected is not None and r.error is None]
    correct = [r for r in scored if r.is_correct]
    errors = [r for r in results if r.error]
    unlabeled = [r for r in results if r.expected is None and r.error is None]

    stats: dict = {
        "total": len(results),
        "scored": len(scored),
        "correct": len(correct),
        "accuracy": (len(correct) / len(scored)) if scored else None,
        "errors": len(errors),
        "confusion": {e: {p: 0 for p in LABELS} for e in LABELS},
        "per_class": {},
        "avg_confidence_correct": None,
        "avg_confidence_wrong": None,
        "low_information_sufficient": [],
    }

    for r in scored:
        if r.predicted in LABELS:
            stats["confusion"][r.expected][r.predicted] += 1

    for label in LABELS:
        tp = stats["confusion"][label][label]
        fp = sum(stats["confusion"][o][label] for o in LABELS if o != label)
        fn = sum(stats["confusion"][label][o] for o in LABELS if o != label)
        stats["per_class"][label] = {
            "support": tp + fn,
            "precision": (tp / (tp + fp)) if (tp + fp) else None,
            "recall": (tp / (tp + fn)) if (tp + fn) else None,
        }

    def avg_confidence(rows: list[Result]) -> float | None:
        vals = [r.confidence for r in rows if r.confidence is not None]
        return (sum(vals) / len(vals)) if vals else None

    stats["avg_confidence_correct"] = avg_confidence(correct)
    stats["avg_confidence_wrong"] = avg_confidence([r for r in scored if r.is_correct is False])

    for r in results:
        if r.error is None and r.information_sufficient is not None and r.information_sufficient < 0.5:
            stats["low_information_sufficient"].append(
                {"id": r.case_id, "utterance": r.utterance, "value": r.information_sufficient,
                 "predicted": r.predicted, "confidence": r.confidence}
            )
    return stats


def print_summary(results: list[Result]) -> None:
    s = summarize(results)
    print()
    print(C.BOLD + "════ 汇总 ════" + C.RESET)
    acc = "–" if s["accuracy"] is None else f"{s['accuracy'] * 100:.1f}%"
    print(f"  用例总数 {s['total']}   有标注 {s['scored']}   判对 {s['correct']}   准确率 {C.BOLD}{acc}{C.RESET}"
          f"   接口报错 {s['errors']}")

    if s["scored"]:
        print()
        print(C.BOLD + "  混淆矩阵（行=真实，列=预测）" + C.RESET)
        print("            " + pad("听写", 8) + pad("编辑", 8))
        for exp in LABELS:
            row = s["confusion"][exp]
            print(f"    真实{exp}  " + pad(str(row[LABEL_DICTATE]), 8) + pad(str(row[LABEL_EDIT]), 8))

        print()
        print(C.BOLD + "  每类指标" + C.RESET)
        for label in LABELS:
            m = s["per_class"][label]
            prec = "–" if m["precision"] is None else f"{m['precision'] * 100:.0f}%"
            rec = "–" if m["recall"] is None else f"{m['recall'] * 100:.0f}%"
            print(f"    {pad(label, 4)} 样本 {pad(str(m['support']), 4)} 精确率 {pad(prec, 6)} 召回率 {rec}")

        cc, cw = s["avg_confidence_correct"], s["avg_confidence_wrong"]
        print()
        print(C.BOLD + "  置信度校准" + C.RESET)
        print(f"    判对时平均 confidence: {'–' if cc is None else f'{cc:.3f}'}")
        print(f"    判错时平均 confidence: {'–' if cw is None else f'{cw:.3f}'}"
              + (C.DIM + "   （应当明显低于判对时，否则说明模型过度自信）" + C.RESET if cw is not None else ""))

    wrong = [r for r in results if r.is_correct is False]
    if wrong:
        print()
        print(C.BOLD + C.RED + "  判错的用例" + C.RESET)
        for r in wrong:
            print(f"    {r.case_id}: 预期 {r.expected} / 预测 {r.predicted} "
                  f"(P编辑={fmt_p(r.p(LABEL_EDIT))})  ← {truncate(r.utterance, 40)}")

    if s["low_information_sufficient"]:
        print()
        print(C.BOLD + C.YELLOW + "  模型自认为信息不足的用例（information_sufficient < 0.5）" + C.RESET)
        for item in s["low_information_sufficient"]:
            print(f"    {item['id']}: 信息充足度 {item['value']:.2f} "
                  f"预测 {item['predicted']} ← {truncate(item['utterance'], 30)}")

    unlabeled = [r for r in results if r.expected is None and r.error is None]
    if unlabeled:
        print()
        print(C.BOLD + "  无标准答案的模糊用例（不计入准确率）" + C.RESET)
        for r in unlabeled:
            print(f"    {r.case_id}: 预测 {r.predicted}  conf={fmt_p(r.confidence)}  "
                  f"信息充足度={fmt_p(r.information_sufficient)}  ← {truncate(r.utterance, 30)}")


def save_results(results: list[Result], out_dir: Path, stamp: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"run_{stamp}.json"
    csv_path = out_dir / f"run_{stamp}.csv"

    payload = {
        "timestamp": stamp,
        "model": next((r.model for r in results if r.model), None),
        "summary": summarize(results),
        "results": [
            {
                "id": r.case_id,
                "expected": r.expected,
                "predicted": r.predicted,
                "correct": r.is_correct,
                "probabilities": r.probabilities,
                "confidence": r.confidence,
                "information_sufficient": r.information_sufficient,
                "latency_ms": r.latency_ms,
                "model_compute_ms": r.model_compute_ms,
                "utterance": r.utterance,
                "note": r.note,
                "error": r.error,
            }
            for r in results
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "expected", "predicted", "correct", "p_听写", "p_编辑",
                         "confidence", "information_sufficient", "latency_ms",
                         "utterance", "note", "error", "model_compute_ms"])
        for r in results:
            writer.writerow([
                r.case_id, r.expected or "", r.predicted or "",
                "" if r.is_correct is None else ("1" if r.is_correct else "0"),
                r.p(LABEL_DICTATE), r.p(LABEL_EDIT), r.confidence,
                r.information_sufficient,
                "" if r.latency_ms is None else f"{r.latency_ms:.0f}",
                r.utterance, r.note, r.error or "", r.model_compute_ms,
            ])
    return json_path, csv_path


# =====================================================================
# 8. 用例文件读写
# =====================================================================

def load_cases(path: Path) -> list[Case]:
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CASES, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{C.DIM}已生成用例文件：{path}（随便改）{C.RESET}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("cases", [])
    cases = []
    for i, item in enumerate(data):
        cases.append(Case(
            id=item.get("id") or f"case_{i + 1:02d}",
            document=item.get("document", "") or "",
            context=item.get("context", "") or "",
            utterance=item.get("utterance", "") or "",
            expected=item.get("expected") or None,
            note=item.get("note", "") or "",
        ))
    return cases


# =====================================================================
# 9. 各种运行模式
# =====================================================================

def run_cases(cases: list[Case], client: JevClient | None, args) -> list[Result]:
    include_uncertainty = args.with_uncertainty
    results: list[Result] = []

    print()
    if args.mock:
        print(C.BOLD + "模型 MOCK（离线启发式，不发任何请求）" + C.RESET)
    elif args.backend == "nimble":
        print(C.BOLD + f"模型 Bespoke-Nimble-9B | 本地 {args.nimble_device}" + C.RESET)
    else:
        print(C.BOLD + f"模型 {args.model}  |  endpoint {args.base_url}" + C.RESET)
    print()
    print_header()

    workers = 1 if args.backend == "nimble" else max(1, args.workers)
    if workers == 1 or len(cases) == 1:
        for case in cases:
            r = evaluate_case(case, client, include_uncertainty, args.mock)
            results.append(r)
            print_result_row(r)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(evaluate_case, c, client, include_uncertainty, args.mock): c
                       for c in cases}
            done: dict[str, Result] = {}
            for fut in as_completed(futures):
                c = futures[fut]
                r = fut.result()
                done[c.id] = r
                print_result_row(r)
        # 按原始顺序还原，方便阅读
        order = {c.id: i for i, c in enumerate(cases)}
        results = sorted(done.values(), key=lambda r: order.get(r.case_id, 0))

    print_summary(results)
    return results


def interactive(client: JevClient | None, args) -> list[Result]:
    """交互模式：随时改已有文本 / 上下文 / 当前语句。"""
    include_uncertainty = args.with_uncertainty
    st = {"document": "", "context": ""}
    print()
    print(C.BOLD + "交互模式" + C.RESET + " —— 直接输入当前语句回车即可判断。")
    print(C.DIM + "指令： :doc <文本> 设置已有文本 | :ctx <文本> 设置最近上下文 | "
                  ":show 查看当前状态 | :load <用例id> 载入用例 | :quit 退出" + C.RESET)

    results: list[Result] = []
    idx = 0
    while True:
        try:
            line = input(C.CYAN + "当前语句> " + C.RESET).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":quit", ":q", "exit", "quit"):
            break
        if line == ":show":
            print(f"  已有文本: {st['document'] or EMPTY_PLACEHOLDER}")
            print(f"  最近上下文: {st['context'] or EMPTY_PLACEHOLDER}")
            continue
        if line.startswith(":doc"):
            st["document"] = line[4:].strip()
            print(C.DIM + "  已有文本已更新" + C.RESET)
            continue
        if line.startswith(":ctx"):
            st["context"] = line[4:].strip()
            print(C.DIM + "  最近上下文已更新" + C.RESET)
            continue
        if line.startswith(":load"):
            case_id = line[5:].strip()
            cases = load_cases(Path(args.cases)) if args.cases else load_cases(CASES_FILE)
            match = next((c for c in cases if c.id == case_id), None)
            if not match:
                print(C.RED + f"  找不到用例 {case_id}" + C.RESET)
                continue
            st["document"], st["context"] = match.document, match.context
            print(C.DIM + f"  已载入 {case_id}（{match.note}）" + C.RESET)
            continue

        idx += 1
        case = Case(id=f"interactive_{idx:02d}", document=st["document"],
                    context=st["context"], utterance=line)
        result = evaluate_case(case, client, include_uncertainty, args.mock)
        results.append(result)
        if result.error:
            print(C.RED + f"  调用失败：{result.error}" + C.RESET)
            continue
        p0, p1 = result.p(LABEL_DICTATE), result.p(LABEL_EDIT)
        color = C.RED if result.predicted == LABEL_EDIT else C.GREEN
        print(f"  判定: {color}{C.BOLD}{result.predicted}{C.RESET}"
              f"   P(听写)={fmt_p(p0)}   P(编辑)={fmt_p(p1)}"
              f"   confidence={fmt_p(result.confidence)}"
              f"   信息充足度={fmt_p(result.information_sufficient)}"
              f"   {C.DIM}{result.latency_ms:.0f}ms{C.RESET}")
        if result.model_compute_ms is not None:
            print(f"  本地计算：{result.model_compute_ms:.0f}ms（不含模型加载；首次含预热开销）")
    return results


def run_once(client: JevClient | None, args) -> list[Result]:
    case = Case(id="cli", document=args.document or "", context=args.context or "",
                utterance=args.utterance)
    print()
    print(C.BOLD + "state 实际发送内容" + C.RESET)
    print(C.DIM + "─" * 70 + C.RESET)
    print(case.render_state())
    print(C.DIM + "─" * 70 + C.RESET)
    r = evaluate_case(case, client, args.with_uncertainty, args.mock)
    if r.error:
        print(C.RED + f"调用失败：{r.error}" + C.RESET)
        return [r]
    print()
    print(C.BOLD + f"判定：{r.predicted}" + C.RESET)
    print(f"  P(听写) = {fmt_p(r.p(LABEL_DICTATE))}")
    print(f"  P(编辑) = {fmt_p(r.p(LABEL_EDIT))}")
    print(f"  confidence = {fmt_p(r.confidence)}   "
          f"信息充足度 = {fmt_p(r.information_sufficient)}   {r.latency_ms:.0f}ms")
    if r.model_compute_ms is not None:
        print(f"  本地计算：{r.model_compute_ms:.0f}ms（不含模型加载；首次含预热开销）")
    return [r]


def dump_prompt() -> None:
    instructions, state_template = split_prompt(PROMPT_TEMPLATE)
    cases = load_cases(CASES_FILE)
    case = cases[0] if cases else Case(id="demo", utterance="把明天改成周三", document="会议定在明天")
    print("=" * 70)
    print("question: intent 的 instructions（原样发送给模型）")
    print("=" * 70)
    print(instructions)
    print()
    print("=" * 70)
    print("question: intent 的 criteria")
    print("=" * 70)
    print(json.dumps(PROMPT_CRITERIA, ensure_ascii=False, indent=2))
    if args_global.with_uncertainty:
        print()
        print("=" * 70)
        print(f"question: {UNCERTAINTY_KEY}（noul）")
        print("=" * 70)
        print(UNCERTAINTY_INSTRUCTIONS)
    print()
    print("=" * 70)
    print("state（以用例 " + case.id + " 为例）")
    print("=" * 70)
    print(case.render_state())


# =====================================================================
# 10. CLI
# =====================================================================

def load_api_key(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"].strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() in ("TYPESAFE_API_KEY", "TYPESAFE_KEY", "API_KEY"):
                return value.strip().strip('"').strip("'")
    return DEFAULT_API_KEY


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Jev (TypeSafe) 语音输入意图分类测试程序：听写 vs 编辑",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--backend", choices=["jev", "nimble"], default="jev", help="Jev API 或本地 Nimble")
    p.add_argument("--nimble-repo", default=str(BASE_DIR.parent / "pretrained_models/nimble"),
                   help="Nimble 官方源码目录")
    p.add_argument("--nimble-config", help="模型配置 JSON；默认使用 Nimble 目录下 .cache/nimble-model.json")
    p.add_argument("--nimble-device", choices=["mlx", "cuda"], default="mlx", help="Nimble 推理后端，Mac 用 mlx")
    p.add_argument("--api-key", help="覆盖 API key（默认读 TYPESAFE_API_KEY / .env）")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"默认 {DEFAULT_BASE_URL}")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"默认 {DEFAULT_MODEL}")
    p.add_argument("--cases", help=f"用例文件路径，默认 {CASES_FILE.name}")
    p.add_argument("--id", dest="case_id", help="只跑指定 id 的用例")
    p.add_argument("--list", action="store_true", help="列出所有用例后退出")
    p.add_argument("--once", action="store_true",
                   help="单次调用，配合 -u/-d/-c 使用")
    p.add_argument("-u", "--utterance", default="", help="当前语句（配合 --once）")
    p.add_argument("-d", "--document", default="", help="已有文本（配合 --once）")
    p.add_argument("-c", "--context", default="", help="最近上下文（配合 --once）")
    p.add_argument("--interactive", action="store_true", help="交互模式")
    p.add_argument("--mock", action="store_true", help="离线跑通流程，不发网络请求")
    p.add_argument("--with-uncertainty", action="store_true",
                   help="额外附带 information_sufficient（noul）问题，用于观察模型的不确定性")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"并发数，默认 {DEFAULT_WORKERS}")
    p.add_argument("--repeat", type=int, default=1, help="每个用例重复跑 N 次，观察稳定性")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单次请求超时秒数")
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="失败重试次数")
    p.add_argument("--dump-prompt", action="store_true", help="打印实际发送的 instructions / state 后退出")
    p.add_argument("--no-save", action="store_true", help="不写 results/ 目录")
    p.add_argument("--export-cases", action="store_true", help="把内置用例写到 test_cases.json 后退出")
    p.add_argument("--verbose", action="store_true", help="打印重试等调试信息")
    return p


def main(argv: list[str] | None = None) -> int:
    global args_global
    args = build_parser().parse_args(argv)
    args_global = args

    if args.export_cases:
        cases_path = Path(args.cases) if args.cases else CASES_FILE
        cases_path.write_text(json.dumps(DEFAULT_CASES, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {cases_path}（{len(DEFAULT_CASES)} 条用例）")
        return 0

    if args.dump_prompt:
        dump_prompt()
        return 0

    cases_path = Path(args.cases) if args.cases else CASES_FILE
    cases = load_cases(cases_path)

    if args.list:
        print()
        print_header()
        for c in cases:
            mark = c.expected or "—"
            print("  ".join(pad(x, w) for x, w in zip(
                [truncate(c.id, 14), mark, "—", " ",
                 "  –  ", "  –  ", "  –  ", "  –  ", "  –  "],
                WIDTHS)) + f"  {C.DIM}{truncate(c.utterance, 30)} ← {truncate(c.note, 30)}{C.RESET}")
        print()
        print(C.DIM + f"共 {len(cases)} 条用例，文件：{cases_path}" + C.RESET)
        return 0

    if args.once and not args.utterance:
        print("--once 需要配合 -u/--utterance 使用", file=sys.stderr)
        return 2
    client = None
    if not args.mock and args.backend == "nimble":
        try:
            if __package__:
                from .nimble_intent_backend import NimbleClient
            else:
                from nimble_intent_backend import NimbleClient
            print("正在加载本地 Nimble，首次加载可能较慢……", flush=True)
            client = NimbleClient(args.nimble_repo, args.nimble_config, args.nimble_device)
        except Exception as exc:
            print(f"Nimble 初始化失败：{exc}\n安装步骤见 tools/NIMBLE_TESTING.md", file=sys.stderr)
            return 2
        print("Nimble 已加载；概率未经校准，confidence 留空。耗时不含模型加载，批量用例串行执行。")
    elif not args.mock:
        client = JevClient(load_api_key(args.api_key), args.base_url, args.model,
                           args.timeout, args.retries, args.verbose)

    if args.once:
        if not args.utterance:
            print("--once 需要配合 -u/--utterance 使用", file=sys.stderr)
            return 2
        results = run_once(client, args)
    elif args.interactive:
        results = interactive(client, args)
    else:
        selected = cases
        if args.case_id:
            selected = [c for c in cases if c.id == args.case_id]
            if not selected:
                print(f"找不到用例 id={args.case_id}（用 --list 查看）", file=sys.stderr)
                return 2
        if args.repeat > 1:
            selected = [Case(id=f"{c.id}#{i + 1}", document=c.document, context=c.context,
                             utterance=c.utterance, expected=c.expected, note=c.note)
                        for c in selected for i in range(args.repeat)]
        results = run_cases(selected, client, args)

    if results and not args.no_save:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        json_path, csv_path = save_results(results, RESULT_DIR, stamp)
        print()
        print(C.DIM + f"结果已保存：{json_path.name} / {csv_path.name}（{RESULT_DIR}）" + C.RESET)

    if any(r.error for r in results):
        return 1
    return 0


args_global = None

if __name__ == "__main__":
    sys.exit(main())
