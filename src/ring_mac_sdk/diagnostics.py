"""Per-session bounded diagnostic accumulation, aligned with Android 2035acc."""
import time
from dataclasses import dataclass, field

@dataclass(frozen=True)
class TouchpadDiagnostics:
    session_id: str = ''
    generation: int = 0
    severity: str = 'INFO'
    issue: str | None = None
    message: str = ''
    active_issues: tuple = ()
    recovered_issues: tuple = ()
    occurrence_counts: dict = field(default_factory=dict)
    interval_counts: dict = field(default_factory=dict)
    counters: dict = field(default_factory=dict)
    device: dict | None = None

class Reporter:
    def __init__(self, session):
        self.session = session
        self.active, self.totals, self.delivered, self.counts = {}, {}, {}, {}
        self.healthy_times = {}
        self.recovered = set()
        self.revision = self.taken_revision = 0
        self.last = None
        self.issue = self.fatal = None
        self.message = ''
        self.device = None

    def record(self, issue, message, severity='WARNING', **counts):
        for key, value in counts.items():
            self.counts[key] = self.counts.get(key, 0)+value
        if self.fatal and severity != 'ERROR':
            return
        self.issue, self.message = issue, message
        self.totals[issue] = self.totals.get(issue, 0)+1
        self.revision += 1
        if severity == 'ERROR':
            self.fatal = issue
        elif severity == 'WARNING':
            self.active[issue] = message
            self.healthy_times.pop(issue, None)
            self.recovered.discard(issue)

    def healthy(self, issue, now=None):
        if issue not in self.active:
            return
        now = time.monotonic() if now is None else now
        start, last = self.healthy_times.get(issue, (now, now))
        if now-last > .25:
            start = now
        self.healthy_times[issue] = start, now
        if now-start >= 1:
            self.recover(issue)

    def recover(self, issue):
        if issue in self.active:
            del self.active[issue]
            self.recovered.add(issue)
            self.revision += 1

    def due(self):
        return self.revision != self.taken_revision and (self.last is None or time.monotonic()-self.last >= 1)

    def snapshot(self, generation, take=False):
        result = TouchpadDiagnostics(self.session, generation,
            'ERROR' if self.fatal else 'WARNING' if self.active else 'INFO', self.fatal or self.issue,
            self.message if self.fatal else '; '.join(self.active.values()) if self.active else
            'Recovered: '+', '.join(sorted(self.recovered)) if self.recovered else self.message,
            tuple(sorted(self.active)), tuple(sorted(self.recovered)), dict(self.totals),
            {k: v-self.delivered.get(k, 0) for k,v in self.totals.items() if v>self.delivered.get(k,0)},
            dict(self.counts), dict(self.device) if self.device else None)
        if take:
            self.last = time.monotonic()
            self.delivered = dict(self.totals)
            self.recovered.clear()
            self.taken_revision = self.revision
        return result
