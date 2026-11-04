"""Run synthetic inputs through the real project worker, Clef and configured LLM."""
import argparse
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
from proximic_ring.text_processing.router_prompts import routing_schema, joint_router_input
from clef_cases import DEFAULT_CASES, load_cases, generation_checks, raw_joint_match
from clef_routing_report import write_routing_html
from compare_clef_routing import metrics

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', help='SystemOne URL; default: PROXIMIC_CLEF_URL')
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES, help='Editable JSON case file; default: all cases')
    parser.add_argument('--case', action='append', help='Run a case ID (repeatable)')
    parser.add_argument('--validate-only', action='store_true', help='Check case inputs offline without calling models')
    parser.add_argument('--output', type=Path, help='Save complete synthetic pipeline report')
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases, args.case)
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
    routed, generated, generation_requests = queue.Queue(), queue.Queue(), queue.Queue()
    def capture_generation_request(request, **kwargs):
        # Record the actual synthetic request body, never headers or credentials.
        generation_requests.put(json.loads(request.data.decode('utf-8')))
        return urlopen(request, **kwargs)
    worker = TextProcessingWorker(
        on_result=generated.put, on_routing_result=routed.put,
        intent_router=ClefIntentRouter(endpoint=args.endpoint),
        processor=OpenAICompatibleTextProcessor(urlopen=capture_generation_request),
    )
    records=[]
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
                    'routing_trace':json.loads(route.model_output) if route.model_output else {}}
            record['raw_exact_match'] = raw_joint_match(record['routing_trace'], case['expected'])
            if not route.error:
                worker.submit(TextProcessingRequest(
                    index*2+2, index+1, route.mode, case['utterance'], settings,
                    target_text=case['target'], task_type=route.task_type,
                    screen_context=route.screen_context, edit_plan=route.edit_plan))
                result=generated.get(timeout=60)
                record.update(final_text=result.final_text, generation_error=result.error,
                              generation_latency_s=result.latency_s, used_llm=result.used_llm)
                record['generation_requests'] = []
                while not generation_requests.empty():
                    record['generation_requests'].append(generation_requests.get_nowait())
                if not result.error:
                    record['generation_checks'] = generation_checks(case['expected'], result.final_text)
            record['total_latency_s']=time.perf_counter()-start
            records.append(record)
            print(json.dumps({k:v for k,v in record.items() if k not in ('routing_trace', 'generation_requests')},ensure_ascii=False),flush=True)
    finally:
        worker.close(wait=True)
    report={'kind':'synthetic-project-worker-pipeline', 'cases_path':str(args.cases), 'llm_model':settings.model,
            'generation_prompt_version': AUTOMATIC_PROMPT_VERSION,
            'prompts': {'joint': routing_schema(), 'shared_input_prefix': joint_router_input('')},
            'llm_host':urlsplit(settings.base_url).hostname,
            'scope':'Real Clef HTTP and text LLM; no live microphone, OCR capture, IME writeback or messages sent.',
            'cases':records}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        display = []
        for record in records:
            trace = record['routing_trace']
            actual = {'task': record['actual_task'], **(record['edit_plan'] or {})}
            expected = record['expected']
            row = {**record, 'trace': {**trace, 'requests': 1, 'latency_s': record['routing_latency_s']},
                   'actual': actual, 'root_correct': trace.get('raw_model_task') == expected['task'],
                   'exact_match': not record['routing_error'] and actual['task'] == expected['task'] and
                       set(actual.get('tasks', [])) == set(expected.get('tasks', []))}
            if record.get('routing_error') or record.get('generation_error'):
                row['error'] = record.get('routing_error') or record.get('generation_error')
            display.append(row)
        write_routing_html({'cases': display, 'metrics': metrics(display),
            'prompts': report['prompts'],
            'prompt_version': records[0]['routing_trace'].get('prompt_version', '') if records else '',
            'note': '真实 Worker → Clef → 文本模型的合成流程；完整匹配只评价分类，不代表生成文本全部正确。未执行麦克风、实时 OCR 或输入法写回。'}, args.output.with_suffix('.html'))
    errors = sum(bool(r.get('routing_error') or r.get('generation_error')) for r in records)
    failed_checks = sum(r.get('generation_checks', {}).get('passed') is False for r in records)
    print(json.dumps({'generation_check_failures': failed_checks, 'samples': len(records), 'completed_generations': sum('final_text' in r for r in records), 'errors': errors}, ensure_ascii=False))
    return 1 if errors else 2 if failed_checks else 0


if __name__ == '__main__':
    raise SystemExit(main())
