"""Opt-in candidate-confirmation protection, independent of SDK click rules."""
import math
import time

from .stroke_contact_intent import CONTACT_INTENT, classify_contact


class TapConfirmationGuard:
    def __init__(self, *, max_path=1.5, cooldown_s=.25, clock=time.monotonic):
        self.max_path = max_path
        self.cooldown_s = cooldown_s
        self.clock = clock
        self.last_commit = None
        self.last_contact = None

    def reset(self):
        self.last_commit = None
        self.last_contact = None

    def committed(self, contact=None):
        self.last_commit = self.clock()
        self.last_contact = ({key: contact.get(key) for key in ('stream_id', 'end_step')}
                             if contact else None)

    def snapshot(self):
        return dict(max_path=self.max_path, cooldown_ms=self.cooldown_s * 1000,
                    scope='candidate-confirmation-only', blocked_contact='preserve-ink-no-stroke',
                    classified_contact_policy=dict(CONTACT_INTENT))

    def decide(self, contact, *, enabled=True):
        points = contact.get('points') or []
        try:
            valid = all(len(p) == 2 and all(math.isfinite(v) for v in p) for p in points)
            path = sum(math.dist(a, b) for a, b in zip(points, points[1:])) if valid else 0.
        except (TypeError, ValueError):
            valid, path = False, 0.
        since_commit = None if self.last_commit is None else self.clock() - self.last_commit
        timebase = 'host'
        if (self.last_contact and contact.get('stream_id') is not None
                and contact['stream_id'] == self.last_contact.get('stream_id')
                and isinstance(contact.get('start_step'), int)
                and isinstance(self.last_contact.get('end_step'), int)):
            since_commit = (contact['start_step']-self.last_contact['end_step'])*.005
            timebase = 'contact-frames'
        # Classified application taps have already passed the shared intent
        # policy. Recheck the geometry, rather than trusting a UI-supplied label,
        # and avoid applying the old 1.5 path limit a second time.
        intent = classify_contact(points, finished=True) if 'intent_decision' in contact else None
        if contact.get('intent_decision', {}).get('reason') == 'incomplete-contact':
            reason = 'incomplete-contact'
        elif intent and intent.get('reason') == 'invalid-geometry':
            reason = 'invalid-geometry'
        elif intent and (intent.get('action') != 'tap' or contact['intent_decision'].get('action') != 'tap'):
            reason = 'uncertain-motion'
        elif not enabled:
            reason = 'disabled'
        elif not valid or not math.isfinite(path):
            reason = 'invalid-geometry'
        elif intent is None and path >= self.max_path:
            reason = 'moving-contact'
        elif since_commit is not None and since_commit < self.cooldown_s:
            reason = 'commit-cooldown'
        else:
            reason = 'light-tap'
        return dict(allowed=reason in ('disabled', 'light-tap'), reason=reason,
                    path_length=path, since_commit_ms=None if since_commit is None else since_commit * 1000,
                    cooldown_timebase=timebase, intent_reason=intent.get('reason') if intent else None,
                    **{k: contact.get(k) for k in ('start_step', 'end_step', 'duration_ms')})
