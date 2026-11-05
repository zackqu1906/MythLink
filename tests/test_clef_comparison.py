import copy

from tools.compare_clef_routing import snapshots, run_case, metrics
from proximic_ring.text_processing.intent_router import ClefIntentRouter
from proximic_ring.text_processing.router_prompts import routing_schema, joint_router_input
from proximic_ring.text_processing.task_routing import router_input


CASE = {'id':'sample','utterance':'三点水的涵','target':'李含来了。','context':{'messages':[]},
        'expected':{'task':'edit','tasks':['modify']}}


class Router:
    def __init__(self, intent='edit', apply=('modify','precise_edit')):
        self.calls=[];self.intent=intent;self.apply=apply
    def _classify_stage(self, state, schema, cancelled):
        self.calls.append((state,schema))
        answers={}
        for name, question in schema.items():
            choice=self.intent if name=='voice_intent' else 'apply' if name in self.apply else 'skip'
            answers[name]={'choice':choice,'probabilities':{key:.9 if key==choice else .1 for key in question['criteria']}}
        return {'model':'test','answers':answers,'latency_s':.1}


def test_comparison_preserves_baseline_and_changes_only_root_input_for_b():
    prompts,sha,revision=snapshots()
    assert len(sha)==40+24 and revision
    for variant in ('A','B','C'):
        router=Router();r=run_case(router,CASE,variant,prompts[variant])
        assert r['exact_match'] and r['root_correct']
        assert len(router.calls)==(1 if variant=='C' else 2)
        if variant=='C':
            assert router.calls[0]==(joint_router_input(router_input(CASE['utterance'],CASE['target'],CASE['context'])),routing_schema())
        else:
            assert router.calls[1][1]==prompts['A']['edit_details']
    assert prompts['A']['intent']==prompts['B']['intent']
    assert prompts['A']['secondary_input_prefix']==prompts['B']['secondary_input_prefix']


def test_dictation_priority_keeps_raw_errors_separate_from_final_accuracy():
    prompts,_,_=snapshots();case=copy.deepcopy(CASE);case['expected']={'task':'dictation','tasks':[]}
    r=run_case(Router('dictation'),case,'C',prompts['C'])
    assert r['root_correct'] and r['exact_match'] and not r['raw_exact_match']
    assert r['actual'] == {'task': 'dictation'}
    assert len(r['trace']['stages'][0]['answers'])==7
    m=metrics([r])
    assert m['errors'] == 0 and m['contradictions'] == m['requests'] == m['warnings'] == 1
    assert m['raw_exact_plan_correct'] == 0 and m['exact_plan_correct'] == 1
    assert m['latency_by_expected_intent']['dictation']['n']==1
    assert m['latency_by_expected_intent']['edit']['n']==0


def test_old_task_sets_fold_for_scoring_without_merging_probability_values():
    from tools.clef_cases import comparable_tasks
    assert comparable_tasks(['condense','structure','expand','translate']) == {'polish','format','translate'}
    assert comparable_tasks(['organize']) == {'organize'}  # Cannot recover two decisions from one old field.
    prompts,_,_=snapshots()
    case=copy.deepcopy(CASE);case['expected']['tasks']=['polish','format']
    result=run_case(Router(apply=('structure','condense')),case,'A',prompts['A'])
    assert result['exact_match']
    assert result['actual']['tasks'] == ['condense','structure']
    assert 'format' not in result['trace']['stages'][1]['answers']


def test_joint_comparison_uses_the_same_empty_source_gate_as_the_app():
    prompts, _, _ = snapshots()
    case = copy.deepcopy(CASE)
    case.update(target='', expected={'task':'edit','tasks':['insert']})
    router = Router(apply=('modify', 'insert'))
    record = run_case(router, case, 'C', prompts['C'])
    assert record['exact_match'] and record['raw_exact_match']
    assert 'modify' not in router.calls[0][1]
    assert record['trace']['task_availability']['excluded_tasks'] == {'modify':'no_editable_source'}
