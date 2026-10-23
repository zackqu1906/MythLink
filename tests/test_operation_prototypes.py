"""Incremental operation membership, evidence, versioning and foreground-isolation contracts."""
import copy
import json
from pathlib import Path
import pytest
from proximic_ring.agent_workflow.operations import operation_catalog as catalog
from proximic_ring.agent_workflow.operations import operation_retrieval as retrieval
from proximic_ring.agent_workflow.operations import operation_model
from proximic_ring.agent_workflow.operations.operation_store import OperationStore,encoded
from proximic_ring.agent_workflow.operations.operation_prototypes import heads,activate
from proximic_ring.agent_workflow.operations.operation_matching import digest
from proximic_ring.agent_workflow.operations.operation_library import library
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.operations.operation_group_view import group_filename
from proximic_ring.agent_workflow.execution.operation_trial_bridge import prepare_trial
from proximic_ring.agent_workflow.execution.workflow_trials import TrialStore,Trial,validate_case


def flow(method='点击新建后输入 {{title}} 和 {{body}}'):
    return dict(name='备忘录创建笔记',goal='在备忘录创建标题为 {{title}}、正文为 {{body}} 的新笔记',
        parameters=[dict(name='title',description='标题'),dict(name='body',description='正文')],
        steps=[dict(action='创建并填写笔记',method=method,check='标题与正文已显示')],conditions=[],fallbacks=[])


def sample(sid='a',f=None):
    return dict(id=sid,flow=f or flow(),kind='test',source=dict(session_id='s',episode_id=sid,outcome='completed'),
                occurred_at='2026-10-16T00:00:00+00:00')


@pytest.fixture
def store(tmp_path,monkeypatch):
    # This suite isolates admission/revision model-call budgets. First-time Hub
    # copy backfill has its own queue and integration coverage in test_operation_display.
    monkeypatch.setattr(catalog,'process_display',lambda *a,**kw:False)
    def vectors(directory,flows,**kw):
        return {v:dict(goal=[1.]+[0.]*1023,steps=[1.]+[0.]*1023) for v in flows},{}
    monkeypatch.setattr(retrieval,'ensure_embeddings',vectors)
    s=OperationStore(tmp_path);yield s;s.close()


def prepare(store):
    store.ingest(sample());catalog.regroup(store)
    store.ingest(sample('b',flow('用 New Note 按钮创建，再填入 {{title}} 和 {{body}}')))
    catalog.regroup(store)


def decision(packet,relation='same',novelty=None):
    return dict(decisions=[dict(operation_id=c['operation_id'],relation=relation,
        reason='可由共同流程表达' if relation=='same' else '独立目标') for c in packet['candidates']])


def synthesized(packet, workflow=None):
    return dict(workflow=workflow or copy.deepcopy(packet['current_workflow']), reason='综合来源方法与要求',
        parameter_bindings={s['version_id']:{p['name']:[p['name']] for p in s['workflow']['parameters']}
                            for s in packet['sources']})


def unchanged(packet,*a,**kw):return synthesized(packet),{}


def same(packet,*a,**kw):return decision(packet),{'thinking_requested':'enabled'}


def test_embedding_similarity_alone_does_not_admit(store):
    prepare(store)
    assert len(library(store))==1 and len(library(store)[0]['samples'])==1
    assert store.rows('operation_admissions')[0]['status']=='pending'
    assert len(store.rows('pairs'))==0


def test_same_synthesizes_before_adding_member_without_rewriting_prototype(store):
    prepare(store);before=heads(store);oid=next(iter(before))
    assert catalog.review_one(store,None,model=same)
    assert len(library(store)[0]['samples'])==1
    assert store.rows('operation_updates')[0]['status']=='pending'
    assert catalog.review_one(store,None,model=unchanged)
    assert len(library(store)[0]['samples'])==2 and heads(store)==before
    assert store.rows('operation_updates')[0]['status']=='no_change'
    assert len(store.rows('operation_revisions'))==1
    assert not catalog.review_one(store,None,model=lambda *a,**k:pytest.fail('unnecessary model'))
    assert recommend(store,scope='test')['items'][0]['workflow_revision']==before[oid]['id']


