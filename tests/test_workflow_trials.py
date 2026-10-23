import copy
import json
from types import SimpleNamespace

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from proximic_ring.agent_workflow.execution.workflow_trials import (make_case, task_prompt, TrialStore,
    Trial, comparison, available_workflows, validate_case, digest, export_trials)


def entry():
    return dict(name='导航', source={'job_id':'source-job'}, flow=dict(name='导航', goal='打开 {{url}}',
        parameters=[dict(name='url',description='网址')], conditions=['浏览器可用'], fallbacks=[],
        steps=[dict(action='通过 com.apple.Safari 绑定 Safari', target='Safari', check='窗口可用'),
               dict(action='聚焦地址栏，输入 {{url}} 并回车', target='地址栏', check='显示目标页面')]))


def case(): return make_case(entry(), 'Safari 打开 {{url}}', {'url':'https://example.com'}, 'Safari 空白标签页')


def test_only_two_exact_alternative_inputs_without_guidance():
    value=case()
    assert value['schema_version']==5 and 'execution_guidance' not in value
    assert task_prompt(value,'baseline')=='Safari 打开 https://example.com'
    text=task_prompt(value,'natural_language')
    assert text.startswith('操作步骤：') and '地址栏' in text and '{{url}}' not in text
    assert '以下流程是本次任务' not in text and 'Safari 空白标签页' not in text
    assert not text.startswith(task_prompt(value,'baseline'))
    assert validate_case(value)==value and case()==value
    with pytest.raises(ValueError,match='JSON 测试已停用'): task_prompt(value,'workflow')


def test_latest_model_methods_and_imported_text_are_frozen_verbatim(tmp_path):
    source=entry()
    source['flow']['steps'][1]['method']='读取当前界面，定位地址栏并输入 {{url}}'
    source['flow']['steps'][1].pop('target')
    source['natural_language_workflow']='操作步骤：\n1. 打开 {{url}}。\n2. 根据最新界面核验。'
    value=make_case(source,'打开 {{url}}',{'url':'https://example.com/{{literal}}'},'空白页')
    text=task_prompt(value,'natural_language')
    assert text==source['natural_language_workflow'].replace('{{url}}','https://example.com/{{literal}}')
    assert validate_case(value)==value
    TrialStore(tmp_path).case(value)
    assert (tmp_path/'workflow-tests'/(value['id']+'.workflow.txt')).read_text()==text
    for params in ({},{'url':''},{'url':'a','extra':'b'}):
        with pytest.raises(ValueError): make_case(source,'打开 {{url}}',params,'空白页')
    changed=copy.deepcopy(value);changed['parameters']['url']='changed'
    with pytest.raises(ValueError,match='已改变'): validate_case(changed)


def test_legacy_snapshots_are_readable_but_cannot_be_executed(tmp_path):
    from pathlib import Path
    source=Path(__file__).resolve().parents[1]/'experiments/workflow_trials/cases'
    for path in source.glob('*.case.json'):
        value=json.loads(path.read_text())
        assert validate_case(value)==value
        for mode in ('baseline','natural_language','workflow'):
            with pytest.raises(ValueError): task_prompt(value,mode)
        TrialStore(tmp_path).case(value)
    page=export_trials(tmp_path).read_text()
    assert '历史测试方案，仅供回查' in page


def test_timing_is_monotonic_and_completion_is_not_goal_success(tmp_path):
    ticks = iter([100, 121.5]); store = TrialStore(tmp_path)
    trial = Trial(store, case(), 'baseline', clock=lambda:next(ticks))
    for _ in range(2):
        trial.observe(dict(method='item/started',params={'item':{'id':'tool1','type':'mcpToolCall'}}))
    result = trial.finish('completed')
    assert result['elapsed_seconds'] == 21.5 and result['tool_calls'] == 1
    assert result['goal_verification'] == 'unknown'
    assert trial.finish('failed') == result
    assert store.runs()[0]['status'] == 'completed'
    assert '尚无可比较' in comparison(store.runs())
    store.verify(result['id'], True)
    assert store.runs()[0]['goal_verification'] == 'success'
    assert (store.directory/result['prompt_file']).read_text()==task_prompt(case(),'baseline')
    assert (store.directory/(case()['id']+'.workflow.txt')).read_text()


