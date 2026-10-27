from copy import deepcopy
import json
from pathlib import Path
import pytest

from proximic_ring.touchpad.stroke.stroke_context import StrokeContext, promote_completed_fuzzy
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_word_correction import merge_candidates
from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_word_correction import WholeCharacterCorrector


def auto(letter, **scores):
    return stroke_evidence(letter, dict(category=letter, category_scores=[
        dict(category=category, distance=distance) for category, distance in scores.items()]))


def test_complete_single_ambiguity_survives_many_literal_prefixes_and_repair_insertion():
    partials = list('里国显吐遇鸭畅野愚晶昌冒啦嗽嘲喇鄙嗜哑嘈晃哮曝')
    entries = [[char, 'szhhs'+('h'*(i+1)), 1] for i,char in enumerate(partials)]
    entries += [['叶','szhhs',1], ['申','szhhs',1], ['甲','szhhs',1], ['叮','szhhs',1], ['用','pzhhs',1]]
    ranks = {char:i+1 for i,char in enumerate(partials)}
    ranks.update({'叶':100,'申':120,'甲':140,'叮':200,'用':225})
    d = StrokeDictionary(dict(templates=[], ranks=ranks, entries=entries))
    predictor = StrokeContext(dict(maxContext=4,total=20000,
        unigrams={**{char:100 for char in partials},'叶':100,'申':100,'甲':100,'叮':100,'用':10000,'们':1000},
        contexts={'我':[100,'们',[100]]}))
    evidence = [auto('s',s=.001,p=.05)] + [auto(c,**{c:.001}) for c in 'zhhs']
    before = deepcopy(evidence)
    rows = d.fuzzy_candidates(evidence, predictor=predictor, context='今天我')
    assert rows[0]['exact_match'] and rows[1]['char']=='用' and rows[1]['context_promoted']
    assert rows[1]['corrections']==[0] and evidence==before
    merged = merge_candidates([r['char'] for r in rows], [dict(char=c) for c in '叫中'])
    assert merged[1]=='用' and len(merged)==len(set(merged))
    assert d.fuzzy_candidates(evidence,limit=1,predictor=predictor,context='今天我')[0]['exact_match']
    assert len(d.fuzzy_candidates(evidence,limit=2,predictor=predictor,context='今天我'))==2


def test_no_context_or_manual_or_distant_input_cannot_gain_a_promoted_slot():
    predictor = StrokeContext(dict(maxContext=1,total=10,unigrams={'乙':5},contexts={'甲':[5,'乙',[5]]}))
    first=dict(char='一',code='hhhhh',cost=0,exact_match=True,corrections=[],language_bonus=1,final_score=1)
    second=dict(char='二',code='hhhhhsh',cost=0,exact_match=False,corrections=[],language_bonus=1,final_score=1)
    candidate=dict(char='乙',code='phhhh',cost=1.2,exact_match=False,corrections=[0],language_bonus=1,final_score=2)
    rows=[first,second,candidate]
    ink=[auto('h',h=.001,p=.03)]*5
    assert promote_completed_fuzzy(rows,ink,predictor,'甲',0)==[]
    assert promote_completed_fuzzy(rows,ink,predictor,'甲',30)[1]['char']=='乙'
    for context in ('','陌生','甲 '):
        assert promote_completed_fuzzy(rows,ink,predictor,context,30)==rows
    assert promote_completed_fuzzy(rows,[stroke_evidence('h')]*5,predictor,'甲',30)==rows
    assert promote_completed_fuzzy(rows,ink[:2],predictor,'甲',30)==rows
    for updates in (dict(cost=3),dict(corrections=[0,1]),dict(code='phhh'),dict(language_bonus=-.1)):
        assert promote_completed_fuzzy([first,second,dict(candidate,**updates)],ink,predictor,'甲',30)[1]==second


def test_context_cannot_admit_a_character_without_physical_stroke_support():
    d=StrokeDictionary(dict(templates=[],ranks={'叶':1,'用':2},entries=[['叶','szhhs',1],['用','pzhhs',1]]))
    predictor=StrokeContext(dict(maxContext=1,total=100,unigrams={'用':100},contexts={'我':[100,'用',[100]]}))
    ink=[auto('s',s=.001,h=.002)]+[auto(c,**{c:.001}) for c in 'zhhs']
    assert [r['char'] for r in d.fuzzy_candidates(ink,predictor=predictor,context='我')]==['叶']


@pytest.mark.parametrize('index,old_rank', [(0,26),(1,22),(2,8),(3,14)])
def test_recorded_complete_yong_reaches_second_slot_after_correction_merge(index,old_rank,monkeypatch):
    import proximic_ring.touchpad.stroke.stroke_input as module
    import proximic_ring.touchpad.stroke.stroke_word_correction as correction
    fixture=json.loads((Path(__file__).parent/'fixtures/context_ranking_regression.json').read_text('utf-8'))
    case=fixture['cases'][index]
    ink=[stroke_evidence(item['result']['category'],item['result'],metrics=item['kinematics'])
         for item in case['evidence']]
    dictionary=StrokeDictionary()
    predictor=StrokeContext()
    corrector=WholeCharacterCorrector(dictionary.entries,dictionary.ranks,allowed_characters=common_characters())
    def candidates():
        normal=dictionary.fuzzy_candidates(ink,predictor=predictor,context=case['context'],
                                          allowed_characters=common_characters())
        rows=corrector.candidates(case['code'],evidence=ink,predictor=predictor,context=case['context'],
                                 excluded_characters=[r['char'] for r in normal])
        return merge_candidates([r['char'] for r in normal],rows,
            reserved=1 if any(r.get('context_extra_slot') for r in normal) else 2)
    new=candidates()
    assert new[1]==case['target'] and new[0]=='叶'
    with monkeypatch.context() as old:
        # Reproduce the historical baseline before fine fold ranking existed.
        old.setattr(dictionary,'_fold_orders',{})
        old.setattr(module,'FUZZY_LANGUAGE_WEIGHT',1.)
        old.setattr(module,'promote_completed_fuzzy',lambda rows,evidence,predictor,context,limit:rows[:limit])
        old.setattr(correction,'CORRECTION_LANGUAGE_WEIGHT',.35)
        baseline=candidates()
        assert baseline.index(case['target'])+1==old_rank


def test_live_trial_keeps_completed_fuzzy_candidate_on_first_page_and_commits_it(tmp_path,monkeypatch):
    from test_complete_stroke_input import load_window
    from test_stroke_trial_correction import wait_for
    fixture=json.loads((Path(__file__).parent/'fixtures/context_ranking_regression.json').read_text('utf-8'))
    case=fixture['cases'][0]
    app,window=load_window(tmp_path,monkeypatch)
    try:
        window.editor.setText(case['context'])
        for item in case['evidence']:
            result=dict(item['result'],distance=next(row['distance'] for row in item['result']['category_scores']
                        if row['category']==item['result']['category']))
            window.dictionary.recognize=lambda points,r=result:r
            window.accept_stroke(dict(points=[(0.,0.),(30.,0.)],kinematics=item['kinematics']))
        assert window.candidates[0]=='叶' and window.candidates[1]=='用'
        wait_for(app,lambda:bool(window.correction.rows))
        assert window.candidates[1]=='用' and set('叶申甲叮').issubset(window.candidates[:6])
        assert window.buttons[1].visual['character']=='用'
        window.commit(1)
        assert window.editor.text()=='今天我用' and not window.code
    finally:
        window.close();app.processEvents()
