import json
from types import SimpleNamespace

import pytest

from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_llm_context import (
    CONTEXT_CHARS, ContinuationPredictor, apply_predictions, local_prediction_settings, parse_predictions,
)


def test_prediction_request_only_contains_committed_context_and_has_small_output_budget():
    captured = []
    class Processor:
        def _ensure_local_server(self, settings):
            captured.append(('start', settings.provider))
        def _request_chat(self, settings, **kwargs):
            captured.append(('request', kwargs))
            return '{"continuations":["去公园","吃饭"]}'
    context = '前' * 80 + '我想'
    assert ContinuationPredictor(Processor()).predict(context, local_prediction_settings()) == ['去公园', '吃饭']
    request = captured[-1][1]
    assert json.loads(request['user_content']) == {'before_cursor': context[-CONTEXT_CHARS:]}
    assert request['max_tokens'] == 128 and request['temperature'] == 0
    assert 'target' not in request['user_content'] and captured[0] == ('start', 'local')


def test_parser_rejects_instructions_probabilities_punctuation_duplicates_and_wrong_schema():
    assert parse_predictions('```json\n{"continuations":["去公园","去公园","吃饭","API",null,"一二三四五六七","，"]}\n```') == ['去公园', '吃饭']
    with pytest.raises(ValueError):
        parse_predictions('{"answer":"去公园"}')
    with pytest.raises(ValueError):
        parse_predictions('请直接输入去公园')


def dictionary():
    chars = list('一二三四五六七八九十')
    return StrokeDictionary(dict(templates=[], ranks={c: i for i, c in enumerate(chars + ['去', '吃'])},
        entries=[[c, 'h', 1] for c in chars] + [['去', 'hshzn', 1], ['吃', 'szhphz', 1]]))


def test_context_can_promote_eligible_candidate_outside_truncated_list_but_not_wrong_stroke():
    d = dictionary()
    normal = d.candidates('h', limit=3)
    assert '去' not in normal
    ink = [stroke_evidence('h')]
    candidates, matches, hints = apply_predictions(normal, [], ['吃饭', '去公园'],
        dictionary=d, code='h', evidence=ink, fuzzy=False, policy=FuzzyPolicy(), allowed={'一', '二', '三', '去', '吃'})
    assert candidates[0] == '去' and '吃' not in candidates
    assert candidates[1:] == normal and hints == {'去': '去公园'}
    assert matches[0]['code'] == 'hshzn' and ink[0]['category'] == 'h'


def test_prediction_does_not_bypass_scope_or_overrule_longer_ink():
    d = dictionary()
    normal = ['一']
    result = apply_predictions(normal, [], ['去公园'], dictionary=d, code='h',
        evidence=[stroke_evidence('h')], fuzzy=True, policy=FuzzyPolicy(), allowed={'一'})
    assert result == (normal, [], {})
    result = apply_predictions(normal, [], ['去公园'], dictionary=d, code='hshz',
        evidence=[stroke_evidence(c) for c in 'hshz'], fuzzy=False, policy=FuzzyPolicy(), allowed=None)
    assert result == (normal, [], {})


def test_empty_ink_suggests_next_character_and_deduplicates_phrase_initials():
    candidates, matches, hints = apply_predictions(['一'], [], ['去公园', '去学校', '吃饭'],
        dictionary=dictionary(), code='', evidence=[], fuzzy=False, policy=FuzzyPolicy(), allowed=None)
    assert candidates == ['去', '吃', '一'] and hints == {'去': '去公园', '吃': '吃饭'}