def test_comparison_excludes_failure_intervention_and_different_cases_or_settings():
    row = dict(case_id='case', status='completed',goal_verification='success', elapsed_seconds=10,
               initial_state_confirmed=True,interventions=[],environment={'model':'same'},mode='baseline')
    guided = {**row,'mode':'natural_language','elapsed_seconds':6}
    assert '快 4.0 秒（40.0%）' in comparison([row,guided])
    assert '不代表统计显著' in comparison([row,guided])
    for change in ({'case_id':'other'},{'status':'failed'},{'goal_verification':'unknown'},
                   {'interventions':['steer']},{'environment':{'model':'other'}},{'environment':{}},{'initial_state_confirmed':False}):
        assert '尚无可比较' in comparison([row,{**guided,**change}])


def test_legacy_json_runs_are_preserved_but_excluded_from_comparison():
    base=dict(case_id='same',status='completed',goal_verification='success',initial_state_confirmed=True,
              interventions=[],environment={'model':'test'},mode='baseline',elapsed_seconds=10)
    legacy={**base,'mode':'workflow','elapsed_seconds':1}
    assert '尚无可比较' in comparison([base,legacy])
    result=comparison([base,legacy,{**base,'mode':'natural_language','elapsed_seconds':6}])
    assert 'JSON Workflow' not in result and '40.0%' in result


class Wire:
    def __init__(self): self.calls=[]; self.sent=[]
    def call(self, method, params, **kwargs):
        self.calls.append((method,params))
        if method == 'thread/start': return {'thread':{'id':'fresh-thread'},'model':'test-model'}
        return {'content':[], 'isError':False}
    def send(self, method, params):
        self.sent.append((method,params)); return len(self.sent)


@pytest.fixture
def flow(tmp_path):
    from proximic_ring.agent_workflow.agent.flow import Flow
    app = Flow(with_ring=False, runtime_dir=tmp_path/'runtime', recording_dir=tmp_path/'records')
    app.rpc = Wire(); app.tid = 'original-thread'
    yield app
    app.events.close()


def launch(app, mode='natural_language'):
    app.command(dict(type='workflow_trial',case=case(),mode=mode,initialStateConfirmed=True))


def test_runs_use_fresh_thread_and_restore_original_after_release(flow):
    launch(flow)
    assert flow.rpc.calls[0][0] == 'thread/start'
    assert flow.rpc.calls[0][1]['ephemeral'] is True
    assert flow.tid == 'fresh-thread' and flow.trial.row['environment']['model'] == 'test-model'
    assert flow.rpc.sent[0][0] == 'turn/start'
    flow.handle({'method':'turn/started','params':{'threadId':'original-thread','turn':{'id':'wrong'}}})
    assert flow.turn is None
    flow.handle({'method':'turn/started','params':{'threadId':'fresh-thread','turn':{'id':'turn'}}})
    flow.handle({'method':'turn/completed','params':{'threadId':'fresh-thread','turn':{'id':'turn','status':'completed'}}})
    assert flow.tid == 'original-thread' and flow.trial is None
    assert flow.state['workflowTrial']['goal_verification'] == 'unknown'
    assert flow.state['workflowTrial']['turn_id'] == 'turn'
    cleanup = [params for method,params in flow.rpc.calls if method=='mcpServer/tool/call']
    assert all(params['threadId']=='fresh-thread' for params in cleanup)
    assert len(TrialStore(flow.recording_dir).runs()) == 1


