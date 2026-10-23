"""推荐入口：场景条件统计 + 可回查依据。没有序列、目标应用加分或反馈加权。"""
from __future__ import annotations
from datetime import datetime, timezone
from proximic_ring.agent_workflow.operations.operation_library import library
from proximic_ring.agent_workflow.recommendation.recommendation_context import clock_context, context_features, instant
from proximic_ring.agent_workflow.recommendation.recommendation_statistics import decay, score_groups
from proximic_ring.agent_workflow.recommendation.recommendation_features import FEATURE_SCHEMA, feature_snapshot
from proximic_ring.agent_workflow.recommendation.recommendation_feedback import feedback_summary
from proximic_ring.agent_workflow.parameters.parameter_contract import missing_required,required

POLICY = 'scene-statistics-1'


def recommend(store, raw=None, *, scope='user', at=None, limit=None, available_parameters=None):
    if scope not in ('user', 'test'):
        raise ValueError('推荐范围只能是 user 或 test')
    if raw is not None and not isinstance(raw, dict):
        raise ValueError('场景必须是对象')
    if raw and raw.get('source') == 'manual_demo' and scope != 'test':
        raise ValueError('手动模拟场景只能用于测试范围')
    at = at or datetime.now(timezone.utc)
    context = context_features(raw, at)
    available_parameters = available_parameters or {}
    if not isinstance(available_parameters, dict):
        raise ValueError('参数必须是对象')
    groups = library(store)
    scores, evidence = score_groups(groups, context, scope=scope, at=at)
    feedback = feedback_summary(store, scope, at=at)
    items = []
    for group in groups:
        oid = group['operation_id']
        missing = lambda flow: missing_required(flow,available_parameters)
        variants = [dict(version_id=v['version_id'], missing_parameters=missing(v['flow']),
                         parameters_ready=not missing(v['flow'])) for v in group['variants']]
        item = dict(operation_id=oid, name=group['name'], workflow=group['workflow'],
            display=group['display'],display_status='ready' if group['display_record'] else 'pending',
            workflow_revision=(group['active_revision'] or {}).get('id'), variants=variants,
            optional_parameters=[p['name'] for p in group['workflow']['parameters'] if not required(p)],
            missing_parameters=missing(group['workflow']), **scores[oid])
        item['features'] = feature_snapshot(context, item)
        item['feedback'] = feedback.get(oid, dict(exposures=0, accepted=0, rejected=0, ignored=0, succeeded=0, failed=0))
        items.append(item)
    items.sort(key=lambda i: (-i['score'], i['operation_id']))
    for position, item in enumerate(items, 1):
        item['position'] = position
    candidate_set = [dict(operation_id=i['operation_id'], workflow_revision=i['workflow_revision'],
                          score=i['score'], features=i['features']) for i in items]
    if limit is not None:
        items = items[:max(1, min(int(limit), 100))]
    return dict(policy=POLICY, feature_schema=FEATURE_SCHEMA, scope=scope, context=context,
        items=items, candidate_set=candidate_set, evidence=evidence, candidate_count=len(groups), feedback_affects_score=False,
        context_recording_error=(raw or {}).get('recording_error', ''),
        abstained=not evidence['evidence_mass'],
        explanation='场景条件统计估计，未校准为真实意图概率；接受／拒绝仅记录。' +
                    ('此范围没有真实使用证据，展示均匀冷启动候选。' if scope=='user' and not evidence['evidence_mass'] else ''),
        generated_at=at.isoformat())
