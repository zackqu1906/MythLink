"""Automatic voice intent and composable editing requirements."""
from __future__ import annotations

from dataclasses import dataclass
import math

DICTATION = "dictation"
EDIT = "edit"
TASK_LABELS = {DICTATION: "听写", EDIT: "编辑"}
TASK_TYPES = frozenset(TASK_LABELS)
EDIT_TASK_LABELS = {
    "modify": "修改",
    "insert": "插入",
    "polish": "润色",
    "format": "格式整理",
    "translate": "翻译",
    "compose": "按目标创作",
}
LEGACY_EDIT_TASK_LABELS = {
    "precise_edit": "精确修改", "verbatim_insert": "插入指定内容",
    "expand": "补充扩写", "organize": "内容整理（精简／结构）",
    "condense": "精简摘要", "structure": "结构格式调整",
}
LEGACY_EDIT_HINT_LABELS = {"character_clarification": "释字拼写", "redictation": "直接重述"}
DEFAULT_ROUTER_MODEL = "clef-flash"
JOINT_POLICY_VERSION = "dictation-priority-v1"


@dataclass(frozen=True)
class EditPlan:
    tasks: tuple[str, ...]

    def __post_init__(self):
        for values, labels in ((self.tasks, EDIT_TASK_LABELS),):
            if not isinstance(values, tuple) or any(not isinstance(v, str) or v not in labels for v in values):
                raise ValueError("未知编辑任务")
            if len(set(values)) != len(values):
                raise ValueError("编辑任务重复")
        if not self.tasks:
            raise ValueError("编辑路由没有识别出可执行需求")

    @property
    def allows_empty_source(self):
        # Creation does not license another task to edit missing text.
        return set(self.tasks) <= {"insert", "compose"}

    def as_dict(self):
        return {"tasks": [k for k in EDIT_TASK_LABELS if k in self.tasks]}


@dataclass(frozen=True)
class RoutingDecision:
    task: str
    edit_plan: EditPlan | None = None

    def __post_init__(self):
        if (self.task not in TASK_TYPES or (self.task == EDIT and not isinstance(self.edit_plan, EditPlan))
                or (self.task == DICTATION and self.edit_plan is not None)):
            raise ValueError("自动路由任务与编辑计划不一致")


def joint_decision(answers):
    """Resolve validated predictions using the user's dictation-first policy."""
    return resolve_joint_decision(answers)[0]


def resolve_joint_decision(answers):
    """Keep model answers intact; return a final decision and separate policy evidence."""
    tied = [name for name, answer in answers.items()
            if len(set(answer["probabilities"].values())) == 1]
    task = answers["voice_intent"]["choice"]
    tasks = tuple(k for k in EDIT_TASK_LABELS if answers[k]["choice"] == "apply")
    codes, warnings = [], []
    if task == DICTATION and tasks:
        codes.append("dictation_with_tasks")
        warnings.append("总体意图为听写，优先按听写输出；模型仍选中了编辑任务：" + "、".join(EDIT_TASK_LABELS[k] for k in tasks) + "，这些任务未执行")
    if tied:
        codes.append("tied_probability")
        warnings.append("分类概率并列（" + "、".join(EDIT_TASK_LABELS.get(k, "总体意图") for k in tied) + "），按听写处理")
    if task == EDIT and not tasks:
        codes.append("edit_without_tasks")
        warnings.append("总体意图为编辑，但没有明确的编辑任务，回退听写")
    final_task = DICTATION if task == DICTATION or tied or not tasks else EDIT
    source = ("uncertain_dictation_fallback" if tied or (task == EDIT and not tasks)
              else "dictation_priority" if task == DICTATION and tasks else "model")
    evidence = {"policy_version": JOINT_POLICY_VERSION, "decision_source": source,
                "consistency": "warning" if codes else "valid", "warning_codes": codes,
                "warning": "；".join(warnings), "warning_level": "yellow" if codes else "",
                "tied_questions": tied, "ignored_tasks": list(tasks) if final_task == DICTATION else []}
    return RoutingDecision(final_task, EditPlan(tasks) if final_task == EDIT else None), evidence


def multiclass_probabilities(result, labels=TASK_LABELS):
    """Validate one choice distribution, never normalize unrelated questions."""
    values = result.get("probabilities") if isinstance(result, dict) else None
    if not isinstance(values, dict) or set(values) != set(labels):
        raise ValueError("自动判断未返回完整的分类概率")
    scores = {}
    for task in labels:
        value = values[task]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError(f"自动判断未返回有效的 {task} 分类概率")
        scores[task] = float(value)
    # Official SystemOne serializes each probability to four decimal places.
    if not math.isclose(sum(scores.values()), 1.0, rel_tol=0, abs_tol=len(labels) * .00005 + 1e-9):
        raise ValueError("自动判断分类概率之和不为 1")
    return scores


def select_multiclass_task(scores):
    ranked = sorted(scores, key=lambda task: (-scores[task], task))
    winner = ranked[0]
    if scores[winner] == scores[ranked[1]]:
        return DICTATION, winner, "tied_dictation_fallback"
    return winner, winner, "model"


def mode_for_task(task: str) -> str:
    if task not in TASK_TYPES:
        raise ValueError("未知自动判断任务")
    return task


def router_input(utterance: str, target: str, context: dict) -> str:
    """Keep full speech; ASR punctuation is not an edit boundary."""
    if len(utterance) > 2000:
        raise ValueError("本句过长，自动判断未执行")
    messages = context.get("messages", [])[-4:]
    history = "\n".join(f"{m.get('speaker', 'unknown')}: {m.get('text', '')}" for m in messages)[-360:]
    return (f"当前口述：{utterance}\n可编辑原文：{target[-400:] or '空'}\n"
            f"原文是否仅提供末尾片段：{'是' if len(target) > 400 else '否'}\n"
            f"聊天上文（仅供参考）：{history or '无'}")
