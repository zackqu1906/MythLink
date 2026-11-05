from concurrent.futures import ThreadPoolExecutor
import copy
import threading

import pytest

from tools.clef_split_routing import requests_for, run_case, start_split
from tools.check_clef_split import metrics, paired_comparison, write_html
from proximic_ring.text_processing.router_prompts import (
    CLEF_ROUTING_PROMPT, EDIT_TASK_CRITERIA, EDIT_TASK_DESCRIPTIONS, routing_schema,
)

CASE = {'id': 'sample', 'utterance': '王字旁的玮', 'target': '联系人张伟。',
        'context': {'messages': []}, 'expected': {'task': 'edit', 'tasks': ['modify']}}


class Router:
    def __init__(self, intent='dictation', tasks=(), error=None, wait=None, entered=None):
        self.intent, self.tasks, self.error, self.wait, self.entered = intent, tasks, error, wait, entered

    def _classify_stage(self, state, schema, cancelled):
        if self.entered: self.entered.set()
        if self.wait: assert self.wait.wait(3), 'test worker timed out'
        if self.error: raise ValueError(self.error)
        answers = {}
        for name, question in schema.items():
            choice = self.intent if name == 'voice_intent' else 'apply' if name in self.tasks else 'skip'
            answers[name] = {'choice': choice, 'probabilities': {k: .9 if k == choice else .1 for k in question['criteria']}}
        return {'model': 'test', 'answers': answers, 'latency_s': .01}


@pytest.mark.parametrize('target', ['', ' \t\n', '空', '联系人张伟。'])
def test_split_preserves_current_prompts_and_hard_source_gate(target):
    case = {**CASE, 'target': target, 'context': {'messages': [{'speaker': 'self', 'text': '张伟'}]}}
    requests = requests_for(case)
    available = set(routing_schema(target=target)) - {'voice_intent'}
    assert set(requests['root']['questions']) == {'voice_intent'}
    assert requests['root']['questions']['voice_intent']['criteria'] == routing_schema()['voice_intent']['criteria']
    assert set(requests['details']['questions']) == available
    assert ('modify' in available) == bool(target.strip())
    for kind in ('root', 'details'):
        assert CLEF_ROUTING_PROMPT in requests[kind]['state']
    for name in available:
        assert EDIT_TASK_DESCRIPTIONS[name] in requests['root']['state']
        assert requests['details']['questions'][name]['criteria'] == EDIT_TASK_CRITERIA[name]
    if not target.strip():
        assert EDIT_TASK_DESCRIPTIONS['modify'] not in requests['root']['state']


def test_dictation_returns_while_details_are_pending_then_logs_conflict():
    release, entered = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        run = start_split(pool, Router(wait=entered), Router(tasks=('modify',), wait=release, entered=entered), requests_for(CASE), CASE['target'])
        try:
            decision, evidence = run.decision()
            assert decision.task == 'dictation' and not evidence['details_used']
            assert not run.details_future.done()
            ready = run.decision_ready_s
        finally:
            release.set()
        audit = run.audit()
        assert audit['drain_s'] >= ready
        assert audit['policy_after_details']['warning_codes'] == ['dictation_with_tasks']
        assert run.details['answers']['modify']['choice'] == 'apply'


def test_unused_details_failure_does_not_block_dictation():
    case = {**CASE, 'expected': {'task': 'dictation', 'tasks': []}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        record = run_case([Router(), Router(error='invalid details')], pool, case, 'parallel')
    assert record['exact_match'] and 'error' not in record
    assert record['details_error'] == 'invalid details'


@pytest.mark.parametrize('tasks,final', [(('modify',), 'edit'), ((), 'dictation')])
def test_edit_waits_for_details_and_reuses_existing_resolution(tasks, final):
    release, entered = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        run = start_split(pool, Router('edit', wait=entered), Router(tasks=tasks, wait=release, entered=entered), requests_for(CASE), CASE['target'])
        assert entered.wait(3)
        assert not run.details_future.done()
        release.set()
        decision, evidence = run.decision()
        assert decision.task == final and evidence['details_used']
        if not tasks: assert evidence['warning_codes'] == ['edit_without_tasks']
        run.audit()


def test_details_failure_is_fatal_when_root_requires_edit():
    with ThreadPoolExecutor(max_workers=2) as pool:
        record = run_case([Router('edit'), Router(error='invalid details')], pool, CASE, 'parallel')
    assert record['error'] == 'invalid details' and record['exact_match'] is False
    assert record['root_correct']


def test_root_failure_cannot_use_speculative_tasks_as_authority():
    with ThreadPoolExecutor(max_workers=2) as pool:
        record = run_case([Router(error='invalid root'), Router(tasks=('modify',))], pool, CASE, 'parallel')
    assert record['error'] == 'invalid root' and 'actual' not in record


def test_root_only_has_no_fabricated_plan_and_report_preserves_probabilities(tmp_path):
    case = copy.deepcopy(CASE)
    case.update(target='', utterance='我叫李静，安静的静', expected={'task': 'dictation', 'tasks': []})
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = [run_case([Router(), Router()], pool, case, mode) for mode in ('joint', 'root', 'parallel')]
    root = records[1]
    assert root['exact_match'] is None and root['decision_ready_s'] is None and 'actual' not in root
    assert metrics([root])['exact_correct'] is None
    report = {'note': 'test', 'cases': records, 'metrics': {r['mode']: metrics([r]) for r in records},
              'paired': {'root': paired_comparison(records, 'root')}}
    path = tmp_path / 'report.html'; write_html_copy = copy.deepcopy(report)
    write_html(report, path)
    assert report == write_html_copy
    html = path.read_text()
    assert 'dictation: 90.00% / edit: 10.00%' in html
    assert '无模型概率' in html and '未测细分' in html
    assert '未参与分类' in html


def test_tied_root_returns_dictation_without_using_details():
    class TiedRouter(Router):
        def _classify_stage(self, *args):
            result = super()._classify_stage(*args)
            result['answers']['voice_intent'] = {'choice': 'edit', 'probabilities': {'dictation': .5, 'edit': .5}}
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        record = run_case([TiedRouter(), Router(tasks=('modify',))], pool, CASE, 'parallel')
    assert record['actual']['task'] == 'dictation'
    assert not record['policy']['details_used']
    assert 'tied_probability' in record['policy_after_details']['warning_codes']
