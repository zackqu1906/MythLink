"""Run synthetic inputs through the real project worker, Clef and configured LLM."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
from pathlib import Path
import queue
import sys
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QSettings
from proximic_ring.text_processing.intent_router import ClefIntentRouter
from proximic_ring.text_processing.model import LLMSettings, InputModeRoutingRequest, TextProcessingRequest
from proximic_ring.text_processing.worker import TextProcessingWorker
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.automatic_prompts import AUTOMATIC_PROMPT_VERSION
from proximic_ring.text_processing.router_prompts import ROUTER_PROMPT_VERSION, routing_schema, joint_router_input
try:
    from .clef_cases import DEFAULT_CASES, load_cases, generation_checks, raw_joint_match
    from .clef_pipeline_report import write_pipeline_report, recording_opener
except ImportError:
    from clef_cases import DEFAULT_CASES, load_cases, generation_checks, raw_joint_match
    from clef_pipeline_report import write_pipeline_report, recording_opener

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', help='SystemOne URL; default: PROXIMIC_CLEF_URL')
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES, help='Editable JSON case file; default: all cases')
    parser.add_argument('--case', action='append', help='Run a case ID (repeatable)')
    parser.add_argument('--validate-only', action='store_true', help='Check case inputs offline without calling models')
    parser.add_argument('--output', type=Path, help='Save complete synthetic pipeline report')
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases, args.case, require_plan=True)
    except ValueError as exc:
        parser.error(str(exc))
    if args.validate_only:
        print(f'CASES_OK: {len(cases)} cases in {args.cases}')
        return 0
    saved = QSettings('ProxiMic', 'ProxiMic Voice')
    provider = str(saved.value('llm/provider', 'volcengine'))
    if provider == 'local':
        raise SystemExit('This smoke test expects an already configured remote text LLM')
    settings = LLMSettings(
        enabled=True, provider=provider,
        base_url=str(saved.value('llm/baseUrl', '')),
        model=str(saved.value('llm/model', '')),
        api_key=str(saved.value('llm/apiKey', '')),
        api_key_env=str(saved.value('llm/apiKeyEnv', 'ARK_API_KEY')),
        timeout_s=float(saved.value('llm/timeoutSeconds', 30.0)),
    )
    settings.validate()
    # Only synthetic case data is sent; desktop settings are never changed.
    routed, generated, routing_calls, generation_calls = (queue.Queue() for _ in range(4))
    router = ClefIntentRouter(endpoint=args.endpoint)
    # Preserve the real client's no-redirect transport and response size limit.
    router._urlopen = recording_opener(router._urlopen, routing_calls)
    worker = TextProcessingWorker(
        on_result=generated.put, on_routing_result=routed.put,
        intent_router=router,
        processor=OpenAICompatibleTextProcessor(urlopen=recording_opener(urlopen, generation_calls)),
    )
    records=[]
    report={'kind':'synthetic-project-worker-pipeline', 'cases_path':str(args.cases.resolve()),
            'created_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'case_sha256':hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            'requested_samples':len(cases), 'llm_model':settings.model,
            'generation_prompt_version': AUTOMATIC_PROMPT_VERSION, 'prompt_version':ROUTER_PROMPT_VERSION,
            'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                ROOT/'src/proximic_ring/text_processing/router_prompts.py',ROOT/'src/proximic_ring/text_processing/intent_router.py',
                ROOT/'src/proximic_ring/text_processing/task_routing.py',ROOT/'src/proximic_ring/text_processing/automatic_prompts.py',
                ROOT/'src/proximic_ring/text_processing/automatic_processing.py',ROOT/'src/proximic_ring/text_processing/llm.py',
                ROOT/'src/proximic_ring/text_processing/worker.py',Path(__file__))},
            'prompts': {'joint': routing_schema(), 'joint_without_source': routing_schema(target=''),
                        'shared_input_prefix': joint_router_input('')},
            'llm_host':urlsplit(settings.base_url).hostname,
            'scope':'真实 Worker → v19 联合 Clef → 按实际路由调用文本模型；每例一次分类、成功后一次生成，不以预期标签覆盖路由、不自动重试。合成输入，不采集麦克风或 OCR，不执行 IME 写回。',
            'cases':records}
    journal=None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        journal=args.output.with_suffix('.jsonl').open('w')
    def drain(calls):
        values=[]
        while not calls.empty(): values.append(calls.get_nowait())
        return values
    try:
        for index, case in enumerate(cases):
            start=time.perf_counter()
            worker.submit_routing(InputModeRoutingRequest(
                index*2+1, index+1, case['utterance'], settings, router_backend='clef',
                target_text=case['target'], screen_context=case.get('context', {})))
            route=routed.get(timeout=45)
            record={'id':case['id'], 'utterance':case['utterance'], 'target':case['target'], 'context':case['context'],
                    'expected':case['expected'], 'actual_task':route.task_type,
                    'edit_plan':route.edit_plan.as_dict() if route.edit_plan else None,
                    'routing_error':route.error, 'routing_latency_s':route.latency_s,
                    'routing_trace':json.loads(route.model_output) if route.model_output else {},
                    'routing_calls':drain(routing_calls), 'generation_review':{'status':'pending'}}
            record['raw_exact_match'] = raw_joint_match(record['routing_trace'], case['expected'])
            record['root_correct'] = record['routing_trace'].get('raw_model_task') == case['expected']['task']
            record['exact_match'] = not route.error and route.task_type == case['expected']['task'] and set((record['edit_plan'] or {}).get('tasks',[])) == set(case['expected'].get('tasks',[]))
            if not route.error:
                worker.submit(TextProcessingRequest(
                    index*2+2, index+1, route.mode, case['utterance'], settings,
                    target_text=case['target'], task_type=route.task_type,
                    screen_context=route.screen_context, edit_plan=route.edit_plan))
                result=generated.get(timeout=60)
                record.update(final_text=result.final_text, generation_error=result.error,
                              generation_latency_s=result.latency_s, used_llm=result.used_llm,
                              generation_model_output=result.model_output)
                record['generation_calls'] = drain(generation_calls)
                record['generation_requests'] = [c['request'] for c in record['generation_calls']]
                if not result.error:
                    record['generation_checks'] = generation_checks(case['expected'], result.final_text)
            record['total_latency_s']=time.perf_counter()-start
            records.append(record)
            if journal:
                journal.write(json.dumps(record,ensure_ascii=False)+'\n');journal.flush()
            print(json.dumps({'index':index+1,'n':len(cases),'id':record['id'],'route_ok':record['exact_match'],
                              'raw_route_ok':record['raw_exact_match'],'routing_s':round(record['routing_latency_s'],3),
                              'generation_s':round(record.get('generation_latency_s',0),3),'total_s':round(record['total_latency_s'],3),
                              'error':record.get('routing_error') or record.get('generation_error')},ensure_ascii=False),flush=True)
    finally:
        worker.close(wait=True)
        if journal: journal.close()
        if args.output: write_pipeline_report(report,args.output)
    errors = sum(bool(r.get('routing_error') or r.get('generation_error')) for r in records)
    failed_checks = sum(r.get('generation_checks', {}).get('passed') is False for r in records)
    print(json.dumps({'generation_check_failures': failed_checks, 'samples': len(records), 'completed_generations': sum('final_text' in r for r in records), 'errors': errors}, ensure_ascii=False))
    return 1 if errors else 2 if failed_checks else 0


if __name__ == '__main__':
    raise SystemExit(main())
