"""推荐点击到 Agent 派发：冻结版本、一次参数替换、幂等执行票据。

曝光不是执行；只有参数完整且用户确认后才保存 accepted 和执行票据。
每次曝光最多派发一次。提交结果不明时不自动重发，避免重复写入/发送。
完成只代表 Agent turn 结束，不冒充业务目标已验证，不自动升级候选版本。
"""
import json
import re
from uuid import uuid4
from proximic_ring.agent_workflow.extraction.workflow_artifacts import workflow_steps
from proximic_ring.agent_workflow.extraction.workflow_store import now
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.parameters.parameter_contract import bind_parameters,render_parameters,omission_guidance
from proximic_ring.agent_workflow.pipeline_policy import require_legacy_workflow

PARAMETER = re.compile(r'\{\{([a-z][a-z0-9_]*)\}\}')


def _check_merged_contract(store, operation_id, revision_id):
    canonical=store.canonical(operation_id)
    head=store.db.execute('SELECT active_revision FROM operation_heads WHERE operation_id=?',(canonical,)).fetchone()
    merged=store.db.execute('SELECT 1 FROM operation_merges WHERE operation_id=?',(canonical,)).fetchone()
    if merged and (canonical!=operation_id or not head or head[0]!=revision_id):
        raise ValueError('此组已合并或参数契约已更新，请重新打开推荐并确认新参数')


def snapshot_item(store, exposure_id):
    row = store.db.execute('''SELECT e.*,r.snapshot,r.scope FROM recommendation_exposures e
        JOIN recommendation_requests r ON r.id=e.request_id WHERE e.id=?''', (exposure_id,)).fetchone()
    if not row: raise ValueError('推荐尚未确认曝光，请重新打开推荐')
    result = json.loads(row['snapshot'])
    item = next(i for i in result['items'] if i['operation_id'] == row['operation_id'])
    return row, result, item


def prepare_execution(store, exposure_id, parameters):
    require_legacy_workflow()
    row, result, item = snapshot_item(store, exposure_id)
    if not item['workflow_revision']: raise ValueError('此历史操作尚未建立通用版本，不能直接执行')
    flow = item['workflow']; names = {p['name'] for p in flow['parameters']}
    parameters=bind_parameters(flow,parameters)
    template = '目标：' + flow['goal'] + '\n\n' + workflow_steps(flow)
    if set(PARAMETER.findall(template)) - names: raise ValueError('流程含未定义参数，请先修正通用流程')
    # A single substitution pass keeps braces in user-supplied text literal.
    fill = lambda text: render_parameters(text,parameters)
    stamp = now(); identifier = uuid4().hex
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        _check_merged_contract(store,item['operation_id'],item['workflow_revision'])
        if store.db.execute('SELECT 1 FROM recommendation_executions WHERE exposure_id=?', (exposure_id,)).fetchone():
            raise ValueError('本次推荐已经提交；需要再次执行请重新获取推荐')
        previous = store.db.execute("SELECT kind FROM recommendation_feedback WHERE exposure_id=? AND category='preference'", (exposure_id,)).fetchone()
        if previous and previous['kind'] != 'accepted': raise ValueError('本次推荐已被拒绝或忽略，请重新获取推荐')
        store.db.execute('INSERT OR IGNORE INTO recommendation_feedback VALUES (?,?,?,?,?)',
                         (uuid4().hex, exposure_id, 'preference', 'accepted', stamp))
        context = dict(result.get('invocation_context') or {}, recommendation=dict(
            request_id=row['request_id'], exposure_id=exposure_id, execution_id=identifier,
            operation_id=item['operation_id'], revision_id=item['workflow_revision'], position=item['position']))
        store.db.execute('INSERT INTO recommendation_executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (identifier, exposure_id, row['scope'], item['operation_id'], item['workflow_revision'], encoded(parameters),
             fill(template)+omission_guidance(flow,parameters), fill(flow['goal']), encoded(context), 'prepared', None, None, stamp, stamp, ''))
    return identifier


def dispatch_command(store, identifier):
    """Only prepared tickets can enter the in-memory Agent queue once."""
    require_legacy_workflow()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        row = store.db.execute('SELECT * FROM recommendation_executions WHERE id=?', (identifier,)).fetchone()
        if not row or row['state'] != 'prepared': raise ValueError('此执行票据已派发或失效')
        _check_merged_contract(store,row['operation_id'],row['revision_id'])
        store.db.execute("UPDATE recommendation_executions SET state='submitted',updated_at=? WHERE id=?", (now(), identifier))
    return dict(type='workflow_recommendation', execution_id=identifier, prompt=row['prompt'], goal=row['goal'],
                context=json.loads(row['context']), source='workflow_recommendation' if row['scope']=='user' else 'workflow_recommendation_test')


def mark_execution(store, identifier, state, *, session_id=None, episode_id=None, error=''):
    with store.db:
        store.db.execute('''UPDATE recommendation_executions SET state=?,updated_at=?,
            session_id=COALESCE(?,session_id),episode_id=COALESCE(?,episode_id),error=? WHERE id=?''',
            (state, now(), session_id, episode_id, error, identifier))
