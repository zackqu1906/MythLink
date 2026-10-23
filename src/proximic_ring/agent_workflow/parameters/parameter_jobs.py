"""归组之后的参数后台阶段，旧任务补齐和未来任务共用这一个入口。

只在 Agent/文本编辑空闲时发起一次独立文本模型请求；网络期间释放 SQLite
锁，不修改 Agent 对话。新任务/新版本按 subject 指纹自动发现，失败不自动
付费重试。无参数、已完成的同版本 Hub 票据无需模型，缺源只记原因。
"""
import json
from .parameter_prompt import VERSION, PROMPT
from .parameter_evidence import prepare_evidence, validate, signature
from .parameter_history import eligible_samples, save_record, current_records
from .parameter_rules import resolve, model_packet, VERSION as RULES_VERSION
from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.operations.operation_prototypes import revision
from proximic_ring.agent_workflow.operations.operation_jobs import create_job,finish_job,safe_error,failed_output
from proximic_ring.agent_workflow.operations.operation_model import call
from proximic_ring.agent_workflow.operations.operation_embeddings import agent_busy,EmbeddingDeferred


def process_parameters(store,settings,*,model=None,retry=False,cancelled=lambda:False,sample_id=None,scope='user',request_id=None):
    # Normal background work is user-only. Imported trials are never silently
    # sent to a model; an explicit CLI --scope test is a separate operation.
    if scope not in ('user','test'):raise ValueError('参数候选范围必须为 user 或 test')
    if cancelled() or agent_busy():return False
    # Files are read only in this background stage. A missing snapshot that is
    # later restored changes signature and automatically becomes eligible again.
    jobs=store.rows('operation_jobs')
    if any(j['status']=='running' for j in jobs):return False
    done={r['subject_id'] for r in store.rows('operation_parameter_history')
          if r['status']=='completed' or not retry}
    failed={j['subject_id'] for j in jobs if j['kind']=='parameters' and j['status']=='failed'}
    for sample in eligible_samples(store):
        if sample['kind']!=scope:continue
        if sample_id and sample['id']!=sample_id:continue
        rev=revision(store,sample['active_revision'])
        if not rev['flow']['parameters']:continue
        source=json.loads(sample['source'])
        # Direct Hub values are already structured and source-backed. Avoid
        # spending a model call to copy that same current-contract execution.
        ticket=store.db.execute('''SELECT 1 FROM recommendation_executions WHERE
            operation_id=? AND revision_id=? AND scope=? AND session_id=? AND episode_id=?
            AND state='completed' AND error='' ''',(sample['operation_id'],sample['active_revision'],
                sample['kind'],source.get('session_id'),source.get('episode_id'))).fetchone()
        if ticket and not request_id:continue
        subject=digest([VERSION,sample['id'],sample['version_id'],sample['operation_id'],
            sample['active_revision'],source,signature(store,source)])
        if request_id:subject=digest([subject,'manual',request_id])
        if subject in done or (subject in failed and not retry):continue
        try:
            packet,blocks=prepare_evidence(store,sample,rev)
        except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
            with store.db:
                save_record(store,sample,subject,[],dict(error=safe_error(exc)),status='missing_source')
            return True
        previous=next((r for r in current_records(store,scope=scope)
                       if r['sample_id']==sample['id']),None)
        resolved,methods=resolve(packet,blocks,previous)
        packet=model_packet(packet,resolved)
        packet.update(prompt_version=VERSION,rules_version=RULES_VERSION,
                      sample_id=sample['id'],revision_id=rev['id'])
        if request_id:packet['workbench_request_id']=request_id
        break
    else:return False
    if cancelled() or agent_busy():return False
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if store.db.execute("SELECT 1 FROM operation_jobs WHERE status='running'").fetchone():return False
        # The source projection happened without a lock. Recheck the exact
        # subject to prevent a concurrent worker/CLI issuing the same paid call.
        if store.db.execute("SELECT 1 FROM operation_jobs WHERE kind='parameters' AND subject_id=? AND status IN ('completed','failed')",
                            (subject,)).fetchone() and not retry:return False
        jid=create_job(store,'parameters',subject,packet)
    value={};generation={}
    try:
        if packet['general_workflow']['parameters']:
            value,generation=call(packet,settings,prompt=PROMPT,model=model,cancelled=cancelled)
        else:value=dict(parameters=[])
        remaining=packet['general_workflow']
        validated=validate(value,remaining,blocks)
        # Validate rule results independently; a model cannot overwrite them.
        rules_flow=dict(parameters=[p for p in rev['flow']['parameters'] if p['name'] in resolved])
        rule_values=validate(dict(parameters=list(resolved.values())),rules_flow,blocks)
        by_name={p['name']:p for p in rule_values+validated}
        parameters=[by_name[p['name']] for p in rev['flow']['parameters']]
        generation.update(prompt_version=VERSION,job_id=jid,rules_version=RULES_VERSION,
                          method='rules_then_llm' if remaining['parameters'] else 'rules',
                          rule_parameters=methods,model_parameters=[p['name'] for p in remaining['parameters']])
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            save_record(store,sample,subject,parameters,generation,job_id=jid)
            finish_job(store,jid,value,generation)
    except EmbeddingDeferred as exc:
        with store.db:finish_job(store,jid,value,generation,status='deferred',error=str(exc))
        return False
    except Exception as exc:
        value,generation=failed_output(exc,value,generation)
        with store.db:
            save_record(store,sample,subject,[],dict(generation,error=safe_error(exc)),job_id=jid,status='failed')
            finish_job(store,jid,value,generation,status='failed',error=safe_error(exc))
    return True
