#!/usr/bin/env python3
"""Freeze a canonical saved-context subset and run the production edit entry."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools import voice_eval_dataset as corpus
from tools.check_llm_edit_router import configured_settings
from tools.check_automatic_edit_generation import RecordedResponse, check_output
from proximic_ring.text_processing.boundary_edit_prompt import BOUNDARY_EDIT_PROMPT, BOUNDARY_EDIT_PROMPT_VERSION
from proximic_ring.text_processing.model import ROUTED_EDIT_BOUNDARY
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def run_case(case, settings):
    model_input = corpus.model_input(case)
    record = {'id': case['id'], 'started_at': now(), 'model_input': model_input,
              'input_sha256': corpus.fingerprint(model_input), 'calls': []}

    def capture(request, **kwargs):
        if record['calls']:
            raise RuntimeError('Only one model request per case is permitted')
        payload = json.loads(request.data)
        assert payload['input'][0]['content'][0]['text'] == BOUNDARY_EDIT_PROMPT
        assert json.loads(payload['input'][1]['content'][0]['text']) == model_input
        assert payload['model'] == settings.model
        call = {'endpoint': request.full_url, 'request': payload}
        record['calls'].append(call)  # Deliberately omit authentication headers.
        start = time.perf_counter()
        response = urlopen(request, **kwargs)
        call['http_status'] = response.status
        return RecordedResponse(response, call, start)

    processor = OpenAICompatibleTextProcessor(urlopen=capture)
    start = time.perf_counter()
    try:
        body, raw, actual, plan = processor.process_routed_edit_with_trace(
            model_input['utterance'], settings, model_input['editable_text'], model_input['screen_context'],
            variant=ROUTED_EDIT_BOUNDARY, saved_context=model_input['saved_context'])
        assert actual == model_input and plan is None and len(record['calls']) == 1
        record.update(output=body, raw_output=raw,
                      checks=check_output(case['editing_expectations'][0]['checks'], body))
    except Exception as exc:
        record.update(error=str(exc) or type(exc).__name__, error_type=type(exc).__name__, output='',
                      raw_output=str(getattr(exc, 'model_output', '')))
    finally:
        record.update(latency_s=time.perf_counter() - start, finished_at=now(),
                      output_sha256=digest(record['output']))
        processor.close()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--case', action='append', default=[])
    args = parser.parse_args()
    master = corpus.load_master()
    subset = corpus.subset_document(master, {'tasks': ['editing'], 'tags': ['saved_context'],
                                             'kind': 'single', 'ids': args.case})
    assert all(isinstance(corpus.model_input(c)['saved_context'], str) for c in subset['cases'])
    print(json.dumps({'cases': subset['count'], 'master_revision': master['revision'],
                      'prompt_version': BOUNDARY_EDIT_PROMPT_VERSION}), flush=True)
    if args.validate_only:
        return
    settings = configured_settings()
    sources = [Path(__file__), ROOT / 'tools/voice_eval_dataset.py',
               ROOT / 'tools/check_llm_edit_router.py', ROOT / 'tools/check_automatic_edit_generation.py',
               ROOT / 'src/proximic_ring/screenshots/context.py',
               *sorted((ROOT / 'src/proximic_ring/text_processing').glob('*.py'))]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=False)
    corpus.write_json(out / 'inputs.json', subset)
    (out / 'prompt.txt').write_text(BOUNDARY_EDIT_PROMPT, encoding='utf-8')
    report = {'created_at': now(), 'model': settings.model, 'provider': settings.provider,
              'base_url': settings.base_url, 'timeout_s': settings.timeout_s,
              'prompt_version': BOUNDARY_EDIT_PROMPT_VERSION, 'prompt_sha256': digest(BOUNDARY_EDIT_PROMPT),
              'master_revision': master['revision'], 'master_sha256': subset['master_sha256'],
              'inputs_sha256': hashlib.sha256((out / 'inputs.json').read_bytes()).hexdigest(),
              'source_sha256': hashes, 'planned_requests': subset['count'], 'cases': [],
              'entry': 'OpenAICompatibleTextProcessor.process_routed_edit_with_trace(variant=ROUTED_EDIT_BOUNDARY)',
              'method': '合成开发用例；预先冻结输入与验收要求；串行、每例一次、不重试；只测编辑正文，不测分类、ASR、OCR或写回。'}
    print(json.dumps({'provider': settings.provider, 'model': settings.model, 'base_url': settings.base_url}), flush=True)
    errors = 0
    try:
        with (out / 'requests.jsonl').open('x', encoding='utf-8') as journal:
            for index, case in enumerate(subset['cases'], 1):
                record = run_case(case, settings)
                report['cases'].append(record)
                journal.write(json.dumps(record, ensure_ascii=False) + '\n')
                journal.flush()
                print(f'{index}/{subset["count"]} {case["id"]} error={bool(record.get("error"))} {record["latency_s"]:.3f}s', flush=True)
                errors = errors + 1 if record.get('error') else 0
                if errors >= 3:
                    raise RuntimeError('Stopped after three consecutive request errors; evidence retained')
    finally:
        report['finished_at'] = now()
        report['completed_all'] = len(report['cases']) == subset['count']
        report['changed_sources'] = [str(p.relative_to(ROOT)) for p in sources
            if hashlib.sha256(p.read_bytes()).hexdigest() != hashes[str(p.relative_to(ROOT))]]
        corpus.write_json(out / 'raw.json', report)


if __name__ == '__main__':
    main()