def test_duplicate_immutable_version_skips_model(store):
    store.ingest(sample());catalog.regroup(store);store.ingest(sample('b'))
    catalog.review_one(store,None,model=lambda *a,**k:pytest.fail('duplicate model call'))
    assert len(library(store)[0]['samples'])==2 and len(store.rows('operation_revisions'))==1


def test_group_pages_preserve_duplicate_experiences_and_exclude_pending_members(store):
    store.ingest(sample());catalog.regroup(store)
    store.ingest(sample('repeat'))
    catalog.review_one(store,None,model=lambda *a,**k:pytest.fail('duplicate call'))
    # b is not admitted yet; it must not appear as a group member.
    store.ingest(sample('b',flow('用 New Note 按钮创建，再填入 {{title}} 和 {{body}}')))
    catalog.regroup(store)
    groups=library(store)
    assert len(groups)==1 and len(groups[0]['samples'])==2
    page=export_operations(store).read_text()
    filename=group_filename(groups[0]['operation_id'])
    assert f"id='op-{groups[0]['operation_id']}'" in page and '个 Workflow 组' in page
    assert page.count("class='workflow-member'")==2
    assert f"href='{filename}'" not in page
    detail=(store.directory/filename).read_text()
    assert detail.count("class='workflow-member'")==2
    assert '&quot;sample_id&quot;: &quot;a&quot;' in detail
    assert '&quot;sample_id&quot;: &quot;repeat&quot;' in detail
    assert '&quot;sample_id&quot;: &quot;b&quot;' not in detail
    assert detail.index("id='general-workflow'")<detail.index("id='members'")
    assert "href='index.html'" in detail and '2 条组内 Workflow · 1 个不同来源版本' in detail


def test_ten_members_still_only_compare_one_general_workflow(store):
    store.ingest(sample());catalog.regroup(store)
    for n in range(10):
        store.ingest(sample(str(n)));catalog.review_one(store,None,model=lambda *a,**k:pytest.fail('duplicate call'))
    store.ingest(sample('new',flow('通过快捷键新建后输入 {{title}} 与 {{body}}')))
    def inspect(packet,*a,**kw):
        assert len(packet['candidates'])==1
        assert set(packet['candidates'][0])=={'operation_id','revision_id','workflow'}
        return same(packet)
    catalog.review_one(store,None,model=inspect)
    catalog.review_one(store,None,model=unchanged)
    assert len(library(store)[0]['samples'])==12 and len(store.rows('pairs'))==0


def test_different_creates_new_group_and_uncertain_waits(store):
    prepare(store)
    catalog.review_one(store,None,model=lambda p,*a,**k:(decision(p,'uncertain'),{}))
    assert len(library(store))==1
    assert not catalog.review_one(store,None,model=same)
    catalog.review_one(store,None,retry=True,model=lambda p,*a,**k:(decision(p,'different'),{}))
    assert len(library(store))==2


def novel_input(store):
    store.ingest(sample());catalog.regroup(store)
    f=flow();f['conditions']=['若输入区尚未聚焦，先聚焦正文编辑区']
    store.ingest(sample('b',f))
    novelty=dict(kind='condition',reason='新增焦点恢复条件，原流程没有说明',
                 evidence=[dict(path='/conditions/0',quote=f['conditions'][0])])
    catalog.review_one(store,None,model=lambda p,*a,**k:(decision(p,novelty=novelty),{}))
    return f


def update_model(packet,*a,**kw):
    new=copy.deepcopy(packet['current_workflow'])
    new['steps'][0]='若输入区尚未聚焦，先聚焦正文编辑区。'+new['steps'][0]
    return synthesized(packet,new),{}


