import copy
import json
import pytest

from proximic_ring.voice.history_details import history_details


def record():
    return {"mode": {"routing": "manual_routed_edit", "edit_plan": {"tasks": ["modify"]}},
            "asr": {"final_text": "把周六改成周日", "model": "deepseek-asr-test", "latency_ms": 120,
                    "audio_duration_ms": 2500},
            "context_preparation": {"enabled": True, "status": "ok", "model": "deepseek-test", "latency_ms": 900,
                "system_prompt": "secret-debug-prompt", "raw_output": '{"private_debug":true}',
                "context": {"topic": "会议安排", "context": "对方说明周日开会。", "draft": None},
                "input": {"current_chat": {"messages": [{"speaker": "other", "text": "周日开会。"}]}}},
            "llm": {"requests": [{"mode": "edit", "routing_mode": "manual_routed_edit", "status": "completed",
                "model": "deepseek-test", "latency_ms": 800, "candidate_text": "周日开会。",
                "request_input": {"utterance": "把周六改成周日", "editable_text": "周六开会。", "screen_context": {}},
                "system_prompt": "secret-debug-prompt", "raw_output": '分类：修改\n正文：周日开会。'}]},
            "outcome": {"status": "applied"}}


def results(details):
    return next(section['items'] for section in details['sections'] if section['key'] == 'edit_result')


def routes(details):
    return next(section['items'] for section in details['sections'] if section['key'] == 'decision')


def test_edit_details_only_show_four_ordered_sections_and_leave_saved_evidence_intact(tmp_path):
    saved = record()
    before = copy.deepcopy(saved)
    details = history_details(tmp_path, saved)
    assert [r['key'] for r in details['sections']] == ['decision', 'edit_result', 'screenshot', 'context_preparation']
    assert results(details)[0]['text'] == '周日开会。'
    assert results(details)[0]['metaText'] == 'DeepSeek · 0.80 秒 · 已完成'
    assert routes(details)[0]['classificationText'] == '修改'
    assert results(details)[0]['errorText'] == '' and results(details)[0]['outputVisible']
    assert details['sections'][-1]['items'][0]['text'] == '主题：会议安排\n对方说明周日开会。'
    displayed = json.dumps(details, ensure_ascii=False)
    for hidden in ('inputText', 'system_prompt', 'secret-debug-prompt', 'private_debug', 'editable_text', 'raw_output'):
        assert hidden not in displayed
    assert saved == before


def test_empty_model_output_is_preserved_but_failure_fallback_is_not_called_output(tmp_path):
    saved = record()
    request = saved['llm']['requests'][0]
    request['candidate_text'] = ''
    assert results(history_details(tmp_path, saved))[0]['text'] == '（输出为空）'
    request.update(status='failed', error='HTTP 500 {"internal":"debug"}', candidate_text='原文回退')
    row = results(history_details(tmp_path, saved))[0]
    assert row['errorText'] == '模型服务内部错误（HTTP 500）' and row['metaText'].endswith('未完成')
    assert history_details(tmp_path, saved)['summary'].endswith('编辑未完成')
    assert not row['outputVisible']
    assert 'debug' not in row['text'] and '原文回退' not in row['text']


def test_manual_alternative_results_keep_only_distinct_readable_candidates(tmp_path):
    saved = record()
    request = saved['llm']['requests'][0]
    request['branches'] = [{'validation': 'valid', 'candidate_text': '周日开会。', 'raw_returns': ['secret']},
                           {'validation': 'valid', 'candidate_text': '周日下午开会。'},
                           {'validation': 'invalid', 'candidate_text': '错误结果'}]
    row = results(history_details(tmp_path, saved))[0]
    assert row['alternativesText'] == '其他结果 1：\n周日下午开会。'


def test_closed_and_pending_ocr_are_readable_and_routing_probabilities_are_hidden(tmp_path):
    saved = record()
    saved['context_preparation'] = {'enabled': False, 'status': 'disabled'}
    saved['mode'].update(routing='auto', task_type='edit', router_model='clef-flash', router_input='修改会议时间',
                        router_trace={'task': 'edit', 'probabilities': {'edit': .9}, 'input_state': 'private state'})
    rows = {r['key']: r for r in history_details(tmp_path, saved)['sections']}
    assert rows['decision']['items'][0]['text'] == '编辑：修改'
    assert '未调用整理模型' in rows['context_preparation']['items'][0]['text']
    assert 'probabilities' not in json.dumps(rows) and 'private state' not in json.dumps(rows)
    saved['context_preparation'] = {'enabled': True, 'status': 'pending'}
    rows = {r['key']: r for r in history_details(tmp_path, saved)['sections']}
    assert rows['context_preparation']['items'][0]['text'] == '正在整理屏幕上文…'


