"""生成候选 ≠ 实际曝光。保存不可变请求快照、客户端可见确认及显式反馈。

曝光标准由网页执行：推荐摘要至少一半可见、文档可见且有焦点，持续 500ms。
服务端只能验证请求及候选身份，无法证明用户确实阅读；method 如实记录此边界。
老 impressions/feedback 原样保留，不追认成新标准的曝光，不进入未来训练接口。
"""
from __future__ import annotations

import json
from collections import defaultdict
from uuid import uuid4

from proximic_ring.agent_workflow.recommendation.recommendation_features import FEATURE_SCHEMA
from proximic_ring.agent_workflow.recommendation.recommendation_context import instant
from proximic_ring.agent_workflow.extraction.workflow_store import now


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS recommendation_invocations(id TEXT PRIMARY KEY,at TEXT NOT NULL,context TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS recommendation_requests(
        id TEXT PRIMARY KEY,scope TEXT NOT NULL,at TEXT NOT NULL,policy TEXT NOT NULL,
        feature_schema TEXT NOT NULL,surface TEXT NOT NULL,snapshot TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS recommendation_exposures(
        id TEXT PRIMARY KEY,request_id TEXT NOT NULL,operation_id TEXT NOT NULL,revision_id TEXT,
        position INTEGER NOT NULL,at TEXT NOT NULL,method TEXT NOT NULL,
        UNIQUE(request_id,operation_id));
      CREATE TABLE IF NOT EXISTS recommendation_feedback(
        id TEXT PRIMARY KEY,exposure_id TEXT NOT NULL,category TEXT NOT NULL,kind TEXT NOT NULL,at TEXT NOT NULL,
        UNIQUE(exposure_id,category));
      CREATE TABLE IF NOT EXISTS recommendation_executions(
        id TEXT PRIMARY KEY,exposure_id TEXT NOT NULL UNIQUE,scope TEXT NOT NULL,
        operation_id TEXT NOT NULL,revision_id TEXT NOT NULL,parameters TEXT NOT NULL,
        prompt TEXT NOT NULL,goal TEXT NOT NULL,context TEXT NOT NULL,state TEXT NOT NULL,
        session_id TEXT,episode_id TEXT,at TEXT NOT NULL,updated_at TEXT NOT NULL,error TEXT NOT NULL);
    ''')


def create_request(store, result, scope, *, surface='web'):
    from proximic_ring.agent_workflow.operations.operation_store import encoded
    if scope not in ('user', 'test') or scope != result.get('scope'):
        raise ValueError('推荐范围不匹配')
    if surface not in ('web', 'cli', 'native'):
        raise ValueError('未知展示入口')
    identifier = uuid4().hex
    with store.db:
        store.db.execute('INSERT INTO recommendation_requests VALUES (?,?,?,?,?,?,?)',
            (identifier, scope, now(), result['policy'], FEATURE_SCHEMA, surface, encoded(result)))
    return identifier


def expose(store, request_id, operation_id, *, method='web_visible_500ms'):
    allowed = {'web_visible_500ms': 'web', 'cli_confirmed': 'cli', 'native_visible_500ms': 'native'}
    row = store.db.execute('SELECT * FROM recommendation_requests WHERE id=?', (request_id,)).fetchone()
    if not row or allowed.get(method) != row['surface']:
        raise ValueError('推荐请求不存在或展示入口不匹配')
    snapshot = json.loads(row['snapshot'])
    selected = next(((i, item) for i, item in enumerate(snapshot['items'], 1)
                     if item['operation_id'] == operation_id), None)
    if selected is None:
        raise ValueError('曝光必须属于这次生成的候选')
    position, item = selected
    # The client cannot supply a different rank/revision/score/context.
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO recommendation_exposures VALUES (?,?,?,?,?,?,?)',
            (uuid4().hex, request_id, operation_id, item['workflow_revision'], position, now(), method))
        found = store.db.execute('SELECT id FROM recommendation_exposures WHERE request_id=? AND operation_id=?',
                                 (request_id, operation_id)).fetchone()
    return found['id']


def feedback(store, exposure_id, operation_id, kind):
    preference = {'accepted', 'rejected', 'ignored'}
    if kind not in preference | {'succeeded', 'failed'}:
        raise ValueError('反馈类型无效')
    category = 'preference' if kind in preference else 'outcome'
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        exposure = store.db.execute('SELECT * FROM recommendation_exposures WHERE id=?', (exposure_id,)).fetchone()
        if not exposure or exposure['operation_id'] != operation_id:
            raise ValueError('反馈需要本次候选已确认的实际曝光；生成请求或旧 impression 不算曝光')
        previous = store.db.execute('SELECT kind FROM recommendation_feedback WHERE exposure_id=? AND category=?',
                                     (exposure_id, category)).fetchone()
        if previous and previous['kind'] != kind:
            raise ValueError('此曝光已提交反馈；请获取新的推荐再评价')
        store.db.execute('INSERT OR IGNORE INTO recommendation_feedback VALUES (?,?,?,?,?)',
                         (uuid4().hex, exposure_id, category, kind, now()))


def feedback_summary(store, scope, *, at=None):
    def included(stamp):
        when = instant(stamp)
        return when is not None and (at is None or when <= at)
    counts = defaultdict(lambda: dict(exposures=0, accepted=0, rejected=0, ignored=0, succeeded=0, failed=0))
    for row in store.db.execute('''SELECT e.operation_id,e.at,f.kind,f.at AS feedback_at
            FROM recommendation_exposures e JOIN recommendation_requests r ON r.id=e.request_id
            LEFT JOIN recommendation_feedback f ON f.exposure_id=e.id WHERE r.scope=?''', (scope,)):
        if not included(row['at']):
            continue
        # Exposure totals are counted separately because one exposure may have both feedback categories.
        if row['kind'] and included(row['feedback_at']):
            counts[store.canonical(row['operation_id'])][row['kind']] += 1
    for row in store.db.execute('''SELECT e.operation_id,e.at FROM recommendation_exposures e
            JOIN recommendation_requests r ON r.id=e.request_id WHERE r.scope=?''', (scope,)):
        if included(row['at']):
            counts[store.canonical(row['operation_id'])]['exposures'] += 1
    return dict(counts)


def training_examples(store, *, scope='user'):
    """Future LinUCB adapter: explicit preference labels with exposure-time features.

    Ignored/unseen items and execution outcomes do not become preference labels.
    Deterministic ordering has no exploratory propensity: store null, never invent one.
    """
    rows = store.db.execute('''SELECT e.*,r.snapshot,r.policy,r.feature_schema,r.scope,f.kind,f.at AS feedback_at
        FROM recommendation_feedback f JOIN recommendation_exposures e ON e.id=f.exposure_id
        JOIN recommendation_requests r ON r.id=e.request_id
        WHERE r.scope=? AND f.kind IN ('accepted','rejected') ORDER BY f.at,f.id''', (scope,))
    for row in rows:
        snapshot = json.loads(row['snapshot'])
        item = next(i for i in snapshot['items'] if i['operation_id'] == row['operation_id'])
        yield dict(exposure_id=row['id'], operation_id=row['operation_id'], workflow_revision=row['revision_id'],
            position=row['position'], context=snapshot['context'], features=item['features'],
            feature_schema=row['feature_schema'], policy=row['policy'], reward=int(row['kind'] == 'accepted'),
            candidate_ids=[i['operation_id'] for i in snapshot['candidate_set']],
            action_probability=None, logging_policy='deterministic_ranking',
            shown_at=row['at'], feedback_at=row['feedback_at'])
