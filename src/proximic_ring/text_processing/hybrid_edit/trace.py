"""Consecutive, monotonic timings for one spoken edit, including interrupted work."""

from contextlib import contextmanager
from threading import Lock
from time import perf_counter_ns


class EditPipeline:
    def __init__(self):
        self._lock = Lock()
        self._steps = []
        self._route = []
        self._started = None
        self._finished = False

    def _end_step(self, now, status):
        if self._started is not None:
            self._steps[-1].update(duration_ms=(now - self._started) / 1_000_000, status=status)
            self._started = None

    def start(self, kind, label, *, route=""):
        with self._lock:
            if self._finished:
                return
            now = perf_counter_ns()
            self._end_step(now, "completed")
            self._steps.append({"kind": kind, "label": label})
            self._started = now
            if route:
                self._route.append(route)

    def describe(self, label, route):
        """Name the local work once the parser has selected its branch."""
        with self._lock:
            if self._started is not None:
                self._steps[-1]["label"] = label
                self._route.append(route)

    def finish(self, status):
        with self._lock:
            self._end_step(perf_counter_ns(), status)
            self._finished = True

    def snapshot(self):
        with self._lock:
            route = " → ".join(self._route)
            if not route and self._finished:
                route = "停在" + self._steps[-1]["label"] if self._steps else "编辑流程尚未开始"
            return {"route": route, "steps": [step.copy() for step in self._steps]}

    @contextmanager
    def recording(self, cancelled):
        self.start("context", "准备本次编辑的上下文")
        try:
            yield
        except Exception:
            self.finish("cancelled" if cancelled() else "failed")
            raise
        else:
            self.finish("completed")
