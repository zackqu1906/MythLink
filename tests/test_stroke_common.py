import json
from importlib.resources import files

from proximic_ring.touchpad.stroke.stroke_common import common_characters, candidate_scope_snapshot
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_gesture_context import StrokeGestureContext
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary


def test_common_scope_covers_every_character_and_preserves_unranked_shi():
    common=common_characters()
    d=StrokeDictionary()
    assert len(common)==6500
    assert common <= {c for c,_,_ in d.entries}
    assert set('什么你我他日常输入霓袅')<=common
    assert not (set('龘國亻氵讠丨丶丿〇')&common)
    assert '什' not in d.ranks
    assert d.candidates('pshs',allowed_characters=common)[0]=='什'
    assert '仃' in d.candidates('pshs',allowed_characters=common)
    assert '乙' in d.candidates('z',allowed_characters=common)
    assert candidate_scope_snapshot()['allowedCount']==6500
    assert candidate_scope_snapshot()['mode']=='common-6500'
    assert candidate_scope_snapshot(False)['mode']=='all-characters'
    data=json.loads(files('proximic_ring').joinpath('assets/stroke-common-characters.json').read_text(encoding='utf-8'))
    assert data['source']['property']=='kTGH' and data['source']['indexLast']==6500
    assert files('proximic_ring').joinpath('assets/unicode-license.txt').is_file()


def test_filter_precedes_limit_and_exact_priority_in_strict_and_singleton_fuzzy():
    d=StrokeDictionary(dict(templates=[],ranks={'亻':0,'龘':1,'什':100000,'何':2},
        entries=[['亻','ps',3],['龘','pshs',1],['什','pshs',1],['何','pshszhs',1]]))
    common=common_characters()
    assert d.candidates('ps',limit=1)==['亻']
    assert d.candidates('ps',limit=1,allowed_characters=common)==['何']
    assert d.candidates('pshs',limit=1,allowed_characters=common)==['什']
    evidence=[stroke_evidence(c) for c in 'pshs']
    assert d.fuzzy_candidates(evidence,limit=1,allowed_characters=common)[0]['char']=='什'
    assert d.fuzzy_candidates(evidence,limit=1)[0]['char']=='龘'
    assert not d.candidates('ps',allowed_characters=set())
    assert not d.fuzzy_candidates(evidence,allowed_characters=set())


def test_fuzzy_filter_cannot_be_overridden_by_strong_rare_context():
    d=StrokeDictionary(dict(templates=[],ranks={'龘':0,'什':100000},
        entries=[['龘','pshs',1],['什','pshs',1]]))
    evidence=[stroke_evidence(c)for c in 'psh']+[stroke_evidence('s',dict(
        category_scores=[dict(category='s',distance=.001),dict(category='p',distance=.002)]))]
    predictor=StrokeContext(dict(maxContext=1,total=1000,unigrams={'龘':999,'什':1},
                                 contexts={'你':[1000,'龘什',[999,1]]}))
    rows=d.fuzzy_candidates(evidence,limit=1,predictor=predictor,context='你',
                            allowed_characters=common_characters())
    assert [row['char']for row in rows]==['什'] and rows[0]['exact_match']
    assert not d.fuzzy_candidates(evidence,allowed_characters={'何'})


def test_next_character_filter_precedes_limit_and_does_not_fall_back_to_rare():
    predictor=StrokeContext(dict(maxContext=1,total=2000,
        unigrams={'龘':1500,'好':500},contexts={'你':[2000,'龘好',[1500,500]],'他':[1500,'龘',[1500]]}))
    assert predictor.suggestions('你',limit=1)==['龘']
    assert predictor.suggestions('你',limit=1,allowed_characters=common_characters())==['好']
    assert predictor.suggestions('他',allowed_characters=common_characters())==[]


def test_deletion_language_evidence_uses_the_same_common_character_scope():
    d=StrokeDictionary(dict(templates=[],ranks={},entries=[['龘','hp',1],['什','hp',1],['何','z',1]]))
    predictor=StrokeContext(dict(maxContext=1,total=1000,unigrams={'龘':999,'什':1,'何':0},
                                 contexts={'你':[1000,'龘什',[999,1]]}))
    full=StrokeGestureContext(d,predictor)
    common=StrokeGestureContext(d,predictor,allowed_characters=common_characters())
    assert full.language_evidence('你','h')['write']['char']=='龘'
    assert common.language_evidence('你','h')['write']['char']=='什'
    assert common.snapshot()['characterScope']['count']==6500


def test_added_characters_have_complete_strict_fuzzy_and_repair_candidates():
    from proximic_ring.touchpad.stroke.stroke_word_correction import WholeCharacterCorrector
    from proximic_ring.touchpad.stroke.stroke_glyphs import glyph_library
    d = StrokeDictionary()
    common = common_characters()
    corrector = WholeCharacterCorrector(d.entries, d.ranks, allowed_characters=common)
    for character in '霓袅':
        code = glyph_library()[character]['code']
        assert character in d.candidates(code, allowed_characters=common)
        assert character in [r['char'] for r in d.fuzzy_candidates(
            [stroke_evidence(c) for c in code], allowed_characters=common)]
        # Wrong first stroke: scope expansion must reach the repair index too.
        observed = ('s' if code[0] != 's' else 'h') + code[1:]
        assert character in [r['char'] for r in corrector.candidates(observed)]
    predictor = StrokeContext(dict(maxContext=1, total=100,
        unigrams={'霓':50, '袅':50}, contexts={'虹':[50,'霓',[50]], '炊':[50,'袅',[50]]}))
    assert predictor.suggestions('虹', allowed_characters=common) == ['霓']
    assert predictor.suggestions('炊', allowed_characters=common) == ['袅']
