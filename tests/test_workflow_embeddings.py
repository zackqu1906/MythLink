"""Boundary tests use deterministic vectors; real model quality lives in the benchmark."""
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from proximic_ring.agent_workflow.operations import operation_embeddings as embeddings
from proximic_ring.agent_workflow.operations import operation_legacy as catalog
from proximic_ring.agent_workflow.operations.operation_matching import candidates, pair_key, digest, review_closure, complete_linkage, VERSION
from proximic_ring.agent_workflow.operations.operation_store import OperationStore, encoded


def flow(name='创建笔记',method='新建条目并填写用户提供的标题和正文'):
    return dict(name=name,goal=name,parameters=[],steps=[dict(action=name,method=method,check='确认保存')],conditions=[],fallbacks=[])


def vector(index=0):
    value=[0.]*1024;value[index]=1.;return value


def fake_runner(requests,directory,**kwargs):
    return dict(vectors={k:dict(vector=vector(),tokens=10) for k in requests},metrics={},interrupted=False)


def test_cache_only_encodes_changed_text_and_warm_path_never_loads_model(tmp_path):
    calls=[]
    def run(requests,*a,**kw):
        calls.append(set(requests));return fake_runner(requests,*a,**kw)
    versions={'a':flow()}
    first,meta=embeddings.ensure_embeddings(tmp_path,versions,runner=run)
    assert meta['cache_misses']==2
    second,warm=embeddings.ensure_embeddings(tmp_path,versions,runner=lambda *a,**kw:pytest.fail('model loaded on warm lookup'))
    assert second==first and warm['cache_hits']==2
    versions['a']['steps'][0]['method']+='并检查标题'
    _,changed=embeddings.ensure_embeddings(tmp_path,versions,runner=run)
    assert changed['cache_hits']==1 and changed['cache_misses']==1 and len(calls)==2


def test_encoder_revision_and_text_renderer_invalidate_cache(monkeypatch):
    key=embeddings.cache_key('相同文本')
    monkeypatch.setattr(embeddings,'REVISION','another-revision')
    assert embeddings.cache_key('相同文本')!=key
    next_key=embeddings.cache_key('相同文本')
    monkeypatch.setattr(embeddings,'TEXT_VERSION','another-renderer')
    assert embeddings.cache_key('相同文本')!=next_key


def test_busy_does_not_spawn_encoder_or_fallback_to_lexical(tmp_path,monkeypatch):
    monkeypatch.setattr(embeddings,'agent_busy',lambda:True)
    with pytest.raises(embeddings.EmbeddingDeferred):
        embeddings.ensure_embeddings(tmp_path,{'a':flow()},runner=lambda *a,**kw:pytest.fail('busy Agent started inference'))
    assert json.loads((tmp_path/'embedding-status.json').read_text())['status']=='deferred'


def test_partial_results_survive_cancellation_and_resume_only_missing(tmp_path):
    def interrupted(requests,*a,**kw):
        key=next(iter(requests))
        return dict(vectors={key:dict(vector=vector(),tokens=8)},metrics={},interrupted=True)
    with pytest.raises(embeddings.EmbeddingDeferred):
        embeddings.ensure_embeddings(tmp_path,{'a':flow()},runner=interrupted)
    _,meta=embeddings.ensure_embeddings(tmp_path,{'a':flow()},runner=fake_runner)
    assert meta['cache_hits']==1 and meta['cache_misses']==1


@pytest.mark.parametrize('bad',[[],[float('nan')]*1024,[2.]*1024])
def test_invalid_vectors_never_enter_cache(tmp_path,bad):
    def invalid(requests,*a,**kw):
        return dict(vectors={k:dict(vector=bad,tokens=2) for k in requests},metrics={},interrupted=False)
    with pytest.raises(ValueError,match='无效'):
        embeddings.ensure_embeddings(tmp_path,{'a':flow()},runner=invalid)
    with sqlite3.connect(tmp_path/'embeddings.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM vectors').fetchone()[0]==0


def test_corrupted_cached_vector_gets_recomputed(tmp_path):
    embeddings.ensure_embeddings(tmp_path,{'a':flow()},runner=fake_runner)
    with sqlite3.connect(tmp_path/'embeddings.sqlite3') as db:
        db.execute("UPDATE vectors SET vector='[]'")
    _,meta=embeddings.ensure_embeddings(tmp_path,{'a':flow()},runner=fake_runner)
    assert meta['cache_hits']==0 and meta['cache_misses']==2


