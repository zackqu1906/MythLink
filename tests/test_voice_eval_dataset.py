"""Corpus integrity and selection contracts; no model calls or desktop interaction."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import voice_eval_dataset as corpus


@pytest.fixture(scope='module')
def master():
    return corpus.load_master()


def by_id(master, case_id):
    return next(c for c in master['cases'] if c['id'] == case_id)


def test_migration_preserves_every_source_record_and_frozen_file(master):
    sources = {}
    all_available = True
    for source in master['sources']:
        path = corpus.ROOT / source['file']
        # Historical reports are intentionally not required by a clean checkout.
        # The master embeds all inputs, truth and provenance; verify local sources
        # wherever they are present without making the corpus depend on them.
        if not path.exists():
            all_available = False
            continue
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
        data = json.loads(path.read_text())
        rows = data if isinstance(data, list) else data.get('cases', [])
        if isinstance(rows, list):
            sources[source['file']] = {r['id']: r for r in rows}
    covered = set()
    for c in master['cases']:
        for source in c['provenance']:
            if source['file'] not in sources:
                continue
            row = sources[source['file']][source['case_id']]
            assert corpus.fingerprint(row) == source['record_sha256']
            covered.add((source['file'], source['case_id']))
            context = deepcopy(row.get('context', {}))
            context.setdefault('messages', [])
            inp = {'target': row.get('target', row.get('source_text', '')), 'context': context,
                   'utterances': [s['instruction'] for s in row['steps']] if 'steps' in row else [row['utterance']]}
            amendments = c['annotation']['input_amendments']
            if amendments:
                assert inp['utterances'][0] == amendments[0]['before']
                inp['utterances'][0] = amendments[0]['after']
            assert inp == c['input']
    expected = {(file, cid) for file, rows in sources.items() for cid in rows
                if file != 'docs/reports/character-clarification-2026-11-16/dataset-errata.json'}
    assert covered == expected
    if all_available:
        assert len(covered) == 1514


def test_user_requested_dictation_exclusion_and_complete_editing_oracles(master):
    binary = corpus.select_cases(master, tasks=['binary'])
    editing = corpus.select_cases(master, tasks=['editing'])
    assert {c['labels']['intent'] for c in binary} == {'dictation', 'edit'}
    assert all(c['labels']['intent'] != 'dictation' for c in editing)
    assert all(len(c['editing_expectations']) == len(c['input']['utterances']) for c in editing)
    for c in editing:
        for e in c['editing_expectations']:
            assert e['requirements']
            assert e['reference_text'] is not None or e['checks']
    assert len(master['cases']) == 699
    assert len(binary) == 697 and len(editing) == 538


def test_six_class_is_multilabel_and_has_no_dictation(master):
    six = corpus.select_cases(master, tasks=['six_class'])
    assert len(six) == 536
    assert all(c['labels']['intent'] == 'edit' and c['labels']['edit_tasks'] for c in six)
    assert set(by_id(master, 'new-combined-02-edit')['labels']['edit_tasks']) == {'modify', 'format', 'insert'}
    assert by_id(master, 'new-cancel-07-edit')['labels']['edit_tasks'] == ['format']


def test_injected_candidates_do_not_become_truth(master):
    c = by_id(master, 'cancel-translation')
    assert c['labels']['edit_tasks'] == ['modify']
    assert any(p['candidate_tasks'] == ['modify', 'translate'] for p in c['provenance'])
    assert by_id(master, 'ocr-not-authority')['labels']['edit_tasks'] == ['polish']


def test_clarification_roles_and_chinese_tag_alias(master):
    all_char = corpus.select_cases(master, tags=['释字'])
    assert all_char == corpus.select_cases(master, tags=['character_clarification'])
    positive = corpus.select_cases(master, tasks=['editing'], tags=['释字'], clarification_role='positive')
    assert by_id(master, 'fresh-modify-spelling') in positive
    assert by_id(master, 'dictation-name') not in positive
    assert by_id(master, 'clarify-dev-079') not in positive  # quote explanation verbatim
    assert by_id(master, 'clarify-dev-079')['labels']['edit_tasks'] == ['insert']
    assert by_id(master, 'clarify-dev-062')['special_annotations']['character_clarification']['role'] == 'negative'


def test_previous_holdout_and_known_errata_are_not_silently_reused(master):
    c = by_id(master, 'clarify-hol-003')
    assert c['split'] == 'regression'
    assert any(p['original_split'] == 'holdout' for p in c['provenance'])
    assert c['input']['utterances'] == ['时事是实际的实，时间的时']
    assert c['annotation']['input_amendments'][0]['before'] != c['input']['utterances'][0]


def test_reference_examples_do_not_contradict_mechanical_checks(master):
    for c in master['cases']:
        for e in c['editing_expectations']:
            text = e['reference_text']
            if text is None:
                continue
            checks = e['checks']
            assert all(s in text for s in checks.get('contains', [])), c['id']
            assert all(s.casefold() in text.casefold() for s in checks.get('contains_ci', [])), c['id']
            assert not any(s in text for s in checks.get('excludes', [])), c['id']
            assert len(text) <= checks.get('max_chars', len(text)), c['id']
    assert by_id(master, 'new-reported-06-edit')['editing_expectations'][0]['reference_text'] == ''


def test_all_binary_contrast_pairs_preserved(master):
    pairs = {}
    for c in master['cases']:
        for pair in c['relations']['contrast_pair_ids']:
            pairs.setdefault(pair, []).append(c)
    assert len(pairs) == 90
    assert all(len(cs) == 2 and {c['labels']['intent'] for c in cs} == {'dictation', 'edit'} for cs in pairs.values())


def test_model_input_never_exposes_answers_or_metadata(master):
    c = by_id(master, 'precise-replace')
    inp = corpus.model_input(c)
    assert set(inp) == {'utterance', 'editable_text', 'screen_context'}
    assert inp == {'utterance': '把咖啡改成牛奶', 'editable_text': '我今天想喝咖啡。', 'screen_context': {'messages': []}}
    inp['screen_context']['messages'].append({'speaker': 'self', 'text': 'mutated'})
    assert c['input']['context']['messages'] == []


def test_multiturn_uses_actual_outputs_and_not_gold_references(master):
    c = by_id(master, 'ablation-T15')
    assert not c['applicability']['binary'] and not c['applicability']['six_class']
    with pytest.raises(ValueError, match='actual previous'):
        corpus.model_input(c, step=1)
    assert corpus.model_input(c, step=1, previous_output='wrong previous output')['editable_text'] == 'wrong previous output'
    assert corpus.model_input(c, step=1, previous_output='')['editable_text'] == ''
    with pytest.raises(ValueError, match='sequences'):
        corpus.legacy_cases([c], 'editing')


@pytest.mark.parametrize('filters', [
    {'tags': ['typo']}, {'tasks': ['six']}, {'ids': ['missing-id']},
    {'intent': 'unknown'}, {'tags': ['释字'], 'exclude_tags': ['character_clarification']},
    {'source': 'missing.json'},
])
def test_bad_selectors_fail_instead_of_silently_emptying(master, filters):
    with pytest.raises(ValueError):
        corpus.select_cases(master, **filters)


@pytest.mark.parametrize('mutation', [
    lambda c: c['applicability'].update(editing=False),
    lambda c: c['labels'].update(intent='dictation'),
    lambda c: c['labels'].update(edit_tasks=['unknown']),
    lambda c: c.update(editing_expectations=[]),
    lambda c: c['special_tags'].append('unknown'),
    lambda c: c['input'].update(answer='forbidden'),
    lambda c: c['input']['utterances'].append('extra turn'),
    lambda c: c['relations']['related_case_ids'].append('missing-case'),
])
def test_invalid_annotations_rejected(master, mutation):
    data = deepcopy(master)
    mutation(by_id(data, 'precise-replace'))
    with pytest.raises(ValueError):
        corpus.validate_master(data)


def test_cli_subsets_and_legacy_loader_compatibility(master, tmp_path):
    assert corpus.main(['refresh', '--output-dir', str(tmp_path/'subsets')]) == 0
    six = json.loads((tmp_path/'subsets/six_class_accuracy.json').read_text())
    assert six['master_sha256'] == corpus.fingerprint(master)
    assert six['count'] == len(six['cases']) == 536
    routing = tmp_path/'routing.json'
    assert corpus.main(['export', '--task', 'binary', '--format', 'routing', '--output', str(routing)]) == 0
    from tools.clef_cases import load_cases
    assert len(load_cases(routing)) == 697
    editing = tmp_path/'editing.json'
    assert corpus.main(['export', '--task', 'editing', '--kind', 'single', '--format', 'editing', '--output', str(editing)]) == 0
    from tools.check_automatic_edit_generation import load_cases as load_editing
    assert len(load_editing(editing)) == 536
    assert json.loads(editing.with_suffix('.manifest.json').read_text())['master_revision'] == master['revision']


def test_updating_master_rejects_duplicates_and_increments_revision(master, tmp_path):
    path = tmp_path/'master.json'
    corpus.write_json(path, master)
    original = path.read_bytes()
    case = deepcopy(by_id(master, 'precise-replace'))
    case['id'] = 'new-case'
    case['aliases'] = ['new-case']
    with pytest.raises(ValueError, match='Duplicate input'):
        corpus.maintain(path, [case], update=False, note='duplicate should be rejected')
    assert path.read_bytes() == original
    case['input']['target'] = '我想喝咖啡。'
    case['editing_expectations'][0]['reference_text'] = '我想喝牛奶。'
    result = corpus.maintain(path, [case], update=False, note='new fixture')
    assert result['revision'] == master['revision']+1
    assert len(result['cases']) == len(master['cases'])+1
    case['annotation']['label_reason'] = 'Updated annotation evidence.'
    case['input']['utterances'] = ['把咖啡换成牛奶']
    result = corpus.maintain(path, [case], update=True, note='clarify label rationale')
    assert result['revision'] == master['revision']+2
    assert len(result['change_log']) == len(master.get('change_log', []))+2
    updated = by_id(result, 'new-case')
    assert updated['annotation']['input_amendments'][-1]['before']['utterances'] == ['把咖啡改成牛奶']
    assert updated['annotation']['input_amendments'][-1]['after']['utterances'] == ['把咖啡换成牛奶']
    assert result['change_log'][-1]['annotation_changes'][0]['previous_labels'] == case['labels']


def test_export_cannot_overwrite_master(master, tmp_path):
    path = tmp_path/'master.json'
    corpus.write_json(path, master)
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        corpus.main(['--master', str(path), 'export', '--task', 'binary', '--output', str(path)])
    assert path.read_bytes() == before


def test_saved_context_survives_export_and_participates_in_identity(master):
    cases = corpus.select_cases(master, tasks=['editing'], tags=['保存上下文'])
    assert len(cases) == 24
    for case, exported in zip(cases, corpus.legacy_cases(cases, 'editing')):
        model_input = corpus.model_input(case)
        assert model_input['saved_context'] == case['input']['saved_context'] == exported['saved_context']
        assert set(model_input) == {'utterance', 'editable_text', 'screen_context', 'saved_context'}
        changed = deepcopy(case['input'])
        changed['saved_context'] += '另一条资料'
        assert corpus.fingerprint(changed) != case['input_sha256']
    assert sum(c['special_annotations']['saved_context']['expected_source'] == 'screen' for c in cases) == 4


def test_saved_context_pairs_only_change_instruction(master):
    for first, second in [(1, 2), (5, 6), (11, 12), (13, 14)]:
        a, b = (by_id(master, f'saved-context-{n:03}') for n in (first, second))
        assert {k:v for k,v in a['input'].items() if k != 'utterances'} == {
            k:v for k,v in b['input'].items() if k != 'utterances'}
        assert a['special_annotations']['saved_context']['expected_source'] == 'saved'
        assert b['special_annotations']['saved_context']['expected_source'] == 'screen'


def test_saved_news_email_cases_use_requested_utterance_and_independent_sources(master):
    cases = corpus.select_cases(master, tasks=['editing'], tags=['saved_context', 'news_summary'])
    assert {c['id'] for c in cases} == {f'saved-news-{i:03}' for i in range(1, 5)}
    for case in cases:
        assert case['input']['utterances'] == ['我写一封邮件，把刚才的摘要分享给团队']
        assert case['special_annotations']['saved_context']['expected_source'] == 'saved'
        assert case['input']['saved_context'] != case['input']['context']['context']
        assert case['labels']['edit_tasks'] == ['compose']
        assert case['split'] == 'development'
        assert len(case['editing_expectations'][0]['requirements']) >= 2


def test_saved_context_rejects_image_or_structured_payload(master):
    broken = deepcopy(master)
    case = by_id(broken, 'saved-context-001')
    case['input']['saved_context'] = {'text': '文字', 'image': 'hidden.png'}
    case['input_sha256'] = corpus.fingerprint(case['input'])
    with pytest.raises(ValueError, match='saved_context must be text'):
        corpus.validate_master(broken)


def test_historical_semantic_audit_remains_bound_to_reviewed_cases(master):
    audit = json.loads((corpus.ROOT/'tools/data/voice_eval_annotation_audit_20261117.json').read_text())
    assert audit['master_revision_after'] == 2 <= master['revision']
    if master['revision'] == audit['master_revision_after']:
        assert audit['master_sha256_after'] == corpus.fingerprint(master)
    added_later = {cid for change in master.get('change_log', [])
                   if change['revision'] > audit['master_revision_after'] and change['action'] == 'add'
                   for cid in change['case_ids']}
    assert audit['reviewed_cases'] == len(audit['cases']) == len(master['cases']) - len(added_later)
    assert {row['id'] for row in audit['cases']} == {c['id'] for c in master['cases']} - added_later
    assert audit['binary_label_changes'] == audit['six_task_label_changes'] == 0
    assert audit['corrected_cases'] == sum(bool(row['changes']) for row in audit['cases']) == 410
    for row in audit['cases']:
        c = by_id(master, row['id'])
        review = c['annotation']['semantic_review']
        assert row['input_sha256'] == c['input_sha256']
        assert row['semantic_sha256'] == review['semantic_sha256'] == corpus.semantic_fingerprint(c)
        assert row['result'] == review['result']
        assert review['independent_human_review'] is False
        for key, change in row['changes'].items():
            assert change['after'] == c[key]
            assert change['before'] != change['after']
        assert 'labels' not in row['changes']


def test_name_introduction_is_clarification_negative_but_never_editing(master):
    c = by_id(master, 'v-dictation-name')
    assert c['labels']['intent'] == 'dictation'
    assert c['special_annotations']['character_clarification'] == {
        'present': True, 'role': 'negative', 'mechanism': '字形拆解'}
    assert c in corpus.select_cases(master, tasks=['binary'], tags=['释字'])
    assert c not in corpus.select_cases(master, tasks=['editing'], tags=['释字'])
    assert c['editing_expectations'] == []


@pytest.mark.parametrize(('case_id', 'mechanism', 'tag'), [
    ('new-language-05-edit', '字母拼读', 'spelling'),
    ('fresh-modify-spelling', '偏旁部首', 'radical'),
    ('new-correction-08-dictation', '字形拆解', 'decomposition'),
])
def test_clarification_mechanism_follows_utterance_not_fixture_name(master, case_id, mechanism, tag):
    c = by_id(master, case_id)
    assert c['special_annotations']['character_clarification']['mechanism'] == mechanism
    assert tag in c['special_tags']


def test_length_and_translation_checks_only_enforce_requested_constraints(master):
    caps = {c['id']: e['checks']['max_chars'] for c in master['cases']
            for e in c['editing_expectations'] if 'max_chars' in e['checks']}
    assert caps == {'v-edit-condense': 20, 'v5-combo-modify-polish-condense': 30}
    e = by_id(master, 'v4-translate-omit-language-zh')['editing_expectations'][0]
    assert not e['checks'].get('contains') and not e['checks'].get('contains_ci')
    assert e['reference_text'] and e['requirements']
    assert '最迟于周五' in by_id(master, 'v4-translate-omit-language-en')['editing_expectations'][0]['reference_text']
    assert 'antes de sexta-feira' in by_id(master, 'fresh-direct-translation')['editing_expectations'][0]['reference_text']
    c = by_id(master, 'new-language-04-edit')
    assert c['editing_expectations'][0]['reference_text'].replace('\n', '') == c['input']['target']


def test_conflicting_clarification_roles_rejected_without_relying_on_review_hash(master):
    data = deepcopy(master)
    c = by_id(data, 'fresh-modify-spelling')
    c['annotation'].pop('semantic_review')
    c['special_tags'].append('character_clarification_negative')
    with pytest.raises(ValueError, match='Contradictory clarification role tags'):
        corpus.validate_master(data)
    c['special_tags'].remove('character_clarification_negative')
    c['labels']['edit_tasks'] = ['insert']
    c['labels']['fine_edit_tasks'] = None
    c['applicability']['fine_22'] = False
    c['exclusion_reasons']['fine_22'] = 'No fine label in this mutation.'
    with pytest.raises(ValueError, match='Positive clarification needs an effective correction'):
        corpus.validate_master(data)


def test_changed_grading_invalidates_old_review_even_when_input_is_unchanged(master, tmp_path):
    data = deepcopy(master)
    c = by_id(data, 'precise-replace')
    c['editing_expectations'][0]['requirements'].append('A changed grading requirement.')
    assert c['input_sha256'] == by_id(master, c['id'])['input_sha256']
    with pytest.raises(ValueError, match='Semantic review is stale'):
        corpus.validate_master(data)
    path = tmp_path/'master.json'
    corpus.write_json(path, master)
    result = corpus.maintain(path, [c], update=True, note='Test grading change needs re-review.')
    updated = by_id(result, c['id'])
    assert 'semantic_review' not in updated['annotation']
    assert updated['annotation']['review_note']
    assert by_id(result, 'fresh-modify-spelling')['annotation']['semantic_review'] == by_id(master, 'fresh-modify-spelling')['annotation']['semantic_review']
