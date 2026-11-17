"""Ten frozen editing examples, one serial request per model and example."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import random
import resource
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.chatgpt_stateless_text_client import request_text
from tools.character_clarification_experiment import run_one, digest, jd, save, now
from tools.check_llm_edit_router import configured_settings

OUT = ROOT/'docs/reports/fast-gpt-latency-10-2026-11-16'
SOURCE = ROOT/'docs/reports/astra-v4d-model-comparison-2026-11-16'
MODELS = {'luna_none':'gpt-6-luna','terra_none':'gpt-6.1-terra','deepseek':'deepseek-v4-1-flash-260910'}
IDS = ['clarify-dev-001','clarify-dev-026','clarify-dev-076','clarify-dev-018',
       'precise-second-occurrence','fresh-polish-tone','six-polish-format',
       'v5-combo-modify-translate-number','fresh-translate-part','clarify-hol-040']


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--max-new',type=int,default=0)
    ap.add_argument('--continue-connection-errors',action='store_true'); args=ap.parse_args()
    OUT.mkdir(exist_ok=True)
    source = json.loads((SOURCE/'dataset.json').read_text())['cases']
    cases = [next(c for c in source if c['id']==cid) for cid in IDS]
    prompt = (SOURCE/'v4d.txt').read_text()
    path = OUT/'manifest.json'
    if path.exists():
        manifest = json.loads(path.read_text())
        assert manifest['prompt_sha256']==digest(prompt) and manifest['cases']==cases
    else:
        rng = random.Random(20261116); jobs=[]
        for c in cases:
            variants=list(MODELS); rng.shuffle(variants)
            jobs += [{'case_id':c['id'],'variant':v} for v in variants]
        manifest={'created_at':now(),'cases':cases,'models':MODELS,'jobs':jobs,'prompt_sha256':digest(prompt),
                  'requests':30,'repeats':1,'workers':1,'selection':'10 purposive examples, short voice-edit outputs, not a random accuracy estimate',
                  'controls':['same exact v4d','same user JSON','one request per case per model','no tools or agent context in GPT request body',
                              'no conversation or previous_response_id','randomized arm order within each case','no answer-based retries'],
                  'timing':'client invocation to completed full text; GPT first text separately; no ASR/OCR/writeback',
                  'limits':{'response_bytes':2097152,'memory_stop_mib':256},
                  'limitations':['ChatGPT subscription backend versus Volcengine backend','GPT streaming versus DeepSeek nonstreaming',
                                 'no fixed output cap available for ChatGPT route','cache/service load not controlled','only 10 selected cases']}
        save(path,manifest);(OUT/'v4d.txt').write_text(prompt)
    by_id={c['id']:c for c in cases}; journal=OUT/'generations.jsonl'
    done={json.loads(l)['id'] for l in journal.open()} if journal.exists() else set()
    settings=configured_settings(); assert settings.model==MODELS['deepseek']
    new=0
    with journal.open('a') as f:
        for job in manifest['jobs']:
            cid,v=job['case_id'],job['variant']; rid=cid+'__'+v
            if rid in done:continue
            c=by_id[cid]
            inp={'utterance':c['utterance'].strip(),'editable_text':c['target'],'screen_context':c['context']}
            if v=='deepseek':
                r=run_one(c,v,1,prompt,settings)
            else:
                r={'started_at':now(),**request_text(prompt,json.dumps(inp,ensure_ascii=False),MODELS[v])}
                r.update(output_sha256=digest(r['final_text']),model_input=inp,input_sha256=jd(inp))
            r.update(id=rid,case_id=cid,variant=v,peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1048576)
            f.write(json.dumps(r,ensure_ascii=False)+'\n');f.flush();done.add(rid);new+=1
            print(json.dumps({'saved':len(done),'id':rid,'seconds':round(r['latency_s'],3),
                              'first_text_s':r.get('first_text_s'),'rss_mib':round(r['peak_rss_mib'],1),'error':r.get('error')},ensure_ascii=False),flush=True)
            stop_error = r.get('error') and not (args.continue_connection_errors and r.get('http_status') is None
                                                 and 'closed connection' in r['error'])
            if stop_error or r['peak_rss_mib']>256 or (args.max_new and new>=args.max_new):break
    save(OUT/'run-status.json',{'saved':len(done),'planned':30,'complete':len(done)==30,'finished_at':now()})


if __name__=='__main__':main()
