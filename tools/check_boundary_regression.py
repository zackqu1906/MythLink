#!/usr/bin/env python3
"""Repeat the frozen 216-case suite through the current production boundary prompt."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.check_boundary_only_edit import frozen_cases,MANIFEST,model_input
from tools.check_edit_instruction_ablation import run_step,configured_settings,sha,MODEL,ENDPOINT,PROMPT_SHA,BOUNDARY_EDIT_PROMPT,BOUNDARY_EDIT_PROMPT_VERSION
from tools.check_automatic_edit_generation import check_output
BASELINE=ROOT/'docs/reports/boundary-only-edit-v1-eval-2026-11-11.raw.json'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--validate-only',action='store_true')
    a=p.parse_args();cases=frozen_cases()
    print(json.dumps({'cases':len(cases),'requests':len(cases),'version':BOUNDARY_EDIT_PROMPT_VERSION,'prompt_sha256':PROMPT_SHA}),flush=True)
    if a.validate_only:return
    paths=[a.output,a.output.with_suffix('.jsonl'),a.output.with_suffix('.inputs.json')]
    if any(p.exists() for p in paths):raise ValueError('Refuse to overwrite existing run')
    settings=configured_settings();settings.validate();assert settings.model==MODEL
    sources=[Path(__file__),ROOT/'tools/check_edit_instruction_ablation.py',ROOT/'tools/check_boundary_only_edit.py',ROOT/'tools/check_automatic_edit_generation.py',ROOT/'tools/check_llm_edit_router.py',*sorted((ROOT/'src/proximic_ring/text_processing').glob('*.py'))]
    hashes={str(p.relative_to(ROOT)):sha(p) for p in sources}
    a.output.parent.mkdir(parents=True,exist_ok=True);paths[2].write_bytes(MANIFEST.read_bytes())
    report={'created_at':datetime.now(timezone.utc).isoformat(),'model':MODEL,'endpoint':ENDPOINT,'prompt_version':BOUNDARY_EDIT_PROMPT_VERSION,
        'prompt':BOUNDARY_EDIT_PROMPT,'prompt_sha256':PROMPT_SHA,'entry':'OpenAICompatibleTextProcessor.process_routed_edit_with_trace(variant=ROUTED_EDIT_BOUNDARY)',
        'input_manifest':{'file':paths[2].name,'sha256':sha(paths[2])},'source_sha256':hashes,'timeout_s':settings.timeout_s,
        'baseline':{'file':BASELINE.name,'sha256':sha(BASELINE)},'planned_cases':len(cases),'cases':[]}
    consecutive_errors=0
    try:
        with paths[1].open('x') as journal:
            for i,c in enumerate(cases,1):
                s=run_step(c['utterance'],c['target'],settings,context=c['context'])
                result={**c,**{k:v for k,v in s.items() if k not in ('instruction','source_text','input_sha256')},'wire_input_sha256':s['input_sha256'],'model_input':model_input(c)}
                if not result.get('error'):result['checks']=check_output(c['generation_expected'],result['final_text'])
                report['cases'].append(result);journal.write(json.dumps(result,ensure_ascii=False)+'\n');journal.flush()
                print(f'{i}/{len(cases)} {c["id"]} error={bool(result.get("error"))} {result["latency_s"]:.3f}s',flush=True)
                consecutive_errors=consecutive_errors+1 if result.get('error') else 0
                if consecutive_errors>=3:raise RuntimeError('Stopped after three consecutive failed requests')
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat();report['completed_all']=len(report['cases'])==len(cases)
        report['changed_sources']=[p for p,h in hashes.items() if sha(ROOT/p)!=h]
        a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
