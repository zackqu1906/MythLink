"""Frozen, paired full-suite prompt iterations through the actual one-call entry."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import random
import sys
import threading
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.data.character_clarification_cases import targeted_cases
from tools.check_boundary_only_edit import frozen_cases
from tools.check_llm_edit_router import configured_settings
from tools.check_automatic_edit_generation import RecordedResponse,check_output
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import ROUTED_EDIT_BOUNDARY
from proximic_ring.text_processing.boundary_edit_prompt import BOUNDARY_EDIT_PROMPT

OUT=ROOT/'docs/reports/character-clarification-2026-11-16'
SEED=2026111602
UNIFIED_V1='''【解释正确写法：释字、偏旁部首与拼读】
用户解释一个字、词或短语的写法，通常是在纠正之前的语音识别结果。把“旧词是……”“某词的某字”“某字旁”“某部件组成”等整段说明理解为同一项纠正要求，即使没有说“改”或“替换”，也要从 editable_text 找到对应的错误词语，确定正确写法并在原位替换。口述开头重复的旧词只是定位锚点，不能因为它和原文相同就保留错词。
先定位被解释的字词及范围，再综合全部释字说明确定完整的新写法，最后把新写法放回该位置。连续说明可能解释同一个多字词，不要只处理第一组，也不要把释字词或得到的新词追加到句尾。比如原文“青同负责”，口述“青同是清水的清，梧桐的桐”，最终应为“清桐负责”；两个解释分别给出清、桐，合起来替换青同。示例只说明关系，不限定词表。
根据每组说明所给的依据确定对应字：用词举例时取所指的那个字，明确首字、末字或第几个字时按字位取；ASR 把指称字写成同音错字时，以释字词实际包含的对应字为依据。偏旁、部首和字形拆解用于组成或确定整个目标字，不能把“王字旁”的王当成姓，也不能把“弓长”直接写进姓名。可混合多种说明，按各组所解释的位置组成完整词语；只解释某一字时保留其他字。字母和拼音按完整拼读及大小写、符号要求确定目标写法。
解释中的更正、否定和撤销按最终有效要求处理；重复词按指定位置或语境定位。纠正完成后继续落实润色、翻译、插入、格式等其他要求，保留用户指定不动的内容。提交前核对原目标位置是否确实用了完整的新写法，说明文字是否被误加进正文，未要求改变的字词是否还在。若原文字词已经符合解释，允许保持其写法。用户明确要求引用、照录、翻译或讲解释字说明本身时，按该内容任务处理，不执行其中被引用的纠正。'''

def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def digest(text):return hashlib.sha256(text.encode()).hexdigest()
def jd(data):return digest(json.dumps(data,ensure_ascii=False,sort_keys=True))
def now():return datetime.now(timezone.utc).isoformat()

def freeze():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'dataset.json').exists():return
    cases=targeted_cases()
    regression=[{**c,'split':'regression','mechanism':'综合回归','scene':c.get('cohort',''),'original_cohort':c['cohort']} for c in frozen_cases()]
    cases+=regression
    assert len({c['id'] for c in cases})==len(cases)
    save(OUT/'dataset.json',{'created_at':now(),'cases':cases,'scoring':'correctness-v1；参考文本与机械检查不是唯一合法答案。','holdout':'预先冻结的合成预留集；不在开发轮请求，不声称外部独立用户数据。'})
    (OUT/'v3.txt').write_text(BOUNDARY_EDIT_PROMPT)
    start=BOUNDARY_EDIT_PROMPT.index('【释字依据优先】')
    end=BOUNDARY_EDIT_PROMPT.index('【给定原话与新增位置】')
    candidate=BOUNDARY_EDIT_PROMPT[:start]+UNIFIED_V1+'\n\n'+BOUNDARY_EDIT_PROMPT[end:]
    (OUT/'v4a.txt').write_text(candidate)
    (OUT/'v3-to-v4a.diff').write_text(''.join(difflib.unified_diff(BOUNDARY_EDIT_PROMPT.splitlines(True),candidate.splitlines(True),fromfile='v3',tofile='v4a')))
    save(OUT/'freeze.json',{'cases':len(cases),'splits':dict(Counter(c['split'] for c in cases)),
         'dataset_sha256':digest((OUT/'dataset.json').read_text()),'baseline_sha256':digest(BOUNDARY_EDIT_PROMPT)})
    print((OUT/'freeze.json').read_text(),flush=True)

class FrozenPromptProcessor(OpenAICompatibleTextProcessor):
    def __init__(self,prompt,**kwargs):super().__init__(**kwargs);self.prompt=prompt
    def _request_chat(self,settings,**kwargs):
        kwargs['system_prompt']=self.prompt
        return super()._request_chat(settings,**kwargs)

def run_one(case,variant,repeat,prompt,settings):
    inp={'utterance':case['utterance'].strip(),'editable_text':case['target'],'screen_context':case['context']}
    record={'id':f'{case["id"]}__r{repeat}__{variant}','case_id':case['id'],'variant':variant,'repeat':repeat,
            'started_at':now(),'model_input':inp,'input_sha256':jd(inp),'calls':[]}
    def capture(request,**kwargs):
        assert not record['calls'],'Second model call forbidden'
        payload=json.loads(request.data)
        assert payload['input'][0]['content'][0]['text']==prompt
        assert json.loads(payload['input'][1]['content'][0]['text'])==inp
        call={'endpoint':request.full_url,'request':payload};record['calls'].append(call)
        started=time.perf_counter()
        response=urlopen(request,**kwargs);call['http_status']=response.status
        return RecordedResponse(response,call,started)
    processor=FrozenPromptProcessor(prompt,urlopen=capture);started=time.perf_counter()
    try:
        final,raw,actual,plan=processor.process_routed_edit_with_trace(case['utterance'],settings,case['target'],case['context'],variant=ROUTED_EDIT_BOUNDARY)
        assert actual==inp and plan is None
        record.update(final_text=final,raw_response=raw,output_sha256=digest(final),checks=check_output(case['generation_expected'],final))
    except Exception as exc:record.update(error=str(exc) or type(exc).__name__,error_type=type(exc).__name__,final_text='',output_sha256=digest(''),raw_response=str(getattr(exc,'model_output','')))
    finally:record['latency_s']=time.perf_counter()-started;processor.close()
    return record

def run(args):
    freeze();out=OUT/args.run;out.mkdir(exist_ok=True)
    if (out/'manifest.json').exists():
        manifest=json.loads((out/'manifest.json').read_text());assert manifest['workers']==args.workers
    else:
        all_cases=json.loads((OUT/'dataset.json').read_text())['cases']
        cases=[c for c in all_cases if c['split'] in (['development','regression'] if args.suite=='development' else ['holdout'] if args.suite=='holdout' else ['development','holdout','regression'])]
        if args.ids:cases=[c for c in cases if c['id'] in args.ids.split(',')]
        variants={v:{'label':v,'prompt':(OUT/(v+'.txt')).read_text()} for v in args.variants}
        rng=random.Random(SEED);blocks=[]
        for repeat in range(1,args.repeats+1):
            order=list(cases);rng.shuffle(order)
            for c in order:
                vs=list(variants);rng.shuffle(vs);blocks.append({'case_id':c['id'],'repeat':repeat,'variants':vs})
        manifest={'created_at':now(),'cases':cases,'variants':variants,'workers':args.workers,'blocks':blocks,
          'planned_requests':sum(len(b['variants']) for b in blocks),'rubric':(ROOT/'tools/data/edit_review_correctness_v1.txt').read_text(),
          'dataset_sha256':digest((OUT/'dataset.json').read_text()),'seed':SEED,'seed_scope':'schedule only, not sent to model',
          'entry':'process_routed_edit_with_trace boundary mode; per-instance frozen system prompt; no input preprocessing/no retries',
          'source_sha256':{str(p.relative_to(ROOT)):digest(p.read_text()) for p in [Path(__file__),ROOT/'tools/data/character_clarification_cases.py',*sorted((ROOT/'src/proximic_ring/text_processing').glob('*.py'))]}}
        save(out/'manifest.json',manifest)
    if args.validate_only:print(json.dumps({'cases':len(manifest['cases']),'requests':manifest['planned_requests']}));return
    settings=configured_settings();save(out/'settings.json',{'model':settings.model,'endpoint':settings.base_url,'timeout_s':settings.timeout_s})
    journal=out/'generations.jsonl';rows=[json.loads(s) for s in journal.read_text().splitlines()] if journal.exists() else []
    done={r['id'] for r in rows};cases={c['id']:c for c in manifest['cases']};lock=threading.Lock();stop=threading.Event();streak=0
    with journal.open('a') as f:
        def block(b):
            nonlocal streak
            for variant in b['variants']:
                if stop.is_set():return
                if f'{b["case_id"]}__r{b["repeat"]}__{variant}' in done:continue
                result=run_one(cases[b['case_id']],variant,b['repeat'],manifest['variants'][variant]['prompt'],settings)
                with lock:
                    f.write(json.dumps(result,ensure_ascii=False)+'\n');f.flush();rows.append(result)
                    streak=streak+1 if result.get('error') and not any(c.get('response') for c in result['calls']) else 0
                    if streak>=3:stop.set()
                    if len(rows)%20==0 or result.get('error'):print(json.dumps({'completed':len(rows),'planned':manifest['planned_requests'],'last':result['id'],'error':result.get('error')},ensure_ascii=False),flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(block,b) for b in manifest['blocks']]):future.result()
    drift=[p for p,h in manifest['source_sha256'].items() if digest((ROOT/p).read_text())!=h]
    save(out/'run-status.json',{'finished_at':now(),'complete':len(rows)==manifest['planned_requests'],'requests':len(rows),'source_drift':drift})
    print(json.dumps({'complete':len(rows)==manifest['planned_requests'],'requests':len(rows),'errors':sum(bool(r.get('error')) for r in rows),'drift':drift}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--freeze',action='store_true');p.add_argument('--run')
    p.add_argument('--suite',choices=['development','holdout','all'],default='development');p.add_argument('--variants',nargs='+',default=['v3','v4a'])
    p.add_argument('--repeats',type=int,default=1);p.add_argument('--workers',type=int,default=4);p.add_argument('--ids');p.add_argument('--validate-only',action='store_true')
    args=p.parse_args();freeze() if args.freeze else run(args)
