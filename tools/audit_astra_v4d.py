"""Prepare output-bound audit queues; retain prior verdicts only for identical IO."""
from __future__ import annotations
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.compare_astra_v4d import OUT, SOURCE, save
from tools.character_clarification_experiment import digest, jd


def prepare():
    old_path = SOURCE/'report.json'
    old = json.loads(old_path.read_text())
    historical = {}
    for c in old['cases']:
        key = (c['evidence']['input_sha256'], c['review']['output_sha256'])
        rv = c['review']
        if rv['status'] == 'observation':
            continue
        if key in historical:
            assert historical[key]['review']['status'] == rv['status']
        historical[key] = c
    m = json.loads((OUT/'manifest.json').read_text())
    cases = {c['id']:c for c in m['cases']}
    rows = [json.loads(s) for s in (OUT/'generations.jsonl').read_text().splitlines()]
    unique = {}
    for row in rows:
        case = cases[row['case_id']]
        if row.get('error') or case['cohort']=='guard':
            continue
        key = (row['input_sha256'], row['output_sha256'])
        candidate = jd(list(key))[:24]
        if candidate not in unique:
            record = {'candidate_id':candidate, 'case_id':case['id'],
                      'input_sha256':key[0], 'output_sha256':key[1],
                      'utterance':case['utterance'], 'source':case['target'],
                      'context':case['context'], 'reference':case.get('reference',''),
                      'review_points':case.get('review_points',''), 'output':row['final_text'],
                      'checks':row.get('checks',[]), 'records':[]}
            if key in historical:
                record['historical_review'] = historical[key]['review']
                record['historical_record'] = historical[key]['id']
            unique[candidate] = record
        unique[candidate]['records'].append(row['id'])
    save(OUT/'audit-candidates.json', {'generations_sha256':digest((OUT/'generations.jsonl').read_text()),
        'historical_report_sha256':digest(old_path.read_text()), 'candidates':list(unique.values())})
    print(json.dumps({'records':len(rows),'unique':len(unique),
        'historical_identical':sum('historical_review' in c for c in unique.values()),
        'new_unique':sum('historical_review' not in c for c in unique.values())}))


def apply():
    prepare()
    data = json.loads((OUT/'audit-candidates.json').read_text())
    plan = json.loads((OUT/'review-plan.json').read_text())
    from tools.build_character_clarification_report import load_reviews
    blind, _ = load_reviews(OUT, plan)
    manual_path = OUT/'codex-new-output-reviews.json'
    manual = json.loads(manual_path.read_text()) if manual_path.exists() else {}
    overrides = {}
    unreviewed = []
    for c in data['candidates']:
        key = c['candidate_id']
        old = blind[key]
        if key in manual:
            item = manual[key]
            assert item['output_sha256'] == c['output_sha256']
            assert item['input_sha256'] == c['input_sha256']
            rv = {'status':item['status'], 'note':item['note'], 'issues':item.get('issues',[]),
                  'method':'Codex 本次逐例复核新输出；参与实验，不是独立人工'}
        elif 'historical_review' in c:
            prior = c['historical_review']
            rv = {k:prior.get(k,[]) for k in ['status','note','issues']}
            rv['method'] = '输入与输出 SHA256 均相同，沿用历史已复核结论；本次盲评仍完整保留'
            rv['historical_record'] = c['historical_record']
        else:
            unreviewed.append(key)
            continue
        rv.update(input_sha256=c['input_sha256'], output_sha256=c['output_sha256'],
                  original_review=old, reviewer='Codex（参与实验，非独立人工）')
        overrides[key] = rv
    save(OUT/'audit-overrides.json', {'source_sha256':data['generations_sha256'],
        'historical_report_sha256':data['historical_report_sha256'],
        'reviews':overrides, 'new_outputs_without_codex_review':unreviewed,
        'method':'先进行模型盲评；相同输入输出沿用已验证结论，其余按本次人工编写的逐例 Codex 记录复核。'})
    print(json.dumps({'audited_unique':len(overrides),'unreviewed_new':len(unreviewed)}))


if __name__ == '__main__':
    apply() if '--apply' in sys.argv else prepare()
