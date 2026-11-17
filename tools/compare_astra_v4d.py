"""Frozen v4d paired benchmark: local ChatGPT Astra low versus Volcengine.

Examples:
  python tools/compare_astra_v4d.py --validate-only
  python tools/compare_astra_v4d.py
  python tools/review_general_edit_ablation.py --output-dir <output>
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.character_clarification_experiment import run_one, digest, jd
from tools.check_llm_edit_router import configured_settings
from tools.check_automatic_edit_generation import check_output
from tools.local_chatgpt_eval_client import LocalChatGPTClient

SOURCE = ROOT/'docs/reports/character-clarification-2026-11-16'
OUT = ROOT/'docs/reports/astra-v4d-model-comparison-2026-11-16'
SEED = 2026111607


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def freeze(out, repeats, workers):
    out.mkdir(parents=True, exist_ok=True)
    path = out/'manifest.json'
    if path.exists():
        manifest = json.loads(path.read_text())
        assert manifest['repeats'] == repeats
        assert digest((out/'v4d.txt').read_text()) == manifest['prompt_sha256']
        return manifest
    cases = json.loads((SOURCE/'dataset.json').read_text())['cases']
    prompt = (SOURCE/'v4d.txt').read_text()
    assert digest(prompt) == '507d22e4ba7a8be1a9774527bdd5d6a26338a1ab3b1cb14b497c3ce2088ce181'
    original_report = json.loads((SOURCE/'report.json').read_text())
    uncertain = sorted({c['id'].split('__')[1] for c in original_report['cases']
        if c['group'] == 'confirmation:v4d' and c['review']['status'] == 'uncertain'})
    settings = configured_settings()
    variants = {
        'deepseek': {'label': '火山 DeepSeek / thinking disabled', 'prompt': prompt,
                     'model': settings.model, 'endpoint': settings.base_url},
        'astra_low': {'label': '本机 ChatGPT · GPT-6 Astra / low', 'prompt': prompt,
                      'model': 'gpt-6-astra', 'effort': 'low',
                      'transport': 'installed Codex app-server, ChatGPT login'},
    }
    rng = random.Random(SEED)
    blocks = []
    for repeat in range(1, repeats+1):
        order = list(cases)
        rng.shuffle(order)
        for case in order:
            arms = list(variants)
            rng.shuffle(arms)
            blocks.append({'case_id': case['id'], 'repeat': repeat, 'variants': arms})
    manifest = {
        'created_at': now(), 'cases': cases, 'variants': variants,
        'workers': workers, 'repeats': repeats, 'blocks': blocks,
        'planned_requests': len(blocks)*2,
        'prompt_sha256': digest(prompt),
        'dataset_sha256': digest((SOURCE/'dataset.json').read_text()),
        'rubric': (ROOT/'tools/data/edit_review_correctness_v1.txt').read_text(),
        'uncertain_case_ids': uncertain,
        'seed': SEED, 'seed_scope': 'case and arm order only; not model sampling',
        'input_contract': 'utterance, editable_text, screen_context; no preprocessing',
        'timing': 'perf_counter: before provider invocation to complete text; local session setup included; one-time app-server startup excluded and separately recorded',
        'controls': ['same frozen v4d bytes', 'same input JSON values', 'same cases and repeat count',
                     'randomized paired order', 'four paired workers', 'fresh local session per case',
                     'no prompt tuning, output repair or application-level retries', 'judge after generation'],
        'limitations': ['Astra low reasoning versus DeepSeek thinking disabled',
                       'local app-server adds protocol/tool/environment context; not a bare Responses API',
                       'Astra local route streams; DeepSeek route is nonstreaming',
                       'provider tokenization/cache/routing/output limits cannot be equated',
                       'model comparison includes provider/runtime differences; not an isolated architecture experiment',
                       'all cases are already-used synthetic development/regression data'],
        'source_sha256': {str(p.relative_to(ROOT)): digest(p.read_text()) for p in [
            Path(__file__), ROOT/'tools/local_chatgpt_eval_client.py',
            ROOT/'tools/character_clarification_experiment.py',
            *sorted((ROOT/'src/proximic_ring/text_processing').glob('*.py'))]},
    }
    (out/'v4d.txt').write_text(prompt)
    save(out/'dataset.json', {'cases': cases})
    save(path, manifest)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, default=OUT)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--workers', type=int, default=1)
    p.add_argument('--memory-limit-mb', type=int, default=768)
    p.add_argument('--max-new-requests', type=int, default=0)
    p.add_argument('--validate-only', action='store_true')
    args = p.parse_args()
    if args.workers != 1:
        p.error('Memory-bounded runner requires --workers 1')
    out = args.output_dir
    manifest = freeze(out, args.repeats, args.workers)
    if args.validate_only:
        print(json.dumps({'cases': len(manifest['cases']), 'requests': manifest['planned_requests'],
                          'splits': dict(Counter(c['split'] for c in manifest['cases'])),
                          'uncertain_cases': manifest['uncertain_case_ids']}))
        return
    settings = configured_settings()
    assert settings.model == manifest['variants']['deepseek']['model']
    assert settings.base_url == manifest['variants']['deepseek']['endpoint']
    prompt = (out/'v4d.txt').read_text()
    cases = {c['id']: c for c in manifest['cases']}
    journal = out/'generations.jsonl'
    done = set()
    errors = 0
    if journal.exists():
        with journal.open() as stream:
            for line in stream:
                r = json.loads(line)
                assert r['id'] not in done
                done.add(r['id'])
                errors += bool(r.get('error'))
    initial_count = len(done)
    phase_sources = {p:digest((ROOT/p).read_text()) for p in manifest['source_sha256']}
    phase = {'phase':'memory-bounded-resume','started_at':now(),'already_saved':initial_count,
             'workers':1,'process_lifetime':'one Astra request; exit process group before next request',
             'memory_limit_mb':args.memory_limit_mb,'sample_interval_s':0.5,
             'memory_scope':'runner plus current app-server process tree RSS',
             'reason':'User reported memory exhaustion; previous processes exited on interruption',
             'source_sha256':phase_sources,
             'intentional_source_changes':[p for p,h in manifest['source_sha256'].items() if phase_sources[p]!=h],
             'timing_note':'Keep original 4-worker phase separate. Main per-call latency excludes process startup; startup and full route time also recorded.'}
    phase_dir = out/'resume-phases'
    phase_dir.mkdir(exist_ok=True)
    save(phase_dir/f'after-{initial_count}.json',phase)
    (out/'local-logs').mkdir(exist_ok=True)
    def astra(case, repeat):
        inp = {'utterance': case['utterance'].strip(), 'editable_text': case['target'],
               'screen_context': case['context']}
        record = {'id': f'{case["id"]}__r{repeat}__astra_low', 'case_id': case['id'],
                  'variant': 'astra_low', 'repeat': repeat, 'started_at': now(),
                  'model_input': inp, 'input_sha256': jd(inp), 'calls': []}
        started = time.perf_counter()
        client = None
        try:
            client = LocalChatGPTClient(Path('/tmp/proximic-v4d-eval')/'bounded',
                out/'local-logs'/(record['id']+'.stderr.log'),timeout_s=90,
                memory_limit_mb=args.memory_limit_mb)
            startup_s = time.perf_counter()-started
            result = client.generate(prompt, json.dumps(inp, ensure_ascii=False))
            record.update(result)
            record['client_startup_s'] = startup_s
            record['full_route_s'] = startup_s+result['latency_s']
            record['output_sha256'] = digest(result['final_text'])
            record['raw_response'] = result['final_text']
            record['checks'] = check_output(case['generation_expected'], result['final_text'])
        except Exception as exc:
            record.update(error=str(exc), error_type=type(exc).__name__, final_text='',
                          output_sha256=digest(''), latency_s=time.perf_counter()-started)
        finally:
            if client is not None:
                record['peak_evaluation_rss_mb'] = client.peak_tree_rss_mb
                record['resource_error'] = client.resource_error
                client.close()
        return record
    with journal.open('a') as stream:
        stop = False
        for b in manifest['blocks']:
            if stop:
                break
            for variant in b['variants']:
                rid = f'{b["case_id"]}__r{b["repeat"]}__{variant}'
                if rid in done:
                    continue
                case = cases[b['case_id']]
                result = astra(case, b['repeat']) if variant == 'astra_low' else run_one(
                    case, variant, b['repeat'], prompt, settings)
                result['run_phase'] = phase['phase']
                result['worker_limit'] = 1
                stream.write(json.dumps(result, ensure_ascii=False)+'\n')
                stream.flush()
                done.add(rid)
                errors += bool(result.get('error'))
                if len(done)%10==0 or result.get('error'):
                    print(json.dumps({'completed':len(done),'planned':manifest['planned_requests'],
                        'last':rid,'seconds':result['latency_s'],'peak_rss_mb':result.get('peak_evaluation_rss_mb'),
                        'error':result.get('error')},ensure_ascii=False),flush=True)
                if result.get('error') or (args.max_new_requests and len(done)-initial_count>=args.max_new_requests):
                    stop = True
                    break
    drift = [p for p,h in phase_sources.items() if digest((ROOT/p).read_text()) != h]
    status = {'finished_at': now(), 'complete': len(done)==manifest['planned_requests'],
              'requests': len(done), 'errors': errors,'source_drift': drift,
              'phase':phase['phase'],'intentional_source_changes':phase['intentional_source_changes']}
    save(out/'run-status.json', status)
    print(json.dumps(status), flush=True)


if __name__ == '__main__':
    main()
