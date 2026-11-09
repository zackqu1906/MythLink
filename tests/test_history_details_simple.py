import copy
import json

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


def test_models_show_outputs_first_and_inputs_as_readable_text_without_debug_data(tmp_path):
    saved = record()
    before = copy.deepcopy(saved)
    details = history_details(tmp_path, saved)
    rows = {r['key']: r for r in details['sections']}
    assert details['sections'][0]['key'] == 'llm_0'
    assert rows['llm_0']['text'] == '周日开会。'
    assert rows['llm_0']['inputText'] == '口述：\n把周六改成周日\n\n原文：\n周六开会。\n\n上文：\n未传入'
    assert rows['llm_0']['metaText'] == 'DeepSeek · 0.80 秒 · 已完成'
    assert rows['context_preparation']['text'] == '主题：会议安排\n对方说明周日开会。'
    assert rows['context_preparation']['inputText'] == '对方：周日开会。'
    displayed = json.dumps(details, ensure_ascii=False)
    for hidden in ('system_prompt', 'secret-debug-prompt', 'private_debug', 'editable_text', 'raw_output'):
        assert hidden not in displayed
    assert saved == before


def test_empty_model_output_is_preserved_but_failure_fallback_is_not_called_output(tmp_path):
    saved = record()
    request = saved['llm']['requests'][0]
    request['candidate_text'] = ''
    assert history_details(tmp_path, saved)['sections'][0]['text'] == '（输出为空）'
    request.update(status='failed', error='HTTP 500 {"internal":"debug"}', candidate_text='原文回退')
    row = history_details(tmp_path, saved)['sections'][0]
    assert row['text'] == '未生成可用结果' and row['metaText'].endswith('未完成')
    assert 'debug' not in row['text'] and '原文回退' not in row['text']


def test_manual_alternative_results_keep_only_distinct_readable_candidates(tmp_path):
    saved = record()
    request = saved['llm']['requests'][0]
    request['branches'] = [{'validation': 'valid', 'candidate_text': '周日开会。', 'raw_returns': ['secret']},
                           {'validation': 'valid', 'candidate_text': '周日下午开会。'},
                           {'validation': 'invalid', 'candidate_text': '错误结果'}]
    row = history_details(tmp_path, saved)['sections'][0]
    assert row['alternativesText'] == '其他结果 1：\n周日下午开会。'


def test_closed_and_pending_ocr_are_readable_and_routing_probabilities_are_hidden(tmp_path):
    saved = record()
    saved['context_preparation'] = {'enabled': False, 'status': 'disabled'}
    saved['mode'].update(routing='auto', task_type='edit', router_model='clef-flash', router_input='修改会议时间',
                        router_trace={'task': 'edit', 'probabilities': {'edit': .9}, 'input_state': 'private state'})
    rows = {r['key']: r for r in history_details(tmp_path, saved)['sections']}
    assert rows['decision']['text'] == '编辑：修改'
    assert '未调用整理模型' in rows['context_preparation']['text']
    assert 'probabilities' not in json.dumps(rows) and 'private state' not in json.dumps(rows)
    saved['context_preparation'] = {'enabled': True, 'status': 'pending'}
    rows = {r['key']: r for r in history_details(tmp_path, saved)['sections']}
    assert rows['context_preparation']['text'] == '正在整理屏幕上文…'
