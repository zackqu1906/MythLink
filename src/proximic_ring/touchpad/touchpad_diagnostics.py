"""Read-only counters around the yyf touchpad pipeline; no input decisions."""
from collections import Counter
import threading


def note(callback, reason):
    if callback is not None:
        try:
            callback(reason)
        except Exception:
            pass  # Diagnostics cannot interrupt movement or confirm a stroke.


class TouchpadDiagnostics:
    def __init__(self):
        self._counts = Counter()
        self._lock = threading.Lock()
        self._previous = None

    def note(self, reason):
        with self._lock:
            self._counts[reason] += 1

    def observe(self, event):
        reason = 'sdk_' + event.kind
        if event.kind == 'contact':
            reason += '_' + event.state
            if event.state == 'reset':
                reason += '_stream' if event.step == 0 else '_expired_motion'
        self.note(reason)

    def snapshot(self):
        with self._lock:
            return dict(self._counts)

    def report(self, stats, counts, captured_at, *, foreground, visibility_epoch, now):
        """Rates use the BLE callback clock, not the possibly delayed GUI clock."""
        previous = self._previous
        current = (stats, counts, captured_at, foreground, visibility_epoch)
        if previous is None:
            self._previous = current
            return None
        old, before, since, was_foreground, epoch = previous
        seconds = captured_at - since
        if seconds <= 0 or (seconds < 2 and epoch == visibility_epoch):
            return None
        self._previous = current
        changes = {key: value - before.get(key, 0) for key, value in counts.items()
                   if value != before.get(key, 0)}
        result = dict(pipeline='unified-trajectory-v4', foreground=foreground,
                      mixed_visibility=epoch != visibility_epoch or was_foreground != foreground,
                      seconds=round(seconds, 3), ui_queue_ms=round(max(0, now-captured_at)*1000, 1),
                      token_hz=round((getattr(stats, 'tokens', 0)-getattr(old, 'tokens', 0))/seconds, 1),
                      move_hz=round(changes.get('sdk_move', 0)/seconds, 1), events=changes,
                      pointer_frame_hz=round(changes.get('pointer_frame', 0)/seconds, 1),
                      pointer_post_hz=round(changes.get('pointer_post', 0)/seconds, 1),
                      warmup_frames=getattr(stats, 'warmup_frames', 0),
                      inference_batch_ms=round(getattr(stats, 'inference_batch_ms', 0.), 2),
                      inference_thread_cpu_ms=round(getattr(stats, 'inference_thread_cpu_ms', 0.), 2),
                      inference_batch_packets=getattr(stats, 'inference_batch_packets', 0),
                      packet_queue_age_ms=round(getattr(stats, 'packet_queue_age_ms', 0.), 2))
        for key in ('packets', 'tokens', 'resets', 'invalid_packets', 'duplicate_packets',
                    'stale_packets', 'queue_overflows', 'sequence_gaps', 'missing_packets',
                    'arrival_gaps', 'idle_resets', 'reboots'):
            result[key] = getattr(stats, key, 0)
        return result
