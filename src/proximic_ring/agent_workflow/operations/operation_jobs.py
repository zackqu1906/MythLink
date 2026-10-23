"""事务领取后台作业；不在数据库锁内调用模型。失败与中断只显式重试。"""
import json
import uuid
from datetime import datetime,timezone
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.extraction.workflow_store import now,atomic_json
from proximic_ring.agent_workflow.extraction.workflow_limits import MAX_INPUT_CHARS
from proximic_ring.agent_workflow.extraction.workflow_compaction import encoded as model_input


def create_job(store,kind,subject,packet):
    # Count the same compact JSON string sent by request_json, not a differently
    # spaced database serialization. This local gate applies to admission/update.
    if len(model_input(packet))>MAX_INPUT_CHARS:raise ValueError(f'后台输入超过 {MAX_INPUT_CHARS:,} 字符；保留原数据，不静默截断')
    jid=uuid.uuid4().hex
    atomic_json(store.directory/(jid+'.operation-input.json'),packet)
    store.db.execute('INSERT INTO operation_jobs VALUES (?,?,?,?,?,?,?,?,?)',
                     (jid,kind,subject,'running',encoded(packet),'{}','{}','',now()))
    return jid


def finish_job(store,jid,value,generation,*,status='completed',error=''):
    atomic_json(store.directory/(jid+'.operation-result.json'),dict(result=value,generation=generation,status=status,error=error))
    store.db.execute('UPDATE operation_jobs SET status=?,result=?,generation=?,error=? WHERE id=?',
                     (status,encoded(value),encoded(generation),error,jid))


def recover(store):
    """Explicit retry recovers a crashed lease after 10 minutes; no automatic paid retry."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        for job in store.rows('operation_jobs'):
            if job['status']!='running':continue
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(job['at'])).total_seconds()
            if age<=600:continue
            store.db.execute("UPDATE operation_jobs SET status='failed',error='进程中断，手动恢复' WHERE id=?",(job['id'],))
            if job['kind'] in ('admission','update'):
                table='operation_admissions' if job['kind']=='admission' else 'operation_updates'
                store.db.execute(f"UPDATE {table} SET status='failed' WHERE id=? AND status='running'",(job['subject_id'],))


def snapshot(store):
    return {h['operation_id']:h['active_revision'] for h in store.rows('operation_heads')}


def safe_error(exc):
    # Never persist arbitrary transport exceptions which could contain credentials.
    return (str(exc) if isinstance(exc,ValueError) else '后台处理失败：'+type(exc).__name__)[:1000]


def failed_output(exc,value,generation):
    """Preserve malformed final assistant text for audit; never hidden reasoning."""
    from proximic_ring.agent_workflow.extraction.workflow_model import ModelOutputError
    if isinstance(exc,ModelOutputError):return {'invalid_final_text':exc.final_text},exc.generation
    return value,generation
