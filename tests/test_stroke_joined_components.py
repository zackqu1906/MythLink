from copy import deepcopy
import gzip
import json

import pytest

from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_joined import (FAMILIES, LEGACY_POLICY, POLICY,
    JoinedStrokeModel, component_paths, merge_joined_candidates)
from proximic_ring.touchpad.stroke.stroke_joined_components import component_registry
from test_stroke_joined import model


@pytest.fixture
def upgraded(model, tmp_path):
    payload = dict(schema='ring-joined-stroke-model',version=1,policy=deepcopy(LEGACY_POLICY),
        source=dict(fingerprint='legacy-test'),positions=model.positions,
        templates=[dict(family=family,features=features.tolist()) for family,features in zip(model.labels,model.bank)])
    path=tmp_path/'old-model.json.gz'
    path.write_bytes(gzip.compress(json.dumps(payload).encode('utf-8')))
    return JoinedStrokeModel.load(path)


def joined(family, category='z'):
    return stroke_evidence(category,dict(category=category,shape='连笔测试',
        category_scores=[dict(category=category,distance=.001)],
        joined_options=[dict(family=family,codes=list(FAMILIES[family]),distance=.01,cost=.9)]))


def decode(dictionary,evidence,model):
    rows=dictionary.fuzzy_candidates(evidence,allowed_characters=common_characters())
    return merge_joined_candidates(dictionary,evidence,[r['char'] for r in rows],rows,model,
        allowed=common_characters())


def test_old_model_automatically_picks_up_new_component_rules(upgraded):
    dictionary=StrokeDictionary()
    for char,code,start in [('反','ppzn',0),('斤','pphs',0),('听','szhpphs',3),
                            ('新','nhnphhspnpphs',9),('所','ppzhpphs',4)]:
        assert upgraded.permits(dictionary,char,code,'fan',start,'pp')
    assert upgraded.permits(dictionary,'近','pphsnzn','walk',5,'zn')
    assert not upgraded.permits(dictionary,'近','pphsnzn','walk',4,'nzn')
    assert upgraded.policy['maxSplits']==3
    assert 'walk' in upgraded.reference_only
    assert not upgraded.permits(dictionary,'人','pn','yi-pie-dian',0,'pn')


@pytest.mark.parametrize('char,code,start', [('斤','pphs',0),('听','szhpphs',3),
    ('新','nhnphhspnpphs',9),('所','ppzhpphs',4)])
def test_roof_rule_is_inherited_inside_characters(upgraded,char,code,start):
    dictionary=StrokeDictionary()
    evidence=[stroke_evidence(c) for c in code[:start]]+[joined('fan','p')]+[stroke_evidence(c) for c in code[start+2:]]
    chars,rows=decode(dictionary,evidence,upgraded)
    assert char in chars[:5]
    row=next(r for r in rows if r['char']==char)
    assert row['logical_entered']==len(code)
    assert row['joined_contacts']==[start]


@pytest.mark.parametrize('char,body', [('过','hsn'),('这','nhpn'),('进','hhps'),('远','hhpz')])
def test_walk_second_and_third_join_while_dot_stays_separate(upgraded,char,body):
    dictionary=StrokeDictionary()
    evidence=[stroke_evidence(c) for c in body+'n']+[joined('walk')]
    chars,rows=decode(dictionary,evidence,upgraded)
    assert char in chars[:5]
    row=next(r for r in rows if r['char']==char)
    assert row['logical_entered']==len(body)+3
    assert row['joined_label']=='连笔 1→2'
    assert row['glyph_fuzzy']==[len(body)+1,len(body)+2]
    assert len(evidence)==len(body)+2


def test_jin_and_walk_can_both_join_in_jin_combination(upgraded):
    dictionary=StrokeDictionary()
    evidence=[joined('fan','p'),stroke_evidence('h'),stroke_evidence('s'),stroke_evidence('n'),joined('walk')]
    chars,rows=decode(dictionary,evidence,upgraded)
    assert '近' in chars[:5]
    row=next(r for r in rows if r['char']=='近')
    assert row['joined_contacts']==[0,4]
    assert [g['start'] for g in row['joined_groups']]==[0,5]
    assert row['logical_to_physical']==[0,0,1,2,3,4,4]
    assert row['logical_entered']==7
    assert len(component_paths(dictionary,evidence,upgraded))<=POLICY['maxPaths']
    # Undo one physical contact removes the complete walk component.
    _,rows=decode(dictionary,evidence[:-1],upgraded)
    assert all(not any(g['family']=='walk' for g in p['groups']) for p in component_paths(dictionary,evidence[:-1],upgraded))
    assert any('近'==c for p in component_paths(dictionary,evidence[:-1],upgraded) for c,_ in p['eligible'])