def test_material_update_is_versioned_candidate_never_default(store):
    novel_input(store);before=heads(store)
    assert store.rows('operation_updates')[0]['status']=='pending'
    catalog.review_one(store,None,model=update_model)
    assert heads(store)==before
    revisions=store.rows('operation_revisions');assert len(revisions)==2
    candidate=next(r for r in revisions if r['status']=='candidate')
    assert len(json.loads(candidate['sources']))==2
    assert json.loads(candidate['generation'])['parameter_bindings']
    assert recommend(store,scope='test')['items'][0]['workflow']['conditions']==[]
    text=export_operations(store).read_text()
    detail=(store.directory/group_filename(candidate['operation_id'])).read_text()
    assert '候选：尚未启用' in detail and '入组' in text
    active_section=detail.split("id='members'",1)[0]
    assert '候选：尚未启用' not in active_section
    assert candidate['id'] in detail and '当前版本' in active_section
    assert (store.directory/'definitions'/candidate['operation_id']/(candidate['id']+'.txt')).exists()


def test_repeated_information_still_enters_synthesis_then_returns_unchanged(store):
    f=flow();f['conditions']=['编辑区可输入'];store.ingest(sample(f=f));catalog.regroup(store)
    changed=copy.deepcopy(f);changed['name']+='流程';store.ingest(sample('b',changed))
    novelty=dict(kind='condition',reason='模型错误认为是新增',evidence=[dict(path='/conditions/0',quote='编辑区可输入')])
    catalog.review_one(store,None,model=lambda p,*a,**k:(decision(p,novelty=novelty),{}))
    assert store.rows('operation_updates')[0]['status']=='pending'
    catalog.review_one(store,None,model=unchanged)
    assert len(library(store)[0]['samples'])==2
    assert store.rows('operation_updates')[0]['status']=='no_change'


@pytest.mark.parametrize('bad',['invented_quote','unknown_parameter','missing_candidate'])
def test_invalid_admission_preserves_membership_and_requires_retry(store,bad):
    prepare(store)
    def model(p,*a,**k):
        v=decision(p)
        if bad=='missing_candidate':v['decisions']=[]
        elif bad=='unknown_parameter':v['decisions'][0]['parameter_map']={'wrong':'title'}
        else:v['decisions'][0]['novelty']=dict(kind='path',reason='新增',evidence=[dict(path='/steps/0/method',quote='编造')])
        return v,{}
    catalog.review_one(store,None,model=model)
    assert store.rows('operation_admissions')[0]['status']=='failed'
    assert len(library(store)[0]['samples'])==1
    assert not catalog.review_one(store,None,model=same)
    catalog.review_one(store,None,model=same,retry=True)
    catalog.review_one(store,None,model=unchanged)
    assert len(library(store)[0]['samples'])==2


@pytest.mark.parametrize('bad',['source','deletion','parameter'])
def test_candidate_rejects_invalid_bindings_empty_steps_and_unknown_parameters(store,bad):
    novel_input(store);before=heads(store)
    def model(p,*a,**k):
        v,g=update_model(p)
        if bad=='source':v['parameter_bindings']['madeup']={}
        elif bad=='deletion':v['workflow']['steps']=[]
        else:v['workflow']['steps'][0]='输入 {{secret}}'
        return v,g
    catalog.review_one(store,None,model=model)
    assert len(store.rows('operation_revisions'))==1 and heads(store)==before
    assert store.rows('operation_updates')[0]['status']=='failed'


def test_source_revision_during_model_does_not_apply_stale_decision(store):
    prepare(store)
    def model(p,*a,**k):
        second=OperationStore(store.root)
        try:second.ingest(sample('b',flow('另一个新的来源版本')))
        finally:second.close()
        return same(p)
    catalog.review_one(store,None,model=model)
    assert len(library(store)[0]['samples'])==1
    assert store.rows('operation_jobs')[0]['status']=='failed'
    assert json.loads(store.rows('operation_jobs')[0]['result'])['decisions']