def test_model_failure_keeps_existing_catalog_identities(tmp_path,monkeypatch):
    store=OperationStore(tmp_path)
    try:
        for i in ('a','b'):
            store.ingest(dict(id=i,flow=flow(method=i),kind='test',source={},occurred_at='2026-10-16T00:00:00+00:00'))
        store.assign([{'a'},{'b'}]);before=store.rows('samples')
        def fail(*a,**kw):raise ValueError('本地模型不可用')
        monkeypatch.setattr(catalog,'ensure_embeddings',fail)
        with pytest.raises(ValueError):catalog.regroup(store)
        assert store.rows('samples')==before and not store.rows('pairs')
    finally:store.close()


def test_embeddings_are_computed_without_catalog_write_lock(tmp_path,monkeypatch):
    store=OperationStore(tmp_path)
    try:
        for i in ('a','b'):
            store.ingest(dict(id=i,flow=flow(method=i),kind='test',source={},occurred_at='2026-10-16T00:00:00+00:00'))
        def check_lock(directory,versions,**kw):
            with sqlite3.connect(store.directory/'catalog.sqlite3',timeout=.1) as other:
                other.execute('BEGIN IMMEDIATE');other.rollback()
            return {v:{'goal':vector(),'steps':vector()} for v in versions},{}
        monkeypatch.setattr(catalog,'ensure_embeddings',check_lock)
        assert catalog.regroup(store)[0]['decision']=='pending'
    finally:store.close()


def test_old_review_migrates_without_new_llm_request_and_keeps_operation_ids(tmp_path,monkeypatch):
    store=OperationStore(tmp_path)
    try:
        for i in ('a','b'):
            store.ingest(dict(id=i,flow=flow(method=i),kind='test',source={},occurred_at='2026-10-16T00:00:00+00:00'))
        store.assign([{'a','b'}]);oid=store.rows('samples')[0]['operation_id']
        left,right=sorted(store.versions());legacy_id=digest(['whole-workflow-tfidf-complete-2',left,right])
        detail=dict(algorithm='whole-workflow-tfidf-complete-2',score=.4,review_job='old',reason='已有模型复核',
                    relation='same',pair_id=legacy_id,parameter_map={},variant_parameters={'left':[],'right':[]})
        with store.db:
            store.db.execute('INSERT INTO pairs VALUES (?,?,?,?,?)',(legacy_id,left,right,'same',encoded(detail)))
            store.db.execute('INSERT INTO review_jobs VALUES (?,?,?,?,?,?)',
                ('old','completed',encoded([legacy_id]),encoded({'review_prompt_version':catalog.REVIEW_PROMPT_VERSION}),'','2026-10-16'))
        monkeypatch.setattr(catalog,'ensure_embeddings',lambda directory,versions,**kw:({v:{'goal':vector(),'steps':vector()} for v in versions},{}))
        catalog.regroup(store)
        assert {s['operation_id'] for s in store.rows('samples')}=={oid}
        assert len(store.rows('pairs'))==2
        new=next(p for p in store.rows('pairs') if p['id']==pair_key(left,right))
        assert new['status']=='same' and json.loads(new['detail'])['review_reused_from']==legacy_id
        assert not catalog.review_one(store,None,model=lambda *a,**kw:pytest.fail('paid review repeated'))
    finally:store.close()


def test_complete_linkage_closure_requests_missing_pair_but_does_not_merge_it():
    flows={x:flow() for x in ('a','b','c')}
    needed=review_closure(flows,[('a','b'),('b','c')])
    assert pair_key('a','c') in needed
    groups=complete_linkage({x:x for x in flows},{pair_key('a','b'),pair_key('b','c')})
    assert len(groups)==2


@pytest.mark.parametrize('status',['failed','reviewing'])
def test_legacy_failure_or_abandoned_review_requires_explicit_retry(tmp_path,monkeypatch,status):
    store=OperationStore(tmp_path)
    try:
        for i in ('a','b'):
            store.ingest(dict(id=i,flow=flow(method=i),kind='test',source={},occurred_at='2026-10-16T00:00:00+00:00'))
        left,right=sorted(store.versions());legacy_id=digest(['old-retrieval',left,right])
        with store.db:
            store.db.execute('INSERT INTO pairs VALUES (?,?,?,?,?)',
                (legacy_id,left,right,status,encoded(dict(algorithm='old-retrieval',review_job='old'))))
            store.db.execute('INSERT INTO review_jobs VALUES (?,?,?,?,?,?)',
                ('old','running' if status=='reviewing' else 'failed',encoded([legacy_id]),'{}','','2020-01-01T00:00:00+00:00'))
        monkeypatch.setattr(catalog,'ensure_embeddings',lambda directory,versions,**kw:({v:{'goal':vector(),'steps':vector()} for v in versions},{}))
        if status=='reviewing':
            with pytest.raises(embeddings.EmbeddingDeferred):catalog.regroup(store)
            assert store.rows('review_jobs')[0]['status']=='running'
            catalog.recover_abandoned_reviews(store)
        catalog.regroup(store)
        assert all(p['status']=='failed' for p in store.rows('pairs'))
        assert not catalog.review_one(store,None,model=lambda *a,**kw:pytest.fail('failed review auto-retried'))
        def review(packet,*a,**kw):
            assert [p['pair_id'] for p in packet['pairs']]==[pair_key(left,right)]
            return {'decisions':[dict(pair_id=pair_key(left,right),relation='different',parameter_map={},
                variant_parameters={'left':[],'right':[]},reason='不同操作')]},{}
        assert catalog.review_one(store,None,retry=True,model=review)
        assert next(p for p in store.rows('pairs') if p['id']==pair_key(left,right))['status']=='different'
    finally:store.close()


