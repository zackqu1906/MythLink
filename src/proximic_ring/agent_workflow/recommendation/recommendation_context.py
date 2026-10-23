"""推荐场景：冻结一次唤起时的已观测状态，不采集新窗口、不推断操作序列。

历史训练在当次 invocation 时间检查焦点时效；实时请求在请求时间检查，
超过 120 秒的旧唤起不能伪装成当前桌面。窗口标题仅留在原始来源，不作评分。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from proximic_ring.agent_workflow.operations.operation_identity import APP_ALIASES

SCHEMA = 'invocation-context-1'
FRESH_SECONDS = 120


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (ValueError, AttributeError, TypeError):
        return None


def clock_context(at=None):
    stamp = at or datetime.now().astimezone()
    return dict(local_hour=stamp.hour, weekday=stamp.weekday(),
                utc_offset_minutes=int(stamp.utcoffset().total_seconds() / 60),
                captured_at=stamp.isoformat())


def invocation_context(focus=None, *, trigger, at=None, identifier=None, status=None):
    stamp = at or datetime.now().astimezone()
    focus = deepcopy(focus or {'availability': 'not_captured'})
    return dict(context_schema=SCHEMA, invocation_id=identifier or uuid4().hex,
                invoked_at=stamp.isoformat(), invocation_trigger=trigger,
                capture_status=status or focus.get('availability', 'captured'),
                before_agent_focus=focus, time_context=clock_context(stamp))


def context_features(raw=None, at=None, *, historical=False):
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError('场景必须是对象')
    at = at or datetime.now(timezone.utc)
    manual = raw.get('source') == 'manual_demo'
    invoked = instant(raw.get('invoked_at'))
    anchor = invoked if historical and invoked and invoked <= at else at
    result = dict(app=None, local_hour=None, weekday=None, time_bucket=None,
                  day_type=None, missing=[], invocation_id=raw.get('invocation_id'),
                  invoked_at=raw.get('invoked_at'), context_source=raw.get('source', 'recorded_or_missing'))
    # No sequence feature is accepted, even if legacy clients still send one.
    stale_invocation = invoked and not historical and not 0 <= (at - invoked).total_seconds() <= FRESH_SECONDS
    if stale_invocation:
        result['missing'].append('invocation_stale')
    focus = raw.get('before_agent_focus', raw)
    if not isinstance(focus, dict):
        focus = {}
    app = focus.get('bundle') or focus.get('bundle_id') or focus.get('app')
    captured = instant(focus.get('captured_at'))
    if (isinstance(app, str) and app.strip() and not stale_invocation
            and (manual or captured and 0 <= (anchor - captured).total_seconds() <= FRESH_SECONDS)):
        lookup = {alias.lower(): bundle for bundle, names in APP_ALIASES.items() for alias in (*names, bundle)}
        result['app'] = lookup.get(app.lower(), app)[:200]
    else:
        result['missing'].append('app_not_captured_or_stale')
    tm = raw.get('time_context', raw)
    if not isinstance(tm, dict):
        tm = {}
    for name, limit in (('local_hour', 24), ('weekday', 7)):
        value = tm.get(name)
        if not stale_invocation and type(value) is int and 0 <= value < limit:
            result[name] = value
        else:
            result['missing'].append(name)
    if result['local_hour'] is not None:
        result['time_bucket'] = result['local_hour'] // 4
    if result['weekday'] is not None:
        result['day_type'] = 'weekend' if result['weekday'] >= 5 else 'weekday'
    return result


def persist_invocation(root, context):
    """Called only on the records worker; never block Agent input on SQLite I/O."""
    from proximic_ring.agent_workflow.operations.operation_store import OperationStore, encoded
    from proximic_ring.agent_workflow.recording.recording import redact
    store = OperationStore(root)
    try:
        with store.db:
            store.db.execute('INSERT OR IGNORE INTO recommendation_invocations VALUES (?,?,?)',
                             (context['invocation_id'], context['invoked_at'], encoded(redact(context))))
    finally:
        store.close()
