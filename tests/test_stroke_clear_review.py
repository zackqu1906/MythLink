"""Control-vs-handwriting regressions from confirmed traces, without BLE access."""
import json
import math
from pathlib import Path

import pytest

from proximic_ring.touchpad.stroke.stroke_clear_review import vertical_structure
from proximic_ring.touchpad.stroke.stroke_test_gestures import recognize

CASES = json.loads((Path(__file__).parent/'fixtures/clear_review_regression.json').read_text())['cases']
FOLDS = [row for row in CASES if row['kind']=='stroke']


@pytest.mark.parametrize('row',CASES,ids=lambda row:f"recorded-{row['sampleNumber']}")
def test_recorded_fold_clear_and_two_pass_controls(row):
    decision=recognize(row['points'])
    assert decision['action']==row['expected']
    if row['kind']=='stroke':
        assert recognize(row['points'],clear_review_enabled=False)['action']=='clear-all'
        assert decision['baselineAction']=='clear-all'
        assert decision['clearReview']['status']=='stroke'
        assert decision['clearReview']['recognition']['category']=='z'
    elif row['kind']=='clear':
        assert not decision['clearReview']['protect']


@pytest.mark.parametrize('scale,rotation',[(.5,0.),(2.,0.),(1.,-8.),(1.,8.)])
def test_fold_protection_survives_size_translation_pauses_and_small_tilt(scale,rotation):
    angle=math.radians(rotation)
    for row in FOLDS:
        moved=[(100+scale*(x*math.cos(angle)-y*math.sin(angle)),
                -70+scale*(x*math.sin(angle)+y*math.cos(angle))) for x,y in row['points']]
        paused=[p for point in moved for p in [point]*3]
        assert recognize(moved)['action']=='stroke'
        assert recognize(paused)['action']=='stroke'
        assert vertical_structure(moved)==vertical_structure(paused)


@pytest.mark.parametrize('rotation',[-15.,0.,15.])
def test_tilted_clear_does_not_load_or_consult_handwriting_model(rotation):
    class Unused:
        def recognize(self,points):
            raise AssertionError('horizontal clear should not consult the model')
    angle=math.radians(rotation)
    points=[(x*math.cos(angle)-y*math.sin(angle),x*math.sin(angle)+y*math.cos(angle))
            for x,y in [(0,0),(40,0),(0,0),(40,0)]]
    decision=recognize(points,stroke_dictionary=Unused())
    assert decision['action']=='clear-all'
    assert not decision['clearReview']['modelAttempted']


@pytest.mark.parametrize('result',[None,
    dict(category='z',shape='横折',distance=.3),
    dict(category='z',shape='横折',distance=float('nan')),
    dict(category='n',shape='点',distance=.02)])
def test_uncertain_model_does_not_authorize_clear_of_structured_descent(result):
    class Uncertain:
        def recognize(self,points): return result
    decision=recognize(FOLDS[0]['points'],stroke_dictionary=Uncertain())
    assert decision['action']=='stroke'
    assert decision['clearReview']['status']=='uncertain'


def test_model_failure_preserves_input_and_marks_review_error():
    class Broken:
        def recognize(self,points): raise RuntimeError('test model failure')
    decision=recognize(FOLDS[0]['points'],stroke_dictionary=Broken())
    assert decision['action']=='stroke'
    assert decision['clearReview']['reason']=='model-error'


def test_close_fold_alternative_is_retained_without_rewriting_primary_recognition():
    result=dict(category='s',shape='竖钩',distance=.02,
                category_scores=[dict(category='z',shape='竖折折钩',distance=.025)])
    class Close:
        def recognize(self,points): return result
    decision=recognize(FOLDS[0]['points'],stroke_dictionary=Close())
    assert decision['clearReview']['status']=='stroke'
    assert decision['clearReview']['recognition'] is result
    assert result['category']=='s'
