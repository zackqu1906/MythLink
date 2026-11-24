from copy import deepcopy
import gzip
import json

import pytest

from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_joined import (FAMILIES, LEGACY_POLICY, EXTENDED_POLICY, POLICY,
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
    walk_templates = [t for t in registry['referenceTemplates'] if t['family'] == 'walk']
    assert len(walk_templates) >= 3
    for template in walk_templates:
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
    assert upgraded.permits(dictionary,'后','pphszh','fan',0,'pp')
    assert not upgraded.permits(dictionary,'瓜','ppznn','fan',0,'pp')
    assert not upgraded.permits(dictionary,'延','pshzzn','walk',3,'nzn')  # 廴 is not 辶.
    for char,variants in upgraded.positions.items():
        for full,annotations in variants.items():
            assert any(e[1]==full for e in dictionary.by_character[char])
            for row in annotations:
                assert row['indices']==list(range(row['start'],row['start']+row['length']))
                assert full[row['start']:row['start']+row['length']] in FAMILIES[row['family']]


def test_pie_horizontal_uses_spatial_pairs_and_keeps_same_code_choices():
    dictionary = StrokeDictionary(recognizer='dtw')
    model = JoinedStrokeModel.load()
    for char in '千午牛禾毛手乍气乞':
        assert any(model.permits(dictionary, char, code, 'pie-horizontal', 0, 'ph')
                   for _, code, _ in dictionary.by_character[char])
    assert model.permits(dictionary, '作', 'psphshh', 'pie-horizontal', 2, 'ph')
    assert model.permits(dictionary, '汽', 'nnhphhz', 'pie-horizontal', 3, 'ph')
    assert not model.permits(dictionary, '斤', 'pphs', 'pie-horizontal', 1, 'ph')
    points = [(0, 0), (-20, 30), (30, 30)]
    options = model.recognize(points)
    assert any(o['family'] == 'pie-horizontal' for o in options)
    result = dictionary.recognize(points)
    assert result['shape'] == '撇折'
    evidence = [stroke_evidence(result['category'], dict(result, joined_options=options)),
                stroke_evidence('h'), stroke_evidence('s')]
    chars, rows = decode(dictionary, evidence, model)
    assert {'手', '午', '牛'} <= set(chars[:5])
    assert all(row['logical_entered'] == 4 for row in rows if row['char'] in '手午牛')
    assert not any(o['family'] == 'pie-horizontal' for o in model.recognize([(0, 0), (-20, 30)]))


def test_mouth_tail_recognizes_shallow_z_with_a_matching_prefix():
    dictionary = StrokeDictionary(recognizer='dtw')
    model = JoinedStrokeModel.load()
    assert model.permits(dictionary, '口', 'szh', 'mouth-tail', 1, 'zh')
    assert model.permits(dictionary, '回', 'szszhh', 'mouth-tail', 3, 'zh')
    assert not model.permits(dictionary, '日', 'szhh', 'mouth-tail', 1, 'zh')
    points = [(0, 0), (45, 0), (0, 12), (45, 12)]
    prefix = [stroke_evidence('s')]
    options = model.recognize(points)
    assert any(o['family'] == 'mouth-tail' for o in options)
    flat = [(0, 0), (45, 0), (0, 0), (45, 0)]
    assert not any(o['family'] == 'mouth-tail' for o in model.recognize(flat))
    result = dictionary.recognize(points)
    chars, rows = decode(dictionary, prefix + [
        stroke_evidence(result['category'], dict(result, joined_options=options))], model)
    assert '口' in chars[:5]
    match = next(row for row in rows if row['char'] == '口')
    assert match['joined_contacts'] == [1] and match['logical_entered'] == 3


@pytest.mark.parametrize('char,tail', [('红', 'hsh'), ('约', 'pzn'), ('绞', 'nhpnpn')])
def test_compact_silk_decodes_the_whole_radical(upgraded, char, tail):
    dictionary = StrokeDictionary(recognizer='dtw')
    points = [(0, 0), (-20, 30), (10, 28), (-12, 54), (18, 47)]
    options = upgraded.recognize(points)
    assert any(o['family'] == 'silk-whole' and o['codes'] == ['zzh'] for o in options)
    assert not any(o['family'] == 'silk-whole' for o in upgraded.recognize(points[:3]))
    assert not upgraded.permits(dictionary, '丝', 'zzzzh', 'silk-whole', 0, 'zzh')
    result = dictionary.recognize(points)
    ink = stroke_evidence(result['category'], dict(result, joined_options=options))
    chars, rows = decode(dictionary, [ink] + [stroke_evidence(c) for c in tail], upgraded)
    assert char in chars[:5]
    match = next(row for row in rows if row['char'] == char)
    assert match['exact_match']
    assert match['logical_entered'] == 3 + len(tail)
    assert match['logical_to_physical'] == [0, 0, 0, *range(1, len(tail)+1)]
    assert match['joined_groups'][0]['family'] == 'silk-whole'
    _, incomplete = merge_joined_candidates(dictionary, [ink], [], [], upgraded, allowed={char})
    assert incomplete[0]['logical_prefix'] == 'zzh' and not incomplete[0]['exact_match']


def test_whole_silk_is_one_contact_and_a_direct_common_scope_choice(upgraded):
    dictionary = StrokeDictionary(recognizer='dtw')
    head = [(0, 0), (-20, 30), (10, 28), (-12, 54), (18, 47)]
    full = head + [(-18, 67), (24, 53)]
    assert all(o['family'] != 'silk-whole' for o in upgraded.recognize(head[:3]))
    result = dictionary.recognize(full)
    contact = stroke_evidence(result['category'], dict(result, joined_options=upgraded.recognize(full)))
    assert any(o['family'] == 'silk-whole' for o in contact['joined_options'])
    chars, rows = decode(dictionary, [contact], upgraded)
    assert '纟' in chars[:5]
    row = next(r for r in rows if r['char'] == '纟')
    assert row['exact_match'] and row['joined_label'] == '连笔 1→3' and row['logical_to_physical'] == [0, 0, 0]
    from proximic_ring.touchpad.stroke.stroke_glyphs import candidate_glyph
    assert candidate_glyph('纟', row['code'], 3, fuzzy=tuple(row['glyph_fuzzy']))['available']
    for char, code, start in [('给', 'zzhpnhszh', 0), ('辫', 'nhnphhpzzhnhnphhs', 7)]:
        ink = [stroke_evidence(c) for c in code[:start]] + [contact] + [stroke_evidence(c) for c in code[start+3:]]
        _, rows = decode(dictionary, ink, upgraded)
        row = next(r for r in rows if r['char'] == char)
        assert row['exact_match'] and row['logical_to_physical'][start:start+3] == [start]*3
    assert not upgraded.permits(dictionary, '丝', 'zzzzh', 'silk-whole', 2, 'zzh')


@pytest.mark.parametrize('char,reference,family,span', [
    ('抑', '仰', 'turning-head', 'pz'), ('茚', '印', 'turning-head', 'pz'),
    ('低', '氏', 'turning-head', 'pz'), ('泉', '水', 'water-tail', 'pn'),
    ('函', '函', 'water-tail', 'pn'), ('涵', '函', 'water-tail', 'pn'),
    ('菡', '菡', 'water-tail', 'pn'), ('蜬', '函', 'water-tail', 'pn'),
    ('泰', '泰', 'water-tail', 'pn'), ('傣', '泰', 'water-tail', 'pn'),
    ('暴', '暴', 'water-tail', 'pn'), ('爆', '暴', 'water-tail', 'pn'),
    ('曝', '暴', 'water-tail', 'pn'), ('瀑', '暴', 'water-tail', 'pn'),
    ('儤', '暴', 'water-tail', 'pn'), ('承', '承', 'water-tail', 'pn'),
    ('而', '而', 'de', 'ps'), ('耐', '耐', 'de', 'ps'),
    ('儒', '需', 'de', 'ps'), ('瑞', '而', 'de', 'ps'),
    ('耏', '而', 'de', 'ps'), ('缅', '面', 'de', 'ps'), ('偭', '面', 'de', 'ps'),
    ('逅', '后', 'fan', 'pp'), ('垢', '后', 'fan', 'pp'),
    ('烦', '页', 'de', 'ps'), ('抱', '包', 'wrap-head', 'pz'),
    ('缎', '段', 'de', 'ps'),
])
def test_joined_reference_geometry_inherits_at_the_correct_offsets(upgraded, char, reference, family, span):
    dictionary = StrokeDictionary(recognizer='dtw')
    template = next(t for t in component_registry()['referenceTemplates']
                    if t['family'] == family and t['referenceCharacter'] == reference)
    points = list(zip(*template['features'][:2]))
    options = upgraded.recognize(points)
    assert any(o['family'] == family for o in options)
    options = [o for o in options if o['family'] == family]
    code, annotation = next((code, span_row) for code, annotations in upgraded.positions[char].items()
                            for span_row in annotations if span_row['family'] == family)
    start = annotation['start']
    assert code[start:start+2] == span
    result = dictionary.recognize(points)
    evidence = ([stroke_evidence(c) for c in code[:start]] +
                [stroke_evidence(result['category'], dict(result, joined_options=options))] +
                [stroke_evidence(c) for c in code[start+2:]])
    _, rows = merge_joined_candidates(dictionary, evidence, [], [], upgraded, allowed={char})
    assert rows[0]['exact_match'] and rows[0]['joined_contacts'] == [start]


def test_han_allows_both_joins_and_preserves_other_strokes(upgraded):
    dictionary = StrokeDictionary()
    evidence = [joined('le-zi'), stroke_evidence('n'), stroke_evidence('h'),
                joined('water-tail'), stroke_evidence('z'), stroke_evidence('s')]
    chars, rows = decode(dictionary, evidence, upgraded)
    assert '函' in chars[:5]
    row = next(r for r in rows if r['char'] == '函')
    assert row['exact_match'] and row['logical_prefix'] == 'zsnhpnzs'
    assert row['logical_to_physical'] == [0, 0, 1, 2, 3, 3, 4, 5]
    assert upgraded.permits(dictionary, '菡', 'hshszsnhpnzs', 'water-tail', 8, 'pn')
    assert upgraded.permits(dictionary, '蜬', 'szhshnzznnpnzs', 'water-tail', 10, 'pn')
    assert not upgraded.permits(dictionary, '函', 'zsnhpnzs', 'water-tail', 3, 'pn')
    assert not upgraded.permits(dictionary, '人', 'pn', 'water-tail', 0, 'pn')
    assert not upgraded.permits(dictionary, '布', 'hpszs', 'de', 1, 'ps')


def test_straight_vertical_is_not_a_pie_shu_join_even_with_a_close_template(model):
    from proximic_ring.touchpad.stroke.stroke_dtw import sequence

    points = [(0, 0), (-1, 20), (-2, 50)]
    templates = [dict(family=family, features=features.tolist())
                 for family, features in zip(model.labels, model.bank)]
    templates.append(dict(family='de', features=sequence(points).tolist()))
    personal = JoinedStrokeModel(dict(schema='ring-joined-stroke-model', version=2, policy=POLICY,
                                      source={}, templates=templates, positions=model.positions))
    assert not any(option['family'] == 'de' for option in personal.recognize(points))


def test_hou_mid_join_stays_removed_in_nested_components_and_old_models(model, tmp_path):
    templates = [dict(family=family, features=features.tolist()) for family, features in zip(model.labels, model.bank)]
    templates += [dict(templates[0], family='silk-head'), dict(templates[0], family='hou-middle')]
    path = tmp_path/'previous-model.json.gz'
    payload = dict(schema='ring-joined-stroke-model', version=2, policy=EXTENDED_POLICY, source={}, templates=templates)
    path.write_bytes(gzip.compress(json.dumps(payload).encode('utf-8')))
    upgraded = JoinedStrokeModel.load(path)
    assert {'silk-head', 'hou-middle'}.isdisjoint(FAMILIES)
    assert {'silk-head', 'hou-middle'}.isdisjoint(upgraded.labels)
    dictionary = StrokeDictionary()
    for char, code, start in [('后', 'pphszh', 0), ('逅', 'pphszhnzn', 0), ('垢', 'hshpphszh', 3)]:
        assert upgraded.permits(dictionary, char, code, 'fan', start, 'pp')
        assert not upgraded.permits(dictionary, char, code, 'hou-middle', start+1, 'ph')
        assert not upgraded.permits(dictionary, char, code, 'pie-horizontal', start+1, 'ph')


def test_page_join_composes_with_reordered_step_and_wrap_with_legacy_grass(upgraded):
    dictionary = StrokeDictionary(recognizer='dtw')
    for char, prefix, family, tail in [('频', 'shshnsnh', 'de', 'zpn'), ('苞', 'hshs', 'wrap-head', 'zhz')]:
        evidence = [stroke_evidence(c) for c in prefix] + [joined(family)] + [stroke_evidence(c) for c in tail]
        _, rows = merge_joined_candidates(dictionary, evidence, [], [], upgraded, allowed={char})
        assert rows[0]['exact_match'] and rows[0]['joined_contacts'] == [len(prefix)]


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