def test_walk_reference_geometry_generates_a_walk_option(upgraded):
    registry=component_registry()
    assert len(registry['referenceTemplates'])>=3
    for template in registry['referenceTemplates']:
        assert template['referenceCode']=='zn' and len(template['referenceIndices'])==2
        points=list(zip(template['features'][0],template['features'][1]))
        options=upgraded.recognize(points)
        assert any(o['family']=='walk' and o['source']=='reference-component' for o in options)


def test_walk_first_schedule_and_nested_jin_keep_reference_mapping(upgraded):
    dictionary=StrokeDictionary()
    evidence=[stroke_evidence('n'),joined('walk'),joined('fan','p'),stroke_evidence('h'),stroke_evidence('s')]
    chars,rows=decode(dictionary,evidence,upgraded)
    assert '近' in chars[:5]
    row=next(r for r in rows if r['char']=='近')
    assert row['logical_entered']==7
    assert tuple(row['observed_to_expected'])==(4,5,6,0,1,2,3)
    assert row['joined_contacts']==[1,2]


def test_registry_does_not_cross_unknown_component_membership(upgraded):
    dictionary=StrokeDictionary()
    assert '后' not in upgraded.positions  # Its source decomposition contains ？.
    assert not upgraded.permits(dictionary,'后','pphszh','fan',0,'pp')
    assert not upgraded.permits(dictionary,'延','pshzzn','walk',3,'nzn')  # 廴 is not 辶.
    for char,variants in upgraded.positions.items():
        for full,annotations in variants.items():
            assert any(e[1]==full for e in dictionary.by_character[char])
            for row in annotations:
                assert row['indices']==list(range(row['start'],row['start']+row['length']))
                assert full[row['start']:row['start']+row['length']] in FAMILIES[row['family']]


def test_missing_walk_dot_is_not_implicitly_inserted(upgraded):
    dictionary=StrokeDictionary()
    # 过: body 寸 has been written; the separate first dot of 辶 is missing.
    evidence=[stroke_evidence(c) for c in 'hsn']+[joined('walk')]
    paths=component_paths(dictionary,evidence,upgraded)
    assert not any(c=='过' for path in paths for c,_ in path['eligible'])


def test_complete_window_two_joined_contacts_visuals_and_undo(tmp_path,monkeypatch,upgraded):
    from test_complete_stroke_input import load_window
    app,window=load_window(tmp_path,monkeypatch)
    from stroke_trial_candidates import trial_candidate_visuals
    try:
        window.correction_toggle.setChecked(False)
        window.joined_model=upgraded
        window.joined_toggle.setEnabled(True)
        window.joined_toggle.setChecked(True)
        window.dictionary.recognize=lambda points:dict(
            category='p' if points[0][0]==0 else 'z',shape='连笔测试',distance=.001,
            category_scores=[dict(category='p' if points[0][0]==0 else 'z',distance=.001)])
        upgraded.recognize=lambda points:joined('fan' if points[0][0]==0 else 'walk')['joined_options']
        fan=[(i,2*i) for i in range(30)]
        tail=[(100+i,i) for i in range(30)]
        window.apply_contact_decision(dict(action='stroke'),fan)
        for c in 'hsn':window.add_manual(c)
        window.apply_contact_decision(dict(action='stroke'),tail)
        assert window.code=='phsnz' and len(window.evidence)==5
        assert '近' in window.candidates[:5]
        visual=next(v for v in trial_candidate_visuals(window) if v['character']=='近')
        assert visual['entered']==7 and '2处' in visual['status'] and 'next' not in visual['states']
        window.undo()
        assert window.code=='phsn' and len(window.evidence)==4
        window.undo()
        assert window.code=='phs'
        assert next(r for r in window.matches if r['char']=='斤')['logical_entered']==4
        window.add_manual('n')
        window.apply_contact_decision(dict(action='stroke'),tail)
        window.commit(window.candidates.index('近'))
        assert window.editor.text()=='近' and not window.evidence
    finally:
        window.close();app.processEvents()
