from pathlib import Path
import json
import tkinter as tk


def test_clear_fold_balanced_plan_predictions_and_persistence(tmp_path,monkeypatch):
    import copy
    tools=Path(__file__).resolve().parents[1]/'tools'
    monkeypatch.syspath_prepend(str(tools))
    import clear_fold_collection as profile
    from ring_selection_collector import CollectionWindow
    from check_selection_capture import contact,move
    assert len(profile.inventory())==84
    session=profile.new_session('P01')
    assert len(session['tasks'])==168
    assert session['version']==2 and session['planVersion']==2
    assert len({t['label'] for t in session['tasks'][:84]})==84
    profile.validate_session(session)
    root=tk.Tk();root.withdraw()
    try:
        window=CollectionWindow(root,tmp_path,profile=profile)
        window.install(session)
        window.connected=window.warm=True
        window.arm()
        window.event(contact('down',1))
        for step in range(1,30):window.event(move(step,1 if step<10 or step>=20 else -1,0))
        window.event(contact('up',30))
        draft=window.session['draft']
        assert draft is not None
        prediction=profile.predict_trace(draft)
        task={'expectedAction':'stroke','title':'横折弯钩'}
        # A task label cannot change a prediction; geometry is captured separately.
        profile.trace_details(draft,task)
        assert profile.predict_trace(draft)==prediction
        window.save_sample()
        restored=profile.validate_session(json.loads(window.path.read_text(encoding='utf-8')))
        saved=restored['samples'][0]
        assert saved['gesturePrediction']['action']=='clear-all'
        assert saved['clearFoldGeometry']['segments']==3
        assert saved['clearReviewPolicy']['schema']=='ring-clear-handwriting-review'
        assert saved['deletionPolicy']['source']=='left-right-geometric-v3'
        assert session['classifier']['deletionSource']=='left-right-geometric-v3'
        assert 'bodyDrop' in saved['clearReviewGeometry']
        assert len(saved['rawEvents'])>=30
        # Reuse the study window to resume a legacy plan, just as a participant
        # does; examples must stay display-only and not rewrite the saved task.
        legacy=profile.new_session('P01',rounds=1)
        legacy['version']=legacy['planVersion']=1
        legacy['tasks']=[{**t,'round':1} for t in profile.inventory(1)]
        original=copy.deepcopy(legacy['tasks'])
        window.install(legacy)
        assert '九' in window.note.get() and '只写这一笔' in window.note.get()
        assert legacy['tasks']==original
        profile.validate_session(json.loads(window.path.read_text(encoding='utf-8')))
        window.raw_stream.close()
    finally:root.destroy()


def test_expanded_fold_plan_covers_all_shapes_and_keeps_legacy_resume(tmp_path,monkeypatch):
    import copy
    from collections import Counter
    tools=Path(__file__).resolve().parents[1]/'tools'
    monkeypatch.syspath_prepend(str(tools))
    import clear_fold_collection as profile
    from selection_capture import SHAPES
    from check_selection_capture import trace
    shapes={shape for category,shape,_ in SHAPES if category=='z'}
    tasks=profile.inventory()
    counts=Counter(t.get('shape') for t in tasks if t.get('category')=='z')
    assert set(counts)==shapes and len(shapes)==25
    assert counts['横折弯钩']==10 and counts['横撇弯钩']==10
    assert all(count>=2 for count in counts.values())
    assert sum(t['kind']=='clear' for t in tasks)==10
    assert sum(t['kind']=='control' for t in tasks)==4
    old=profile.new_session('P01',rounds=1)
    old['version']=old['planVersion']=1
    old['tasks']=[{**t,'round':1}for t in profile.inventory(1)]
    old['samples']=[{**trace(),'taskIndex':0,'task':old['tasks'][0]}]
    old['draft']=trace()
    original=copy.deepcopy(old)
    profile.validate_session(old)
    assert old==original and len(old['tasks'])==36
    # The expanded plan must not silently relabel a v1 export.
    invalid=copy.deepcopy(old)
    invalid['version']=invalid['planVersion']=2
    import pytest
    with pytest.raises(ValueError):profile.validate_session(invalid)
