"""Replay existing test workflows through real BGE-M3 and optional paid LLM review.

All output is isolated from the app's operation catalog. No desktop tasks or new
observations are executed; the original six dataset workflows remain unchanged.
"""
import argparse
import html
import json
import sqlite3
import time
from pathlib import Path
from collections import Counter
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_ingest import ingest_dataset
from proximic_ring.agent_workflow.operations.operation_catalog import regroup,review_one
from proximic_ring.agent_workflow.operations.operation_library import library
from proximic_ring.agent_workflow.operations.operation_matching import app_tags
from proximic_ring.agent_workflow.operations.operation_embeddings import agent_busy
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json,now
from proximic_ring.agent_workflow.recording.records import _page,_write


def run(output,dataset,settings,cache=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if (output/'operations/catalog.sqlite3').exists():raise ValueError('请用全新目录，避免把上次结果当作新一轮测试')
    store=OperationStore(output)
    try:
        if cache and Path(cache).exists():
            with sqlite3.connect(Path(cache).resolve().as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(store.directory/'embeddings.sqlite3') as target:
                source.backup(target)
        ingest_dataset(store,dataset)
        # Copy only the fixed test dataset for portable provenance links.
        import shutil
        shutil.copytree(dataset,store.directory/'dataset')
        with store.db:
            for s in store.rows('samples'):
                source=json.loads(s['source']);source['source_file']='dataset/'+source['source_file']
                store.db.execute('UPDATE samples SET source=? WHERE id=?',(json.dumps(source,ensure_ascii=False),s['id']))
        started=time.perf_counter();regroup(store)
        transitions=[]
        while not agent_busy():
            before=len(store.rows('operation_jobs'));tick=time.perf_counter()
            if not review_one(store,settings):break
            jobs=store.rows('operation_jobs')[before:]
            row=dict(seconds=time.perf_counter()-tick,jobs=[dict(id=j['id'],kind=j['kind'],status=j['status'],error=j['error']) for j in jobs],
                     operations=len(library(store)),members=[len(g['samples']) for g in library(store)])
            transitions.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        groups=library(store);samples=store.rows('samples')
        assignments={s['id']:g['operation_id'] for g in groups for s in g['samples']}
        labels={s['id']:tuple(app_tags(store.versions()[s['version_id']])) for s in samples}
        expected=[(a,b) for a in labels for b in labels if a<b and labels[a]==labels[b]]
        actual=[(a,b) for a in assignments for b in assignments if a<b and assignments[a]==assignments[b]]
        jobs=store.rows('operation_jobs')
        paid=[j for j in jobs if json.loads(j['generation']).get('thinking_requested')]
        report=dict(created_at=now(),dataset=str(Path(dataset).resolve()),isolated_root=str(output.resolve()),
            samples=len(samples),operations=len(groups),admitted_samples=len(assignments),
            same_operation_pairs=len(expected),recovered_pairs=sum(p in actual for p in expected),
            incorrect_pairs=[p for p in actual if p not in expected],
            llm_requests=len(paid),llm_seconds=sum(json.loads(j['generation']).get('elapsed_seconds',0) for j in paid),
            elapsed_seconds=time.perf_counter()-started,
            revision_statuses=dict(Counter(r['status'] for r in store.rows('operation_revisions'))),
            admission_statuses=dict(Counter(r['status'] for r in store.rows('operation_admissions'))),
            updates=store.rows('operation_updates'),transitions=transitions,
            failures=[dict(id=j['id'],error=j['error']) for j in jobs if j['status']=='failed'],
            note='仅六条历史测试流程的小样本回放；不代表真实用户准确率。未执行电脑操作，候选没有自动启用。')
        atomic_json(output/'report.json',report);export_operations(store)
        body='<p>独立数据目录：'+html.escape(str(output.resolve()))+'</p><p><a href="operations/index.html">查看归纳出的通用操作、候选修订及来源</a> · <a href="report.json">完整测量</a></p><pre>'+html.escape(json.dumps(report,ensure_ascii=False,indent=2))+'</pre>'
        _write(output/'index.html',_page('通用 Workflow 增量入组回放',body))
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        return report
    finally:store.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--dataset',type=Path,default=Path('experiments/workflow_recommendations/dataset'))
    p.add_argument('--cache',type=Path)
    p.add_argument('--run-llm',action='store_true',help='明确使用现有文本模型，可能计费')
    a=p.parse_args()
    if not a.run_llm:p.error('真实回放需要 --run-llm；离线规则测试请运行 tests/test_operation_prototypes.py')
    from proximic_ring.agent_workflow.extraction.workflow_model import saved_settings
    run(a.output,a.dataset,saved_settings(),a.cache)
