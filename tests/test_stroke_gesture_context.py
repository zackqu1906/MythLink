from types import SimpleNamespace

from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_gesture_context import StrokeGestureContext


CONFIG = dict(minAngle=-30., maxAngle=13., minLength=5., minStraightness=.93, minLeftConsistency=.96)


def helper():
    dictionary = SimpleNamespace(entries=[('我', 'phshzpn', 1), ('物', 'phshhppzpp', 1),
                                         ('犄', 'phshhpszh', 1), ('你', 'pspzspn', 1)])
    predictor = StrokeContext(dict(maxContext=2, total=1000, unigrams={'我':20, '物':5, '犄':1, '你':20},
                                    contexts={'帮': [100, '我物', [90, 1]]}))
    return StrokeGestureContext(dictionary, predictor)


def decide(engine, angle, baseline='none', text='帮', code='phshz', **overrides):
    metrics = dict(dx=-40., leftAngle=angle, length=42., straightness=1., leftConsistency=1.)
    metrics.update(overrides)
    return engine.decide(metrics, baseline, before_text=text, stroke_code=code,
                         horizontal_segments=1, delete_config=CONFIG)


def test_local_context_can_preserve_shallow_pie_or_recover_a_sloped_delete():
    engine = helper()
    writing = decide(engine, 10., 'delete-one')
    assert writing['action'] == 'none' and writing['changed']
    repair = decide(engine, 15., code='phshh')
    assert repair['action'] == 'delete-one' and repair['changed']
    assert abs(writing['shiftDegrees']) <= 4 and abs(repair['shiftDegrees']) <= 4
    assert engine.language_evidence('帮', 'phshz')['repair']['char'] != '我'


def test_conflicting_intent_remains_a_real_failure_not_hidden_by_a_prompt():
    # The same linguistic evidence that helps a pie can hurt a genuine left
    # swipe. Keep that decision observable; no expected-label parameter exists.
    assert decide(helper(), 12., 'delete-one')['action'] == 'none'
    assert decide(helper(), 15., 'none', code='phshh')['action'] == 'delete-one'


def test_missing_weak_or_unusable_language_never_overrides_geometry():
    engine = helper()
    for text, code in [('', 'phshz'), ('未知', 'phshz'), ('帮 ', 'phshz'), ('帮', ''), ('帮', 'q')]:
        result = decide(engine, 10., 'delete-one', text=text, code=code)
        assert result['action'] == 'delete-one' and not result['contextUsed']
    weak = StrokeContext(dict(maxContext=2, total=100, unigrams={'我':10}, contexts={'帮':[2,'我',[2]]}))
    result = decide(StrokeGestureContext(engine.dictionary, weak), 10., 'delete-one')
    assert not result['contextUsed'] and result['action'] == 'delete-one'


def test_clear_reversals_strong_geometry_and_other_stroke_features_keep_priority():
    engine = helper()
    for angle, action in [(1., 'delete-one'), (35., 'none')]:
        assert decide(engine, angle, action)['action'] == action
    assert decide(engine, 10., 'clear-all')['action'] == 'clear-all'
    assert decide(engine, 10., 'none', straightness=.8)['action'] == 'none'
    assert decide(engine, 10., 'none', leftConsistency=.7)['action'] == 'none'
    result = engine.decide(dict(dx=-40., leftAngle=15., length=42., straightness=1., leftConsistency=1.),
        'none', before_text='帮', stroke_code='phshh', horizontal_segments=2, delete_config=CONFIG)
    assert not result['contextUsed'] and result['action'] == 'none'


def test_no_dictionary_candidates_alone_is_not_permission_to_delete():
    engine = helper()
    result = decide(engine, 15., code='zzzz')
    assert result['action'] == 'none' and not result['contextUsed']
