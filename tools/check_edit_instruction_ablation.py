#!/usr/bin/env python3
"""Frozen repeated instruction ablation through the production boundary-only path."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.check_llm_edit_router import configured_settings
from tools.check_automatic_edit_generation import RecordedResponse
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import ROUTED_EDIT_BOUNDARY
from proximic_ring.text_processing.boundary_edit_prompt import BOUNDARY_EDIT_PROMPT, BOUNDARY_EDIT_PROMPT_VERSION

FIXTURE=ROOT/'tools/data/edit_instruction_ablation_v1.json'
ENDPOINT='https://ark.cn-beijing.volces.com/api/v3/responses'
MODEL='deepseek-v4-1-flash-260910'
# Freeze the current production prompt for this run. Historical reports retain
# their own prompt snapshots and hashes; do not label a new run as the old v1.
PROMPT_SHA=hashlib.sha256(BOUNDARY_EDIT_PROMPT.encode()).hexdigest()

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def input_digest(value):return digest(json.dumps(value,ensure_ascii=False,sort_keys=True))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def load_trials():
    data=json.loads(FIXTURE.read_text())
    assert digest(BOUNDARY_EDIT_PROMPT)==PROMPT_SHA
    assert len(data['cases'])==28 and len({c['id'] for c in data['cases']})==28
    assert sum(len(c['steps']) for c in data['cases'])*data['repeats']==99
    for c in data['cases']:
        assert c['context']=={} and c['source_text'] and c['steps']
        assert all(s['instruction'] and s['criteria'] for s in c['steps'])
    rng=random.Random(data['shuffle_seed']);trials=[]
    for repeat in range(1,data['repeats']+1):
        block=list(data['cases']);rng.shuffle(block)
        trials.extend({**c,'condition_id':c['id'],'id':f'{c["id"]}-r{repeat}','repeat':repeat} for c in block)
    return data,trials

def run_step(instruction,source,settings,opener=urlopen,*,context=None):
    expected={'utterance':instruction.strip(),'editable_text':source,'screen_context':context if context is not None else {}}
    record={'instruction':instruction,'source_text':source,'input_sha256':input_digest(expected),'calls':[]}
    def capture(request,**kwargs):
        if record['calls']:raise RuntimeError('More than one request in a step')
        payload=json.loads(request.data)
        assert request.full_url==ENDPOINT and payload['model']==MODEL
        assert payload['input'][0]['content'][0]['text']==BOUNDARY_EDIT_PROMPT
        assert json.loads(payload['input'][1]['content'][0]['text'])==expected
        call={'endpoint':request.full_url,'request':payload};record['calls'].append(call)
        started=time.perf_counter()
        try:
            response=opener(request,**kwargs);call['http_status']=response.status
            return RecordedResponse(response,call,started)
        except Exception as exc:
            call.update(http_latency_s=time.perf_counter()-started,error_type=type(exc).__name__)
            raise
    processor=OpenAICompatibleTextProcessor(urlopen=capture);started=time.perf_counter()
    try:
        body,raw,model_input,plan=processor.process_routed_edit_with_trace(instruction,settings,source,expected['screen_context'],variant=ROUTED_EDIT_BOUNDARY)
        assert plan is None and model_input==expected and len(record['calls'])==1
        record.update(final_text=body,raw_response=raw,output_sha256=digest(body))
    except Exception as exc:
        record.update(error=str(exc) or type(exc).__name__,error_type=type(exc).__name__,raw_response=str(getattr(exc,'model_output','')))
    finally:
        record['latency_s']=time.perf_counter()-started;processor.close()
    return record

def run_trial(trial,settings,opener=urlopen):
    record={**trial,'results':[]};source=trial['source_text'];started=time.perf_counter()
    for step in trial['steps']:
        result=run_step(step['instruction'],source,settings,opener)
        result['criteria']=step['criteria'];record['results'].append(result)
        if result.get('error'):
            record['error']=result['error'];break
        source=result['final_text']
    record.update(final_text=source if not record.get('error') else '',latency_s=time.perf_counter()-started)
    return record

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args();data,trials=load_trials()
    print(json.dumps({'conditions':len(data['cases']),'trials':len(trials),'requests':99,'prompt_sha256':PROMPT_SHA}),flush=True)
    if args.validate_only:return
    paths=[args.output,args.output.with_suffix('.jsonl'),args.output.with_suffix('.inputs.json')]
    if any(p.exists() for p in paths):raise ValueError('Refuse to overwrite an existing run')
    settings=configured_settings();settings.validate();assert settings.model==MODEL
    sources=[Path(__file__),FIXTURE,ROOT/'tools/check_automatic_edit_generation.py',ROOT/'tools/check_llm_edit_router.py',*sorted((ROOT/'src/proximic_ring/text_processing').glob('*.py'))]
    hashes={str(p.relative_to(ROOT)):sha(p) for p in sources}
    manifest={**data,'trial_order':[c['id'] for c in trials],'system_prompt':BOUNDARY_EDIT_PROMPT,'prompt_sha256':PROMPT_SHA}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    paths[2].write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    report={'created_at':datetime.now(timezone.utc).isoformat(),'model':MODEL,'endpoint':ENDPOINT,'prompt_version':BOUNDARY_EDIT_PROMPT_VERSION,
        'prompt':BOUNDARY_EDIT_PROMPT,'prompt_sha256':PROMPT_SHA,'entry':'OpenAICompatibleTextProcessor.process_routed_edit_with_trace(variant=ROUTED_EDIT_BOUNDARY)',
        'input_manifest':{'file':paths[2].name,'sha256':sha(paths[2])},'source_sha256':hashes,'timeout_s':settings.timeout_s,
        'shuffle_seed':data['shuffle_seed'],'seed_scope':'Only trial ordering; no model seed sent','planned_trials':len(trials),'planned_requests':99,'cases':[]}
    consecutive_errors=0
    try:
        with paths[1].open('x') as journal:
            for i,trial in enumerate(trials,1):
                result=run_trial(trial,settings);report['cases'].append(result)
                journal.write(json.dumps(result,ensure_ascii=False)+'\n');journal.flush()
                print(f'{i}/{len(trials)} {trial["id"]} steps={len(result["results"])} error={bool(result.get("error"))} {result["latency_s"]:.3f}s',flush=True)
                consecutive_errors=consecutive_errors+1 if result.get('error') else 0
                if consecutive_errors>=3:raise RuntimeError('Stopped after three consecutive failed trials')
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        report['completed_all']=len(report['cases'])==len(trials)
        report['changed_sources']=[p for p,h in hashes.items() if sha(ROOT/p)!=h]
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
