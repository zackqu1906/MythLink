"""Experimental measurement contracts, not application behavior changes."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.check_fine_edit_router import experiment_cases, run_case
from tools.fine_edit_experiment import fine_prompt, parse_fine_response
from proximic_ring.text_processing.model import LLMSettings


def test_frozen_annotations_match_inputs_and_parent_coverage():
    cases = experiment_cases()
    assert len(cases) == 168
    assert sum(c['expected_fine_tasks'] is not None for c in cases) == 162


@pytest.mark.parametrize('raw,target', [
    ('分类：修改\n正文：新', '旧'),
    ('分类：替换指定内容、替换指定内容\n正文：新', '旧'),
    ('分类：替换指定内容\n正文：新', ''),
    ('分类：无可执行编辑\n正文：旧', '旧'),
    ('正文：新', '旧'),
])
def test_invalid_fine_labels_are_not_silently_repaired(raw, target):
    with pytest.raises(ValueError):
        parse_fine_response(raw, target)


def test_multiline_and_empty_body_preserved():
    assert parse_fine_response('分类：分段排版\n正文：\n  甲\n乙  \n', '旧') == (['layout'], '  甲\n乙  \n')
    assert parse_fine_response('分类：清空全文\n正文：', '旧') == (['clear'], '')


@pytest.mark.parametrize('arm,label', [('fine', '替换指定内容'), ('coarse', '修改')])
def test_same_input_one_http_call_no_labels_or_credentials_logged(monkeypatch, arm, label):
    requests = []
    def respond(request, **kwargs):
        requests.append(json.loads(request.data))
        response = io.BytesIO(json.dumps({'status': 'completed', 'output': [{'type': 'message',
            'content': [{'type': 'output_text', 'text': f'分类：{label}\n正文：我今天想喝牛奶。'}]}]}).encode())
        response.status = 200
        return response
    monkeypatch.setattr('tools.check_fine_edit_router.urlopen', respond)
    settings = LLMSettings(enabled=True, provider='volcengine', model='deepseek-test',
                           base_url='https://example.test/api/v3', api_key='secret-test-key', api_key_env='')
    case = next(c for c in experiment_cases() if c['id'] == 'precise-replace')
    result = run_case(case, arm, settings)
    assert 'error' not in result and len(requests) == 1
    assert set(json.loads(requests[0]['input'][1]['content'][0]['text'])) == {'utterance', 'editable_text', 'screen_context'}
    assert 'secret-test-key' not in json.dumps(result)
    assert result['coarse_exact'] is True
    if arm == 'fine':
        assert result['fine_exact'] is True
        assert '【修改】' not in fine_prompt('原文')
        assert '【润色' not in fine_prompt('原文')