def test_two_workers_never_duplicate_paid_call(store):
    prepare(store)
    def model(p,*a,**k):
        second=OperationStore(store.root)
        try:assert not catalog.review_one(second,None,model=lambda *a,**k:pytest.fail('duplicate'))
        finally:second.close()
        return same(p)
    catalog.review_one(store,None,model=model)
    assert len(store.rows('operation_jobs'))==1


def test_busy_worker_starts_no_model(store,monkeypatch):
    prepare(store)
    monkeypatch.setattr(catalog,'agent_busy',lambda:True)
    assert not catalog.review_one(store,None,model=lambda *a,**k:pytest.fail('busy request'))
    assert not store.rows('operation_jobs')


def test_busy_after_response_keeps_result_but_defers_update(store,monkeypatch):
    store.ingest(sample());catalog.regroup(store)
    f=flow();f['conditions']=['先聚焦编辑区'];store.ingest(sample('b',f))
    def model(p,*a,**k):
        monkeypatch.setattr(catalog,'agent_busy',lambda:True)
        return decision(p,novelty=dict(kind='condition',reason='新增条件',evidence=[dict(path='/conditions/0',quote=f['conditions'][0])])),{}
    catalog.review_one(store,None,model=model)
    assert store.rows('operation_jobs')[0]['status']=='completed'
    assert not catalog.review_one(store,None,model=lambda *a,**k:pytest.fail('update while busy'))
    assert store.rows('operation_updates')[0]['status']=='pending'


def test_legacy_migration_keeps_ids_feedback_and_pairs(store):
    store.ingest(sample());store.ingest(sample('b',flow('同一目标的另一种路径')));store.assign([{'a','b'}])
    oid=store.rows('samples')[0]['operation_id']
    # Insert actual legacy schema rows, not new verified exposures. Migration must preserve them.
    with store.db:
        store.db.execute('INSERT INTO impressions VALUES (?,?,?,?,?,?)',('legacy','test','{}','[]','2026-10-15','old'))
        store.db.execute('INSERT INTO feedback(impression_id,operation_id,kind,at) VALUES (?,?,?,?)',
                         ('legacy',oid,'accepted','2026-10-15'))
    with store.db:store.db.execute('INSERT INTO pairs VALUES (?,?,?,?,?)',('old','x','y','same','{}'))
    catalog.regroup(store);catalog.review_one(store,None,model=same)
    assert {s['operation_id'] for s in store.rows('samples')}=={oid}
    assert len(store.rows('pairs'))==1 and len(store.rows('feedback'))==1
    assert recommend(store,scope='test')['items'][0]['feedback']['accepted']==0
    assert not store.rows('recommendation_exposures')


def test_activation_requires_matching_successful_steps_trial(store):
    novel_input(store);catalog.review_one(store,None,model=update_model)
    rid=next(r['id'] for r in store.rows('operation_revisions') if r['status']=='candidate')
    with store.db:store.db.execute("UPDATE operation_revisions SET change_kind='legacy_update' WHERE id=?",(rid,))
    path=prepare_trial(store,rid,{'title':'测试','body':'正文'},'备忘录已打开，无弹窗')
    case=validate_case(json.loads(path.read_text()));trials=TrialStore(store.root)
    baseline=Trial(trials,case,'baseline');baseline.finish('completed');trials.verify(baseline.row['id'],True)
    with pytest.raises(ValueError):activate(store,rid,run_file=trials.directory/(baseline.row['id']+'.run.json'))
    trial=Trial(trials,case,'natural_language');trial.finish('completed')
    with pytest.raises(ValueError):activate(store,rid,run_file=trials.directory/(trial.row['id']+'.run.json'))
    trials.verify(trial.row['id'],True)
    activate(store,rid,run_file=trials.directory/(trial.row['id']+'.run.json'))
    assert next(iter(heads(store).values()))['id']==rid
    assert next(iter(heads(store).values()))['validation']['state']=='execution_verified'


def test_recommendation_never_loads_embeddings_or_llm(store,monkeypatch):
    prepare(store);catalog.review_one(store,None,model=same)
    monkeypatch.setattr(retrieval,'ensure_embeddings',lambda *a,**k:pytest.fail('ranking encoded'))
    monkeypatch.setattr(operation_model,'call',lambda *a,**k:pytest.fail('ranking called model'))
    assert recommend(store,scope='test')['items'][0]['workflow']


