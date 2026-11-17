#!/usr/bin/env python3
"""Isolated boundary-only experiment. Never imported by or mutates application code."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.check_llm_edit_router import configured_settings
from tools.check_automatic_edit_generation import check_output, RecordedResponse
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.edit_response import apply_edit_response
from proximic_ring.text_processing.edit_tool import EDIT_MODE_FULL
from proximic_ring.text_processing.processing_common import _text_output_budget

VERSION='boundary-only-v1-no-explicit-routing'
PROMPT_FILE=ROOT/'tools/data/boundary_only_edit_v1_system.txt'
BASELINE=ROOT/'docs/reports/manual-routed-edit-v5-eval-2026-11-11.raw.json'
MANIFEST=ROOT/'docs/reports/manual-routed-edit-v5-eval-2026-11-11.raw.inputs.json'
ENDPOINT='https://ark.cn-beijing.volces.com/api/v3/responses'
MODEL='deepseek-v4-1-flash-260910'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def prompt():return PROMPT_FILE.read_text().rstrip('\n')
def model_input(case):
    return {'utterance':case['utterance'].strip(),'editable_text':case['target'],'screen_context':case['context']}

def run_case(case,settings,opener=urlopen):
    record={**case,'calls':[],'classification_applicable':False}
    def capture(request,**kwargs):
        if record['calls']:raise RuntimeError('Second HTTP request forbidden')
        payload=json.loads(request.data)
        assert request.full_url==ENDPOINT and payload['model']==MODEL
        assert payload['input'][0]['content'][0]['text']==prompt()
        assert json.loads(payload['input'][1]['content'][0]['text'])==model_input(case)
        call={'endpoint':request.full_url,'request':payload}
        record['calls'].append(call)
        started=time.perf_counter()
        try:
            response=opener(request,**kwargs)
            call['http_status']=response.status
            return RecordedResponse(response,call,started)
        except Exception as exc:
            call.update(http_latency_s=time.perf_counter()-started,error_type=type(exc).__name__)
            raise
    processor=OpenAICompatibleTextProcessor(urlopen=capture)
    started=time.perf_counter()
    raw=''
    try:
        try:
            raw=processor._request_chat(settings,system_prompt=prompt(),user_content=json.dumps(model_input(case),ensure_ascii=False),
                temperature=0.0,max_tokens=_text_output_budget(settings),edit_tool=None,require_complete=True,preserve_whitespace=True)
        except Exception:
            # App transport rejects empty text. Accept only an actual completed empty
            # text response here, so a requested clear can be evaluated without labels.
            response=record['calls'][0].get('response',{}) if record['calls'] else {}
            chunks=[x for item in response.get('output',[]) if item.get('type')=='message'
                    for x in item.get('content',[]) if x.get('type')=='output_text']
            if response.get('status')!='completed' or not chunks or any(x.get('text')!='' for x in chunks):raise
            raw=''
        final=apply_edit_response(case['target'],{'modified_text':raw},EDIT_MODE_FULL)
        record.update(final_text=final,raw_response=raw,model_input=model_input(case),
                      checks=check_output(case['generation_expected'],final),unchanged=final==case['target'])
    except Exception as exc:
        record.update(error=str(exc),error_type=type(exc).__name__,raw_response=raw or str(getattr(exc,'model_output','')))
    finally:
        record['latency_s']=time.perf_counter()-started
        processor.close()
    return record

def frozen_cases():
    cases=json.loads(MANIFEST.read_text())['cases']
    old=json.loads(BASELINE.read_text())
    assert len(cases)==216 and len({c['id'] for c in cases})==216
    for c,b in zip(cases,old['cases'],strict=True):
        assert c['id']==b['id'] and c['input_sha256']==b['input_sha256']
        assert c['input_sha256']==hashlib.sha256(json.dumps({k:c[k] for k in ('utterance','target','context')},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        # Baseline runner appended preserve_text twice. Duplicate contains checks
        # have no scoring effect; keep the original pre-inference manifest intact.
        canonical=lambda d:{k:sorted(set(v)) if isinstance(v,list) else v for k,v in d.items()}
        assert canonical(c['generation_expected'])==canonical(b['generation_expected'])
        assert c['expected_tasks']==b['expected_tasks']
    assert not any(x in prompt() for x in ('分类','类别','模块','edit_plan','submit_text_edit'))
    return cases

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args();cases=frozen_cases()
    print(json.dumps({'cases':len(cases),'prompt_chars':len(prompt()),'version':VERSION},ensure_ascii=False),flush=True)
    if args.validate_only:return
    paths=[args.output,args.output.with_suffix('.jsonl'),args.output.with_suffix('.inputs.json')]
    if any(p.exists() for p in paths):raise ValueError('Refuse to overwrite existing run')
    settings=configured_settings();settings.validate()
    assert settings.model==MODEL
    sources=[Path(__file__),PROMPT_FILE,ROOT/'tools/check_automatic_edit_generation.py',ROOT/'tools/check_llm_edit_router.py',
             *sorted((ROOT/'src/proximic_ring/text_processing').glob('*.py'))]
    hashes={str(p.relative_to(ROOT)):sha(p) for p in sources}
    report={'created_at':datetime.now(timezone.utc).isoformat(),'model':settings.model,'prompt_version':VERSION,
        'timeout_s':settings.timeout_s,'planned_cases':len(cases),'classification_applicable':False,
        'source_sha256':hashes,'prompt':prompt(),'baseline':{'file':BASELINE.name,'sha256':sha(BASELINE)},
        'scope':'独立脚本，一次请求直接正文；同一冻结 216 条合成样例同顺序；未修改应用代码。旧六类 v5 为非同期正文对照。',
        'cases':[]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    paths[2].write_bytes(MANIFEST.read_bytes())
    report['input_manifest']={'file':paths[2].name,'sha256':sha(paths[2]),'identical_to_baseline':sha(paths[2])==sha(MANIFEST)}
    failures=0
    try:
        with paths[1].open('x') as stream:
            for i,c in enumerate(cases,1):
                result=run_case(c,settings);report['cases'].append(result)
                stream.write(json.dumps(result,ensure_ascii=False)+'\n');stream.flush()
                print(f'{i}/{len(cases)} {c["id"]} error={result.get("error_type","none")} {result["latency_s"]:.3f}s',flush=True)
                failure=bool(result.get('error')) and not any('response' in x for x in result['calls'])
                failures=failures+1 if failure else 0
                if failures>=3:raise RuntimeError('Stopped after three consecutive pre-response failures')
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        report['completed_all']=len(report['cases'])==len(cases)
        report['changed_sources']=[p for p,h in hashes.items() if sha(ROOT/p)!=h]
        report['sources_unchanged']=not report['changed_sources']
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
