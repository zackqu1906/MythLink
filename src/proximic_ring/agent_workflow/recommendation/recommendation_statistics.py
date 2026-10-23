"""场景条件统计，不调用模型、不读取反馈、不把目标应用当作发起场景。

初始参数：30 天半衰期、每组 0.5 次总体伪计数、每层 3 次先验质量。
这是可解释的工程基线，未做准确率校准，不声称来自某篇论文的最优参数。
按 app → 四小时时段 → 周内/周末逐层收窄；只使用当前已知维度，
P_child=(本组匹配质量+3*P_parent)/(全部匹配质量+3)。无匹配自动回退。
每个任务总计一份质量；同任务属于多个组时均分，避免多次提取放大使用。
"""
from __future__ import annotations

from collections import defaultdict

from proximic_ring.agent_workflow.recommendation.recommendation_context import context_features, instant

HALF_LIFE_DAYS = 30
GLOBAL_ALPHA = 0.5
SMOOTHING_MASS = 3.0
LABELS = {'app': '同应用', 'time_bucket': '同四小时时段', 'day_type': '同周内／周末'}


def decay(stamp, at):
    when = instant(stamp)
    if when is None or when > at:
        return 0.0
    return 2 ** (-(at - when).total_seconds() / 86400 / HALF_LIFE_DAYS)


def score_groups(groups, context, *, scope, at):
    episodes = defaultdict(dict)
    for group in groups:
        for sample in sorted(group['samples'], key=lambda s: s['id']):
            if sample['kind'] != scope:
                continue
            source = sample['source']
            sid, eid = source.get('session_id'), source.get('episode_id')
            key = ('episode', sid, eid) if sid and eid else ('sample', sample['id'])
            episodes[key].setdefault(group['operation_id'], sample)
    rows = []
    valid_tasks = 0
    for members in episodes.values():
        valid = [(oid, sample, decay(sample['occurred_at'], at)) for oid, sample in members.items()]
        valid = [(oid, sample, weight) for oid, sample, weight in valid if weight]
        valid_tasks += bool(valid)
        for oid, sample, weight in valid:
            prior = context_features(sample['context'], instant(sample['occurred_at']), historical=True)
            rows.append((oid, weight / len(valid), prior))
    count = len(groups)
    if not count:
        return {}, dict(tasks=0, evidence_mass=0, dimensions=[])
    global_mass = sum(row[1] for row in rows)
    global_counts = defaultdict(float)
    uses = defaultdict(int)
    for oid, weight, _ in rows:
        global_counts[oid] += weight
        uses[oid] += 1
    probabilities = {g['operation_id']: (global_counts[g['operation_id']] + GLOBAL_ALPHA) /
                     (global_mass + GLOBAL_ALPHA * count) for g in groups}
    priors = dict(probabilities)
    traces = defaultdict(list)
    dimensions = []
    for dimension in ('app', 'time_bucket', 'day_type'):
        if context[dimension] is None:
            continue
        dimensions.append(dimension)
        matched = [(oid, weight) for oid, weight, ctx in rows
                   if all(ctx[d] is not None and ctx[d] == context[d] for d in dimensions)]
        mass = sum(weight for _, weight in matched)
        counts = defaultdict(float)
        for oid, weight in matched:
            counts[oid] += weight
        for oid, parent in probabilities.copy().items():
            probabilities[oid] = (counts[oid] + SMOOTHING_MASS * parent) / (mass + SMOOTHING_MASS)
            traces[oid].append(dict(dimensions=list(dimensions), label='＋'.join(LABELS[d] for d in dimensions),
                matched_mass=mass, operation_mass=counts[oid], parent_score=parent,
                smoothing_mass=SMOOTHING_MASS, score=probabilities[oid], fallback=not bool(mass)))
    result = {}
    for oid in probabilities:
        used = next((layer for layer in reversed(traces[oid]) if not layer['fallback']), None)
        reasons = [f"{uses[oid]} 次{'真实' if scope == 'user' else '测试'}任务；30 天半衰期"]
        if used:
            reasons.append(f"{used['label']}：该组质量 {used['operation_mass']:.2f} / 全部 {used['matched_mass']:.2f}，向上层平滑")
        else:
            reasons.append('场景缺失或没有匹配记录，回退总体使用频率')
        if not global_mass:
            reasons.append('此范围没有有效使用记录，各组使用均匀冷启动先验')
        reasons.append('接受／拒绝仅记录，不参与本版评分')
        result[oid] = dict(score=probabilities[oid], usage_count=uses[oid], decayed_usage=global_counts[oid],
            known_context_weight=used['matched_mass'] if used else 0, cold_start=not uses[oid],
            components=dict(global_prior=priors[oid], context_estimate=probabilities[oid]), reasons=reasons,
            statistics=dict(global_operation_mass=global_counts[oid], global_total_mass=global_mass,
                global_alpha=GLOBAL_ALPHA, group_count=count, levels=traces[oid],
                used_level=used['label'] if used else '总体频率', half_life_days=HALF_LIFE_DAYS))
    return result, dict(tasks=valid_tasks, evidence_mass=global_mass, dimensions=dimensions)
