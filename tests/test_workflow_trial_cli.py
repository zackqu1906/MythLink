from pathlib import Path
import json
import shutil

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from proximic_ring.agent_workflow.execution import workflow_trial_cli as cli
from proximic_ring.agent_workflow.execution.workflow_trials import task_prompt


def test_shipped_inputs_and_preview_are_exact(capsys):
    for task in ('notes','music'):
        case=cli.load_case(task)
        for mode in ('baseline','steps'):
            assert cli.main(['preview','--task',task,'--mode',mode])==0
            assert capsys.readouterr().out==task_prompt(case,cli.MODES[mode])+'\n'
    for args in (['run','--task','music','--mode','json'],['list','--suite','guided']):
        with pytest.raises(SystemExit): cli.main(args)
    help_text=cli.parser().format_help()
    assert '--suite' not in help_text and '{baseline,steps}' in help_text


def test_permission_answers_require_enabled_options_and_current_token():
    request=dict(token='specific-request',kind='permission',options=[
        dict(index=0,label='不可用',enabled=False),dict(index=1,label='拒绝',enabled=True)])
    with pytest.raises(ValueError): cli.decision(request,'1')
    with pytest.raises(ValueError): cli.decision(request,'随意回答')
    assert cli.decision(request,'2')==dict(type='choose',token='specific-request',index=1,source='workflow_trial_cli')
    with pytest.raises(KeyboardInterrupt): cli.decision(request,'q')
    request.update(kind='question',blocked='敏感信息请在原应用填写')
    with pytest.raises(ValueError): cli.decision(request,'secret text')


def test_stale_request_answer_is_not_sent(monkeypatch,capsys):
    request=dict(token='old',kind='question',message='question',options=[])
    monkeypatch.setattr(cli,'ask',lambda _: 'answer')
    monkeypatch.setattr(cli,'read_state',lambda _:dict(request={'token':'new'}))
    monkeypatch.setattr(cli,'send',lambda *a:pytest.fail('stale answer sent'))
    cli.answer_request(Path('.'),request)
    assert '未提交旧回答' in capsys.readouterr().out


def test_report_does_not_overwrite_earlier_selected_report(tmp_path,capsys):
    directory=tmp_path/'workflow-tests';directory.mkdir()
    previous=directory/'report-selected.html';previous.write_text('historical report')
    assert cli.main(['report','--root',str(tmp_path)])==0
    assert previous.read_text()=='historical report'
    assert (directory/'comparison.html').exists()


def test_runner_uses_existing_flow_protocol_and_stops_child(monkeypatch,tmp_path):
    from proximic_ring.ui.agent import agent_controller
    case=cli.load_case('music')
    args=cli.parser().parse_args(['run','--task','music','--mode','steps','--initial-state-confirmed',
                                 '--skip-verification','--root',str(tmp_path/'records')])
    monkeypatch.setattr(cli,'REPO',tmp_path)
    monkeypatch.setattr(agent_controller,'find_codex',lambda: cli.sys.executable)
    monkeypatch.setattr(cli.sys,'platform','darwin')
    states=iter([dict(connectionState='ready'),dict(status='本轮结束',computerControlState='released',
        workflowTrial=dict(id='a'*32,status='completed',elapsed_seconds=2,tool_calls=3))])
    monkeypatch.setattr(cli,'read_state',lambda _:next(states))
    sent=[];monkeypatch.setattr(cli,'send',lambda runtime,command:sent.append(command))
    monkeypatch.setattr(cli,'report',lambda *a:tmp_path/'report.html')
    captured=[]
    class Child:
        pid=111
        def __init__(self,cmd,**kwargs): captured.append((cmd,kwargs));self.done=False
        def poll(self): return 0 if self.done else None
        def wait(self,timeout): self.done=True;return 0
    monkeypatch.setattr(cli.subprocess,'Popen',Child)
    assert cli.run(args,case)==0
    assert captured[0][0][1:3]==['-m','proximic_ring.agent_workflow.execution.workflow_trial_cli']
    assert captured[0][1]['start_new_session']
    assert sent[0]==dict(type='workflow_trial',case=case,mode='natural_language',initialStateConfirmed=True)
    assert sent[-1]['type']=='exit'


def test_editable_inputs_freeze_revisions_and_keep_previous_runs(tmp_path, capsys):
    from proximic_ring.agent_workflow.execution.workflow_trials import Trial, TrialStore
    tasks=tmp_path/'tasks';shutil.copytree(cli.TASKS,tasks)
    before=cli.load_case('notes',tasks_dir=tasks)
    store=TrialStore(tmp_path/'records')
    old=Trial(store,before,'baseline');old.finish('completed')
    config=tasks/'notes/task.json'
    value=json.loads(config.read_text());value['parameters']['note_title']='新课堂 {{literal}}'
    config.write_text(json.dumps(value,ensure_ascii=False))
    after=cli.load_case('notes',tasks_dir=tasks)
    assert before['id']!=after['id']
    for mode in ('baseline','natural_language'):
        assert '新课堂 {{literal}}' in task_prompt(after,mode)
        assert 'Workflow 对比测试' in task_prompt(before,mode)
    old_prompt=(store.directory/old.row['prompt_file']).read_text()
    assert '新课堂' not in old_prompt
    args=['report','--task','notes','--tasks-dir',str(tasks),'--root',str(store.root)]
    assert cli.main(args)==0
    page=(store.directory/'comparison.html').read_text()
    assert before['id'] in page and after['id'] in page and old.row['id'] in page
    assert (store.directory/(before['id']+'.case.json')).exists()
    # Broken or undeclared inputs fail before any Agent is started.
    (tasks/'notes/steps.txt').write_text('输入 {{missing}}')
    assert cli.main(['preview','--task','notes','--mode','steps','--tasks-dir',str(tasks)])==1
    assert '未声明' in capsys.readouterr().err


def test_task_catalog_and_copied_custom_task(tmp_path,capsys):
    tasks=tmp_path/'tasks';tasks.mkdir()
    shutil.copytree(cli.TASKS/'music',tasks/'custom-music')
    assert cli.main(['list','--tasks-dir',str(tasks)])==0
    listing=capsys.readouterr().out
    assert 'custom-music' in listing and str(tasks/'custom-music') in listing
    assert cli.main(['preview','--task','custom-music','--mode','baseline','--tasks-dir',str(tasks)])==0
    assert 'Apple Music' in capsys.readouterr().out
    with pytest.raises(ValueError): cli.load_case('../notes',tasks_dir=tasks)


def test_prepare_imports_generated_steps_without_model_or_overwriting(tmp_path,monkeypatch,capsys):
    from test_workflow_trials import entry
    selected=entry();selected['key']='job:0'
    monkeypatch.setattr(cli,'available_workflows',lambda root:[selected])
    args=['prepare','--workflow','job:0','--task','new-task','--tasks-dir',str(tmp_path)]
    assert cli.main(args)==0
    directory=tmp_path/'new-task'
    assert {p.name for p in directory.iterdir()}=={'baseline.txt','steps.txt','task.json'}
    assert '地址栏' in (directory/'steps.txt').read_text()
    config=json.loads((directory/'task.json').read_text())
    assert config['parameters']=={'url':''} and config['source']==selected['source']
    before={p.name:p.read_bytes() for p in directory.iterdir()}
    assert cli.main(args)==1
    assert {p.name:p.read_bytes() for p in directory.iterdir()}==before
