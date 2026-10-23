"""Durable, explicitly targeted operations, consumed by the existing worker."""
import json
import sqlite3
import uuid

from .workbench_data import selected, audit_for_job, same_source
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore, now
from proximic_ring.agent_workflow.operations.operation_store import OperationStore, encoded
from proximic_ring.agent_workflow.operations.operation_jobs import safe_error
from proximic_ring.agent_workflow import pipeline_policy


def enqueue(root, payload):
    pipeline_policy.require_legacy_workflow()
    step = payload.get('step')
    if step not in ('group', 'parameters'):
        raise ValueError('未知处理步骤')
    task, flow = selected(root, payload)
    if step == 'parameters' and (not flow['revision_id'] or payload.get('revision_id') != flow['revision_id']):
        raise ValueError('请先完成归组，并选择该组当前使用的通用 Workflow')
    if step == 'parameters' and (task['status'] != 'completed' or flow['scope'] not in ('user', 'test')):
        raise ValueError('参数提取需要已完成且来源范围明确的任务')
    target = dict(job_id=task['latest_job']['id'], workflow_index=flow['index'],
                  sample_id=flow['sample_id'], version_id=flow['version_id'],
                  revision_id=flow['revision_id'] if step == 'parameters' else None)
    store = WorkflowStore(root)
    try:
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            prior = store.db.execute("""SELECT id FROM workbench_actions WHERE
                session_id=? AND episode_id=? AND step=? AND target=? AND status IN ('queued','running')""",
                (task['session_id'], task['id'], step, encoded(target))).fetchone()
            if prior:
                return dict(id=prior['id'], message='该步骤已在队列中')
            identifier = uuid.uuid4().hex
            stamp = now()
            store.db.execute('INSERT INTO workbench_actions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (identifier, task['session_id'], task['id'], step, encoded(target), 'queued',
                 '等待后台空闲', '{}', '', stamp, stamp))
        return dict(id=identifier, message='已排队，只处理所选任务的此步骤')
    finally:
        store.close()


def update(queue, action, *, status='running', progress='', result=None, error=''):
    with queue.db:
        queue.db.execute('UPDATE workbench_actions SET status=?,progress=?,result=?,error=?,updated_at=? WHERE id=?',
            (status, progress, encoded(result or {}), error, now(), action['id']))


def recover(queue):
    """Called under the worker OS lock. Never silently repeat an unknown paid call."""
    rows = list(queue.db.execute("SELECT * FROM workbench_actions WHERE status='running'"))
    if not rows:
        return
    operations = OperationStore(queue.root)
    try:
        for row in rows:
            result = json.loads(row['result'])
            subjects = {result.get('admission_id'), result.get('update_id')}
            with operations.db:
                for job in operations.rows('operation_jobs'):
                    packet = json.loads(job['input'])
                    mine = job['subject_id'] in subjects or (
                        job['kind'] == 'parameters' and packet.get('workbench_request_id') == row['id'])
                    if mine and job['status'] == 'running':
                        operations.db.execute("UPDATE operation_jobs SET status='failed',error=? WHERE id=?",
                            ('操作台进程中断，结果未知，请手动重试', job['id']))
                        if job['kind'] in ('admission', 'update'):
                            table = 'operation_admissions' if job['kind'] == 'admission' else 'operation_updates'
                            operations.db.execute(f"UPDATE {table} SET status='failed' WHERE id=?", (job['subject_id'],))
            update(queue, row, status='failed', result=result, error='后台进程中断，远端结果未知；请查看历史后手动重试')
    finally:
        operations.close()


