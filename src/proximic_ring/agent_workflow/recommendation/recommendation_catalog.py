"""操作库更新标识：只看影响推荐的内容，不用周期性 report 的时间戳。

成员加入、最新样本/场景变化、通用版本启用会改变 token；候选修订尚未启用
不会改变推荐内容。反馈不参与分数，但单独统计供界面展示。打开的面板只提示
更新，不自动重新排序；重开/刷新会读取当前事务快照并用新场景重新打分。
"""
import json
from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.parameters.parameter_contract import VERSION as PARAMETER_CONTRACT_VERSION


def catalog_state(store):
    with store.db:
        if not store.db.in_transaction: store.db.execute('BEGIN')
        heads = [tuple(r) for r in store.db.execute('SELECT * FROM operation_heads ORDER BY operation_id')]
        # Only active-version copy refreshes the catalog token. An already open
        # hub keeps its frozen labels, field keys and ranking until reopened.
        displays = [tuple(r) for r in store.db.execute('''SELECT d.revision_id,d.content FROM operation_displays d
            JOIN operation_heads h ON h.active_revision=d.revision_id ORDER BY d.revision_id''')]
        members = [tuple(r) for r in store.db.execute('''SELECT m.sample_id,m.version_id,m.operation_id,
            s.kind,s.context,s.occurred_at FROM operation_memberships m JOIN samples s
            ON m.sample_id=s.id AND m.version_id=s.version_id ORDER BY m.sample_id,m.version_id''')]
        # A successful retry keeps the failed paid request for audit. Only the
        # latest admission for a current sample version can still block admission;
        # counting all historical jobs would show a permanent failure after repair.
        admissions = [r[0] for r in store.db.execute('''SELECT a.status FROM operation_admissions a
            JOIN samples s ON s.id=a.sample_id AND s.version_id=a.version_id
            WHERE a.rowid=(SELECT max(b.rowid) FROM operation_admissions b
                          WHERE b.sample_id=a.sample_id AND b.version_id=a.version_id)''')]
        pending = sum(s in ('pending','running','uncertain','generalization_pending') for s in admissions)
        failed = admissions.count('failed') + store.db.execute('''SELECT count(*) FROM operation_updates u
            JOIN operation_heads h ON h.operation_id=u.operation_id AND h.active_revision=u.base_revision
            WHERE u.status='failed' ''').fetchone()[0]
        historical_failed = store.db.execute("SELECT count(*) FROM operation_jobs WHERE status='failed'").fetchone()[0]
        from proximic_ring.agent_workflow.parameters.parameter_history import current_records
        parameters=[(r['id'],r['status']) for r in current_records(store)]
        executions=[tuple(r) for r in store.db.execute('''SELECT e.id,e.parameters,e.updated_at FROM recommendation_executions e
            JOIN operation_heads h ON h.operation_id=e.operation_id AND h.active_revision=e.revision_id
            WHERE e.state='completed' AND e.error='' ORDER BY e.id''')]
    return dict(token=digest([PARAMETER_CONTRACT_VERSION,heads,members,displays,parameters,executions]), groups=len(heads), members=len(members),
                pending=pending, failed=failed, historical_failed=historical_failed)


def pipeline_status(root):
    """Read worker status only; asking for recommendations never starts extraction."""
    from pathlib import Path
    root=Path(root)
    try:
        value=json.loads((root/'workflows/status.json').read_text())
        jobs=value.get('jobs',[])
        return dict(queued=sum(j['status']=='queued' for j in jobs),running=sum(j['status']=='running' for j in jobs),
                    failed=sum(j['status']=='failed' for j in jobs),error=value.get('error',''))
    except (OSError,ValueError,KeyError):return dict(queued=0,running=0,failed=0,error='')