@pytest.mark.parametrize('error', [
    '编辑结果缺少分类行或正文标记',
    '模型返回了约定六类之外的编辑类别',
    '编辑任务重复',
    '空原文不能选择修改任务',
    '用户已确认编辑，但模型未选择编辑类别',
    '编辑原文或光标已变化，未执行替换',
])
def test_specific_failure_reason_is_visible_without_replacing_it_with_generic_text(tmp_path, error):
    saved = record()
    saved['llm']['requests'][0].update(status='failed', error=error, candidate_text='原文回退')
    row = results(history_details(tmp_path, saved))[0]
    assert row['errorText'] == error
    route = routes(history_details(tmp_path, saved))[0]
    assert route['classificationText'] == '修改' and route['classificationNote']
    assert not row['outputVisible']


def test_multiple_requests_do_not_borrow_the_latest_classification(tmp_path):
    saved = record()
    saved['llm']['requests'][0].update(status='failed', error='请求超时', raw_output='')
    saved['llm']['requests'].append(dict(saved['llm']['requests'][0], status='completed', error=None,
                                       edit_plan={'tasks': ['polish']}, raw_output='分类：润色\n正文：新'))
    saved['mode']['edit_plan'] = {'tasks': ['polish']}
    details = history_details(tmp_path, saved)
    assert routes(details)[0]['classificationText'] == '润色'
    assert routes(details)[1]['classificationText'] == '未取得分类'
    assert results(details)[1]['errorText'] == '处理超时'


def test_writeback_failure_is_visible_separately_from_successful_model_result(tmp_path):
    saved = record()
    saved['outcome'] = {'status': 'apply_failed', 'error': '编辑原文或光标已变化，未执行替换'}
    details = history_details(tmp_path, saved)
    assert details['outcomeTitle'] == '写入失败'
    assert details['outcomeError'] == saved['outcome']['error']
    assert results(details)[0]['outputVisible']
    assert routes(details)[0]['classificationText'] == '修改'


def test_same_failure_is_not_duplicated_in_outcome_and_model_cards(tmp_path):
    saved = record()
    saved['outcome'] = {'status': 'apply_failed', 'error': '大模型请求超时'}
    saved['llm']['requests'][0].update(status='failed', error='大模型请求超时', raw_output='')
    details = history_details(tmp_path, saved)
    assert details['outcomeError'] == ''
    assert results(details)[0]['errorText'] == '处理超时'


def test_pending_job_failure_and_desktop_abandonment_keep_their_saved_reasons(tmp_path):
    saved = record()
    saved['llm']['requests'][0].update(status='failed', error=None, end_reason='主程序已退出', raw_output='')
    saved['outcome'] = {'status': 'abandoned'}
    saved['feedback'] = [{'action': 'abandoned', 'error': '修改目标快照已经失效'}]
    details = history_details(tmp_path, saved)
    assert results(details)[0]['errorText'] == '主程序已退出'
    assert details['outcomeError'] == '修改目标快照已经失效'


def test_failure_before_model_call_explicitly_has_no_classification(tmp_path):
    saved = record()
    saved['mode']['user_intent'] = 'edit'
    saved['llm']['requests'] = []
    saved['outcome'] = {'status': 'apply_failed', 'error': '输入框未提供可校验的编辑原文'}
    details = history_details(tmp_path, saved)
    assert routes(details)[0]['key'] == 'edit_classification'
    assert routes(details)[0]['classificationText'] == '未取得分类'
    assert results(details)[0]['text'] == '本次未取得编辑结果。'
    assert details['outcomeError'] == '输入框未提供可校验的编辑原文'


@pytest.mark.parametrize('raw', ['', '{"output":null}', '{"output":[{"content":null}]}'])
def test_failed_output_without_classification_does_not_invent_one(tmp_path, raw):
    saved = record()
    saved['llm']['requests'][0].update(status='failed', error='模型输出不完整', raw_output=raw)
    row = routes(history_details(tmp_path, saved))[0]
    assert row['classificationText'] == '未取得分类'
