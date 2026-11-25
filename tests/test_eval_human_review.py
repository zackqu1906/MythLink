"""Accepting a result requires a persisted change to the matching grading contract."""
from copy import deepcopy
import hashlib
import json

import pytest

from tools import eval_human_review as human
from tools.eval_report import render, review_run_key
from tools.voice_eval_dataset import DEFAULT_MASTER, fingerprint, load_master


def test_review_is_bound_to_the_response_and_rule_writes_are_idempotent(tmp_path):
    dataset = load_master(DEFAULT_MASTER)
    source = next(c for c in dataset['cases'] if c['editing_expectations'] and c['kind']=='single')
    dataset_file = tmp_path / 'corpus.json'
    human.save(dataset_file, dataset)
    output = '本次人工接受的等义正文'
    case = dict(id=source['id'], instruction='编辑', group='专项', source_text='原文', output=output,
                expected_result='保留原意', evidence={'input_sha256':source['input_sha256']},
                review=dict(status='uncertain',note='等待人工确认',output_sha256=hashlib.sha256(output.encode()).hexdigest()))
    report = dict(title='单次实验',schema_version=1,evaluation={'classification':False},cases=[case])
    html = tmp_path/'report.html'
    html.write_text(render(report)[0])
    human.save(html.with_suffix('.json'),report)
    entry = dict(action='accept',id=case['id'],run=review_run_key(report),input_sha256=source['input_sha256'],
                 output_sha256=case['review']['output_sha256'])
    before = dataset_file.read_bytes()
    with pytest.raises(ValueError,match='更新标准并判对'):
        human.record(html,entry,dataset_file)
    original = source['editing_expectations'][0]
    unchanged = dict(entry, action='rule', requirements=original['requirements'],
                     contract_sha256=fingerprint(original))
    for requirements in ([], ['  '], original['requirements'], ['  '+line+' ' for line in reversed(original['requirements'])]):
        with pytest.raises(ValueError):
            human.record(html,dict(unchanged,requirements=requirements),dataset_file)
    assert not html.with_suffix('.human-reviews.json').exists()
    assert before == dataset_file.read_bytes()
    for field in ['input_sha256','output_sha256','run','id']:
        with pytest.raises(ValueError):
            human.record(html, {**unchanged,field:'other'}, dataset_file)
    rule = dict(entry, action='rule',requirements=['改对目标词并保留其余句意。'],
                contract_sha256=fingerprint(source['editing_expectations'][0]))
    first = human.record(html,rule,dataset_file)
    assert first['requirements']==rule['requirements']
    current = load_master(dataset_file)
    changed = next(c for c in current['cases'] if c['id']==case['id'])
    assert current['cases'] == [changed if c['id']==case['id'] else c for c in dataset['cases']]
    assert changed['editing_expectations'][0]['requirements']==rule['requirements']
    assert changed['editing_expectations'][0]['accepted_alternatives'].count(output)==1
    after = dataset_file.read_bytes()
    assert human.record(html,rule,dataset_file)==first
    # An unrelated new review cannot reuse someone else's unchanged wording.
    with pytest.raises(ValueError,match='请先修改'):
        human.write_rule(dataset_file,case,dict(rule,run='different-run',contract_sha256=fingerprint(changed['editing_expectations'][0])),'other','2026-11-24')
    assert after == dataset_file.read_bytes()
    with pytest.raises(ValueError,match='已被更新'):
        human.record(html,dict(rule,requirements=['覆盖成别的标准']),dataset_file)
    observations = deepcopy(report)
    observations['cases'][0]['observation']=True
    with pytest.raises(ValueError,match='没有参加'):
        human.check_case(observations,entry)
    assert len(human.read(html.with_suffix('.human-reviews.json')))==1


def test_input_edits_keep_the_run_frozen_and_attach_future_rules_to_the_new_input(tmp_path):
    data = load_master(DEFAULT_MASTER)
    source = next(c for c in data['cases'] if c['kind']=='single' and c['editing_expectations'])
    file = tmp_path/'dataset.json'
    human.save(file,data)
    old = dict(id=source['id'],evidence={'input_sha256':source['input_sha256']},output='历史回复')
    original = deepcopy(source['input'])
    first = dict(field='editable_text',value='替换后的输入文本',version=source['input_sha256'])
    target = human.edit_input(file,old,first,'test')
    with pytest.raises(ValueError,match='题库输入已改变'):
        human.edit_input(file,old,dict(first,field='utterance',value='另一项请求'),'test')
    request = human.edit_input(file,old,dict(first,field='utterance',version=target['version'],value='把说法改得简洁。'),'test')
    result = human.linked_input(load_master(file),old)
    assert original==source['input']
    assert result['input']['target']==target['editable_text'] and result['input']['utterances']==[request['utterance']]
    assert result['input']['context']==source['input']['context'] and result['provenance']==source['provenance']
    assert result['input_sha256']==fingerprint(result['input'])
    e = result['editing_expectations'][0]
    lines=['接受保留原意的精简表达。']
    rule=dict(run='input-revision',requirements=lines,contract_sha256=fingerprint(e),input_sha256=source['input_sha256'],
              version=request['version'],output_sha256=hashlib.sha256(old['output'].encode()).hexdigest())
    assert human.write_rule(file,old,rule,'test','2026-11-24')==(lines,False)
    updated = human.linked_input(load_master(file),old)
    assert old['output'] not in updated['editing_expectations'][0].get('accepted_alternatives',[])
    assert 'output_sha256' not in updated['annotation']['grading_confirmations'][-1]


