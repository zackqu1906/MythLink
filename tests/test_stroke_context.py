from proximic_ring.touchpad.stroke.stroke_context import StrokeContext


def test_longer_context_overrides_generic_followers():
    predictor = StrokeContext(dict(maxContext=4, total=100, unigrams={'吗':10,'好':50},
        contexts={'你':[50,'好',[50]],'好':[10,'吗',[10]],'你好':[10,'吗',[10]]}))
    assert predictor.suggestions('你好')[0] == '吗'
    assert predictor.suggestions('你')[0] == '好'


def test_non_chinese_suffix_and_unknown_context_do_not_invent_candidates():
    predictor = StrokeContext(dict(maxContext=4,total=100,unigrams={'好':50},contexts={'你':[10,'好',[10]]}))
    assert predictor.suggestions('你 ') == []
    assert predictor.suggestions('abc') == []
    assert predictor.suggestions('陌生') == []


def test_packaged_demo_model_has_real_followup_suggestions():
    assert StrokeContext().suggestions('你好')


def test_wanxiang_model_covers_common_continuations():
    from proximic_ring.touchpad.stroke.stroke_context import EARLY_STROKES, early_bonuses
    predictor = StrokeContext(model='wanxiang')
    assert predictor.model == 'wanxiang' and predictor.early_language_factor > 1
    assert predictor.data['sources']['license'] == 'CC BY 4.0'
    # Inside a word and across words; the legacy top-8 table misses most of these.
    assert '天' in predictor.suggestions('今', limit=20)
    assert '板' in predictor.suggestions('石', limit=20)
    assert '的' in predictor.suggestions('我们', limit=10)
    bonuses = predictor.stroke_bonuses('我们', '的了')
    early = early_bonuses(predictor, bonuses, EARLY_STROKES)
    assert early['的'] == bonuses['的'] * predictor.early_language_factor
    assert early_bonuses(predictor, bonuses, EARLY_STROKES + 1) is bonuses


def test_legacy_and_custom_models_get_no_early_boost():
    from proximic_ring.touchpad.stroke.stroke_context import early_bonuses
    legacy = StrokeContext()
    custom = StrokeContext(dict(maxContext=4, total=100, unigrams={'好': 50}, contexts={}))
    assert legacy.model == 'legacy' and custom.model == 'custom'
    bonuses = {'好': .5}
    assert early_bonuses(legacy, bonuses, 1) is bonuses and early_bonuses(custom, bonuses, 1) is bonuses
    assert early_bonuses(object(), bonuses, 1) is bonuses
