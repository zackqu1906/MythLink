import copy
import json
from pathlib import Path
import tkinter as tk

import pytest


@pytest.fixture
def study(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    import joined_stroke_collection
    return joined_stroke_collection


def test_plan_is_balanced_and_preserves_valid_code_variants(study):
    tasks = study.inventory()
    assert len(tasks) == 13
    joined = [t for t in tasks if t['kind'] == 'joined']
    assert len(joined) == 9
    assert {t['target'] for t in joined} == {'walk'}
    for task in tasks:
        assert all(len(code) == task['expectedLogicalStrokes'] for code in task['expectedCodes'])
    assert all(t['expectedCodes']==['zn'] and t['expectedLogicalStrokes']==2 for t in joined)
    assert {t['contextCharacter'] for t in joined} == {'过','近','这'}
    for phase in ('train', 'evaluation'):
        session = study.new_session('P01', phase=phase)
        assert session['version'] == 1 and session['planVersion'] == 3
        assert len(session['tasks']) == 26
        for offset in (0, 13):
            assert {t['label'] for t in session['tasks'][offset:offset+13]} == {t['label'] for t in tasks}
        assert study.validate_session(session) is session
    for rounds in (0, 31, True, 1.5):
        with pytest.raises(ValueError):
            study.new_session('P01', rounds=rounds)


def test_pie_dian_keeps_one_and_two_stroke_labels_and_legacy_resume(study):
    from check_selection_capture import trace
    tasks = study.inventory(2)
    yi = [t for t in tasks if t.get('target') == 'yi-pie-dian']
    assert len(yi) == 3 and all(t['expectedCodes'] == ['pn'] for t in yi)
    assert next(t for t in tasks if t.get('target') == 'zi')['expectedCodes'] == ['zs', 'zz']
    controls = [t for t in tasks if t.get('shape') == '撇点']
    assert len(controls) == 2
    assert all(t['expectedLogicalStrokes'] == 1 and t['expectedCodes'] == ['z'] for t in controls)
    assert any(t.get('variant') == 'small' for t in controls)
    # No unconfirmed radical families were silently added.
    assert {t['target'] for t in tasks if t.get('target')} == {'fan', 'de', 'le', 'zi', 'yi-pie-dian'}
    legacy = study.new_session('P01', rounds=1)
    legacy['planVersion'] = 1
    legacy['tasks'] = [{**t, 'round': 1} for t in study.inventory(1)]
    assert len(legacy['tasks']) == 24
    assert len([t for t in legacy['tasks'] if t['kind'] == 'joined']) == 9
    legacy['samples'] = [{**trace(), 'taskIndex': 0, 'task': legacy['tasks'][0]}]
    legacy['draft'] = trace()
    original = copy.deepcopy(legacy)
    study.validate_session(legacy)
    assert legacy == original
    changed = copy.deepcopy(legacy)
    changed['planVersion'] = 2
    with pytest.raises(ValueError):
        study.validate_session(changed)
    legacy['planVersion']=2
    legacy['tasks']=[{**t,'round':1} for t in study.inventory(2)]
    legacy['samples']=[]
    assert len(study.validate_session(legacy)['tasks'])==35


def test_resume_rejects_relabeling_repetition_and_wrong_samples(study):
    from check_selection_capture import trace
    session = study.new_session('P01', rounds=1)
    session['draft'] = trace()
    before = copy.deepcopy(session)
    study.validate_session(session)
    assert session == before
    for mutate in (
        lambda s: s['tasks'][0].update(expectedCodes=['h']),
        lambda s: s['tasks'].__setitem__(1, copy.deepcopy(s['tasks'][0])),
        lambda s: s['samples'].append({**trace(), 'taskIndex': 1, 'task': s['tasks'][0]}),
    ):
        broken = copy.deepcopy(before)
        mutate(broken)
        with pytest.raises(ValueError):
            study.validate_session(broken)


def test_predictions_do_not_use_prompt_and_stationary_contacts_survive(study):
    from check_selection_capture import trace
    sample = trace()
    joined = next(t for t in study.inventory() if t['kind'] == 'joined')
    control = next(t for t in study.inventory() if t['kind'] == 'fold-control')
    assert study.sample_annotations(sample, joined) == study.sample_annotations(sample, control)
    stationary = copy.deepcopy(sample)
    for point in stationary['points']:
        point['x'] = point['y'] = 0
    assert study.sample_annotations(stationary, joined)['singleStrokeBaseline'] is None


def test_capture_save_retry_resume_and_audit(study, tmp_path):
    from ring_selection_collector import CollectionWindow
    from check_selection_capture import contact, move
    from analyze_joined_strokes import summarize
    root = tk.Tk()
    root.withdraw()
    try:
        window = CollectionWindow(root, tmp_path, profile=study)
        session = study.new_session('P01', rounds=1)
        window.install(session)
        window.connected = window.warm = True
        window.arm()
        window.event(contact('down', 10))
        for step in range(10, 40):
            window.event(move(step, -1 if step < 24 else 1, 1))
        window.event(contact('up', 40))
        assert window.session['draft'] is not None
        assert len(window.session['draft']['rawEvents']) == 32
        window.save_sample()
        restored = study.validate_session(json.loads(window.path.read_text(encoding='utf-8')))
        saved = restored['samples'][0]
        assert saved['singleStrokeBaseline']['recognizer'] == 'dtw_v1'
        assert 'strokes.json.gz' in saved['baselineSource']['assetSha256']
        assert restored['tasks'] == session['tasks']
        report = summarize([window.path])
        assert report['sessions'][0]['saved'] == 1
        assert report['groups'][0]['count'] == 1
        with pytest.raises(ValueError, match='sessionId'):
            summarize([window.path, window.path])
        window.install(restored)
        assert len(window.session['samples']) == 1
        window.arm()
        window.event(contact('down', 100))
        for step in range(100, 110):
            window.event(move(step, 0, 0))
        window.event(contact('up', 110))
        assert window.session['draft']['pathLength'] == 0
        window.retry()
        assert len(window.session['rejected']) == 1 and window.armed
        assert window.session['draft'] is None
        legacy = study.new_session('P01', rounds=1)
        legacy['planVersion'] = 1
        legacy['tasks'] = [{**t, 'round': 1} for t in study.inventory(1)]
        window.install(study.validate_session(legacy))
        assert '每轮 24 笔' in window.plan_help.get()
        assert len(window.session['tasks']) == 24
        window.raw_stream.close()
    finally:
        root.destroy()
