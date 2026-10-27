from copy import deepcopy
import time

import pytest

from proximic_ring.touchpad.stroke.stroke_dtw import sequence
from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, retune_evidence, stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_joined import JoinedStrokeModel, POLICY, merge_joined_candidates


@pytest.fixture
def model():
    shapes = {
        'fan': [(0,0),(-1,1),(0,0),(-2,3)],
        'de': [(0,0),(-1,1),(0,0),(0,3)],
        'le-zi': [(0,0),(2,0),(1,1),(1,4),(0,3)],
        'yi-pie-dian': [(0,0),(-1,2),(1,3)],
        'single': [(0,0),(4,0)],
    }
    positions = {
        '反': {'ppzn':[dict(family='fan',start=0)]},
        '的': {'pszhhpzn':[dict(family='de',start=0)]},
        '了': {'zs':[dict(family='le-zi',start=0)],'zz':[dict(family='le-zi',start=0)]},
        '子': {'zsh':[dict(family='le-zi',start=0)]},
        '好': {'zphzsh':[dict(family='le-zi',start=3)]},
        '补': {'nzspnsn':[dict(family='yi-pie-dian',start=3)]},
    }
    return JoinedStrokeModel(dict(schema='ring-joined-stroke-model',version=1,policy=deepcopy(POLICY),
        source=dict(fingerprint='synthetic-test'), positions=positions,
        templates=[dict(family=f,features=sequence(points).tolist()) for f,points in shapes.items()]))


def ink(family, category='z'):
    codes = {'fan':['pp','hp'],'de':['ps'],'le-zi':['zs','zz'],'yi-pie-dian':['pn']}
    return stroke_evidence(category, dict(category=category,shape='撇点',
        category_scores=[dict(category=category,distance=.001)],
        joined_options=[dict(family=family,codes=codes[family],distance=.01,controlDistance=.02,cost=.9)]))


def candidates(dictionary, evidence, model, fuzzy=True, enabled=True):
    rows = dictionary.fuzzy_candidates(evidence) if fuzzy else []
    chars = [r['char'] for r in rows] if fuzzy else dictionary.candidates(''.join(s['category'] for s in evidence))
    return merge_joined_candidates(dictionary,evidence,chars,rows,model,fuzzy=fuzzy,enabled=enabled), (chars, rows)


def test_shape_proposals_keep_geometry_separate_from_semantics(model):
    points = [(0,0),(2,0),(1,1),(1,4),(0,3)]
    assert any(o['family']=='le-zi' for o in model.recognize(points))
    assert model.recognize([(0,0)]*5) == []
    dictionary = StrokeDictionary()
    assert model.permits(dictionary,'好','zphzsh','le-zi',3,'zs')
    assert not model.permits(dictionary,'女','pnph','yi-pie-dian',0,'pn')
    assert not model.permits(dictionary,'你','ps','de',0,'ps')


@pytest.mark.parametrize('fuzzy', [False, True])
def test_one_contact_expands_with_normal_first_dedup_and_candidate_glyph_count(model, fuzzy):
    dictionary = StrokeDictionary()
    evidence = [ink('le-zi')]
    (chars, rows), normal = candidates(dictionary,evidence,model,fuzzy)
    assert chars[0] == normal[0][0] and '了' in chars[:5]
    assert len(chars) <= 30 and len(chars) == len(set(chars))
    match = next(r for r in rows if r['char']=='了')
    assert match['logical_entered']==2 and match['joined_contacts']==[0]
    assert len(evidence)==1 and evidence[0]['category']=='z'
    assert candidates(dictionary,evidence,model,fuzzy,enabled=False)[0] == normal
    evidence.append(stroke_evidence('h'))
    (chars, rows), _ = candidates(dictionary,evidence,model,fuzzy)
    assert '子' in chars[:5]
    assert next(r for r in rows if r['char']=='子')['logical_entered']==3


def test_manual_input_stays_locked_and_real_pie_dian_does_not_expand_everywhere(model):
    dictionary=StrokeDictionary()
    evidence=[ink('yi-pie-dian'),stroke_evidence('p'),stroke_evidence('h')]
    joined, normal=candidates(dictionary,evidence,model)
    assert joined==normal
    assert '女' in joined[0]
    evidence=[ink('le-zi')]
    evidence[0]['manual']=True
    joined, normal=candidates(dictionary,evidence,model)
    assert joined==normal
    retained=retune_evidence([ink('le-zi')],FuzzyPolicy(100))[0]
    assert retained['joined_options']==ink('le-zi')['joined_options']


