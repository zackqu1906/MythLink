"""首版/历史通用操作的文案补齐队列，运行在已有独立后台 worker。

首版复制来源流程时没有生成调用，因此只为缺失 display 的当前版本发起一次
小请求。修订/入组中已同时生成的文案不再调用。普通成员增加不重写文案。
不读取原始会话、截图或用户反馈；不启动桌面操作，不持有 SQLite 锁等待网络。
"""
import json
from proximic_ring.agent_workflow.operations.operation_display import read_display, save_display
from proximic_ring.agent_workflow.operations.operation_display_prompt import PROMPT, VERSION
from proximic_ring.agent_workflow.operations.operation_prototypes import heads
from proximic_ring.agent_workflow.operations.operation_jobs import create_job, finish_job, safe_error, failed_output
from proximic_ring.agent_workflow.operations.operation_model import call
from proximic_ring.agent_workflow.operations.operation_embeddings import agent_busy, EmbeddingDeferred


def process_display(store, settings, *, model=None, retry=False, cancelled=lambda: False, operation_id=None, refresh=False):
    if refresh and not operation_id:raise ValueError('重新生成文案需要指定操作 ID')
    if cancelled() or agent_busy(): return False
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        jobs = store.rows('operation_jobs')
        if any(j['status'] == 'running' for j in jobs): return False
        failed = {j['subject_id'] for j in jobs if j['kind'] == 'display' and j['status'] == 'failed'}
        # A successfully saved legacy display may have older failed attempts.
        # Those do not block its one-time sentence upgrade. A failure of this
        # prompt version still blocks automatic retry, including network errors.
        failed_current={j['subject_id'] for j in jobs if j['kind']=='display' and j['status']=='failed'
                        and json.loads(j['input']).get('prompt_version')==VERSION}
        current=heads(store)
        if operation_id and operation_id not in current:raise ValueError('通用操作不存在')
        pending = [r for oid,r in current.items() if (not operation_id or oid==operation_id)
                   and (refresh or not (read_display(store, r['id']) or {}).get('content',{}).get('sentence'))
                   and (retry or refresh or r['id'] not in failed or
                        (read_display(store,r['id']) and r['id'] not in failed_current))]
        if not pending: return False
        rev = min(pending, key=lambda r: (r['created_at'], r['id']))
        packet = dict(revision_id=rev['id'], workflow=rev['flow'], prompt_version=VERSION)
        upgrading=bool(read_display(store,rev['id']))
        jid = create_job(store, 'display', rev['id'], packet)
    value = {}; generation = {}
    try:
        value, generation = call(packet, settings, prompt=PROMPT, model=model, cancelled=cancelled)
        generation.update(prompt_version=VERSION, job_id=jid)
        if not isinstance(value, dict) or set(value) != {'display'} or 'sentence' not in value['display']: raise ValueError('展示文案输出格式错误')
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            # A revision is immutable. If the head moved while awaiting the LLM,
            # save against the old revision only; never apply to the new head.
            save_display(store, rev['id'], value['display'], generation,replace=refresh or upgrading)
            finish_job(store, jid, value, generation)
    except EmbeddingDeferred as exc:
        with store.db: finish_job(store, jid, value, generation, status='deferred', error=str(exc))
        return False
    except Exception as exc:
        value, generation = failed_output(exc, value, generation)
        with store.db: finish_job(store, jid, value, generation, status='failed', error=safe_error(exc))
    return True
