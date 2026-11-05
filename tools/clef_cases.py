"""Shared, editable case files for intent, joint and pipeline probes."""
import json
from pathlib import Path

from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS

DEFAULT_CASES = Path(__file__).parent / "data/automatic_voice_routing_cases.json"


def load_cases(path=DEFAULT_CASES, selected=None, *, require_plan=False):
    try:
        cases = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取样例文件 {path}: {exc}") from exc
    if not isinstance(cases, list) or not cases:
        raise ValueError("样例文件必须是非空 JSON 数组")
    seen = set()
    for index, case in enumerate(cases, 1):
        prefix = f"第 {index} 条样例"
        if not isinstance(case, dict):
            raise ValueError(f"{prefix}必须是对象")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError(f"{prefix}的 id 必须非空且唯一")
        seen.add(case_id)
        prefix = f"样例 {case_id}"
        for key in ("utterance", "target"):
            if not isinstance(case.get(key), str):
                raise ValueError(f"{prefix}缺少字符串字段 {key}；空原文请填空字符串")
        if not case["utterance"].strip() or len(case["utterance"]) > 2000:
            raise ValueError(f"{prefix}的 utterance 必须为 1 至 2000 字符")
        context = case.get("context")
        if not isinstance(context, dict) or not isinstance(context.get("messages"), list):
            raise ValueError(f'{prefix}缺少 context.messages；无 OCR 时填 {{"messages": []}}')
        for message in context["messages"]:
            if (not isinstance(message, dict) or message.get("speaker") not in ("self", "other", "unknown")
                    or not isinstance(message.get("text"), str)):
                raise ValueError(f"{prefix}的 OCR 每条须有 speaker(self/other/unknown) 和字符串 text")
        expected = case.get("expected")
        if not isinstance(expected, dict) or expected.get("task") not in ("dictation", "edit"):
            raise ValueError(f"{prefix}的 expected.task 须为 dictation 或 edit")
        if "modified_text" in expected and not isinstance(expected["modified_text"], str):
            raise ValueError(f"{prefix}的 expected.modified_text 必须为字符串")
        if "preserve_text" in expected and (not isinstance(expected["preserve_text"], list) or
                any(not isinstance(part, str) or not part for part in expected["preserve_text"])):
            raise ValueError(f"{prefix}的 expected.preserve_text 必须为非空字符串列表")
        if "hints" in expected:
            raise ValueError(f"{prefix}的 hints 已移除；释字和直接重述请标记 modify")
        for key, labels in (("tasks", EDIT_TASK_LABELS),):
            if key in expected:
                values = expected[key]
                if (not isinstance(values, list) or any(not isinstance(v, str) or v not in labels for v in values)
                        or len(set(values)) != len(values)):
                    raise ValueError(f"{prefix}的 expected.{key} 包含未知或重复标签")
                if expected["task"] == "dictation" and values:
                    raise ValueError(f"{prefix}为听写，不能包含编辑任务")
        if not case["target"].strip() and "modify" in expected.get("tasks", []):
            raise ValueError(f"{prefix}没有可编辑原文，修改任务不可用")
    if selected:
        unknown = set(selected) - seen
        if unknown:
            raise ValueError("未知样例 id: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case["id"] in selected]
    if require_plan:
        for case in cases:
            expected = case["expected"]
            if expected["task"] == "edit" and not expected.get("tasks"):
                raise ValueError(f"样例 {case['id']} 缺少具体任务标签；联合评测还需非空 expected.tasks")
    return cases


def comparable_tasks(tasks):
    """Map only unambiguous old labels; organize cannot be split from a score alone."""
    aliases = {"precise_edit": "modify", "verbatim_insert": "insert", "expand": "polish",
               "condense": "polish", "structure": "format"}
    return {aliases.get(task, task) for task in tasks}


def raw_joint_match(trace, expected):
    """Score original model choices independently of any dictation fallback policy."""
    stage = next((s for s in trace.get('stages', []) if s.get('stage') == 'joint'), {})
    answers = stage.get('answers', {})
    availability = trace.get('task_availability')
    if availability and set(answers) != {'voice_intent', *availability['available_tasks']}:
        return False
    if not answers or any(len(set(a.get('probabilities', {}).values())) < 2 for a in answers.values()):
        return False
    return (answers.get('voice_intent', {}).get('choice') == expected['task'] and
            {k for k, a in answers.items() if k != 'voice_intent' and a.get('choice') == 'apply'} == set(expected.get('tasks', [])))


def generation_checks(expected, text):
    """Optional deterministic assertions; absence is unscored, not quality acceptance."""
    checks = []
    if 'modified_text' in expected:
        checks.append({'kind':'exact_text','expected':expected['modified_text'],
                       'passed':text == expected['modified_text']})
    for part in expected.get('preserve_text', []):
        checks.append({'kind':'preserve_text','expected':part,'passed':part in text})
    return {'passed':all(c['passed'] for c in checks) if checks else None, 'checks':checks}
