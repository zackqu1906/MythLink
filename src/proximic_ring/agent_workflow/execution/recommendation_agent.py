"""推荐票据进入现有 Computer Use Agent；只在用户点击后运行。

不绕开现有工具审批、取消、电脑控制释放。连接失败/拒绝派发不冒充完成；
任务状态与目标验证分开。普通 Agent 输入不访问推荐数据库。
"""
import json
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.execution.recommendation_execution import mark_execution


def start_recommendation(flow, command):
    from proximic_ring.agent_workflow import pipeline_policy
    if not pipeline_policy.legacy_workflow_enabled():
        flow.publish(detail=pipeline_policy.DISABLED_MESSAGE)
        return
    identifier = command.get('execution_id')
    if not flow.recorder: raise ValueError('工作记录未就绪，不能派发推荐')
    store = OperationStore(flow.recorder.root)
    try:
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            row = store.db.execute('SELECT * FROM recommendation_executions WHERE id=?', (identifier,)).fetchone()
            if not row or row['state'] != 'submitted':
                flow.event('recommendation_dispatch_rejected', execution_id=identifier, reason='ticket_not_submitted')
                return
            if flow.turn or flow.start_id or flow.task_queue or flow.pending or flow.cancelled:
                mark_execution(store, identifier, 'not_started', error='Agent 正在工作，请任务结束后重新获取推荐')
                flow.publish(detail='推荐未启动：Agent 正在工作')
                return
            store.db.execute("UPDATE recommendation_executions SET state='starting' WHERE id=?", (identifier,))
        source = 'workflow_recommendation' if row['scope']=='user' else 'workflow_recommendation_test'
        context = json.loads(row['context'])
        episode = flow.recorder.new_episode(row['goal'], source, context)
        flow.recommendation_id = identifier
        mark_execution(store, identifier, 'running', session_id=flow.recorder.session_id, episode_id=episode)
        flow.event('recommendation_selected', episodeId=episode, execution_id=identifier,
                   operation_id=row['operation_id'], revision_id=row['revision_id'])
        flow.start_task(row['prompt'], source, episode)
    finally: store.close()


def finish_recommendation(flow, state, error=''):
    identifier = getattr(flow, 'recommendation_id', None)
    if not identifier or not flow.recorder: return
    flow.recommendation_id = None
    store = OperationStore(flow.recorder.root)
    try: mark_execution(store, identifier, state, error=error)
    finally: store.close()