def test_multiple_same_candidates_do_not_force_a_group(store):
    prepare(store)
    catalog.review_one(store,None,model=lambda p,*a,**k:(decision(p,'different'),{}))
    assert len(library(store))==2
    store.ingest(sample('c',flow('第三种自然语言表达')))
    catalog.review_one(store,None,model=same)
    assert store.rows('operation_admissions')[-1]['status']=='generalization_pending'
    assert len(json.loads(store.rows('operation_updates')[-1]['novelty'])['expected_heads'])==2
    assert sum(len(g['samples']) for g in library(store))==2


def test_top_k_caps_groups_not_history_members(store):
    f=flow();prototypes={str(i):dict(id=str(i),flow=f) for i in range(10)}
    selected,ranked=retrieval.retrieve(store.directory,f,prototypes)
    assert len(selected)==3 and len(ranked)==10


def test_recheck_preserves_membership_history_and_forces_semantic_review(store):
    from proximic_ring.agent_workflow.operations.operation_prototypes import recheck
    prepare(store);catalog.review_one(store,None,model=same)
    catalog.review_one(store,None,model=unchanged)
    old_events=len(store.rows('operation_membership_events'))
    recheck(store,'b');calls=[]
    def model(p,*a,**kw):calls.append(1);return same(p)
    catalog.review_one(store,None,model=model)
    catalog.review_one(store,None,model=unchanged)
    assert len(calls)==1 and len(store.rows('operation_membership_events'))==old_events+1
    assert len(store.rows('operation_revisions'))==1


def test_no_change_from_update_model_creates_no_revision(store):
    novel_input(store)
    catalog.review_one(store,None,model=unchanged)
    assert len(store.rows('operation_revisions'))==1
    assert store.rows('operation_updates')[0]['status']=='no_change'


def test_review_releases_write_lock_during_network(store):
    prepare(store)
    def model(p,*a,**kw):
        second=OperationStore(store.root)
        try:
            with second.db:
                second.db.execute('BEGIN IMMEDIATE')
                second.db.execute('UPDATE operations SET created_at=created_at')
        finally:second.close()
        return same(p)
    catalog.review_one(store,None,model=model)
    assert store.rows('operation_jobs')[0]['status']=='completed'


def test_interrupted_job_not_automatically_retried(store):
    prepare(store)
    from proximic_ring.agent_workflow.operations.operation_jobs import create_job,recover
    aid=store.rows('operation_admissions')[0]['id']
    with store.db:
        jid=create_job(store,'admission',aid,{})
        store.db.execute("UPDATE operation_jobs SET at='2020-01-01T00:00:00+00:00' WHERE id=?",(jid,))
        store.db.execute("UPDATE operation_admissions SET status='running' WHERE id=?",(aid,))
    assert not catalog.review_one(store,None,model=lambda *a,**k:pytest.fail('crash auto retried'))
    recover(store)
    assert store.rows('operation_jobs')[0]['status']=='failed'
    assert not catalog.review_one(store,None,model=same)
    catalog.review_one(store,None,model=same,retry=True)
    catalog.review_one(store,None,model=unchanged)
    assert len(library(store)[0]['samples'])==2


def test_invalid_final_text_retained_without_auto_retry(store):
    from proximic_ring.agent_workflow.extraction.workflow_model import ModelOutputError
    prepare(store)
    def bad(*a,**kw):raise ModelOutputError('模型没有返回 JSON','可回查的最终回答',{'elapsed_seconds':1})
    catalog.review_one(store,None,model=bad)
    job=store.rows('operation_jobs')[0]
    assert json.loads(job['result'])=={'invalid_final_text':'可回查的最终回答'}
    assert json.loads(job['generation'])['elapsed_seconds']==1
    assert not catalog.review_one(store,None,model=bad)
