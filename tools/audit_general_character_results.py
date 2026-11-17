"""Bind fresh blind judgments and explicit audits to exact input/output pairs."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.general_character_experiment import OUT, SOURCE, UNCERTAIN
from tools.character_clarification_experiment import digest, jd, save
from tools.build_character_clarification_report import load_reviews


def audit():
    out = OUT / 'paired'
    plan = json.loads((out / 'review-plan.json').read_text())
    raw_reviews, _ = load_reviews(out, plan)
    manifest = json.loads((out / 'manifest.json').read_text())
    cases = {c['id']: c for c in manifest['cases']}
    prior = {}
    for r in json.loads((SOURCE / 'report.json').read_text())['cases']:
        rv = r['review']
        if rv.get('reviewer', '').startswith('Codex') and rv.get('input_sha256'):
            prior[(rv['input_sha256'], rv['output_sha256'])] = (r['id'], rv)
    manual = json.loads((OUT / 'codex-review.json').read_text()) if (OUT / 'codex-review.json').exists() else {}
    overrides = {}; queue = []; index = []
    for batch in plan['batches']:
        cid = batch['id'].split('__batch')[0]; c = cases[cid]; payload = batch['payload']
        ih = jd({k: payload[k] for k in ('utterance', 'editable_text', 'screen_context')})
        for item in payload['candidates']:
            key = item['candidate_id']; output = item['output']; oh = digest(output)
            raw = raw_reviews[key]; status = note = expected = ''; origin = ''
            old = prior.get((ih, oh))
            if old:
                status, note = old[1]['status'], old[1]['note']
                expected = '; '.join(i['expected'] for i in old[1].get('issues', []))
                origin = '既有 Codex 审校复用：输入与输出双哈希一致，'+old[0]
            reference = c.get('reference') or c['generation_expected'].get('modified_text')
            if reference is not None and output == reference:
                status, note, origin = 'pass', '正文逐字符合已冻结的可接受参考；不要求其他合法表达与参考相同。', '冻结参考的正向确认'
            limit = 100 if c['utterance'].startswith('写一段五十到一百字的虚构童话') else 30 if '再压缩到三十字以内' in c['utterance'] else 20 if c['utterance'] == '压缩到二十个字以内' else None
            if limit and len(re.findall(r'[\u4e00-\u9fff]', output)) > limit:
                count = len(re.findall(r'[\u4e00-\u9fff]', output))
                status, note, expected, origin = 'fail', f'仅汉字已有 {count} 字，超过明确的 {limit} 字以内上限。', f'不超过 {limit} 字', '明确字数上限核验'
            if key in manual:
                m = manual[key]
                assert m['input_sha256'] == ih and m['output_sha256'] == oh
                status, note, expected, origin = m['status'], m['note'], m.get('expected', ''), '本次 Codex 逐项复核'
            if cid in UNCERTAIN:
                status, note, expected, origin = 'uncertain', '该题在调用前已标为输入冲突或缺少唯一目标真值；两版统一待确认，保留原题及盲评。', '', '冻结的歧义题清单'
            if status:
                overrides[key] = {'candidate_id': key, 'status': status, 'note': 'Codex 审校：'+note,
                    'issues': [{'reason': note, 'expected': expected or c['utterance'], 'actual': output}] if status == 'fail' else [],
                    'input_sha256': ih, 'output_sha256': oh, 'original_review': raw,
                    'reviewer': 'Codex（参与提示词设计，非独立人工）', 'audit_origin': origin}
            final = overrides.get(key, raw)
            entry = {'key': key, 'case_id': cid, 'split': c['split'], 'input_sha256': ih, 'output_sha256': oh,
                     'source': c['target'], 'utterance': c['utterance'], 'context': c['context'], 'output': output,
                     'reference': reference, 'review': final, 'raw_review': raw}
            index.append(entry)
            if not status:
                queue.append(entry)
    save(out / 'audit-overrides.json', {'source_sha256': plan['source_sha256'],
        'blind_reviews_sha256': digest((out / 'reviews.jsonl').read_text()),
        'method': 'Fresh version-blind reviews; exact input/output-bound prior Codex audits, frozen-reference positive confirmations, known ambiguous cases, and explicit new Codex audits. Raw blind reviews stay immutable.',
        'reviews': overrides})
    save(OUT / 'audit-index.json', index)
    save(OUT / 'audit-queue.json', queue)
    print(json.dumps({'unique_candidates': len(index), 'overrides': len(overrides), 'remaining_for_inspection': len(queue)}, ensure_ascii=False))


if __name__ == '__main__':
    audit()
