import json

import pytest

from proximic_ring.touchpad.stroke.stroke_experiment import SentenceTrial, edit_distance


def test_retries_failed_delivery_extra_strokes_and_unsupported_labels():
    now = [10.]
    trial = SentenceTrial('小，A', [['小', 'spn', 1]], clock=lambda: now[0])
    trial.stroke(0, 0, {'category': 'h', 'shape': '横'}, 2)
    trial.event('undo_stroke')
    trial.stroke(0, 0, {'category': 's', 'shape': '竖'}, 4)
    trial.stroke(0, 3, {'category': 'h'}, 3)  # Extra stroke is an error.
    trial.stroke(2, 0, {'category': 'h'}, 1)  # No reference for A.
    trial.commit(0, None, False)
    trial.commit(0, '小', True)
    now[0] = 70.
    report = trial.finish('小')
    assert report['elapsed_s'] == 60
    assert report['stroke_accuracy'] == pytest.approx(1 / 3)
    assert report['unscored_strokes'] == 1
    assert report['delivery_success_rate'] == .5
    assert report['text_accuracy'] == pytest.approx(1 / 3)
    assert report['characters_per_minute'] == 1
    assert report['per_category']['s']['accuracy'] == .5
    trial.event('manual_stroke')
    now[0] = 100.
    assert trial.summary('小')['elapsed_s'] == 60
    assert len(trial.events) == 7


def test_edit_distance_and_empty_denominators():
    assert edit_distance('你好，世界。', '你号世界。') == 2
    trial = SentenceTrial('小', [['小', 'spn', 1]])
    report = trial.summary('')
    assert report['stroke_accuracy'] is None
    assert report['delivery_success_rate'] is None
    assert report['text_accuracy'] == 0
    with pytest.raises(ValueError):
        SentenceTrial('', [])


def test_sentence_ui_auto_finish_punctuation_report_and_restart(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window, prepare, trace
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.target_input.setPlainText('小。')
        window.start_trial()
        window.accept_stroke(dict(points=trace(*([(0, 2)] * 30))))
        assert window.trial.events[-1]['expected'] == 's'
        window.punctuate('。')
        assert window.editor.text() == '小。'
        assert not window.trial.active
        report = json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['completed']
        assert report['commit_attempts'] == 2
        assert report['stroke_accuracy'] == 1
        assert report['delivery_success_rate'] == 1
        assert window.report_path.with_suffix('.csv').is_file()
        saved_report = window.report_path.read_bytes()
        saved_csv = window.report_path.with_suffix('.csv').read_bytes()
        metrics = window.trial_metrics.text(), window.target_preview.text(), window.progress.value()
        events = list(window.trial.events)
        assert not window.editor.isReadOnly()
        window.accept_stroke(dict(points=trace(*([(0, 2)] * 30))))
        assert window.code == 's'
        tap_time = window.tap_guard.clock() + 1
        monkeypatch.setattr(window.tap_guard, 'clock', lambda: tap_time)
        window.accept_sdk_tap()
        assert window.editor.text() == '小。小' and not window.code
        window.punctuate('，')
        assert window.editor.text() == '小。小，'
        window.delete_one()
        assert window.editor.text() == '小。小'
        window.add_manual('s')
        window.undo()
        assert not window.code
        window.add_manual('s')
        window.clear()
        assert not window.code
        window.update_trial()
        assert (window.trial_metrics.text(), window.target_preview.text(), window.progress.value()) == metrics
        assert window.trial.events == events
        assert window.report_path.read_bytes() == saved_report
        assert window.report_path.with_suffix('.csv').read_bytes() == saved_csv
        window.start_trial()
        assert window.trial.active and not window.editor.text() and not window.code
        window.add_manual('s')
        window.finish_trial()
        report = json.loads(window.report_path.read_text(encoding='utf-8'))
        assert not report['completed']
        assert report['stroke_accuracy'] is None
        assert report['counts']['manual_stroke'] == 1
        window.clear()
        window.add_manual('s')
        window.commit(window.candidates.index('小'))
        assert window.editor.text() == '小' and not window.code
        assert json.loads(window.report_path.read_text(encoding='utf-8')) == report
    finally:
        window.close()
        app.processEvents()


def test_free_writing_without_target_then_start_clears_practice(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window, prepare, trace, select_after_hold
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.target_input.clear()
        window.accept_stroke(dict(points=trace(*([(0, 2)] * 30))))
        assert window.code == 's' and '小' in window.candidates
        select_after_hold(window, (0, 0))
        assert window.editor.text() == '小' and not window.code
        window.punctuate('。')
        assert window.editor.text() == '小。'
        assert window.trial is None and window.report_path is None
        assert not window.finish_trial_button.isEnabled()
        window.add_manual('s')
        window.target_input.setPlainText('小。')
        window.start_trial()
        assert window.trial.active and window.trial.events == []
        assert not window.editor.text() and not window.code and not window.evidence
        assert window.editor.isReadOnly()
    finally:
        window.close()
        app.processEvents()


def test_sentence_ui_wrong_commit_delete_failed_tap_and_close_save(tmp_path, monkeypatch):
    from test_complete_stroke_input import load_window, prepare
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.target_input.setPlainText('小')
        window.start_trial()
        window.candidates = []
        window.accept_sdk_tap()
        assert window.trial.events[-1]['delivered'] is False
        window.candidates = ['你']
        window.commit(0)
        assert window.editor.text() == '你' and window.trial.active
        assert window.trial.events[-1]['correct'] is False
        window.delete_one()
        assert not window.editor.text()
        window.close()
        report = json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['finish_reason'] == 'window_closed'
        assert report['commit_attempts'] == 2
        assert report['delivery_success_rate'] == .5
        assert report['commit_accuracy'] == 0
        assert report['counts']['delete_character'] == 1
    finally:
        window.close()
        app.processEvents()