def test_case_removal_checks_preview_version_and_reconciles_retries(tmp_path, monkeypatch):
    data = load_master(DEFAULT_MASTER)
    source = next(c for c in data['cases'] if c['kind'] == 'single' and c['editing_expectations'])
    other = next(c for c in data['cases'] if c['id'] != source['id'])
    other['relations']['related_case_ids'] = [source['id']]
    path = tmp_path/'dataset.json'
    human.save(path, data)
    cid = source['id']
    case = dict(id=cid, evidence={'input_sha256': source['input_sha256']})
    preview = dict(action='prepare_delete', run='test')
    before = path.read_bytes()
    confirm = human.remove_case(path, case, preview, 'test')
    assert confirm['id'] == cid and confirm['utterance'] == source['input']['utterances'][0]
    assert path.read_bytes() == before
    human.edit_input(path, case, dict(field='utterance', value='新口述', version=source['input_sha256']), 'test')
    remove = dict(preview, action='delete_case', version=confirm['version'])
    with pytest.raises(ValueError, match='题目已更新'):
        human.remove_case(path, case, remove, 'test')
    current = human.remove_case(path, case, preview, 'test')
    assert current['utterance'] == '新口述'
    remove['version'] = current['version']
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise OSError('temporarily unavailable')
        patch.setattr(human, 'update_browser_index', fail)
        with pytest.raises(OSError):
            human.remove_case(path, case, remove, 'test')
    assert human.read(path)['removal_views_pending']
    assert human.remove_case(path, case, remove, 'test') == {'removed': True}
    after = load_master(path)
    assert 'removal_views_pending' not in after
    assert [c['id'] for c in after['cases']] == [c['id'] for c in data['cases'] if c['id'] != cid]
    assert not next(c for c in after['cases'] if c['id'] == other['id'])['relations']['related_case_ids']
    assert len([e for e in after['change_log'] if e.get('action') == 'remove_case']) == 1
    assert human.remove_case(path, case, preview, 'another-report') == {'removed': True}
    with pytest.raises(ValueError, match='没有与这次输入'):
        human.remove_case(path, dict(case,evidence={'input_sha256':'mismatch'}), preview, 'another-report')
    assert human.remove_case(path, case, remove, 'test') == {'removed': True}
    assert human.read(path) == after


def test_reconciliation_uses_real_receipts_and_never_invents_a_verdict(tmp_path):
    data = load_master(DEFAULT_MASTER)
    c = next(c for c in data['cases'] if c['kind']=='single' and c['editing_expectations'])
    c['scoring_rule'] = 'semantic'
    exp = c['editing_expectations'][0]
    exp['reference_text'] = '参考成品'
    c['annotation'].pop('semantic_review', None)
    path = tmp_path/'dataset.json'
    human.save(path,data)
    out = '另一种可接受的成品'
    receipt = dict(id=c['id'], input_sha256=c['input_sha256'], output_sha256=hashlib.sha256(out.encode()).hexdigest())
    row = dict(id=c['id'], instruction='润色', source_text='原文', context={}, output=out, expected_result='通顺',
               group='润色', review=dict(status='uncertain',note='需要复核',output_sha256=receipt['output_sha256']),
               evidence={'input_sha256':c['input_sha256']})
    doc = dict(title='报告',schema_version=1,evaluation={'classification':False},cases=[row])
    page = tmp_path/'result.html'
    page.write_text(render(doc)[0])
    human.save(page.with_suffix('.json'),doc)
    receipt.update(run=review_run_key(doc),requirements=['两种表达都可以。'])
    key = fingerprint([receipt[k] for k in ('run','id','input_sha256','output_sha256')])
    item = dict(receipt, basis='human_review', contract_sha256=fingerprint({'scoring_rule':'semantic',**exp}),
                receipt_sha256=fingerprint(receipt), changes={'requirements':['润色后大意一致、没有明显逻辑错误即可。']})
    with pytest.raises(ValueError,match='无法核对'):
        human.reconcile_rule(page,item,path)
    ledger = page.with_suffix('.human-reviews.json')
    human.save(ledger,{key:receipt})
    with pytest.raises(ValueError,match='人工标注已更新'):
        human.reconcile_rule(page,dict(item,receipt_sha256='stale'),path)
    assert human.reconcile_rule(page,item,path)['updated']
    assert not human.reconcile_rule(page,item,path)['updated']
    saved = human.read(path)
    current = next(x for x in saved['cases'] if x['id']==c['id'])
    expected = current['editing_expectations'][0]
    assert expected['requirements']==item['changes']['requirements']
    assert expected['accepted_alternatives'].count(out)==1
    assert sum(e.get('receipt_key')==key for e in saved['change_log'])==1
    with pytest.raises(ValueError,match='期间发生变更'):
        human.reconcile_rule(page,dict(item,changes={'requirements':['过时计划']}),path)
    human.reconcile_rule(page,dict(item,basis='user_policy',contract_sha256=fingerprint({'scoring_rule':'semantic',**expected}),
                                   changes={'requirements':['允许自然措辞；大意相符即可。']}),path)
    assert human.read(ledger)=={key:receipt}
    assert 'receipt_key' not in human.read(path)['change_log'][-1]
