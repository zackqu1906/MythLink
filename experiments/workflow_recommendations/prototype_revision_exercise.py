"""Controlled omission exercise using an existing Notes workflow.

The seed is an explicitly labelled test-only copy with recovery sections removed.
The incoming workflow remains the original dataset flow. This probes actual LLM
update/version behaviour; it is not a new user task or an accuracy measurement.
"""
import argparse
import copy
import json
import sqlite3
import time
from pathlib import Path
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_catalog import regroup,review_one
from proximic_ring.agent_workflow.operations.operation_prototypes import heads
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.operations.operation_embeddings import agent_busy
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json


def run(root,dataset,settings,cache=None):
    root=Path(root)
    if (root/'operations/catalog.sqlite3').exists():raise ValueError('需要新的独立输出目录')
    source=next(json.loads(p.read_text()) for p in sorted((Path(dataset)/'samples').glob('*.json'))
                if json.loads(p.read_text())['flow']['name'].startswith('苹果备忘录'))
    seed=copy.deepcopy(source);seed['id']='controlled-omission-seed'
    seed['flow']['fallbacks']=[]
    seed['source']=dict(source['source'],controlled_fixture='仅从原流程移除 fallback，以测试通用修订；不是另一次用户操作')
    incoming=copy.deepcopy(source);incoming['id']='original-notes-restored';incoming['source']['controlled_fixture']='原始完整 Notes 流程作为新增证据'
    store=OperationStore(root)
    try:
        if cache and Path(cache).exists():
            with sqlite3.connect(Path(cache).resolve().as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(store.directory/'embeddings.sqlite3') as b:a.backup(b)
        # Bundle original evidence so every source_file link is portable.
        import shutil
        shutil.copytree(dataset,store.directory/'dataset')
        for s in (seed,incoming):s['source']['source_file']='dataset/'+s['source']['source_file']
        store.ingest(seed);regroup(store);before=heads(store)
        store.ingest(incoming);started=time.perf_counter()
        while not agent_busy() and review_one(store,settings):
            print(json.dumps([dict(kind=j['kind'],status=j['status'],error=j['error']) for j in store.rows('operation_jobs')],ensure_ascii=False),flush=True)
        report=dict(controlled_fixture=True,description=seed['source']['controlled_fixture'],
            active_unchanged=heads(store)==before,elapsed_seconds=time.perf_counter()-started,
            admissions=store.rows('operation_admissions'),updates=store.rows('operation_updates'),
            revisions=store.rows('operation_revisions'),jobs=[dict(id=j['id'],kind=j['kind'],status=j['status'],error=j['error'],generation=json.loads(j['generation'])) for j in store.rows('operation_jobs')])
        atomic_json(root/'report.json',report);export_operations(store)
        print(json.dumps({k:v for k,v in report.items() if k not in ('admissions','revisions','updates')},ensure_ascii=False,indent=2),flush=True)
    finally:store.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--dataset',type=Path,default=Path('experiments/workflow_recommendations/dataset'))
    p.add_argument('--cache',type=Path);p.add_argument('--run-llm',action='store_true');args=p.parse_args()
    if not args.run_llm:p.error('需要 --run-llm；可能产生后台模型费用')
    from proximic_ring.agent_workflow.extraction.workflow_model import saved_settings
    run(args.output,args.dataset,saved_settings(),args.cache)
