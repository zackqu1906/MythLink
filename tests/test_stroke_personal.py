"""Causal writer adaptation: only confirmed ink supervises later recognition."""
import pytest

from proximic_ring.touchpad.stroke.stroke_fuzzy import FuzzyPolicy, retune_evidence, stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_personal import (MAX_COMMITS, PER_CATEGORY, PersonalStrokeModel,
    TextLearningHistory, capture_ink, confirmed_samples, profile_key, qt_text_index)

INK = [(0., 0.), (-20., 100.)]


def dictionary(codes=('s',)):
    return StrokeDictionary(dict(templates=[], ranks={'甲': 0},
                                entries=[['甲', code, 1] for code in codes]))


def result():
    return dict(category='p', shape='撇', distance=.02, category_scores=[
        dict(category='p', shape='撇', distance=.02),
        dict(category='s', shape='竖', distance=.024),
        dict(category='h', shape='横', distance=.5)])


def evidence(raw=None):
    item = stroke_evidence('p', raw or result())
    item['ink'] = capture_ink(INK)
    return [item]


def test_confirmed_wrong_prediction_improves_later_ink_without_mutating_base():
    model, base = PersonalStrokeModel(), result()
    assert model.adapt(INK, base) is base
    token = model.learn(dictionary(), '甲', evidence())
    assert token and model.adapt(INK, base) is base  # One confirmation cannot activate a habit.
    assert model.learn(dictionary(), '甲', evidence()) != token
    corrected = model.adapt(INK, base)
    assert corrected['category'] == 's' and corrected['shape'] == '竖'
    assert corrected['personal_adaptation']['changed']
    assert corrected['base_category_scores'] == base['category_scores']
    assert base['category'] == 'p' and base['distance'] == .02
    assert PersonalStrokeModel(model.dumps()).adapt(INK, base)['category'] == 's'
    assert model.retract() and model.adapt(INK, base) is base


def test_predictions_alone_cannot_learn_and_unseen_ink_keeps_base():
    model = PersonalStrokeModel()
    for _ in range(10):
        model.adapt(INK, result())
    assert model.snapshot()['confirmations'] == 0
    for _ in range(3): model.learn(dictionary(), '甲', evidence())
    assert model.adapt([(0., 0.), (100., 0.)], result()) is not None
    assert model.adapt([(0., 0.), (100., 0.)], result())['category'] == 'p'


def test_prefixes_learn_only_written_consensus_and_never_label_fine_shapes():
    rows = confirmed_samples(dictionary(('sh', 'sn')), '甲', evidence())
    assert len(rows) == 1 and rows[0]['category'] == 's' and 'shape' not in rows[0]
    assert not confirmed_samples(dictionary(('s', 'p')), '甲', evidence())
    assert not confirmed_samples(dictionary(('s',)), '甲', evidence()*2)
    assert not confirmed_samples(dictionary(), '不存在', evidence())
    assert not confirmed_samples(dictionary(), '甲', [stroke_evidence('s')])
    invalid = evidence(); invalid[0].pop('ink')
    assert not confirmed_samples(dictionary(), '甲', invalid)


def test_bad_matches_and_self_certified_adapted_scores_cannot_supervise():
    bad = result(); bad['category_scores'][1]['distance'] = .3
    assert not confirmed_samples(dictionary(), '甲', evidence(bad))
    bad = result(); bad['base_category_scores'] = [dict(category='s', distance=.3), dict(category='p', distance=.02)]
    assert not confirmed_samples(dictionary(), '甲', evidence(bad))
    item = evidence(bad)[0]
    retuned = retune_evidence([item], FuzzyPolicy(100))[0]
    assert retuned['ink'] == item['ink']
    assert retuned['recognition']['base_category_scores'] == bad['base_category_scores']
    assert not confirmed_samples(dictionary(), '甲', [retuned])


def test_repeated_strokes_within_one_character_do_not_count_as_multiple_confirmations():
    model = PersonalStrokeModel()
    model.learn(dictionary(('sssss',)), '甲', evidence()*5)
    assert model.snapshot()['support']['s'] == 1
    assert model.snapshot()['samples'] == 4
    assert model.adapt(INK, result())['category'] == 'p'


def test_storage_and_active_banks_are_bounded_and_retractable_after_reload():
    model = PersonalStrokeModel()
    for _ in range(MAX_COMMITS + 5): model.learn(dictionary(), '甲', evidence())
    assert model.snapshot()['confirmations'] == MAX_COMMITS
    assert model.snapshot()['samples'] == PER_CATEGORY
    restored = PersonalStrokeModel(model.dumps())
    assert restored.retract() and restored.snapshot()['confirmations'] == MAX_COMMITS - 1
    restored.clear()
    assert restored.snapshot()['samples'] == 0


def test_text_edits_retract_only_affected_confirmations():
    model, history = PersonalStrokeModel(), TextLearningHistory()
    first = model.learn(dictionary(), '甲', evidence()); history.track(first, '甲', 1)
    second = model.learn(dictionary(), '甲', evidence()); history.track(second, '甲甲', 2)
    assert not history.reconcile(model, '甲甲乙')
    assert history.reconcile(model, '甲') and model.snapshot()['confirmations'] == 1
    assert history.reconcile(model, '') and model.snapshot()['confirmations'] == 0
    assert not history.reconcile(model, '甲甲')  # Reinsert/redo cannot silently relearn.


@pytest.mark.parametrize('payload', ['{broken', '{}', '{"version":2,"commits":[]}',
    '{"version":1,"commits":[{"id":"one","samples":[{"category":"s","trace":[[0,0]]}]}]}'])
def test_corrupt_profiles_fall_back_without_overwriting_them(payload):
    model = PersonalStrokeModel(payload)
    assert model.error and model.snapshot()['samples'] == 0
    assert model.learn(dictionary(), '甲', evidence()) is None
    model.clear()
    assert model.learn(dictionary(), '甲', evidence())


@pytest.mark.parametrize('points', [[], [(1, 1)]*5, [(0, 0), (float('nan'), 1)],
                                   [(0, 0), (float('inf'), 1)], [(0, 0, 0), (1, 2, 3)]])
def test_invalid_sensor_ink_is_not_learned(points):
    assert capture_ink(points) is None


def test_profile_identifiers_are_separate_and_not_paths():
    assert profile_key('P01') != profile_key('P02')
    assert len(profile_key('../../escape')) == 24
    assert qt_text_index('𠀀甲', 3) == 2


def test_dictionary_adaptation_is_explicit_and_template_baseline_stays_available():
    model = PersonalStrokeModel()
    for _ in range(2): model.learn(dictionary(), '甲', evidence())
    adapted = StrokeDictionary(recognizer='dtw', personal_model=model)
    adapted.recognize(INK)
    adapted._dtw.recognize = lambda points: result()
    assert adapted.recognize(INK)['category'] == 's'
    adapted.personal_model = None
    assert adapted.recognize(INK)['category'] == 'p'


def test_left_dot_input_convention_is_not_a_general_dot_training_label():
    d = dictionary(('n',))
    d._left_dots = {('甲', 'n'): (0,)}
    raw = result(); raw['category_scores'].append(dict(category='n', distance=.025))
    assert not confirmed_samples(d, '甲', evidence(raw))