def test_raw_trial_reconstruction_uses_actual_committed_prefix_and_rejects_misalignment(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    from evaluate_stroke_llm import replay_cases, select_contexts
    events = [dict(kind='stroke', recognized='h', stroke_index=0, character='一', position=0, elapsed_s=1.),
              dict(kind='commit', character='一', delivered=True, elapsed_s=2.),
              dict(kind='stroke', recognized='h', stroke_index=0, character='去', position=1, elapsed_s=3.),
              dict(kind='commit', character='去', delivered=True, elapsed_s=4.)]
    raw = [dict(result=dict(category='h'), code='h'), dict(result=dict(category='h'), code='h')]
    rows = replay_cases(dict(events=events, actual='一去'), raw, 'trial')
    assert [r['context'] for r in rows] == ['', '一'] and rows[1]['target'] == '去'
    assert rows[1]['context_at_s'] == 2.
    assert select_contexts(rows, 1) == ['一']
    with pytest.raises(ValueError):
        replay_cases(dict(events=events, actual='错误'), raw, 'trial')


def test_local_ui_promotes_short_ink_without_leaking_task_and_logs_commit(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window
    from test_stroke_trial_correction import wait_for
    app, window = load_window(tmp_path, monkeypatch)
    requests = []
    try:
        def predict(context, settings):
            requests.append(context)
            return ['去公园', '吃饭']
        window.language.worker.predictor = SimpleNamespace(predict=predict)
        window.target_input.setPlainText('不应提供给模型')
        window.editor.setText('我想')
        window.llm_toggle.setChecked(True)
        window.add_manual('h')
        wait_for(app, lambda: bool(window.language.phrases))
        assert requests == ['我想'] and window.candidates[0] == '去'
        assert '联想「去公园」' in window.buttons[0].visual['status']
        window.commit(0)
        assert window.editor.text() == '我想去' and not window.code
        window.llm_toggle.setChecked(False)
        assert not window.language.phrases and window.language.status == '已关闭'
        window.log.flush()
        logs = [json.loads(line) for line in window.log_path.read_text(encoding='utf-8').splitlines()]
        assert any(r['kind'] == 'llm_context_commit' and r['phrase'] == '去公园' for r in logs)
    finally:
        window.close()
        app.processEvents()


def test_async_prediction_freezes_during_selection_and_discards_old_context(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window
    from test_stroke_trial_correction import wait_for
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.language.worker.predictor = SimpleNamespace(predict=lambda context, settings: ['去公园'])
        window.editor.setText('我想')
        window.llm_toggle.setChecked(True)
        window.add_manual('h')
        first = window.candidates[0]
        window.show_selection_state(dict(phase='selecting', progress=100, direction='center'))
        wait_for(app, lambda: window.language.deferred is not None)
        assert window.candidates[0] == first and not window.language.phrases
        generation = window.language.generation
        window.editor.setText('现在')
        window.language.worker.results.put(dict(generation=generation, context='我想',
                                                phrases=['去公园'], elapsed_ms=1., error=''))
        window.language._poll()
        assert not window.language.phrases
        window.show_selection_state(dict(phase='idle', progress=0, direction=None))
        window.llm_toggle.setChecked(False)
        window.language._poll()
        assert not window.language.phrases
    finally:
        window.close()
        app.processEvents()


def test_prediction_failure_keeps_ink_and_candidates_and_does_not_log_response_details(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window
    from test_stroke_trial_correction import wait_for
    app, window = load_window(tmp_path, monkeypatch)
    try:
        def fail(context, settings):
            raise RuntimeError('private endpoint response')
        window.language.worker.predictor = SimpleNamespace(predict=fail)
        window.editor.setText('我想')
        window.add_manual('h')
        before = list(window.candidates)
        window.llm_toggle.setChecked(True)
        wait_for(app, lambda: '暂不可用' in window.language.status)
        assert window.code == 'h' and window.candidates == before
        window.log.flush()
        assert 'private endpoint response' not in window.log_path.read_text(encoding='utf-8')
    finally:
        window.close()
        app.processEvents()


def test_revisiting_committed_context_reuses_prediction_without_another_request(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window
    from test_stroke_trial_correction import wait_for
    app, window = load_window(tmp_path, monkeypatch)
    calls = []
    try:
        def predict(context, settings):
            calls.append(context)
            return ['去公园']
        window.language.worker.predictor = SimpleNamespace(predict=predict)
        window.editor.setText('我想')
        window.llm_toggle.setChecked(True)
        window.add_manual('h')
        wait_for(app, lambda: bool(window.language.phrases))
        window.editor.setText('现在')
        window.editor.setText('我想')
        assert calls == ['我想'] and window.candidates[0] == '去'
        assert '缓存' in window.language.status
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize('observed', ['p', 's', 'ps', 'phs', 'nns', 'nzh'])
def test_per_character_matcher_has_same_eligibility_as_full_dictionary(observed):
    from proximic_ring.touchpad.stroke.stroke_common import common_characters
    d = StrokeDictionary()
    ink = [stroke_evidence(c, dict(category_scores=[dict(category=c, distance=.001)])) for c in observed]
    phrases = list('我你快心宝实学去天了')
    expected = [char for char in phrases if d.fuzzy_candidates(ink, 1, allowed_characters={char})]
    candidates, _, hints = apply_predictions([], [], phrases, dictionary=d,
        code=observed, evidence=ink, fuzzy=True, policy=FuzzyPolicy(), allowed=common_characters())
    assert candidates == expected and list(hints) == expected
