"""Latest-input-only background repair for the standalone Qt trial."""
from copy import deepcopy
from queue import Empty, SimpleQueue
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from proximic_ring.touchpad.stroke.stroke_word_correction import WholeCharacterCorrector, DEFAULT_CORRECTION_POLICY


class LatestCorrectionWorker:
    """A daemon owns computation only; it never accesses widgets or Qt objects."""
    def __init__(self):
        self.results = SimpleQueue()
        self.condition = threading.Condition()
        self.pending = None
        self.closed = False
        self.thread = None

    def submit(self, request):
        with self.condition:
            if self.closed:
                return
            self.pending = request  # Replace queued work instead of accumulating it.
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True,
                                               name='stroke-whole-correction')
                self.thread.start()
            self.condition.notify()

    def cancel(self):
        with self.condition:
            self.pending = None

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.condition.notify()

    def _run(self):
        dictionary, scope, corrector = None, None, None
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                request, self.pending = self.pending, None
            started = time.perf_counter()
            try:
                if dictionary is not request['dictionary'] or scope != request['allowed']:
                    corrector = WholeCharacterCorrector(request['entries'], request['ranks'],
                                                       allowed_characters=request['allowed'])
                    dictionary = request['dictionary']
                    scope = request['allowed']
                rows = corrector.candidates(request['code'], evidence=request['evidence'],
                    context=request['context'], predictor=request['predictor'],
                    allowed_characters=request['allowed'], excluded_characters=request['normal'])
                result = dict(generation=request['generation'], code=request['code'], rows=rows,
                              elapsed_ms=(time.perf_counter() - started) * 1000, error='')
            except Exception:
                result = dict(generation=request['generation'], code=request['code'], rows=[],
                              elapsed_ms=(time.perf_counter() - started) * 1000,
                              error='整字纠错暂不可用，已保留普通候选')
            self.results.put(result)


class TrialCorrection(QObject):
    changed = Signal(object)

    def __init__(self, can_apply, parent=None):
        super().__init__(parent)
        self.can_apply = can_apply
        self.worker = LatestCorrectionWorker()
        self.generation = 0
        self.key = None
        self.request = None
        self.rows = []
        self.status = '至少输入3笔后分析'
        self.deferred = None
        self.closed = False
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(180)
        self.debounce.timeout.connect(self._start)
        self.poll = QTimer(self)
        self.poll.setInterval(30)
        self.poll.timeout.connect(self._poll)
        self.poll.start()

    def update(self, *, enabled, code, evidence, context, dictionary, predictor, allowed, normal):
        if self.closed:
            return []
        costs = tuple((row['category'], tuple(sorted(row.get('costs', {}).items()))) for row in evidence)
        key = (enabled, id(dictionary), id(predictor), code, costs, context,
               frozenset(allowed) if allowed is not None else None, tuple(normal))
        if key == self.key:
            return self.rows
        self.key = key
        self.generation += 1
        self.rows, self.deferred = [], None
        self.debounce.stop()
        self.worker.cancel()
        policy = DEFAULT_CORRECTION_POLICY
        valid = enabled and policy.min_strokes <= len(code) <= policy.max_strokes
        self.status = ('后台分析中…' if valid else '已关闭' if not enabled else
                       '笔序过长，保留普通候选' if len(code) > policy.max_strokes else '至少输入3笔后分析')
        self.request = None
        if valid:
            self.request = dict(generation=self.generation, code=code, evidence=deepcopy(evidence),
                context=context[-32:], dictionary=dictionary, predictor=predictor,
                allowed=frozenset(allowed) if allowed is not None else None, normal=tuple(normal),
                requested_at=time.perf_counter())
            self.debounce.start()
        return self.rows

    def _start(self):
        if self.closed or not self.request:
            return
        request = dict(self.request, entries=tuple(self.request['dictionary'].entries),
                       ranks=dict(self.request['dictionary'].ranks))
        self.worker.submit(request)

    def _poll(self):
        if self.closed:
            return
        while True:
            try:
                result = self.worker.results.get_nowait()
            except Empty:
                break
            if result['generation'] == self.generation:
                self.deferred = result
        if self.deferred is None or not self.can_apply():
            return
        result, self.deferred = self.deferred, None
        if result['generation'] != self.generation:
            return
        self.rows = result['rows']
        result['response_ms'] = (time.perf_counter() - self.request['requested_at']) * 1000
        self.status = (result['error'] or
                       f"纠错候选 {len(self.rows)} 个 · {result['elapsed_ms']:.1f} ms")
        self.changed.emit(result)

    def close(self):
        self.closed = True
        self.generation += 1
        self.debounce.stop()
        self.poll.stop()
        self.worker.close()
