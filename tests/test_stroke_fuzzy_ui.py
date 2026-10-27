"""Fuzzy recognition on main's local editor and external session lifecycle."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPointF
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_stroke_integration import idle
from test_stroke_session import stroke, wake, until
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary


@pytest.fixture(autouse=True)
def fake_supported_platform(monkeypatch):
    # Only the controller's platform gate is mocked; all output/IME adapters are fake.
    import proximic_ring.ui.touchpad.touchpad_controller as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='darwin'))


RESULT = dict(category='s', shape='竖', distance=.001, category_scores=[
    dict(category='s', distance=.001), dict(category='p', distance=.002)])


def dictionary(tp):
    tp.fuzzyMatching = True
    tp._dictionary = StrokeDictionary(dict(templates=[], ranks={'小': 0, '你': 0},
        entries=[['小', 'spn', 1], ['你', 'pspzspn', 1]]))
    tp._predictor = StrokeContext(dict(maxContext=4, total=100,
        unigrams={'小': 50, '你': 50}, contexts={'爱': [100, '你', [100]]}))
    tp._dictionary.recognize = lambda points: RESULT


def local_editor(inline_ui):
    c, bridge, root, tp, source = idle(inline_ui)
    root.resize(940, 700)
    root.setProperty('currentPage', 3)
    root.findChild(QObject, 'touchpadPage').setProperty('section', 'stroke')
    root.show()
    root.requestActivate()
    tp.setInputMode('stroke')
    QTest.qWait(50)
    root.findChild(QObject, 'strokeTestEditor').forceActiveFocus()
    until(lambda: tp._local_stroke_input)
    return c, bridge, root, tp, source


def visual_child(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_child(child, name)
        if found is not None:
            return found


def test_fuzzy_mark_toggle_recognition_undo_and_local_commit(inline_ui, tmp_path):
    c, _, root, tp, source = local_editor(inline_ui)
    dictionary(tp)
    tp.start()
    until(lambda: bool(source.starts))
    source.future.set_result(None)
    until(lambda: tp.state == 'running')
    try:
        tp._run.output.options['on_stroke']([(0, 0), (-20, 100)])
        until(lambda: bool(tp.strokeTokens))
        assert tp.strokeTokens == [dict(symbol='丨', fuzzy=True)]
        assert tp.strokeCandidates == ['小', '你']
        mark = visual_child(root.contentItem(), 'strokeFuzzyMark_0')
        assert mark.isVisible() and mark.property('text') == '?'
        assert mark.property('font').pixelSize() == 9
        assert mark.mapToItem(mark.parentItem(), QPointF()).x() > 0
        assert root.grabWindow().save(str(tmp_path/'stroke-fuzzy-main.png'))
        tp.addStroke('s')
        assert tp.strokeCandidates == ['你'] and not tp.strokeTokens[1]['fuzzy']
        tp.fuzzyMatching = False
        assert not tp.strokeCandidates and not any(t['fuzzy'] for t in tp.strokeTokens)
        assert c._settings.value('touchpad/fuzzyMatching', type=bool) is False
        tp.fuzzyMatching = True
        assert tp.strokeCandidates == ['你']
        tp.undoStroke()
        assert len(tp._stroke_evidence) == 1 and tp.strokeCandidates == ['小', '你']
        tp.clearStrokes()
        assert not tp._stroke_evidence and not tp.strokeTokens
        tp.addStroke('s')
        assert not tp.strokeTokens[0]['fuzzy'] and tp.strokeCandidates == ['小']
        tp.selectCandidate(0)
        assert root.findChild(QObject, 'strokeTestEditor').property('text') == '小'
        assert not tp._stroke_evidence and not tp.strokeTokens
        tp._append_stroke('s', '竖', RESULT)
        tp.setInputMode('pointer')
        assert not tp._stroke_evidence and not tp._candidate_matches
    finally:
        tp.close()
        QTest.qWait(30)


def test_context_change_reorders_existing_fuzzy_stroke(inline_ui):
    _, _, root, tp, _ = local_editor(inline_ui)
    dictionary(tp)
    tp._append_stroke('s', '竖', RESULT)
    assert tp.strokeCandidates[0] == '小'
    root.findChild(QObject, 'strokeTestEditor').setProperty('text', '我爱')
    root.findChild(QObject, 'strokeTestEditor').setProperty('cursorPosition', 2)
    assert tp.strokeCandidates[0] == '你'


def test_single_replacement_renders_red_and_clears_after_local_commit(inline_ui):
    _, _, root, tp, _ = local_editor(inline_ui)
    tp.fuzzyMatching = True
    tp._dictionary = StrokeDictionary(dict(templates=[], ranks={'我': 0}, entries=[['我', 'phshzpn', 1]]))
    for letter in 'phsh':
        tp.addStroke(letter)
    tp._append_stroke('h', '横', dict(category='h', shape='横', category_scores=[
        dict(category='h', distance=.001), dict(category='z', shape='横折', distance=.03)]))
    for letter in 'pn':
        tp.addStroke(letter)
    assert tp.strokeCandidates == ['我'] and tp._code == 'phshhpn'
    assert tp.strokeTokens[4]['correction']
    assert tp.strokeCandidateVisuals[0]['states'][4] == 'correction'
    tp.fuzzyMatching = False
    assert not tp.strokeCandidates and not any(token.get('correction') for token in tp.strokeTokens)
    tp.fuzzyMatching = True
    assert tp.strokeCandidates == ['我']
    tp.selectCandidate(0)
    assert root.findChild(QObject, 'strokeTestEditor').property('text') == '我'
    assert not tp.strokeTokens and not tp._stroke_evidence


def test_local_left_dot_input_retains_ink_toggles_and_respects_manual_lock(inline_ui):
    _, _, root, tp, _ = local_editor(inline_ui)
    tp.fuzzyMatching = True
    tp._dictionary = StrokeDictionary(dict(templates=[], ranks={'心': 0},
        entries=[['心', 'nznn', 1]]), left_dots=[dict(character='心', code='nznn', positions=[0])])
    tp._predictor = StrokeContext(dict(maxContext=1, total=1, unigrams={'心': 1}, contexts={}))
    for category in 'pznn':
        tp._append_stroke(category, '撇', dict(category=category, shape='撇', distance=.001))
    assert tp._code == 'pznn' and tp.strokeCandidates == ['心']
    assert tp.strokeTokens[0] == dict(symbol='丿', fuzzy=True)
    assert tp._candidate_matches[0]['left_dot_corrections'] == [0]
    tp.fuzzyMatching = False
    assert not tp.strokeCandidates and not any(token['fuzzy'] for token in tp.strokeTokens)
    tp.fuzzyMatching = True
    assert tp.strokeCandidates == ['心']
    tp.selectCandidate(0)
    assert root.findChild(QObject, 'strokeTestEditor').property('text') == '心'
    assert not tp._stroke_evidence and not tp.strokeTokens
    tp.addStroke('p')
    assert not tp.strokeCandidates and not tp.strokeTokens[0]['fuzzy']
    tp.clearStrokes()


def test_strength_slider_retunes_ink_persists_and_blocks_during_commit(inline_ui):
    _, _, root, tp, _ = local_editor(inline_ui)
    dictionary(tp)
    tp._append_stroke('s','竖',dict(category='s',shape='竖',distance=.001,
        category_scores=[dict(category='s',distance=.001),dict(category='p',distance=.071)]))
    tp.fuzzyStrength=0
    assert tp._code=='s' and tp.strokeCandidates==['小']
    slider=root.findChild(QObject,'strokeFuzzyStrengthSlider')
    assert slider.property('value')==0 and slider.property('enabled')
    slider.setProperty('value',100)
    slider.moved.emit()
    assert tp.fuzzyStrength==100 and '你' in tp.strokeCandidates
    assert tp.owner._settings.value('touchpad/fuzzyStrength',type=int)==100
    assert root.findChild(QObject,'strokeTestEditor').hasActiveFocus()
    tp._pending_stroke_commit='token'
    tp.fuzzyStrength=0
    assert tp.fuzzyStrength==100
    tp._pending_stroke_commit=''
    tp.fuzzyStrength=50
    assert slider.property('value')==50
    tp.fuzzyMatching=False
    assert not slider.property('enabled') and tp.strokeCandidates==['小']
    tp.fuzzyMatching=True
    tp.clearStrokes(); tp.addStroke('s'); tp.fuzzyStrength=100
    assert tp.strokeCandidates==['小'] and tp._stroke_evidence[0]['manual']


def test_enriched_callback_applies_physical_speed_and_slider_rebuilds(inline_ui):
    from proximic_ring.touchpad.stroke.stroke_kinematics import measure_stroke
    _, _, root, tp, source = local_editor(inline_ui)
    dictionary(tp)
    tp._dictionary.recognize = lambda points: dict(category='s', shape='竖', distance=.001,
        category_scores=[dict(category='s', distance=.001), dict(category='p', distance=.091)])
    tp.start(); until(lambda: bool(source.starts))
    source.future.set_result(None); until(lambda: tp.state == 'running')
    try:
        points = [(0, 0), (0, 100)]
        tp._run.output.options['on_stroke_details'](dict(points=points, kinematics=measure_stroke(points, 50)))
        until(lambda: bool(tp.strokeTokens))
        assert tp._code == 's' and set(tp.strokeCandidates) == {'小', '你'}
        status = root.findChild(QObject, 'strokeAdaptiveFuzzyStatus')
        assert '50 → 65' in status.property('text')
        tp.fuzzyStrength = 0
        assert tp.strokeCandidates == ['小'] and '0 → 15' in tp.adaptiveFuzzyStatus
        tp.fuzzyStrength = 50
        assert '你' in tp.strokeCandidates
        tp.undoStroke()
        assert not tp._stroke_evidence and '最高增加30档' in tp.adaptiveFuzzyStatus
    finally:
        tp.close(); QTest.qWait(30)


def test_long_character_keeps_complete_evidence(inline_ui):
    _, _, _, tp, _ = local_editor(inline_ui)
    code = next(code for char, code, _ in tp._stroke_dictionary().entries if char == '输' and len(code) > 12)
    for letter in code:
        tp.addStroke(letter)
    assert tp._code == code and len(tp._stroke_evidence) == len(code)
    assert '输' in tp._candidates
    tp.undoStroke()
    assert tp._code == code[:-1] and len(tp._stroke_evidence) == len(code)-1


def test_external_context_and_caret_clear_do_not_leak_evidence(stroke):
    c, _, tp, _, session = stroke
    dictionary(tp)
    wake(stroke, context='我爱')
    tp._run.output.options['on_stroke']([(0, 0), (-20, 100)])
    until(lambda: bool(tp._stroke_evidence))
    assert tp.strokeCandidates[0] == '你'
    tp.setStrokeContext('别的框')
    assert tp._stroke_context == '我爱'
    c._inline_input._accept(dict(type='stroke_ink_cleared', epoch=c._inline_input._epoch,
        client_id=c._inline_input.stroke_target[1], stroke_session=session._token))
    assert not tp.strokeTokens and not tp._stroke_evidence and not tp._candidate_matches
    assert session.phase == 'running'
    tp._append_stroke('s', '竖', RESULT)
    session.cancel()
    assert not tp._stroke_evidence and not tp.strokeTokens and not tp._candidate_matches
    until(lambda: tp._run.output_ready)
    wake(stroke)
    tp.addStroke('s')
    assert tp.strokeCandidates == ['小'] and not tp.strokeTokens[0]['fuzzy']


@pytest.mark.parametrize('success', [False, True])
def test_pending_external_commit_preserves_evidence_until_ack(stroke, success):
    c, bridge, tp, _, session = stroke
    dictionary(tp)
    wake(stroke)
    tp._append_stroke('s', '竖', RESULT)
    evidence = list(tp._stroke_evidence)
    candidates = list(tp.strokeCandidates)
    tp.selectCandidate(0)
    until(lambda: tp.strokeCommitPending)
    request = next(m for m in reversed(bridge.messages) if m['type'] == 'stroke_commit')
    tp.fuzzyMatching = False
    tp.undoStroke()
    tp.clearStrokes()
    tp.addStroke('h')
    assert tp.fuzzyMatching and tp._stroke_evidence == evidence and tp.strokeCandidates == candidates
    c._inline_input._accept(dict(type='stroke_result', epoch=c._inline_input._epoch,
        client_id=request['client_id'], request_id=request['request_id'], success=success))
    assert not tp.strokeCommitPending and session.phase == 'running'
    if success:
        assert not tp._stroke_evidence and not tp.strokeTokens and not tp._candidate_matches
    else:
        assert tp._stroke_evidence == evidence and tp.strokeCandidates == candidates
