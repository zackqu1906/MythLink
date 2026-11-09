#!/usr/bin/env python3
"""Small live smoke through production dispatch; synthetic input, no writeback."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from tools.check_llm_edit_router import configured_settings, RecordedResponse
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import TextProcessingRequest, INPUT_ROUTING_ROUTED_EDIT
from proximic_ring.text_processing.processing_dispatch import process_text_request
from proximic_ring.text_processing.routed_edit_prompts import ROUTED_EDIT_PROMPT_VERSION


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Do not overwrite previous results')
    settings=configured_settings()
    examples=[
        ('修改与插入','把周六改成周日，末尾加原话：请准时。','周六见面。',{},['modify','insert']),
        ('空框创作','写一句通知：周五下午三点开会，不要补充其他信息。','',{},['compose']),
        ('只读OCR','根据上文纠正人名，其他不动','请联系张玮。',{'messages':[{'speaker':'other','text':'联系人是张伟。'}]},['modify']),
        ('取消全部','不用改了，保持原样','请周五交材料。',{},[]),
    ]
    report={'scope':'正式 App 文本分派与单次请求实测；合成输入，无截图与实际写回',
            'model':settings.model,'prompt_version':ROUTED_EDIT_PROMPT_VERSION,'cases':[]}
    for i,(name,speech,target,context,expected) in enumerate(examples,1):
        calls=[]
        def capture(request,**kwargs):
            if calls:raise AssertionError('Second request forbidden')
            call={'endpoint':request.full_url,'request':json.loads(request.data)};calls.append(call)
            start=time.perf_counter();response=urlopen(request,**kwargs)
            call['http_status']=response.status
            return RecordedResponse(response,call,start)
        processor=OpenAICompatibleTextProcessor(urlopen=capture)
        request=TextProcessingRequest(i,i,'edit',speech,settings,target,routing_mode=INPUT_ROUTING_ROUTED_EDIT,screen_context=context)
        row={'name':name,'utterance':speech,'target':target,'expected_tasks':expected,'calls':calls}
        started=time.perf_counter()
        try:
            result=process_text_request(processor,request,on_trace=lambda *a:None)
            row.update(result=asdict(result),route_exact=set(result.edit_plan.tasks if result.edit_plan else [])==set(expected))
        except Exception as exc:row.update(error=str(exc),route_exact=False)
        finally:row['latency_s']=time.perf_counter()-started;processor.close()
        report['cases'].append(row)
        print(json.dumps({k:row[k] for k in ('name','route_exact','latency_s')},ensure_ascii=False),flush=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return int(any(c.get('error') or not c['route_exact'] for c in report['cases']))


if __name__=='__main__':raise SystemExit(main())