def test_busy_or_unconfirmed_run_cannot_get_queued_or_execute(flow):
    flow.turn = 'busy'; launch(flow)
    assert not flow.rpc.calls and not flow.rpc.sent and not flow.task_queue
    flow.turn = None
    flow.command(dict(type='workflow_trial',case=case(),mode='baseline',initialStateConfirmed=False))
    assert not flow.rpc.calls and not flow.rpc.sent


def test_start_error_is_saved_and_does_not_leave_wrong_thread(flow):
    launch(flow, 'baseline')
    assert '地址栏' not in json.dumps(flow.rpc.sent[-1],ensure_ascii=False)
    flow.handle({'id':flow.start_id,'error':{'message':'test error'}})
    assert flow.tid == 'original-thread' and flow.trial is None
    assert flow.state['workflowTrial']['status'] == 'start_failed'
    assert '尚无可比较' in comparison(TrialStore(flow.recording_dir).runs())


def test_timing_disk_error_does_not_prevent_computer_control_release(flow, monkeypatch):
    launch(flow)
    def fail(_): raise OSError('disk full')
    monkeypatch.setattr(flow.trial.store,'save',fail)
    flow.handle({'method':'turn/started','params':{'threadId':'fresh-thread','turn':{'id':'turn'}}})
    flow.handle({'method':'turn/completed','params':{'threadId':'fresh-thread','turn':{'id':'turn','status':'completed'}}})
    assert flow.state['computerControlState']=='released'
    assert flow.tid=='original-thread' and flow.trial is None
    assert '保存失败' in flow.state['workflowTrialError']


def test_direct_steer_is_marked_as_intervention(flow):
    launch(flow)
    flow.handle({'method':'turn/started','params':{'threadId':'fresh-thread','turn':{'id':'turn'}}})
    flow.text('改个目标',source='test',mode='steer',target_turn='turn')
    assert flow.trial.row['interventions']==['steer']


def test_option_answer_is_also_an_intervention(flow, monkeypatch):
    launch(flow)
    request=dict(id=17,kind='question',method='item/tool/requestUserInput',
                 questions=[{'id':'choice'}],questionIndex=0,answers={})
    monkeypatch.setattr(flow,'reply',lambda *args:None)
    flow.answer(request,'第一项')
    assert flow.trial.row['interventions']==['answer']


def test_report_escapes_user_content_and_keeps_unknown_results_out_of_comparison(tmp_path):
    value=make_case(entry(),'<script>alert(1)</script>',{'url':'https://example.com'},'空白页')
    store=TrialStore(tmp_path);ticks=iter([0,2])
    trial=Trial(store,value,'baseline',clock=lambda:next(ticks));trial.finish('completed')
    page=export_trials(tmp_path).read_text()
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert '尚无可比较' in page and trial.row['id']+'.run.json' in page


def test_test_episodes_are_not_automatically_extracted(tmp_path, monkeypatch):
    from proximic_ring.agent_workflow.extraction import workflows
    from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
    monkeypatch.setattr(workflows,'task_list',lambda root:[dict(source='workflow_trial')])
    monkeypatch.setattr(workflows,'source_packet',lambda *a:pytest.fail('test episode sent for automatic extraction'))
    store=WorkflowStore(tmp_path)
    try:
        workflows.scan(store,'',set())
        assert store.jobs()==[]
    finally: store.close()


def test_cancel_is_recorded_even_when_completion_races_with_interrupt(flow):
    launch(flow)
    flow.handle({'method':'turn/started','params':{'threadId':'fresh-thread','turn':{'id':'turn'}}})
    flow.control('cancel','test')
    flow.handle({'method':'turn/completed','params':{'threadId':'fresh-thread','turn':{'id':'turn','status':'completed'}}})
    row=TrialStore(flow.recording_dir).runs()[0]
    assert row['interventions']==['cancel']
    assert row['goal_verification']=='unknown'


def test_disabled_json_and_legacy_cases_never_start_agent(flow):
    launch(flow,'workflow')
    assert not flow.rpc.calls and not flow.rpc.sent
    assert 'JSON 测试已停用' in flow.state['workflowTrialError']
