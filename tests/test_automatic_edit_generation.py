import json

import pytest

from proximic_ring.text_processing.automatic_prompts import EDIT_COMBINATION_RULES, EDIT_PROMPT_MODULES, automatic_system_prompt
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.model import LLMSettings
from proximic_ring.text_processing.task_routing import EditPlan
from tools.check_automatic_edit_generation import RecordedResponse, check_output, load_cases, metrics, write_report


@pytest.mark.parametrize('utterance,output', [
    ('先插入乙，再把第一句改成丙', '丙。甲。'),
    ('先把第一句改成丙，再插入乙', '乙。丙。'),
])
def test_ordered_utterance_reaches_one_composed_generation_unchanged(monkeypatch,utterance,output):
    calls=[]
    def request(settings,**kwargs):
        calls.append(kwargs);return {'modified_text':output}
    processor=OpenAICompatibleTextProcessor()
    monkeypatch.setattr(processor,'_request_chat',request)
    # Reverse plan order deliberately: the set of classes must not rewrite the
    # spoken operation order or create separate model calls for the two steps.
    result,_=processor.process_task_with_trace(utterance,'edit',LLMSettings(enabled=True,model='test',api_key_env=''),
                                              '甲。',{},edit_plan=EditPlan(('insert','modify')))
    assert result==output and len(calls)==1
    assert json.loads(calls[0]['user_content'])['utterance']==utterance
    prompt=calls[0]['system_prompt']
    assert prompt.count(EDIT_COMBINATION_RULES)==1
    assert EDIT_PROMPT_MODULES['modify'] in prompt and EDIT_PROMPT_MODULES['insert'] in prompt
    assert EDIT_PROMPT_MODULES['translate'] not in prompt


def test_every_edit_plan_uses_same_shared_composition_and_only_selected_modules():
    from itertools import combinations
    for size in range(1,len(EDIT_PROMPT_MODULES)+1):
        for tasks in combinations(EDIT_PROMPT_MODULES,size):
            prompt=automatic_system_prompt('edit',EditPlan(tasks))
            assert prompt.count(EDIT_COMBINATION_RULES)==1
            for name,module in EDIT_PROMPT_MODULES.items():
                assert (module in prompt)==(name in tasks)
    assert EDIT_COMBINATION_RULES not in automatic_system_prompt('dictation')


def test_recorded_http_response_is_consumed_unchanged_and_saved():
    from io import BytesIO
    from time import perf_counter
    raw=b'{"output":[],"usage":{"input_tokens":12,"output_tokens":3}}'
    response=BytesIO(raw);record={}
    with RecordedResponse(response,record,perf_counter()) as proxy:
        assert proxy.read()==raw
    assert response.closed and record['response']==json.loads(raw)
    assert record['http_latency_s']>=0


def test_constraint_checks_do_not_claim_semantic_review(tmp_path):
    checks=check_output({'contains_ci':['north'],'excludes':['south'],'max_chars':20},'North gate')
    record={'id':'x','utterance':'<指令>','target':'原文','edit_plan':{'tasks':['translate']},'latency_s':.2,
            'final_text':'North gate','checks':checks,'constraint_passed':all(c['passed'] for c in checks),
            'review':{'status':'pending'}}
    m=metrics([record]);assert m['constraint_passed']==1 and m['review_passed']==0 and m['review_pending']==1
    report={'scope':'test','prompt_version':'test','routing_version':'test','model':'test','cases':[record]}
    path=tmp_path/'report.json';write_report(report,path)
    assert '&lt;指令&gt;' in path.with_suffix('.html').read_text()
    assert json.loads(path.read_text())['cases'][0]['checks']==checks


def test_fixture_has_only_valid_source_tasks_and_all_pair_combinations():
    from itertools import combinations
    cases=load_cases('tools/data/automatic_edit_order_cases.json')
    covered={frozenset(pair) for c in cases for pair in combinations(c['edit_plan']['tasks'],2)}
    assert {frozenset(pair) for pair in combinations(EDIT_PROMPT_MODULES,2)}<=covered
