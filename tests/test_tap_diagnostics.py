"""Intent labeling must work when both click detectors miss the user's click."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import collect_tap_diagnostics as collect
from ring_python_sdk.touchpad.core import ClickDetector


@pytest.fixture
def app():
    return collect.QApplication.instance() or collect.QApplication([])


def emit(window, kind, **value):
    window.recorder.record(kind, **value)
    window.observe(dict(kind=kind, **value))


def test_continuous_click_intent_saves_every_outcome_without_manual_confirmation(app, tmp_path):
    path = tmp_path/'continuous.jsonl'
    window = collect.Window(path, synthetic=True)
    window.connected()
    emit(window, 'stroke', points=[(0, 0), (4, 0)], result=dict(category='h', shape='横'))
    emit(window, 'sdk_click', event=dict(step=10))
    emit(window, 'firmware_gesture', event=dict(class_id=5))
    emit(window, 'raw_velocity', step=11, vx=0., vy=0.)
    assert window.outcomes['stroke'] == 1
    window.close()
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert rows[0]['mode'] == 'continuous_clicks'
    assert all(row['intended'] == 'click' for row in rows)
    active = [row for row in rows if row['phase'] == 'collecting']
    assert {'stroke', 'sdk_click', 'firmware_gesture', 'raw_velocity'} <= {row['kind'] for row in active}
    assert not any(row['kind'] in {'annotation', 'trial_start'} for row in rows)
    assert rows[-1]['kind'] == 'session_end'


def test_style_and_reset_are_preserved_without_rejecting_click_recording(app, tmp_path):
    path = tmp_path/'reset.jsonl'
    window = collect.Window(path, synthetic=True)
    window.connected()
    window.change_style('快速连续点击')
    emit(window, 'sdk_contact', event=dict(state='reset', step=10))
    emit(window, 'stroke', points=[(0, 0), (4, 0)], result=dict(category='h', shape='横'))
    assert window.outcomes['contact_reset'] == 1 and window.outcomes['stroke'] == 1
    window.close()
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    stroke = next(row for row in rows if row['kind'] == 'stroke')
    assert stroke['intended'] == 'click' and stroke['click_style'] == '快速连续点击'


def test_no_output_recording_still_preserves_interval_and_raw_data(app, tmp_path):
    path = tmp_path/'none.jsonl'
    window = collect.Window(path, synthetic=True)
    window.recorder.record('raw_velocity', step=1, vx=0., vy=0.)
    window.connected()
    window.recorder.record('raw_velocity', step=2, vx=0., vy=0.)
    window.close()
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    velocities = [row for row in rows if row['kind'] == 'raw_velocity']
    assert [row['phase'] for row in velocities] == ['warmup', 'collecting']
    assert next(row for row in rows if row['kind'] == 'collection_end')['outcomes'] == {}


@pytest.mark.parametrize('duration,speed,reason', [(12, .1, 'click'), (35, 1., 'movement'),
                                                (100, .1, 'duration')])
def test_observing_raw_velocity_and_rejection_does_not_change_detector(duration, speed, reason):
    rows = []
    recorder = SimpleNamespace(record=lambda kind, **value: rows.append(dict(kind=kind, **value)))
    detector = ClickDetector()
    processor = SimpleNamespace(post=SimpleNamespace(click=detector), poll=lambda: [])
    collect.instrument_processor(processor, recorder)
    original = ClickDetector()
    for step, probability in enumerate([.01]*25+[.99]*duration+[.01]*25):
        assert detector.probability(step, probability) == original.probability(step, probability)
        assert detector.movement(step, (speed, 0.)) == original.movement(step, (speed, 0.))
        assert list(detector.contact_edges) == list(original.contact_edges)
        detector.contact_edges.clear()
        original.contact_edges.clear()
    verdicts = [row for row in rows if row['kind'] == 'sdk_click_verdict']
    assert len(verdicts) == 1 and verdicts[0]['reason'] == reason
    assert verdicts[0]['duration_ms'] == duration*5
    assert len([row for row in rows if row['kind'] == 'raw_velocity']) == 50+duration
