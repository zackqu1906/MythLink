"""Asynchronous, cached local language predictions for the standalone trial."""
from collections import OrderedDict
from queue import Empty
import time

from PySide6.QtCore import QObject, QTimer, Signal

from proximic_ring.touchpad.stroke.stroke_llm_context import CONTEXT_CHARS, ContinuationPredictor, local_prediction_settings
from stroke_trial_correction import LatestCorrectionWorker


class PredictionWorker(LatestCorrectionWorker):
    def __init__(self, predictor=None):
        super().__init__()
        self.predictor = predictor or ContinuationPredictor()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                request, self.pending = self.pending, None
            start = time.perf_counter()
            try:
                phrases = self.predictor.predict(request['context'], request['settings'])
                error = ''
            except Exception as exc:
                phrases = []
                # Endpoint errors may contain arbitrary response data; keep it
                # out of status/logs. The ordinary candidate path remains live.
                error = f'联想暂不可用（{type(exc).__name__}），继续使用本地候选'
            self.results.put(dict(generation=request['generation'], context=request['context'],
                phrases=phrases, elapsed_ms=(time.perf_counter() - start) * 1000, error=error))


class TrialLanguageContext(QObject):
    changed = Signal(object)

    def __init__(self, can_apply, parent=None, *, settings=None, worker=None):
        super().__init__(parent)
        self.can_apply = can_apply
        self.settings = settings or local_prediction_settings()
        self.worker = worker or PredictionWorker()
        self.key, self.request, self.deferred = None, None, None
        self.generation = 0
        self.phrases = []
        self.cache = OrderedDict()
        self.closed = False
        self.status = '已关闭'
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)
        self.debounce.timeout.connect(self._start)
        self.poll = QTimer(self)
        self.poll.setInterval(30)
        self.poll.timeout.connect(self._poll)
        self.poll.start()

    def update(self, enabled, context):
        if self.closed:
            return []
        context = context[-CONTEXT_CHARS:]
        key = (enabled, context)
        if key == self.key:
            return self.phrases
        self.key = key
        self.generation += 1
        self.phrases, self.deferred, self.request = [], None, None
        self.debounce.stop()
        self.worker.cancel()
        if not enabled or not context.strip():
            self.status = '已关闭' if not enabled else '输入文字后联想 · 本地Qwen'
        elif context in self.cache:
            self.phrases = list(self.cache[context])
            self.cache.move_to_end(context)
            self.status = f'已缓存 {len(self.phrases)} 个词语'
        else:
            self.status = '后台联想中 · 本地Qwen'
            self.request = dict(generation=self.generation, context=context, settings=self.settings)
            self.debounce.start()
        return self.phrases

    def _start(self):
        if self.request is not None and not self.closed:
            self.worker.submit(self.request)

    def _poll(self):
        if self.closed:
            return
        while True:
            try:
                result = self.worker.results.get_nowait()
            except Empty:
                break
            if not result['error']:
                self.cache[result['context']] = result['phrases']
                self.cache.move_to_end(result['context'])
                while len(self.cache) > 64:
                    self.cache.popitem(last=False)
            if result['generation'] == self.generation:
                self.deferred = result
        if self.deferred is None or not self.can_apply():
            return
        result, self.deferred = self.deferred, None
        self.phrases = result['phrases']
        self.status = result['error'] or f'联想 {len(self.phrases)} 词 · {result["elapsed_ms"]:.0f} ms'
        self.changed.emit(result)

    def reset(self):
        self.key = None
        self.generation += 1
        self.phrases, self.deferred, self.request = [], None, None
        self.debounce.stop()
        self.worker.cancel()

    def close(self):
        self.reset()
        self.closed = True
        self.poll.stop()
        self.worker.close()
