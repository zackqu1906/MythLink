"""Wake and verify the IMK target while acquiring focus for a voice gesture."""
from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class PreparedInputContext:
    state: dict | None
    outcome: str
    elapsed_ms: float
    previous_client: str = ''

    @property
    def diagnostics(self):
        state = self.state or {}
        return dict(type='input_context_refresh', outcome=self.outcome,
                    success=self.outcome in {'refreshed', 'coalesced'},
                    application=state.get('application', ''),
                    previous_client=self.previous_client, client_id=state.get('client_id', ''),
                    client_changed=bool(state and self.previous_client and state.get('client_id') != self.previous_client),
                    elapsed_ms=self.elapsed_ms)


class InputContextPreparation:
    """Shared bounded refresh, target fencing and fresh metadata receipt.

    One instance belongs to a bridge. Concurrent readers share a completed
    refresh only while targeting exactly the same client, sentence and range.
    No editor text is read or retained by this module.
    """
    def __init__(self, bridge, *, refresh=None):
        self.bridge = bridge
        self._refresh = refresh
        self._lock = threading.Lock()
        self._generation = 0
        self._last_target = None
        self._last_refreshed = False

    @staticmethod
    def _target(state):
        selection = state.get('selection')
        return (*(state.get(k) for k in ('epoch', 'client_id', 'utterance_id', 'application')),
                tuple(selection) if isinstance(selection, (list, tuple)) else ())

    def prepare(self, origin, *, foreground, timeout=.35, known_state=None):
        started = time.monotonic()
        deadline = started + min(timeout, .35)
        epoch = self.bridge.epoch
        generation = self._generation
        previous_client = str((known_state or {}).get('client_id', ''))

        def result(outcome, state=None):
            return PreparedInputContext(state, outcome, round((time.monotonic() - started) * 1000, 2), previous_client)

        def changed():
            return (not origin or foreground() != origin or not epoch
                    or self.bridge.epoch != epoch or not self.bridge.connected)

        def live(state):
            return bool(state and state.get('epoch') == epoch and state.get('ready')
                        and state.get('client_id') and state.get('application') == origin[0])

        if changed():
            return result('target_changed')
        if timeout <= 0 or not self._lock.acquire(timeout=max(0, deadline - time.monotonic())):
            return result('timeout')
        attempted = False
        try:
            if changed():
                return result('target_changed')
            state = known_state if generation == self._generation else None
            if state is None:
                state = self.bridge.focus_state(timeout=min(.1, max(0, deadline - time.monotonic())))
            if changed():
                return result('target_changed')
            if not live(state):
                return result('unavailable')
            previous_client = str(state['client_id'])
            if generation != self._generation and self._last_target == (origin, self._target(state)):
                return result('coalesced' if self._last_refreshed else 'already_attempted', state)
            if not state.get('context_key_supported'):
                # Native excludes live compositions, writes and copy reads.
                # Their existing baseline/verification must stay untouched.
                return result('not_permitted', state)
            budget = min(.12, deadline - time.monotonic() - .1)
            if budget <= 0:
                return result('budget_exhausted', state)
            refresh = self._refresh
            if refresh is None:
                from .input_context_key import refresh_input_context
                refresh = refresh_input_context
            refreshed = False
            attempted = True
            self._last_target = None
            self._last_refreshed = False
            try:
                refreshed = bool(refresh(self.bridge, origin, timeout=budget))
            except Exception:
                pass  # A missing receipt is not a selection-validity gate.
            if changed():
                return result('target_changed')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return result('timeout')
            state = self.bridge.focus_state(timeout=min(.1, remaining))
            if changed():
                return result('target_changed')
            if time.monotonic() >= deadline or not live(state):
                return result('unavailable')
            self._last_target = (origin, self._target(state))
            self._last_refreshed = refreshed
            return result('refreshed' if refreshed else 'continued_without_receipt', state)
        finally:
            if attempted:
                self._generation += 1
            self._lock.release()