def test_nested_component_maps_logical_counts_without_expanding_two_contacts(model):
    dictionary=StrokeDictionary()
    evidence=[ink('yi-pie-dian'),stroke_evidence('p'),stroke_evidence('h'),ink('le-zi'),stroke_evidence('h')]
    (chars, rows), _=candidates(dictionary,evidence,model)
    assert '好' in chars[:5]
    row=next(r for r in rows if r['char']=='好')
    assert row['joined_contacts']==[3] and row['logical_entered']==6
    assert row['glyph_fuzzy']==[3,4]
    assert len(evidence)==5


def test_trial_undo_toggle_commit_learning_and_single_display(tmp_path, monkeypatch, model):
    from test_complete_stroke_input import load_window
    app,window=load_window(tmp_path,monkeypatch)
    from stroke_trial_candidates import trial_candidate_visuals
    try:
        window.correction_toggle.setChecked(False)
        window.joined_model=model
        window.joined_toggle.setEnabled(True)
        window.joined_toggle.setChecked(True)
        window.dictionary.recognize=lambda points:dict(category='z',shape='横撇弯钩',distance=.001,
            category_scores=[dict(category='z',distance=.001)])
        model.recognize=lambda points:ink('le-zi')['joined_options']
        points=[(i,2*i) for i in range(30)]
        def enter(): window.apply_contact_decision(dict(action='stroke'),points)
        enter()
        assert window.code=='z' and len(window.evidence)==1 and '了' in window.candidates[:5]
        visual=next(v for v in trial_candidate_visuals(window) if v['character']=='了')
        assert visual['entered']==2 and '连笔' in visual['status']
        window.fuzzy_strength_slider.setValue(80)
        assert window.evidence[0]['joined_options']
        window.joined_toggle.setChecked(False)
        assert not any(r.get('match_type')=='joined_stroke' for r in window.matches)
        window.joined_toggle.setChecked(True)
        window.add_manual('h')
        assert '子' in window.candidates[:5]
        window.undo()
        assert window.code=='z' and len(window.evidence)==1
        window.undo()
        assert not window.code and not window.evidence
        enter()
        learned=[]
        monkeypatch.setattr(window.personal_model,'learn',lambda *args:learned.append(args))
        window.commit(window.candidates.index('了'))
        assert window.editor.text()=='了' and not window.code and not learned
        # Progressive language promotion must preserve a joined candidate's
        # logical count and the learning guard for that same character.
        monkeypatch.setattr(window.language,'update',lambda *args:['了'])
        window.llm_toggle.setChecked(True)
        enter()
        assert next(r for r in window.matches if r['char']=='了')['match_type']=='joined_stroke'
        window.clear()
        window.llm_toggle.setChecked(False)
        monkeypatch.setattr(window.language,'update',lambda *args:[])
        window.editor.clear()
        window.once_toggle.setChecked(True)
        enter()
        deadline=time.monotonic()+5
        while window.batch.loading and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.01)
        assert not window.batch.loading and '了' in window.candidates[:5]
        assert any(r.get('match_type')=='joined_stroke' for r in window.matches)
        window.show(); app.processEvents()
        assert window.grab().save(str(tmp_path/'joined-complete.png'))
    finally:
        window.close();app.processEvents()


def test_trial_records_joined_commits_without_false_single_stroke_scores(tmp_path,monkeypatch,model):
    import json
    from test_complete_stroke_input import load_window
    app,window=load_window(tmp_path,monkeypatch)
    try:
        window.joined_model=model
        window.joined_toggle.setEnabled(True)
        window.joined_toggle.setChecked(True)
        window.correction_toggle.setChecked(False)
        window.dictionary.recognize=lambda p:dict(category='z',shape='横撇弯钩',distance=.001,
            category_scores=[dict(category='z',distance=.001)])
        model.recognize=lambda p:ink('le-zi')['joined_options']
        window.target_input.setPlainText('了')
        window.start_trial()
        assert not window.joined_toggle.isEnabled()
        window.apply_contact_decision(dict(action='stroke'),[(i,2*i) for i in range(30)])
        window.commit(window.candidates.index('了'))
        assert window.report_path is not None
        report=json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['completed'] and report['joined_stroke_enabled']
        assert report['joined_contacts_unscored']==1
        assert any(e['kind']=='joined_stroke_commit' and e['delivered'] for e in report['events'])
        assert window.joined_toggle.isEnabled()
    finally:
        window.close();app.processEvents()
