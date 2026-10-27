from PySide6.QtCore import QObject

from test_inline_ui import inline_ui
from test_stroke_fuzzy_ui import local_editor
from test_stroke_session import stroke, wake
from test_complete_stroke_input import load_window
from test_stroke_personal import INK, dictionary, result
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext


def setup_dictionary(tp):
    tp._dictionary = dictionary()
    tp._predictor = StrokeContext(dict(maxContext=1, total=1, unigrams={'甲': 1}, contexts={}))
    tp.fuzzyMatching = True


def append(tp):
    tp._append_stroke('p', '撇', result(), points=INK)
    assert tp.strokeCandidates == ['甲']


def test_main_confirm_learns_persists_and_text_deletion_retracts(inline_ui):
    _, _, root, tp, _ = local_editor(inline_ui)
    setup_dictionary(tp)
    editor = root.findChild(QObject, 'strokeTestEditor')
    for _ in range(2):
        append(tp)
        tp.selectCandidate(0)
    assert editor.property('text') == '甲甲'
    assert tp._writer_model().snapshot()['confirmations'] == 2, tp._writer_model().snapshot()
    assert tp._writer_model().adapt(INK, result())['category'] == 's'
    tp.setWritingProfile('P02')
    assert tp._writer_model().snapshot()['confirmations'] == 0
    tp.setWritingProfile('默认')
    assert tp._writer_model().snapshot()['confirmations'] == 2
    # Reestablish in-memory edit history for a new confirmation after switching.
    append(tp); tp.selectCandidate(0)
    assert tp._writer_model().snapshot()['confirmations'] == 3
    editor.setProperty('text', '甲甲')
    assert tp._writer_model().snapshot()['confirmations'] == 2
    tp.undoPersonalLearning()
    assert tp._writer_model().snapshot()['confirmations'] == 1
    tp.clearPersonalLearning()
    assert tp._writer_model().snapshot()['samples'] == 0


def test_main_manual_undo_and_disable_do_not_learn(inline_ui):
    _, _, _, tp, _ = local_editor(inline_ui)
    setup_dictionary(tp)
    append(tp); tp.undoStroke()
    tp.addStroke('s'); tp.selectCandidate(0)
    assert tp._writer_model().snapshot()['confirmations'] == 0
    tp.personalLearning = False
    append(tp); tp.selectCandidate(0)
    assert tp._writer_model().snapshot()['confirmations'] == 0
    assert tp._dictionary.personal_model is None
    tp.personalLearning = True
    append(tp)
    tp.setWritingProfile('P02'); tp.personalLearning = False
    assert tp.writingProfile == '默认' and tp.personalLearning  # Keep one composition consistent.
    tp.clearStrokes()


def test_native_learning_requires_successful_matching_ack(stroke):
    c, bridge, tp, _, _ = stroke
    wake(stroke)
    setup_dictionary(tp)
    append(tp)
    tp.selectCandidate(0)
    assert tp._pending_stroke_commit == '甲'
    assert tp._writer_model().snapshot()['confirmations'] == 0
    request = next(m for m in reversed(bridge.messages) if m['type'] == 'stroke_commit')
    c._inline_input._accept(dict(type='stroke_result', epoch=c._inline_input._epoch,
        client_id=request['client_id'], request_id=request['request_id'], success=False, error='retry'))
    assert tp._writer_model().snapshot()['confirmations'] == 0 and tp._stroke_evidence
    tp.selectCandidate(0)
    request = next(m for m in reversed(bridge.messages) if m['type'] == 'stroke_commit')
    c._inline_input._accept(dict(type='stroke_result', epoch=c._inline_input._epoch,
        client_id=request['client_id'], request_id=request['request_id'], success=True))
    assert tp._writer_model().snapshot()['confirmations'] == 1 and not tp._stroke_evidence


def trial_setup(window):
    window.dictionary = dictionary()
    window.dictionary.recognize = lambda points: result()
    window.predictor = StrokeContext(dict(maxContext=1, total=1, unigrams={'甲': 1}, contexts={}))


def test_trial_ink_learning_controls_reload_and_delete(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        trial_setup(window)
        window.accept_stroke(dict(points=INK))
        assert window.evidence[0]['ink']
        window.commit(window.candidates.index('甲'))
        assert window.personal_model.snapshot()['confirmations'] == 1
        assert window.personal_path().is_file(), window.personal_model.snapshot()
        window.load_personal_profile()
        assert window.personal_model.snapshot()['confirmations'] == 1
        window.accept_stroke(dict(points=INK)); window.commit(window.candidates.index('甲'))
        assert window.personal_model.snapshot()['confirmations'] == 2
        window.delete_one()
        assert window.personal_model.snapshot()['confirmations'] == 1
        window.show(); app.processEvents()
        assert window.personal_toggle.isVisible() and window.personal_profile.isVisible()
        assert window.grab().save(str(tmp_path / 'personal-learning-trial.png'))
        window.personal_toggle.setChecked(False)
        window.accept_stroke(dict(points=INK)); window.commit(window.candidates.index('甲'))
        assert window.personal_model.snapshot()['confirmations'] == 1
        window.personal_profile.setText('P02'); window.change_personal_profile()
        assert window.personal_model.snapshot()['confirmations'] == 0
    finally:
        window.close(); app.processEvents()


def test_trial_new_experiment_keeps_training_and_records_initial_model(tmp_path, monkeypatch):
    import json
    app, window = load_window(tmp_path, monkeypatch)
    try:
        trial_setup(window)
        window.accept_stroke(dict(points=INK)); window.commit(window.candidates.index('甲'))
        window.target_input.setPlainText('甲甲')
        window.start_trial()
        assert window.personal_model.snapshot()['confirmations'] == 1
        assert not window.personal_toggle.isEnabled()
        window.accept_stroke(dict(points=INK)); window.commit(window.candidates.index('甲'))
        assert window.personal_model.snapshot()['confirmations'] == 2
        window.finish_trial()
        report = json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['personal_model_initial']['confirmations'] == 1
        assert report['personal_model_final']['confirmations'] == 2
        assert report['personal_learning'] and window.personal_toggle.isEnabled()
    finally:
        window.close(); app.processEvents()
