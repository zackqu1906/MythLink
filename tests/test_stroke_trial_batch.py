from queue import SimpleQueue
import time

import pytest

from proximic_ring.touchpad.stroke.stroke_llm_context import CandidateChooser, local_prediction_settings
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from test_complete_stroke_input import load_window


class FakeWorker:
    def __init__(self):
        self.results = SimpleQueue()
        self.requests = []

    def submit(self, request):
        self.requests.append(request)

    def cancel(self):
        pass

    def close(self):
        pass


def fake_batch(window):
    window.batch.worker.close()
    window.batch.choice_worker.close()
    window.batch.worker = FakeWorker()
    window.batch.choice_worker = FakeWorker()
    window.once_toggle.setChecked(True)
    return window.batch


def local(batch, chars='叶用申甲叮叫'):
    batch.worker.results.put(dict(generation=batch.generation, chars=list(chars), rows=[], error=''))
    batch.poll()


def choice(batch, value='用', generation=None):
    batch.choice_worker.results.put(dict(generation=batch.generation if generation is None else generation,
        choice=value, finished=time.monotonic()))
    batch.poll()


def test_chooser_uses_two_tokens_and_rejects_outside_choices():
    class Processor:
        answer = ' 用\n'

        def _ensure_local_server(self, settings):
            pass

        def _request_chat(self, settings, **kwargs):
            self.kwargs = kwargs
            return self.answer

    processor = Processor()
    chooser = CandidateChooser(processor)
    assert chooser.choose('今天我', list('叶用申甲叮叫'), local_prediction_settings()) == '用'
    assert processor.kwargs['max_tokens'] == 2
    assert processor.kwargs['user_content'] == '前文：今天我\n候选：叶、用、申、甲、叮、叫'
    for invalid in ('想', '用。', '用叶', ''):
        processor.answer = invalid
        assert chooser.choose('今天我', list('叶用申甲叮叫'), local_prediction_settings()) == ''


def test_loading_then_six_fixed_candidates_and_no_late_reordering(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.editor.setText('今天我')
        batch = fake_batch(window)
        assert batch.loading and window.candidates == []
        assert all(b.loading and not b.isEnabled() and b.visual is None for b in window.buttons)
        window.commit(0)
        window.accept_sdk_tap({})
        assert window.editor.text() == '今天我'
        local(batch)
        assert batch.loading and not window.candidates
        choice(batch)
        assert window.candidates == list('用叶申甲叮叫')
        assert all(b.isEnabled() and not b.loading for b in window.buttons)
        assert [b.visual['character'] for b in window.buttons] == window.candidates
        generation = batch.generation
        choice(batch, '叫')
        window.refresh()
        window.language_ready({})
        window.correction_ready({})
        assert batch.generation == generation and window.candidates == list('用叶申甲叮叫')
        assert not window.llm_toggle.isEnabled()
        window.once_toggle.setChecked(False)
        assert window.llm_toggle.isEnabled() and not any(b.loading for b in window.buttons)
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize('source', ['button', 'held-direction'])
def test_punctuation_discards_pending_strokes_even_while_candidates_load(tmp_path, monkeypatch, source):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.editor.setText('正文')
        batch = fake_batch(window)
        window.add_manual('s')
        old_generation = batch.generation
        assert batch.loading and window.code == 's'
        if source == 'button':
            window.punctuate('。')
        else:
            window.show_selection_state(dict(phase='holding', progress=10, direction=None))
            window.accept_selection(dict(decision=dict(phase='selecting', direction='up', action='page')))
        assert window.editor.text() == '正文。' and not window.code and not window.evidence
        assert batch.generation != old_generation
        # Discarded handwriting must not reappear via a delayed worker result.
        batch.worker.results.put(dict(generation=old_generation, chars=['小'], rows=[], error=''))
        batch.poll()
        assert window.editor.text() == '正文。' and not window.code and '小' not in window.candidates
    finally:
        window.close()
        app.processEvents()


def test_deadline_stale_results_and_contact_during_loading(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.editor.setText('今天我')
        batch = fake_batch(window)
        local(batch)
        old = batch.generation
        window.show_selection_state(dict(phase='holding', progress=25, direction=None))
        batch.deadline = time.monotonic() - 1
        batch.poll()
        assert batch.loading and not window.candidates
        choice(batch)
        window.accept_sdk_tap({})
        assert window.editor.text() == '今天我'
        batch.poll()
        assert window.candidates == list('叶用申甲叮叫') and not batch.loading
        window.add_manual('s')
        assert batch.generation != old and batch.loading and not window.candidates
        choice(batch, generation=old)
        assert batch.loading and not window.candidates
        window.clear()
        local(batch, '甲')
        assert window.candidates == ['甲']
        # An idle callback can arrive before the SDK tap verdict. A contact
        # begun against loading placeholders still cannot select newly shown ink.
        window.add_manual('h')
        window.show_selection_state(dict(phase='holding', progress=10, direction=None))
        local(batch, '甲')
        window.show_selection_state(dict(phase='idle', progress=0, direction=None))
        batch.poll()
        assert not batch.loading
        window.accept_sdk_tap({})
        assert window.editor.text() == '今天我'
        choice(batch, generation=old)
        assert window.candidates == ['甲']
    finally:
        window.close()
        app.processEvents()


def test_real_local_worker_correction_and_trial_switch_report(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.dictionary = StrokeDictionary(dict(templates=[], ranks={'我': 1, '甲': 2},
            entries=[['我', 'phshzpn', 1], ['甲', 'phshhpn', 1]]))
        window.fuzzy_toggle.setChecked(False)
        window.once_toggle.setChecked(True)
        window.target_input.setPlainText('你')
        window.start_trial()
        assert not window.once_toggle.isEnabled()
        for c in 'phshhpn':
            window.add_manual(c)
        deadline = time.monotonic() + 4
        while window.batch.loading:
            app.processEvents()
            assert time.monotonic() < deadline
            time.sleep(.005)
        assert window.candidates == ['甲', '我']
        assert '整字纠错' in window.buttons[1].visual['status']
        assert window.batch.result['error'] == ''
        window.finish_trial()
        import json
        report = json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['single_display_enabled'] and report['single_display_budget_ms'] == 400
        assert window.once_toggle.isEnabled()
    finally:
        window.close()
        app.processEvents()
