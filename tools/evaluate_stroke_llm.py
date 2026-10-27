"""Paired replay of exported real handwriting; targets never enter model requests."""
import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_llm_context import (
    PROMPT, CONTEXT_CHARS, ContinuationPredictor, apply_predictions, local_prediction_settings,
)
from proximic_ring.touchpad.stroke.stroke_word_correction import WholeCharacterCorrector, merge_candidates


def raw_trials(path):
    """Only return strokes inside an explicitly started/ended trial."""
    result, strokes = {}, None
    if not path.is_file():
        return result
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['kind'] == 'sentence_trial_start':
                strokes = []
            elif row['kind'] == 'stroke' and strokes is not None:
                strokes.append(row)
            elif row['kind'] == 'sentence_trial_end' and strokes is not None:
                summary = row['summary']
                result[(summary['target'], summary['elapsed_s'])] = strokes
                strokes = None
    return result


def replay_cases(report, strokes, name):
    """Replay committed text / edits and retain full observed per-stroke scores."""
    events = report['events']
    measured = [e for e in events if e['kind'] == 'stroke' and e.get('recognized')]
    if (len(strokes) != len(measured) or any(raw['result']['category'] != event['recognized']
                                           for raw, event in zip(strokes, measured))):
        raise ValueError('raw stroke/event mismatch')
    ink, text, context_at, index, samples = [], '', 0., 0, []
    for event in events:
        kind = event['kind']
        if kind == 'stroke' and event.get('recognized'):
            raw = strokes[index]
            index += 1
            position = event['stroke_index']
            ink = ink[:position]
            if len(ink) != position:
                raise ValueError('unreconstructable stroke prefix')
            policy = FuzzyPolicy(raw.get('fuzzy_policy', {}).get('level', 50))
            ink.append(stroke_evidence(event['recognized'], raw['result'], policy=policy,
                                       metrics=raw.get('kinematics')))
            code = ''.join(e['category'] for e in ink)
            if raw['code'] != code or event['position'] != len(text):
                raise ValueError('raw composition/text position mismatch')
            if len(code) <= 3 and event.get('character'):
                samples.append(dict(trial=name, target=event['character'], context=text[-CONTEXT_CHARS:],
                    elapsed_s=event['elapsed_s'], context_at_s=context_at, code=code,
                    evidence=list(ink), level=policy.level, fuzzy=raw.get('fuzzy_matching', True)))
        elif kind == 'commit' and event.get('delivered'):
            text += event['character']
            context_at = event['elapsed_s']
            ink = []
        elif kind == 'undo_stroke':
            ink = ink[:-1]
        elif kind == 'clear_strokes':
            ink = []
        elif kind == 'delete_character':
            text = text[:-1]
            context_at = event['elapsed_s']
    if text != report['actual']:
        raise ValueError('reconstructed text differs from report')
    return samples


def load_cases(directory):
    cache, cases, trials, excluded = {}, [], [], []
    for path in sorted(directory.glob('*-trial-*.json')):
        report = json.loads(path.read_text(encoding='utf-8'))
        if not report.get('completed') or report.get('actual') != report.get('target'):
            excluded.append(dict(file=path.name, reason='incomplete or target/actual mismatch'))
            continue
        if report.get('contains_manual_strokes') or not report.get('stroke_attempts'):
            excluded.append(dict(file=path.name, reason='manual or no observed strokes'))
            continue
        raw = directory / Path(report['raw_log']).name
        if raw not in cache:
            cache[raw] = raw_trials(raw)
        strokes = cache[raw].get((report['target'], report['elapsed_s']))
        if strokes is None:
            excluded.append(dict(file=path.name, reason='matching raw trial unavailable'))
            continue
        try:
            samples = replay_cases(report, strokes, path.name)
        except ValueError as exc:
            excluded.append(dict(file=path.name, reason=str(exc)))
            continue
        cases.extend(samples)
        trials.append(dict(file=path.name, target=report['target'], samples=len(samples),
                           stroke_attempts=report['stroke_attempts']))
    return cases, trials, excluded


def select_contexts(cases, limit):
    """Deterministic round-robin across trials; never select by model outcome."""
    groups = defaultdict(list)
    for row in cases:
        context = row['context']
        if context.strip() and context not in groups[row['trial']]:
            groups[row['trial']].append(context)
    chosen = []
    for offset in range(max(map(len, groups.values()), default=0)):
        for values in groups.values():
            if offset < len(values) and values[offset] not in chosen:
                chosen.append(values[offset])
                if len(chosen) == limit:
                    return chosen
    return chosen