def test_new_algorithm_has_no_tfidf_gate_or_automatic_merge():
    flows={'a':flow('把授课重点留成便笺'),'b':flow('创建课堂记录')}
    vecs={x:{'goal':vector(),'steps':vector()} for x in flows}
    pair=candidates(flows,vecs)[0]
    assert pair['decision']=='pending' and pair['algorithm']==VERSION
    with pytest.raises(ValueError,match='不回退'):
        candidates(flows)


def test_busy_heartbeat_expires_and_checks_live_process(monkeypatch):
    path=embeddings.publish_activity(True)
    assert embeddings.agent_busy()
    data=json.loads(path.read_text());data['at']-=20;path.write_text(json.dumps(data))
    assert not embeddings.agent_busy()


def test_machine_wide_compute_lease_is_nonblocking():
    with embeddings.compute_lock():
        with pytest.raises(embeddings.EmbeddingDeferred):
            with embeddings.compute_lock():pytest.fail('two model workers obtained lease')


def test_real_subprocess_is_terminated_when_agent_starts(tmp_path,monkeypatch):
    from proximic_ring.agent_workflow.operations import embedding_install
    monkeypatch.setattr(embedding_install,'installed',lambda:True)
    model_path=tmp_path/'model';model_path.mkdir();(model_path/'proximic-model.json').write_text('{}')
    monkeypatch.setattr(embeddings,'runtime_python',lambda:Path(sys.executable))
    monkeypatch.setattr(embeddings,'model_directory',lambda:model_path)
    stub=tmp_path/'stub.py'
    stub.write_text("import json,sys,time\nfrom pathlib import Path\np=json.loads(Path(sys.argv[1]).read_text())\nk=next(iter(p['requests']))\nPath(sys.argv[2]).write_text(json.dumps({'vectors':{k:{'vector':[1.]+[0.]*1023,'tokens':1}},'metrics':{}}))\ntime.sleep(30)\n")
    original=subprocess.Popen;processes=[]
    def spawn(args,**kw):
        args[1]=str(stub)
        assert kw['env']['HF_HUB_OFFLINE']=='1' and kw['env']['OMP_NUM_THREADS']=='2'
        p=original(args,**kw);processes.append(p);return p
    monkeypatch.setattr(embeddings.subprocess,'Popen',spawn)
    start=time.monotonic()
    result=embeddings.run_encoder({'key':'text'},tmp_path,cancelled=lambda:bool(list(tmp_path.glob('embedding-*/output.json'))))
    assert result['interrupted'] and 'key' in result['vectors']
    assert time.monotonic()-start<4 and processes[0].poll() is not None


def test_importing_recommendation_and_embedding_client_does_not_load_torch():
    code="import sys; from proximic_ring.agent_workflow.operations import operation_embeddings; from proximic_ring.agent_workflow.recommendation import operation_recommendations; assert 'torch' not in sys.modules; assert 'sentence_transformers' not in sys.modules"
    subprocess.run([sys.executable,'-c',code],check=True,timeout=5)


def test_agent_busy_change_queues_worker_flag_without_waiting_for_heartbeat():
    pytest.importorskip('PySide6')
    from types import SimpleNamespace
    from proximic_ring.ui.agent.agent_controller import AgentController
    flags=[]
    signal=SimpleNamespace(emit=lambda:None)
    agent=SimpleNamespace(busy=False,changed=signal,cardsChanged=signal)
    agent._configure_workflows=lambda:flags.append(agent.busy)
    AgentController._emit(agent)
    agent.busy=True;AgentController._emit(agent);AgentController._emit(agent)
    agent.busy=False;AgentController._emit(agent)
    assert flags==[True,False]
