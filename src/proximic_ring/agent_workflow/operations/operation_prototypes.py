"""阶段 2：持久化通用 Workflow、不可变内容版本和原始来源。

首版直接复制已有的参数化自然语言 Workflow，不凭空编写执行步骤。
旧库迁移保留操作 ID，以最早成员作种子，其余成员重新与该通用流程复核。
内容/来源/generation 不覆盖；status/validation 可随人工核验更新。
"""
import json
import uuid
from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.extraction.workflow_store import now


def revision(store, rid):
    row=store.db.execute('SELECT * FROM operation_revisions WHERE id=?',(rid,)).fetchone()
    if row is None:raise ValueError('通用 Workflow 版本不存在')
    value=dict(row)
    for key in ('flow','sources','generation','validation'):value[key]=json.loads(value[key])
    return value


def heads(store):
    return {r['operation_id']:revision(store,r['active_revision']) for r in store.rows('operation_heads')}


def source_refs(store, vid):
    return [dict(sample_id=r['sample_id'],version_id=vid,source=json.loads(r['source']))
            for r in store.db.execute('SELECT * FROM sample_versions WHERE version_id=?',(vid,))]


def add_revision(store, oid, flow, *, parent=None, kind='seed', reason='', sources=(), generation=None):
    rid=digest([oid,parent,flow,kind,list(sources)])
    store.db.execute('INSERT OR IGNORE INTO operation_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (rid,oid,parent,encoded(flow),'active' if parent is None else 'candidate',kind,reason,
         encoded(list(sources)),encoded(generation or {}),now(),encoded({'state':'source_backed' if parent is None else 'not_executed'})))
    if parent is None:store.db.execute('INSERT INTO operation_heads VALUES (?,?)',(oid,rid))
    return rid


def attach(store, sample, oid, rid, admission_id):
    """Only the reviewed exact source version is admitted; edited evidence is a new admission."""
    old=sample['operation_id']
    values=(sample['id'],sample['version_id'],oid,rid,admission_id,now())
    store.db.execute('INSERT OR IGNORE INTO operation_membership_events VALUES (?,?,?,?,?,?)',values)
    store.db.execute('INSERT INTO operation_memberships VALUES (?,?,?,?,?,?) '
                     'ON CONFLICT(sample_id,version_id) DO UPDATE SET operation_id=excluded.operation_id,'
                     'revision_id=excluded.revision_id,admission_id=excluded.admission_id,at=excluded.at',values)
    if old!=oid:
        store.db.execute('INSERT INTO assignments(sample_id,old_id,new_id,at) VALUES (?,?,?,?)',
                         (sample['id'],old,oid,now()))
        store.db.execute('UPDATE samples SET operation_id=? WHERE id=?',(oid,sample['id']))


def seed(store, sample, *, oid=None, admission_id='seed'):
    oid=oid or uuid.uuid4().hex
    store.db.execute('INSERT OR IGNORE INTO operations VALUES (?,?,NULL)',(oid,now()))
    flow=store.versions()[sample['version_id']]
    rid=add_revision(store,oid,flow,reason='以来源中已有的参数化完整流程建立首版；不新增或改写步骤',
                     sources=source_refs(store,sample['version_id']))
    attach(store,sample,oid,rid,admission_id)
    return oid,rid


def prepare(store):
    """Short transaction only; never run embedding/model under SQLite write lock.

    Existing group IDs remain stable. A later member cannot trigger global
    reclustering or steal the first three members of an existing ten-member group.
    Pending legacy members retain their old assignment for audit, but recommendation
    usage counts include only members accepted under this pipeline.
    """
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        samples=sorted(store.rows('samples'),key=lambda s:(s['occurred_at'],s['id']))
        current=heads(store)
        for sample in samples:
            if sample['operation_id']:
                oid=store.canonical(sample['operation_id'])
                if oid not in current:
                    seed(store,sample,oid=oid,admission_id='legacy_seed')
                    current=heads(store)
        if samples and not current:
            seed(store,samples[0]);current=heads(store)
        for sample in samples:
            admitted=store.db.execute('SELECT 1 FROM operation_memberships WHERE sample_id=? AND version_id=?',
                                     (sample['id'],sample['version_id'])).fetchone()
            if admitted:continue
            aid=digest(['prototype-admission-1',sample['id'],sample['version_id']])
            store.db.execute('INSERT OR IGNORE INTO operation_admissions VALUES (?,?,?,?,?,?)',
                             (aid,sample['id'],sample['version_id'],'pending','{}',now()))


def activate(store, rid, *, run_file):
    """Activate only after a successful natural-language trial of this exact revision.

    A completion event alone is insufficient: the existing Trials goal verification
    must explicitly be success, and the frozen case must identify this revision and
    contain exactly its workflow text. No desktop execution is started here.
    """
    from pathlib import Path
    from proximic_ring.agent_workflow.extraction.workflow_artifacts import workflow_steps
    from proximic_ring.agent_workflow.execution.workflow_trials import validate_case,task_prompt,digest as trial_digest
    path=Path(run_file).resolve()
    if not path.is_relative_to(store.root.resolve()):raise ValueError('验证记录必须位于本记录目录内')
    run=json.loads(path.read_text());rev=revision(store,rid)
    if rev['change_kind']=='generalization':
        raise ValueError('泛化候选不能通过单次 Trials 启用：须另行审阅各条件分支及参数迁移，使用明确批准的 merge-reviewed 计划')
    case_path=store.root/'workflow-tests'/(run.get('case_id','')+'.case.json')
    if not case_path.resolve().is_relative_to((store.root/'workflow-tests').resolve()):raise ValueError('验证任务路径无效')
    case=validate_case(json.loads(case_path.read_text()))
    if (run.get('mode')!='natural_language' or run.get('status')!='completed' or run.get('goal_verification')!='success'
        or case.get('source',{}).get('operation_revision')!=rid or case.get('workflow')!=rev['flow']
        or case.get('natural_language_workflow')!=workflow_steps(rev['flow'])
        or run.get('prompt_sha256')!=trial_digest(task_prompt(case,'natural_language'))):
        raise ValueError('需要此版本的自然语言 Trials 成功记录，并明确核验目标成功')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        current=heads(store)[rev['operation_id']]
        if rev['parent_id']!=current['id']:raise ValueError('候选基于旧版本；需要重新生成并验证，不能覆盖当前版本')
        store.db.execute("UPDATE operation_revisions SET status='historical' WHERE id=?",(current['id'],))
        store.db.execute("UPDATE operation_revisions SET status='active',validation=? WHERE id=?",
                         (encoded(dict(state='execution_verified',run_file=str(path.relative_to(store.root.resolve())),at=now())),rid))
        store.db.execute('UPDATE operation_heads SET active_revision=? WHERE operation_id=?',(rid,rev['operation_id']))


def recheck(store,sample_id):
    """Explicitly reclassify a task after a prototype changes; keep all prior decisions."""
    row=store.db.execute('SELECT * FROM samples WHERE id=?',(sample_id,)).fetchone()
    if row is None:raise ValueError('样本不存在')
    aid=uuid.uuid4().hex
    with store.db:
        store.db.execute('INSERT INTO operation_admissions VALUES (?,?,?,?,?,?)',
                         (aid,sample_id,row['version_id'],'pending',encoded({'force_review':True}),now()))
    return aid