def process_one(queue, settings, *, model=None, cancelled=lambda: False):
    if not pipeline_policy.legacy_workflow_enabled(): return False
    from proximic_ring.agent_workflow.operations.operation_embeddings import agent_busy, EmbeddingDeferred
    if cancelled() or agent_busy():
        return False
    with queue.db:
        queue.db.execute('BEGIN IMMEDIATE')
        row = queue.db.execute("SELECT * FROM workbench_actions WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
        if row is None:
            return False
        action = dict(row)
        queue.db.execute("UPDATE workbench_actions SET status='running',updated_at=? WHERE id=?", (now(), action['id']))
    target = json.loads(action['target'])
    result = json.loads(action['result'])
    store = OperationStore(queue.root)
    before = {j['id'] for j in store.rows('operation_jobs')}
    try:
        if any(j['status'] == 'running' for j in store.rows('operation_jobs')):
            raise EmbeddingDeferred('等待已有后台任务结束')
        task, flow = selected(queue.root, dict(session_id=action['session_id'], episode_id=action['episode_id'], **target))
        if flow['version_id'] != target['version_id']:
            raise ValueError('来源 Workflow 已变化，请重新选择')
        if action['step'] == 'group':
            from proximic_ring.agent_workflow.extraction.workflows import source_packet
            from proximic_ring.agent_workflow.operations.operation_ingest import sample_from_audit
            from proximic_ring.agent_workflow.operations.operation_prototypes import heads, seed, recheck
            from proximic_ring.agent_workflow.operations.operation_admission import process_admission
            from proximic_ring.agent_workflow.operations.operation_update import process_update
            audit = audit_for_job(queue.root, task['latest_job'])
            original = json.loads((queue.directory/(task['latest_job']['id']+'.input.json')).read_text())
            fresh = source_packet(queue.root, action['session_id'], action['episode_id'])
            if (original['fingerprint'] != audit['generation']['source_fingerprint']
                    or not same_source(original, fresh)):
                raise ValueError('原始任务记录已变化，请先重新提取 Workflow')
            sample = sample_from_audit(audit, flow['index'],
                source_file='../workflows/' + task['latest_job']['result_file'])
            store.ingest(sample)
            if not heads(store):
                with store.db:
                    row = dict(store.db.execute('SELECT * FROM samples WHERE id=?', (sample['id'],)).fetchone())
                    oid, rid = seed(store, row)
                result.update(operation_id=oid, revision_id=rid)
                status, progress = 'completed', '已建立通用组首版'
            else:
                if not result.get('admission_id'):
                    result['admission_id'] = recheck(store, sample['id'])
                update(queue, action, progress='召回相似通用组并判断是否同类', result=result)
                admission = dict(store.db.execute('SELECT * FROM operation_admissions WHERE id=?', (result['admission_id'],)).fetchone())
                if admission['status'] == 'pending':
                    if not process_admission(store, settings, model=model, admission_id=admission['id'], cancelled=cancelled):
                        raise EmbeddingDeferred('等待空闲后继续归组')
                    admission = dict(store.db.execute('SELECT * FROM operation_admissions WHERE id=?', (admission['id'],)).fetchone())
                detail = json.loads(admission['detail'])
                if admission['status'] == 'failed':
                    raise ValueError(detail.get('error', '归组判断失败'))
                if admission['status'] == 'generalization_pending':
                    result['update_id'] = detail['update_id']
                    update(queue, action, progress='融合同类任务，生成通用 Workflow', result=result)
                    u = dict(store.db.execute('SELECT * FROM operation_updates WHERE id=?', (detail['update_id'],)).fetchone())
                    if u['status'] == 'pending':
                        if not process_update(store, settings, model=model, update_id=u['id'], cancelled=cancelled):
                            raise EmbeddingDeferred('等待空闲后继续生成通用流程')
                        u = dict(store.db.execute('SELECT * FROM operation_updates WHERE id=?', (u['id'],)).fetchone())
                    result['revision_id'] = u['revision_id']
                    if u['status'] == 'candidate':
                        status, progress = 'review', '通用流程已生成，查看并采用后生效'
                    elif u['status'] == 'no_change':
                        status, progress = 'completed', '已归入现有通用组，流程无需修改'
                    else:
                        failures = [j for j in store.rows('operation_jobs') if j['subject_id'] == u['id'] and j['error']]
                        raise ValueError(failures[-1]['error'] if failures else '未形成可用通用流程，请查看结果后重新归组')
                elif admission['status'] in ('accepted', 'new_operation', 'merged'):
                    result.update({k: detail[k] for k in ('operation_id', 'revision_id') if k in detail})
                    status, progress = 'completed', '已归入通用组' if admission['status'] != 'new_operation' else '已生成新的通用组'
                else:
                    raise ValueError('此归组请求已失效，请重新提交')
        else:
            if not flow['revision_id'] or flow['revision_id'] != target['revision_id']:
                raise ValueError('通用组版本已变化，请刷新后针对新版本提取参数')
            if flow['scope'] not in ('user', 'test'):
                raise ValueError('此任务的来源范围尚未确认，不能生成历史参数')
            from proximic_ring.agent_workflow.parameters.parameter_jobs import process_parameters
            from proximic_ring.agent_workflow.parameters.parameter_history import current_records
            update(queue, action, progress='按当前通用组的参数定义提取本任务的值', result=result)
            if not flow['general_flow']['parameters']:
                status, progress = 'completed', '此通用流程没有参数，无需提取'
            else:
                if not process_parameters(store, settings, model=model, sample_id=flow['sample_id'],
                                          scope=flow['scope'], request_id=action['id'], cancelled=cancelled):
                    raise EmbeddingDeferred('等待空闲后提取参数')
                record = next((r for r in current_records(store) if r['sample_id'] == flow['sample_id']), None)
                if not record or record['status'] != 'completed':
                    raise ValueError((record or {}).get('generation', {}).get('error', '参数提取未完成，查看记录后重试'))
                result['parameter_record_id'] = record['id']
                status, progress = 'completed', '参数提取完成；未指定的值保持留空'
        result['operation_job_ids'] = list(dict.fromkeys(result.get('operation_job_ids', []) +
            [j['id'] for j in store.rows('operation_jobs') if j['id'] not in before]))
        update(queue, action, status=status, progress=progress, result=result)
    except EmbeddingDeferred as exc:
        result['operation_job_ids'] = list(dict.fromkeys(result.get('operation_job_ids', []) +
            [j['id'] for j in store.rows('operation_jobs') if j['id'] not in before]))
        update(queue, action, status='queued', progress=str(exc), result=result)
        return False
    except Exception as exc:
        result['operation_job_ids'] = [j['id'] for j in store.rows('operation_jobs') if j['id'] not in before]
        update(queue, action, status='failed', result=result, error=safe_error(exc))
    finally:
        store.close()
    return True


def adopt(root, payload):
    """The visible source-by-source review is the user's merge authorization."""
    pipeline_policy.require_legacy_workflow()
    from proximic_ring.agent_workflow.operations.operation_prototypes import revision
    from proximic_ring.agent_workflow.operations.operation_merge import merge_reviewed
    from proximic_ring.agent_workflow.operations.operation_synthesis import VERSION
    task, flow = selected(root, payload)
    rid = payload.get('candidate_id')
    if not any(c['id'] == rid for c in flow['candidates']):
        raise ValueError('此候选已变化或不属于所选任务，请刷新')
    store = OperationStore(root)
    try:
        rev = revision(store, rid)
        generation = rev['generation']
        if generation.get('pipeline') != VERSION:
            raise ValueError('历史候选请先重新归组以生成当前格式')
        notes = payload.get('reviews')
        vids = {r['version_id'] for r in rev['sources']}
        if not isinstance(notes, dict) or set(notes) != vids or any(
                not isinstance(n, str) or not 1 <= len(n.strip()) <= 1500 for n in notes.values()):
            raise ValueError('请填写每个来源任务的分支与参数核对说明')
        versions = store.versions()
        plan = dict(revision_id=rid, expected_heads=generation['expected_heads'],
            membership_token=generation['membership_token'],
            review=dict(authorization='用户在本机任务操作台逐项核对后点击“采用此通用流程”',
                execution_verified=False, branches={v: dict(condition=versions[v]['goal'],
                    steps=list(range(1, len(rev['flow']['steps']) + 1)),
                    verification=notes[v].strip(), parameter_notes=notes[v].strip()) for v in vids}),
            # Preserve old values as audit only; semantic roles may have changed.
            # Current-contract values are explicitly extracted in step 3.
            parameter_moves={v: {} for v in vids})
        result = merge_reviewed(store, plan)
        queue = WorkflowStore(root)
        try:
            for row in queue.db.execute("SELECT * FROM workbench_actions WHERE status='review'").fetchall():
                saved = json.loads(row['result'])
                if saved.get('revision_id') == rid:
                    update(queue, row, status='completed', progress='已采用通用流程，请按新版本提取参数',
                           result=dict(saved, merge_id=result['id']))
        finally:
            queue.close()
        from proximic_ring.agent_workflow.operations.operation_view import export_operations
        try:
            export_operations(store)
        except (OSError, ValueError, sqlite3.Error):
            return dict(message='通用流程已采用，历史页面导出暂失败；操作台已保存结果', merge_id=result['id'])
        return dict(message='通用流程已采用；第三步会按新版本提取各任务参数', merge_id=result['id'])
    finally:
        store.close()
