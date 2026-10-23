"""Recorded ordinary tasks pass through the candidate API into Hub ring selection."""
from PySide6.QtCore import QCoreApplication
from test_parameter_history import history,task,model,candidates
from test_recommendation_hub import hub,swipe
from test_inline_ui import inline_ui
from test_recommendation_execution import catalog
from proximic_ring.agent_workflow.parameters.parameter_jobs import process_parameters


def test_ordinary_history_values_cycle_without_opening_manual_editor(history,hub):
    task(history,'two',title='英语课')
    while process_parameters(history,None,model=model):pass
    options=candidates(history)['title'];assert {v['value'] for v in options}=={'数学课','英语课'}
    owner,_,runtime,sent,_=hub;dialog=runtime.dialog
    item=next(i for i in runtime.snapshot['items'] if i['workflow']['parameters'])
    item['parameter_candidates']={'title':options}
    dialog.choose(item['position']);swipe(owner,'tap');QCoreApplication.processEvents()
    editor=dialog.editors['title'];first=editor.toPlainText()
    assert first==options[0]['value'] and editor.selection_preview
    swipe(owner,'swipe-up')
    assert editor.toPlainText()==options[1]['value'] and editor.selection_preview
    assert '2/2' in dialog.sentence.candidate_hint() and not sent
    swipe(owner,'swipe-down')
    assert editor.toPlainText()==first and not dialog.sentence.editing and not sent


def test_optional_input_starts_empty_can_cycle_back_and_submits_empty(hub,monkeypatch):
    _,_,runtime,sent,_=hub;dialog=runtime.dialog
    item=next(i for i in runtime.snapshot['items'] if i['workflow']['parameters'])
    item['workflow']['parameters'][0]['required']=False
    item['parameter_candidates']={'title':[dict(value='历史标题',count=1)]}
    calls=[];monkeypatch.setattr(runtime,'execute',lambda p,v:calls.append((p,v)))
    dialog.choose(item['position']);QCoreApplication.processEvents()
    assert dialog.editors['title'].toPlainText()==''
    assert '可选' in dialog.editors['title'].accessibleName()
    dialog.enter_word(0);dialog.cycle_word(1)
    assert dialog.editors['title'].toPlainText()=='历史标题'
    dialog.cycle_word(1)
    assert dialog.editors['title'].toPlainText()==''
    assert '可选输入留空' in dialog.sentence.candidate_hint()
    dialog.confirm_word();runtime.input.submit()
    assert calls==[(item['position'],{'title':''})] and not sent
    assert len(item['parameter_candidates']['title'])==1  # UI omission choice is not history.
