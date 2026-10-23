"""Real configured-model replay of recorded tasks, isolated from the live library.

Prepare/extract/review/enrich/recommend are restartable phases. Candidate activation
requires a separately reviewed merge plan; this runner never silently enables it.
No historical desktop actions are replayed. Credentials stay in saved settings.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from collections import Counter

from proximic_ring.agent_workflow.extraction.workflow_model import saved_settings, configuration_error
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore, atomic_json, now
from proximic_ring.agent_workflow.extraction.workflows import task_list, source_packet, process_one
from proximic_ring.agent_workflow.extraction.workflow_view import export_workflows
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_catalog import sync_catalog
from proximic_ring.agent_workflow.operations.operation_admission import process_admission
from proximic_ring.agent_workflow.operations.operation_update import process_update
from proximic_ring.agent_workflow.operations.operation_display_jobs import process_display
from proximic_ring.agent_workflow.parameters.parameter_jobs import process_parameters
from proximic_ring.agent_workflow.operations.operation_prototypes import heads
from proximic_ring.agent_workflow.operations.operation_embeddings import agent_busy
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.operations.operation_library import library
from proximic_ring.agent_workflow.recording.records import export_html, _page
from proximic_ring.agent_workflow.reporting.overview import export_overview
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations


def say(**value):print(json.dumps(value,ensure_ascii=False),flush=True)


def source_hashes(source):
    return {str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((source/'sessions').rglob('*')) if p.is_file() and p.name in ('events.jsonl','session.json','index.sqlite3')}


def prepare(source,root):
    if root.exists():raise ValueError('prepare requires a fresh output directory')
    root.mkdir(parents=True)
    tasks=sorted(task_list(source),key=lambda t:t['created_at'])
    manifest=dict(created_at=now(),source=str(source.resolve()),root=str(root.resolve()),tasks=tasks,
        source_hashes=source_hashes(source),models={},scope='isolated replay of existing task records')
    # Copy immutable evidence; back up each SQLite through its read-only connection.
    shutil.copytree(source/'sessions',root/'sessions',ignore=shutil.ignore_patterns('*.sqlite3','*.sqlite3-wal','*.sqlite3-shm'))
    for sid in {t['session_id'] for t in tasks}:
        with sqlite3.connect((source/'sessions'/sid/'index.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(root/'sessions'/sid/'index.sqlite3') as b:a.backup(b)
    store=WorkflowStore(root)
    try:
        for t in tasks:store.enqueue(source_packet(root,t['session_id'],t['id']))
        store.status();export_workflows(store)
    finally:store.close()
    # Exact-text cache entries only: newly rendered workflows still require real encoding.
    cache=source/'operations/embeddings.sqlite3'
    if cache.exists():
        (root/'operations').mkdir(exist_ok=True)
        with sqlite3.connect(cache.resolve().as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(root/'operations/embeddings.sqlite3') as b:a.backup(b)
    atomic_json(root/'manifest.json',manifest)
    export_html(root)
    say(phase='prepared',tasks=len(tasks),statuses=dict(Counter(t['status'] for t in tasks)),root=str(root))


def run_extract(root,settings):
    store=WorkflowStore(root)
    try:
        while any(j['status']=='queued' for j in store.jobs()):
            if agent_busy():say(phase='paused',reason='Agent busy');break
            queued=[j for j in reversed(store.jobs()) if j['status']=='queued']
            say(phase='extract_start',episode=queued[0]['episode_id'],goal=queued[0]['goal'],retry=queued[0]['retry_number'])
            start=time.perf_counter()
            if not process_one(store,settings):
                delay=store.retry_delay()
                if delay is None:break
                time.sleep(min(delay+.05,10));continue
            finished=[j for j in store.jobs() if j['status'] in ('completed','failed')]
            j=max(finished,key=lambda j:j['updated_at'])
            say(phase='extract_end',episode=j['episode_id'],status=j['status'],error=j['error'],seconds=round(time.perf_counter()-start,2))
        export_workflows(store)
    finally:store.close()


def run_review(root,settings,enrich=False):
    warnings=sync_catalog(root)
    if warnings:say(phase='import_warnings',warnings=warnings)
    store=OperationStore(root)
    try:
        while not agent_busy():
            before={j['id'] for j in store.rows('operation_jobs')}
            stages=(process_display,process_parameters) if enrich else (process_admission,process_update)
            worked=False
            for stage in stages:
                say(phase='stage_start',stage=stage.__name__)
                if stage(store,settings):worked=True;break
            for j in store.rows('operation_jobs'):
                if j['id'] not in before:
                    g=json.loads(j['generation']);say(phase='job_end',kind=j['kind'],id=j['id'],status=j['status'],error=j['error'],seconds=g.get('elapsed_seconds'),usage=g.get('usage'))
            export_operations(store)
            if not worked:break
    finally:store.close()


def recommend(root):
    store=OperationStore(root)
    try:
        # user scope uses only original observed contexts. No replay counts are added.
        result=request_recommendations(store,{},scope='user',trigger='cli',surface='cli')
        atomic_json(root/'recommendation-global.json',result)
        contexts={}
        for s in store.rows('samples'):
            c=json.loads(s['context'])
            bundle=c.get('before_agent_focus',{}).get('bundle')
            if bundle:contexts[bundle]=c
        # Historical what-if scenarios are explicitly marked; not current focus observations.
        from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend as rank
        previews=[]
        preview_store=OperationStore(root/'scenario-preview')
        store.db.backup(preview_store.db)
        with preview_store.db:preview_store.db.execute("UPDATE samples SET kind='test'")
        for bundle,c in contexts.items():
            tm=c.get('time_context',{})
            manual=dict(source='manual_demo',app=bundle,local_hour=tm.get('local_hour',12),weekday=tm.get('weekday',1))
            preview=rank(preview_store,manual,scope='test',limit=4)
            previews.append(dict(label='isolated hypothetical context',result=preview))
        preview_store.close()
        atomic_json(root/'recommendation-scenarios.json',previews)
        say(phase='recommendations',items=[dict(name=i['name'],score=i['score'],usage_count=i.get('usage_count')) for i in result['items']])
    finally:store.close()


def retry_parameters(root,settings):
    """One explicitly requested retry per failed current sample; preserve failures."""
    from proximic_ring.agent_workflow.parameters.parameter_history import current_records
    store=OperationStore(root)
    try:
        failed=[r['sample_id'] for r in current_records(store,scope='user') if r['status']=='failed']
        for sample_id in failed:
            before={j['id'] for j in store.rows('operation_jobs')}
            if not process_parameters(store,settings,retry=True,sample_id=sample_id):break
            for j in store.rows('operation_jobs'):
                if j['id'] not in before:
                    g=json.loads(j['generation'])
                    say(phase='explicit_parameter_retry',sample_id=sample_id,id=j['id'],
                        status=j['status'],error=j['error'],seconds=g.get('elapsed_seconds'),usage=g.get('usage'))
        export_operations(store)
    finally:store.close()


def report(root,source):
    extractions=[];requests=[]
    ws=WorkflowStore(root)
    try:
        for j in ws.jobs():
            row={k:j[k] for k in ('id','episode_id','goal','status','error','retry_number')}
            if j['result_file']:
                a=json.loads((ws.directory/j['result_file']).read_text());g=a['generation']
                row.update(workflows=a['workflows'],notes=a.get('notes',[]),generation=g)
                requests.append(dict(kind='extract',id=j['id'],**g))
            else:
                p=ws.directory/(j['id']+'.rejected.json')
                if p.exists():
                    g=json.loads(p.read_text()).get('generation') or {}
                    if g:requests.append(dict(kind='extract',id=j['id'],**g))
            extractions.append(row)
    finally:ws.close()
    store=OperationStore(root)
    try:
        jobs=store.rows('operation_jobs')
        for j in jobs:
            g=json.loads(j['generation'])
            if g.get('thinking_requested'):requests.append(dict(kind=j['kind'],id=j['id'],**g))
        totals={k:sum(r.get('usage',{}).get(k,0) for r in requests) for k in ('input_tokens','output_tokens','total_tokens')}
        result=dict(created_at=now(),root=str(root.resolve()),source_unchanged=source_hashes(source)==json.loads((root/'manifest.json').read_text())['source_hashes'],
            extractions=extractions,groups=library(store),admissions=store.rows('operation_admissions'),updates=store.rows('operation_updates'),
            jobs=[dict(j,input=json.loads(j['input']),result=json.loads(j['result']),generation=json.loads(j['generation'])) for j in jobs],
            requests=requests,totals=totals,llm_calls=len(requests),llm_seconds=sum(r.get('elapsed_seconds',0) for r in requests),
            merges=store.rows('operation_merges'),parameters=store.rows('operation_parameter_history'))
        atomic_json(root/'measurements.json',result);export_operations(store)
    finally:store.close()
    export_overview(root)
    say(phase='report',llm_calls=len(requests),totals=totals,source_unchanged=result['source_unchanged'],groups=len(result['groups']))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['prepare','extract','review','enrich','retry-parameters','recommend','report'])
    p.add_argument('--source',type=Path,default=Path('agent-experience'))
    p.add_argument('--root',type=Path,required=True)
    a=p.parse_args();a.root=a.root.resolve();a.source=a.source.resolve()
    if a.root==a.source:raise ValueError('Replay output must differ from live source')
    if a.phase=='prepare':prepare(a.source,a.root)
    elif a.phase in ('extract','review','enrich','retry-parameters'):
        settings=saved_settings();problem=configuration_error(settings)
        if problem:raise ValueError(problem)
        say(phase='configuration',provider=settings.provider,model=settings.model)
        if a.phase=='extract':run_extract(a.root,settings)
        elif a.phase=='retry-parameters':retry_parameters(a.root,settings)
        else:run_review(a.root,settings,enrich=a.phase=='enrich')
    elif a.phase=='recommend':recommend(a.root)
    elif a.phase=='report':report(a.root,a.source)
