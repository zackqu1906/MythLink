from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_agent_ui import finish_fixture_voice
from test_workflow_trials import entry
from proximic_ring.agent_workflow.execution.workflow_trials import Trial, TrialStore

import pytest
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')


def test_manual_runner_freezes_identical_case_and_never_auto_runs(inline_ui, tmp_path, monkeypatch):
    from proximic_ring.ui.agent import workflow_trial_dialog as module
    owner = inline_ui[0]; agent = owner.agent; finish_fixture_voice(owner)
    monkeypatch.setattr(module, 'available_workflows', lambda root:[entry()])
    agent.workspace.state['recordsRoot'] = str(tmp_path)
    agent._connection_state = 'ready'
    sent = []
    monkeypatch.setattr(agent, '_send', lambda command:sent.append(command) or True)
    monkeypatch.setattr(agent, '_configure_workflows', lambda:None)
    agent.openWorkflowTrials()
    dialog = agent._workflow_trial_dialog
    try:
        assert dialog.isVisible() and not sent
        dialog.parameters['url'].setText('https://example.com')
        assert '操作步骤：' in dialog.preview.toPlainText()
        assert set(dialog.run_buttons)=={'baseline','natural_language'}
        dialog.initial.setPlainText('Safari 空白页')
        dialog.prepare()
        assert dialog.case and not sent
        assert not dialog.run_buttons['natural_language'].isEnabled()
        dialog.reset.setChecked(True)
        QTest.mouseClick(dialog.run_buttons['baseline'], module.Qt.LeftButton)
        assert len(sent)==1 and sent[0]['mode']=='baseline'
        frozen = sent[0]['case']
        assert not dialog.reset.isChecked()
        dialog.run('natural_language'); assert len(sent)==1
        store=TrialStore(tmp_path)
        ticks=iter([0,10]); baseline=Trial(store,frozen,'baseline',clock=lambda:next(ticks))
        baseline.row['environment']={'model':'test'}
        baseline.finish('completed')
        agent.workspace.state['workflowTrial']=baseline.row
        dialog.poll()
        dialog.parameters['url'].setText('https://changed.example')
        # Editing the preparation tab does not silently alter the frozen task.
        dialog.reset.setChecked(True); dialog.run('natural_language')
        assert sent[1]['case']==frozen and sent[1]['mode']=='natural_language'
        ticks=iter([20,26]); guided=Trial(store,frozen,'natural_language',clock=lambda:next(ticks))
        guided.row['environment']={'model':'test'}
        guided.finish('completed'); agent.workspace.state['workflowTrial']=guided.row
        dialog.poll()
        assert dialog.table.rowCount()==2
        assert '尚无可比较' in dialog.summary.toPlainText()
        for index in range(2): dialog.table.selectRow(index); dialog.verify(True)
        assert '快 4.0 秒' in dialog.summary.toPlainText()
        assert dialog.grab().save(str(tmp_path/'workflow-trial-dialog.png'))
        assert len(sent)==2
    finally:
        dialog.close(); agent.close()
