"""Arm on an SDK-confirmed tap; hold only on the next single-finger down."""
from collections import deque


class TouchpadDrag:
    interval = .25

    def __init__(self, press, release, click):
        self.press, self.release, self.click = press, release, click
        self.active = None
        self.held = False
        self.last_tap = None
        self.candidate = None
        self.completed = deque(maxlen=256)
        self.previewed = deque(maxlen=256)
        self.handled = deque(maxlen=256)
        self.cancelled = deque(maxlen=256)

    def cancel(self):
        if self.active is not None:
            self.cancelled.append(self._contact_key(self.active))
        self.active = None
        self.last_tap = None
        self.candidate = None
        self.completed.clear()
        if self.held:
            self.held = False
            self.release()

    @staticmethod
    def _contact_key(event):
        return event.session_id, event.generation, event.episode_id

    def consumed(self, event):
        return event.button == 'left' and any(
            end == event.source_timestamp for end, _ in self.handled)

    def preview(self, event, context, *, singles, now):
        end = event.source_timestamp
        if (event.button != 'left' or (end, context) not in self.completed or
                end in self.previewed or end is None or not 0 <= now-end <= self.interval):
            return
        self.previewed.append(end)
        # Deliver a normal first tap once, without waiting for SDK pairing. A
        # deferred formal click must never interrupt a held second contact.
        if singles:
            self.handled.append((end, context))
            self.click(event)
        self.last_tap = (event, singles)

    def contact(self, event, *, enabled, doubles, context):
        if event.phase == 'BEGIN':
            if self.active is not None:
                self.cancel()
            if enabled:
                self.active = event
                previous, self.last_tap = self.last_tap, None
                if previous is not None:
                    tap, _ = previous
                    end = tap.source_timestamp
                    if (0 < event.start_timestamp-end <= self.interval and
                            0 <= event.timestamp-end <= self.interval):
                        self.candidate = previous
        if event.phase in {'END', 'CANCEL'} and self._contact_key(event) in self.cancelled:
            self.handled.append((event.end_timestamp, None))
        if self.active is None or self.active.episode_id != event.episode_id:
            return
        if event.phase == 'CANCEL':
            self.handled.append((event.end_timestamp, None))
            self.cancel()
            return
        # BEGIN can be UNKNOWN. Wait for the SDK's finger classification so a
        # two-finger scroll never sends a speculative left-button down.
        if enabled and event.mode == 'SINGLE' and self.candidate is not None and not self.held:
            tap, delivered = self.candidate
            # Double-click output and tap-to-drag have independent switches.
            # With singles off, emit the first click only after a second down
            # has actually qualified for a double; drag alone needs no click.
            if doubles and not delivered:
                self.click(tap)
                delivered = True
            self.handled.append((tap.source_timestamp, context))
            count = 2 if doubles and delivered else 1
            self.held = True
            self.press(count)
        elif event.mode == 'TWO_FINGER':
            self.candidate = None
        if event.phase == 'END':
            self.active = None
            if self.held:
                self.handled.append((event.end_timestamp, context))
                self.held = False
                self.release()
            elif event.mode == 'SINGLE':
                self.completed.append((event.end_timestamp, context))
            self.candidate = None