def metrics(rows, key):
    def hit(row, k):
        rank = row[key]
        return rank is not None and rank <= k
    count = len(rows)
    return dict(samples=count, **{f'top{k}': sum(hit(r, k) for r in rows) / count if count else None
                                 for k in (1, 3, 6)},
                mrr=sum(1 / r[key] if r[key] else 0 for r in rows) / count if count else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=Path, default=ROOT / 'data/stroke-test')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/diagnostics/llm-context')
    parser.add_argument('--max-contexts', type=int, default=64)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--cache-only', action='store_true', help='Recompute using saved predictions, including saved failures')
    parser.add_argument('--timeout', type=float, default=10.)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases, trials, excluded = load_cases(args.records)
    selected = select_contexts(cases, args.max_contexts)
    manifest = dict(trials=trials, excluded=excluded, short_prefix_samples=len(cases),
                    selected_contexts=selected, selection='round-robin across trials before model calls')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(trials=len(trials), exclusions=len(excluded), samples=len(cases),
                          contexts=len(selected)), ensure_ascii=True), flush=True)
    if args.prepare_only:
        return
    settings = replace(local_prediction_settings(), timeout_s=args.timeout)
    prompt_hash = hashlib.sha256(PROMPT.encode()).hexdigest()
    cache_path = args.output / 'predictions.json'
    cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.is_file() else {}
    if cache.get('model') != settings.model or cache.get('prompt_sha256') != prompt_hash:
        cache = dict(model=settings.model, prompt_sha256=prompt_hash, predictions={})
    predictor = ContinuationPredictor()
    for i, context in enumerate(selected, 1):
        if args.cache_only and context not in cache['predictions']:
            raise ValueError('No matching cached prediction for context')
        if not args.cache_only and (context not in cache['predictions'] or cache['predictions'][context].get('error')):
            started = time.perf_counter()
            try:
                phrases = predictor.predict(context, settings)
                error = ''
            except Exception as exc:
                phrases, error = [], str(exc)[:500]
            cache['predictions'][context] = dict(phrases=phrases,
                elapsed_ms=(time.perf_counter() - started) * 1000, error=error)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
        item = cache['predictions'][context]
        print(json.dumps(dict(done=i, total=len(selected), elapsed_ms=round(item['elapsed_ms']),
                              error=item['error']), ensure_ascii=True), flush=True)
    dictionary, offline = StrokeDictionary(), StrokeContext()
    allowed = common_characters()
    corrector = WholeCharacterCorrector(dictionary.entries, dictionary.ranks, allowed_characters=allowed)
    selected_set, rows = set(selected), []
    for case in cases:
        if case['context'] not in selected_set:
            continue
        policy = FuzzyPolicy(case['level'])
        matches = dictionary.fuzzy_candidates(case['evidence'], predictor=offline,
            context=case['context'], allowed_characters=allowed, policy=policy) if case['fuzzy'] else []
        normal = [r['char'] for r in matches] if case['fuzzy'] else dictionary.candidates(case['code'], allowed_characters=allowed)
        repairs = corrector.candidates(case['code'], evidence=case['evidence'], context=case['context'],
            predictor=offline, allowed_characters=allowed, excluded_characters=normal)
        baseline = merge_candidates(normal, repairs)
        item = cache['predictions'][case['context']]
        assisted, _, _ = apply_predictions(baseline, matches + repairs, item['phrases'],
            dictionary=dictionary, code=case['code'], evidence=case['evidence'], fuzzy=case['fuzzy'],
            policy=policy, allowed=allowed)
        rank = lambda chars: chars.index(case['target']) + 1 if case['target'] in chars else None
        # Optimistic availability estimate: measured request + 250ms debounce,
        # excluding queueing and selection freezing. First call includes loading.
        ready = not item['error'] and case['elapsed_s'] - case['context_at_s'] >= .25 + item['elapsed_ms'] / 1000
        rows.append(dict(trial=case['trial'], context=case['context'], target=case['target'],
            code=case['code'], baseline_rank=rank(baseline), assisted_rank=rank(assisted),
            latency_bound_rank=rank(assisted) if ready else rank(baseline), prediction_ready_bound=ready,
            target_strokes=min((len(entry[1]) for entry in dictionary.by_character.get(case['target'], ())), default=999),
            llm_ms=item['elapsed_ms'], error=item['error'], baseline_top6=''.join(baseline[:6]),
            assisted_top6=''.join(assisted[:6])))
    successful = [item['elapsed_ms'] for context, item in cache['predictions'].items()
                  if context in selected_set and not item['error']]
    summary = dict(model=settings.model, scope=len(allowed), trials=len({r['trial'] for r in rows}),
        unique_contexts=len(selected), request_failures=sum(bool(cache['predictions'][c]['error']) for c in selected),
        baseline=metrics(rows, 'baseline_rank'), cached_upper_bound=metrics(rows, 'assisted_rank'),
        measured_latency_upper_bound=metrics(rows, 'latency_bound_rank'),
        inference_median_ms=statistics.median(successful) if successful else None,
        inference_max_ms=max(successful) if successful else None,
        ready_fraction_upper_bound=sum(r['prediction_ready_bound'] for r in rows) / len(rows) if rows else None,
        improved_ranks=sum((r['assisted_rank'] or 999999) < (r['baseline_rank'] or 999999) for r in rows),
        worsened_ranks=sum((r['assisted_rank'] or 999999) > (r['baseline_rank'] or 999999) for r in rows),
        targets_with_at_most_5_strokes=dict(
            baseline=metrics([r for r in rows if r['target_strokes'] <= 5], 'baseline_rank'),
            assisted=metrics([r for r in rows if r['target_strokes'] <= 5], 'assisted_rank')),
        by_prefix_length={str(n):dict(baseline=metrics([r for r in rows if len(r['code']) == n], 'baseline_rank'),
             assisted=metrics([r for r in rows if len(r['code']) == n], 'assisted_rank')) for n in (1, 2, 3)},
        limitations=['Offline candidate replay, not a measured change in human input speed.',
            'Only completed, non-manual trials with reconstructable raw strokes.',
            'Both arms use current 6500 scope and whole-character correction.',
            'Recorded ASR-free stroke scores are reused; trajectories are not reclassified.',
            'Several sentences are familiar song lyrics; results may reflect memorized continuations.',
            'Model was warmed during format checks; final timings exclude initial cold loading.',
            'Timing bound omits queueing and selection freeze; cannot claim real-time benefit.'])
    (args.output / 'comparison.json').write_text(json.dumps(dict(summary=summary, samples=rows),
                                                        ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 本地大模型联想：真实书写记录回放', '',
        f"模型：`{settings.model}`。{summary['trials']}轮记录，{len(selected)}种前文，{len(rows)}个1–3笔输入状态。",
        '两组使用相同6500字范围、已记录的识别分数和当前整字纠错；模型只接收当时已输入的前文。', '',
        '| 条件 | 首选命中 | 前3命中 | 前6命中 | MRR |', '|---|---:|---:|---:|---:|']
    for label, key in [('原离线上下文', 'baseline'), ('模型结果已缓存（排名上限）', 'cached_upper_bound'),
                       ('计入单次耗时（仍为乐观上限）', 'measured_latency_upper_bound')]:
        value = summary[key]
        if value['samples']:
            lines.append(f"| {label} | {value['top1']:.1%} | {value['top3']:.1%} | {value['top6']:.1%} | {value['mrr']:.3f} |")
    lines += ['', f"预测请求失败：{summary['request_failures']}。排名改善{summary['improved_ranks']}个状态，下降{summary['worsened_ranks']}个状态。",
              f"成功请求中位耗时：{summary['inference_median_ms']} ms，最大：{summary['inference_max_ms']} ms。", '',
              '## 总笔画不超过5笔的目标字', '',
              '| 条件 | 状态数 | 首选命中 | 前3命中 | 前6命中 |', '|---|---:|---:|---:|---:|']
    for label, key in [('原离线上下文', 'baseline'), ('模型预测已缓存', 'assisted')]:
        value = summary['targets_with_at_most_5_strokes'][key]
        if value['samples']:
            lines.append(f"| {label} | {value['samples']} | {value['top1']:.1%} | {value['top3']:.1%} | {value['top6']:.1%} |")
    lines += ['',
              '这不是实际输入速度测试。延迟估计未计入请求排队和选字冻结，因此仍偏乐观。',
              '模型已在格式检查时预热，最终推理耗时不包含初次冷启动。1次无有效输出时回退原候选。' if summary['request_failures'] == 1
              else '模型已在格式检查时预热，最终推理耗时不包含初次冷启动。无有效输出时回退原候选。',
              '完成记录的筛选会低估未完成时的困难；多条文本为歌词，模型可能依赖熟悉的续写。',
              '部分重复句子来自不同轮次；按前文去重模型请求，笔画输入状态仍按实测轮次保留。', '',
              '抽样和排除清单：`manifest.json`；预测及耗时：`predictions.json`；逐状态对照：`samples.csv`。']
    (args.output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    with (args.output / 'samples.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['trial'])
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
