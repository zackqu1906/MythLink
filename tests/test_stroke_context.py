from proximic_ring.stroke_context import StrokeContext


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
