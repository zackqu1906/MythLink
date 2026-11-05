#!/usr/bin/env python3
"""Run ON ASCEND: isolated shared-weight, two-thread/two-stream Clef probe.

No HTTP listener and no deployment changes. Run without another model process
occupying the card. Compare identical frozen requests in serial and stream modes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import threading
import time


def summary(values):
    values = sorted(values)
    return {'n': len(values), 'p50_s': statistics.median(values), 'p95_s': values[math.ceil(.95*len(values))-1]}


def answer_diff(a, b):
    if set(a) != set(b):
        return {'same_choices': False, 'max_probability_delta': None}
    return {'same_choices': all(a[k]['choice'] == b[k]['choice'] for k in a),
            'max_probability_delta': max(abs(a[k]['probabilities'][v]-b[k]['probabilities'][v]) for k in a for v in a[k]['probabilities'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--requests', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    import torch
    import torch_npu
    from transformers import AutoProcessor
    sys.path.insert(0, str(args.model_path))
    from joint_schema_model import load_release_model, systemone, encode_record

    torch.npu.set_device('npu:0')
    model, _ = load_release_model(args.model_path, device='npu:0', dtype=torch.bfloat16,
                                  attn_implementation='eager', local_files_only=True)
    text_config = model.language_model.config.text_config
    if text_config.rope_parameters['rope_type'] != 'default':
        raise RuntimeError('Probe requires static RoPE; dynamic shared buffers need separate validation')
    assert not model.training and not model.language_model.config.use_cache
    torch.npu.synchronize()
    cases = json.loads(args.requests.read_text())
    for case in cases:
        for body in case['requests'].values():
            if set(body) != {'model', 'state', 'questions'}:
                raise ValueError('Only text requests are supported in this probe')

    class Worker:
        def __init__(self):
            # One instance per dedicated executor thread; share only read-only
            # model weights. Tokenizer/processor and stream are worker-local.
            torch.npu.set_device('npu:0')
            self.stream = torch.npu.Stream(device='npu:0')
            self.processor = AutoProcessor.from_pretrained(args.model_path, local_files_only=True)

        def infer(self, body, started, barrier=None):
            torch.npu.set_device('npu:0')
            if barrier is not None:
                barrier.wait(timeout=10)
            dispatched = time.perf_counter() - started
            tokens = len(encode_record(self.processor.tokenizer, body, processor=self.processor, max_length=1_000_000).input_ids)
            if tokens > 8192:
                raise ValueError('Input exceeds service limit')
            with torch.npu.stream(self.stream):
                result = systemone(model, self.processor, body, max_length=8192)
                # Synchronize this request's stream, never device-wide inside
                # either worker: a global barrier would join unrelated work.
                self.stream.synchronize()
            return {**result, 'input_tokens': tokens, 'dispatched_s': dispatched,
                    'completed_s': time.perf_counter() - started, 'stream_id': int(self.stream.npu_stream)}

    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'torch': torch.__version__,
              'torch_npu': torch_npu.__version__, 'device': torch.npu.get_device_name(0),
              'requests_sha256': hashlib.sha256(args.requests.read_bytes()).hexdigest(),
              'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'model_revision': (args.model_path.parent/'revision.txt').read_text().strip(),
              'note': 'Isolated host wall-clock including encoding, launch, NPU execution and per-stream completion; excludes HTTP/SSH. No profiler trace: overlapping host calls do not prove overlapping kernels. Fixed development subset, not accuracy acceptance.',
              'cases': [], 'warmup': []}
    modes = ('joint', 'root', 'serial_pair', 'streams_pair')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as root_pool, ThreadPoolExecutor(max_workers=1) as detail_pool:
            root_worker = root_pool.submit(Worker).result()
            detail_worker = detail_pool.submit(Worker).result()
            assert root_worker.stream.npu_stream != detail_worker.stream.npu_stream

            def run(case, mode):
                torch.npu.synchronize()
                started = time.perf_counter()
                record = {'id': case['id'], 'expected': case['expected'], 'mode': mode, 'stages': {}}
                if mode in ('joint', 'root'):
                    stage = root_pool.submit(root_worker.infer, case['requests'][mode], started).result()
                    record['stages'][mode] = stage
                    record['root_ready_s'] = stage['completed_s']
                elif mode == 'serial_pair':
                    root = root_pool.submit(root_worker.infer, case['requests']['root'], started).result()
                    details = detail_pool.submit(detail_worker.infer, case['requests']['details'], started).result()
                    record['stages'] = {'root': root, 'details': details}
                    record['root_ready_s'] = root['completed_s']
                else:
                    barrier = threading.Barrier(2)
                    root_future = root_pool.submit(root_worker.infer, case['requests']['root'], started, barrier)
                    details_future = detail_pool.submit(detail_worker.infer, case['requests']['details'], started, barrier)
                    root, details = root_future.result(), details_future.result()
                    record['stages'] = {'root': root, 'details': details}
                    record['root_ready_s'] = root['completed_s']
                record['drain_s'] = time.perf_counter() - started
                record['memory_allocated_mb'] = torch.npu.memory_allocated()/1024**2
                record['memory_reserved_mb'] = torch.npu.memory_reserved()/1024**2
                record['peak_allocated_mb'] = torch.npu.max_memory_allocated()/1024**2
                return record

            for mode in modes:
                report['warmup'].append(run(cases[0], mode))
            with args.output.with_suffix('.jsonl').open('w') as journal:
                for repeat in range(args.repeats):
                    for index, case in enumerate(cases):
                        shift = (index+repeat) % len(modes)
                        for mode in modes[shift:]+modes[:shift]:
                            record = run(case, mode);record['repeat'] = repeat
                            report['cases'].append(record)
                            journal.write(json.dumps(record, ensure_ascii=False)+'\n');journal.flush()
                            print(json.dumps({k:record[k] for k in ('id','repeat','mode','root_ready_s','drain_s','peak_allocated_mb')}),flush=True)
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        report['metrics'] = {mode: {field: summary([r[field] for r in report['cases'] if r['mode']==mode])
                                    for field in ('root_ready_s','drain_s')}
                             for mode in modes if any(r['mode']==mode for r in report['cases'])}
        lookup = {(r['id'],r['repeat'],r['mode']):r for r in report['cases']}
        report['serial_vs_streams'] = []
        for (case_id, repeat, mode), serial in lookup.items():
            if mode != 'serial_pair': continue
            parallel = lookup.get((case_id,repeat,'streams_pair'))
            if parallel:
                report['serial_vs_streams'].append({'id':case_id, 'repeat':repeat,
                    **{branch: answer_diff(serial['stages'][branch]['answers'],parallel['stages'][branch]['answers']) for branch in ('root','details')}})
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
