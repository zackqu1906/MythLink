"""Publish a single immutable candidate batch, with a bounded Qwen wait."""
from copy import deepcopy
from dataclasses import replace
from queue import Empty
import time

from PySide6.QtCore import QObject, QTimer, Signal

from proximic_ring.touchpad.stroke.stroke_llm_context import CandidateChooser, local_prediction_settings
from proximic_ring.touchpad.stroke.stroke_word_correction import WholeCharacterCorrector, merge_candidates
from proximic_ring.touchpad.stroke.stroke_joined import merge_joined_candidates
from proximic_ring.touchpad.stroke.stroke_tolerant import fill_page, index_for
from stroke_trial_correction import LatestCorrectionWorker

BUDGET_MS = 400


class BatchWorker(LatestCorrectionWorker):
    def _run(self):
        dictionary, scope, corrector = None, None, None
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                request, self.pending = self.pending, None
            r = request
            error = ''
            chars, rows = [], []
            try:
                d = r['dictionary']
                rows = d.encoding_candidates(r['evidence'], predictor=r['predictor'],
                    context=r['context'], allowed_characters=r['allowed'], policy=r['policy'], fuzzy=r['fuzzy'])
                chars = [row['char'] for row in rows]
                chars, rows = merge_joined_candidates(d, r['evidence'], chars, rows, r.get('joined_model'),
                    enabled=r.get('joined_enabled', False), fuzzy=r['fuzzy'], predictor=r['predictor'],
                    context=r['context'], allowed=r['allowed'], policy=r['policy'])
                if not r['code']:
                    chars = r['predictor'].suggestions(r['context'], allowed_characters=r['allowed']) if r['context'] else []
                    rows = []
                elif r['correction']:
                    if dictionary is not d or scope != r['allowed']:
                        corrector = WholeCharacterCorrector(d.entries, d.ranks, allowed_characters=r['allowed'])
                        dictionary, scope = d, r['allowed']
                    repairs = corrector.candidates(r['code'], evidence=r['evidence'], context=r['context'],
                        predictor=r['predictor'], allowed_characters=r['allowed'], excluded_characters=chars)
                    chars = merge_candidates(chars, repairs,
                        reserved=1 if any(row.get('context_extra_slot') for row in rows) else 2)
                    rows += repairs
                if r.get('fill') and r['fuzzy'] and r['code']:
                    chars, rows = fill_page(chars, rows, index=index_for(d, r['allowed']), evidence=r['evidence'],
                        predictor=r['predictor'], context=r['context'], allowed=r['allowed'], priors=d._fuzzy_priors)
            except Exception as exc:
                error = type(exc).__name__
            self.results.put(dict(generation=r['generation'], chars=chars, rows=rows, error=error))


class ChoiceWorker(LatestCorrectionWorker):
    def __init__(self):
        super().__init__()
        self.chooser = CandidateChooser()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                r, self.pending = self.pending, None
            try:
                choice = self.chooser.choose(r['context'], r['chars'], r['settings'])
            except Exception:
                choice = ''
            self.results.put(dict(generation=r['generation'], choice=choice,
                                  finished=time.monotonic()))


class CandidateBatch(QObject):
    changed = Signal(object)

    def __init__(self, can_publish, parent=None, *, worker=None, choice_worker=None):
        super().__init__(parent)
        self.can_publish = can_publish
        self.worker = worker or BatchWorker()
        self.choice_worker = choice_worker or ChoiceWorker()
        self.settings = replace(local_prediction_settings(), timeout_s=.4)
        self.generation = 0
        self.key = None
        self.loading = False
        self.result = None
        self.local = None
        self.ready = None
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.poll)

    def update(self, key, request):
        if self.closed or key == self.key:
            return
        self.reset()
        self.key = key
        self.loading = True
        self.started = time.monotonic()
        self.deadline = self.started + BUDGET_MS / 1000
        self.request = dict(request, generation=self.generation,
                            evidence=deepcopy(request['evidence']))
        self.worker.submit(self.request)
        self.timer.start()

    def poll(self):
        if self.closed:
            return
        for worker, kind in ((self.worker, 'local'), (self.choice_worker, 'choice')):
            while True:
                try:
                    result = worker.results.get_nowait()
                except Empty:
                    break
                if result['generation'] != self.generation or not self.loading or self.ready is not None:
                    continue
                if kind == 'local':
                    self.local = result
                    if not self.request['context'].strip() or len(result['chars']) < 2:
                        self.seal('', '本地候选')
                    elif time.monotonic() >= self.deadline:
                        self.seal('', '达到时限 · 本地候选')
                    else:
                        self.choice_worker.submit(dict(generation=self.generation,
                            context=self.request['context'], chars=result['chars'][:6], settings=self.settings))
                elif self.local is not None:
                    on_time = result['finished'] <= self.deadline and time.monotonic() <= self.deadline
                    self.seal(result['choice'] if on_time else '',
                              'Qwen选字' if on_time and result['choice'] else '本地候选')
        if self.loading and self.local is not None and self.ready is None and time.monotonic() >= self.deadline:
            self.seal('', '达到时限 · 本地候选')
        if self.ready is not None and self.loading and self.can_publish():
            self.result, self.ready = self.ready, None
            self.loading = False
            self.timer.stop()
            self.result['elapsed_ms'] = (time.monotonic() - self.started) * 1000
            self.changed.emit(self.result)

    def seal(self, choice, reason):
        chars = list(self.local['chars'])
        if choice and choice in chars[:6]:
            chars.remove(choice)
            chars.insert(0, choice)
        self.ready = dict(self.local, chars=chars, reason=reason, choice=choice)

    def reset(self):
        self.generation += 1
        self.timer.stop()
        self.key = self.result = self.local = self.ready = None
        self.loading = False
        self.worker.cancel()
        self.choice_worker.cancel()

    def close(self):
        self.reset()
        self.closed = True
        self.worker.close()
        self.choice_worker.close()
