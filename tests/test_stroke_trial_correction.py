import json
import threading
import time

from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from test_complete_stroke_input import load_window


def wait_for(app, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError('background correction did not finish')
        time.sleep(.005)


def configure(window, *, normal=False):
    entries = [['我', 'phshzpn', 1]] + ([['甲', 'phshhpn', 1]] if normal else [])
    window.dictionary = StrokeDictionary(dict(templates=[], ranks={'我': 1, '甲': 2}, entries=entries))
    window.fuzzy_toggle.setChecked(False)


def enter(window, code):
    for letter in code:
        window.add_manual(letter)


def test_async_correction_with_existing_candidate_commit_report_and_no_target_leak(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        configure(window, normal=True)
        # Deliberately different reference: it must never be a model input.
        window.target_input.setPlainText('你')
        window.start_trial()
        assert not window.correction_toggle.isEnabled()
        enter(window, 'phshhpn')
        assert window.candidates == ['甲']
        wait_for(app, lambda: '我' in window.candidates)
        assert window.candidates == ['甲', '我'] and window.code == 'phshhpn'
        assert '整字纠错' in window.buttons[1].visual['status']
        window.commit(1)
        assert window.editor.text() == '我' and not window.code and not window.evidence
        window.finish_trial()
        report = json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['whole_correction_enabled'] and report['whole_correction_commits'] == 1
        assert report['whole_correction_analyses'] == 1 and report['whole_correction_mean_ms'] >= 0
        assert report['whole_correction_mean_response_ms'] >= report['whole_correction_mean_ms']
        event = next(e for e in report['events'] if e['kind'] == 'whole_correction_commit')
        assert event['code'] == 'phshhpn' and event['corrected_code'] == 'phshzpn'
        assert event['delivered'] and event['edits'][0]['kind'] == 'substitute'
        assert not report['completed'] and window.correction_toggle.isEnabled()
        assert 'correction_summary' in window.report_path.with_suffix('.csv').read_text(encoding='utf-8-sig')
    finally:
        window.close()
        app.processEvents()


def test_missing_stroke_visual_toggle_and_raw_evidence_are_independent_of_fuzzy(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        configure(window)
        enter(window, 'phshpn')
        wait_for(app, lambda: bool(window.correction.rows))
        visual = window.buttons[0].visual
        assert visual['entered'] == 7 and visual['states'][4] == 'correction'
        assert visual['states'] == ['entered'] * 4 + ['correction'] + ['entered'] * 2
        assert '补第5笔' in visual['status']
        assert window.code == 'phshpn' and len(window.evidence) == 6
        window.correction_toggle.setChecked(False)
        assert not window.candidates and window.code == 'phshpn'
        assert window.correction_status.text() == '已关闭'
        window.correction_toggle.setChecked(True)
        wait_for(app, lambda: bool(window.candidates))
        window.show()
        app.processEvents()
        assert window.correction_toggle.isVisible() and window.correction_status.isVisible()
        assert window.grab().save(str(tmp_path / 'whole-character-correction.png'))
        window.undo()
        assert window.code == 'phshp' and not window.correction.rows
        window.clear()
        assert not window.code and not window.correction.rows
    finally:
        window.close()
        app.processEvents()


def test_late_result_is_discarded_after_clear_retype_disable_and_scope_change(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        configure(window)
        requests = []
        monkeypatch.setattr(window.correction.worker, 'submit', requests.append)
        enter(window, 'phshpn')
        window.correction._start()
        old = requests[-1]
        window.clear()
        enter(window, 'phshpn')  # Same content, different composition generation.
        window.correction.worker.results.put(dict(generation=old['generation'], code=old['code'],
                                                  rows=[dict(char='我')], elapsed_ms=1, error=''))
        window.correction._poll()
        assert not window.candidates and not window.correction.rows
        window.correction._start()
        old = requests[-1]
        window.correction_toggle.setChecked(False)
        window.correction.worker.results.put(dict(generation=old['generation'], code=old['code'],
                                                  rows=[dict(char='我')], elapsed_ms=1, error=''))
        window.correction._poll()
        assert not window.candidates
        window.correction_toggle.setChecked(True)
        window.correction._start()
        old = requests[-1]
        window.common_toggle.setChecked(False)
        window.correction.worker.results.put(dict(generation=old['generation'], code=old['code'],
                                                  rows=[dict(char='我')], elapsed_ms=1, error=''))
        window.correction._poll()
        assert not window.candidates
    finally:
        window.close()
        app.processEvents()


def test_correction_waits_during_hold_and_cannot_change_release_selection(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        configure(window, normal=True)
        enter(window, 'phshhpn')
        window.show_selection_state(dict(phase='selecting', progress=100, direction='center'))
        wait_for(app, lambda: window.correction.deferred is not None)
        assert window.candidates == ['甲'] and not window.correction.rows
        window.show_selection_state(dict(phase='idle', progress=0, direction=None))
        # Release acts synchronously before the background poll can alter rows.
        window.commit(0)
        window.correction._poll()
        assert window.editor.text() == '甲' and not window.code and not window.correction.rows
    finally:
        window.close()
        app.processEvents()


def test_worker_replaces_pending_work_and_close_does_not_wait_for_computation(monkeypatch):
    from stroke_trial_correction import LatestCorrectionWorker
    from proximic_ring.touchpad.stroke.stroke_word_correction import WholeCharacterCorrector
    entered, release = threading.Event(), threading.Event()
    real = WholeCharacterCorrector.candidates

    def blocked(self, *args, **kwargs):
        entered.set()
        assert release.wait(3)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(WholeCharacterCorrector, 'candidates', blocked)
    worker = LatestCorrectionWorker()
    dictionary = object()
    request = dict(generation=1, dictionary=dictionary, entries=[['我', 'phshzpn', 1]],
                   ranks={'我': 1}, code='phshpn', evidence=[], context='', predictor=None,
                   allowed=None, normal=[])
    try:
        worker.submit(request)
        assert entered.wait(2)
        worker.submit(dict(request, generation=2))
        worker.submit(dict(request, generation=3))
        assert worker.pending['generation'] == 3
        worker.close()
        assert worker.pending is None and worker.closed
    finally:
        release.set()
        if worker.thread:
            worker.thread.join(3)
    assert not worker.thread.is_alive()


def test_paging_and_gesture_commit_use_the_merged_candidate_order(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        normals = list('甲乙丙丁戊己庚辛壬癸')
        window.dictionary = StrokeDictionary(dict(templates=[],
            ranks={c: i for i, c in enumerate(normals + ['我'])},
            entries=[[c, 'phshhpn', 1] for c in normals] + [['我', 'phshzpn', 1]]))
        window.common_toggle.setChecked(False)
        window.fuzzy_toggle.setChecked(False)
        enter(window, 'phshhpn')
        wait_for(app, lambda: bool(window.correction.rows))
        assert window.candidates[:6] == list('甲乙丙丁我戊')
        window.page(6)
        assert window.selected == 6 and window.buttons[0].visual['character'] == '己'
        window.refresh()
        assert window.selected == 6 and window.buttons[0].visual['character'] == '己'
        window.page(-6)
        # Down direction selects slot five, now the repair rather than 戊.
        window.apply_contact_decision(dict(action='select', target=4), [])
        assert window.editor.text() == '我' and not window.code
    finally:
        window.close()
        app.processEvents()


def test_real_expanded_scope_keeps_exact_ji_and_adds_wo_repair(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        enter(window, 'phshhpn')
        assert '犄' in window.candidates and '我' not in window.candidates
        original_first = window.candidates[0]
        wait_for(app, lambda: '我' in window.candidates)
        assert window.candidates[0] == original_first
        assert '我' in window.candidates[:6] and '犄' in window.candidates
        assert window.code == 'phshhpn'
    finally:
        window.close()
        app.processEvents()
