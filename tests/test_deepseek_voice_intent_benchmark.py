import io
import json

from proximic_ring.text_processing.model import LLMSettings
from tools.check_deepseek_voice_intent import (
    benchmark_cases, build_prompt, run_case, score, stability_subset,
)


def test_previous_cases_are_preserved_and_stability_sample_covers_both_intents():
    cases = benchmark_cases()
    assert len(cases) == 212
    assert sum(c['cohort'] == 'previous_edit_150' for c in cases) == 150
    assert sum(c['cohort'] == 'previous_dictation_56' for c in cases) == 56
    selected = stability_subset(cases)
    assert len(selected) == len({c['id'] for c in selected}) == 36
    assert sum(c['expected_task'] == 'dictation' for c in selected) == 18
    assert {t for c in selected for t in c['expected_tasks']} == {
        'modify', 'insert', 'polish', 'format', 'translate', 'compose'}


def test_real_transport_sends_one_request_without_expected_labels_or_case_metadata():
    case = {'id': 'SECRET_TEST_CASE_ID', 'utterance': '今天天气不错', 'target': '',
            'context': {'messages': []}, 'expected_task': 'dictation', 'expected_tasks': [],
            'cohort': 'LABEL_SOURCE_NOT_FOR_MODEL'}
    calls = []

    class Response(io.BytesIO):
        status = 200

    def opener(request, **kwargs):
        body = json.loads(request.data)
        calls.append(body)
        assert body['thinking'] == {'type': 'disabled'}
        assert body['stream'] is False
        assert body['max_output_tokens'] == 16
        assert 'tools' not in body and 'temperature' not in body
        sent = json.dumps(body, ensure_ascii=False)
        assert case['id'] not in sent and case['cohort'] not in sent
        assert 'expected_task' not in sent
        return Response(json.dumps({'status': 'completed', 'output': [{
            'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': '听写'}]}]}).encode())

    settings = LLMSettings(enabled=True, provider='volcengine', model='deepseek-test',
                           base_url='https://ark.cn-beijing.volces.com/api/v3', api_key_env='')
    record = run_case(case, settings, build_prompt(), phase='main', round_number=1, opener=opener)
    assert len(calls) == 1
    assert record['correct'] and record['raw_output'] == '听写'


def test_request_failures_are_not_scored_as_correct_dictation_or_dropped_from_accuracy():
    result = score([
        {'expected_task': 'dictation', 'actual_task': 'edit', 'correct': False, 'latency_s': 1},
        {'expected_task': 'edit', 'actual_task': 'edit', 'correct': True, 'latency_s': 2},
        {'expected_task': 'dictation', 'correct': False, 'error': 'timeout', 'latency_s': 30},
    ])
    assert result['accuracy'] == 1 / 3
    assert result['errors'] == 1
    assert result['confusion_matrix']['dictation'] == {'dictation': 0, 'edit': 1, 'error': 1}
    assert result['latency']['n'] == 2
    assert result['all_attempt_latency']['max_s'] == 30
