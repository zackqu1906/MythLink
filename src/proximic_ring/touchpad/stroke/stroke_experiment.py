"""Sentence trial measurements, independent of Qt and the recognizer.

Reference labels are dictionary five-class stroke orders, not fine shape labels.
Every retry stays in the denominator; manual input is never a recognition sample.
"""
from collections import Counter
import time


def edit_distance(expected, actual):
    row = list(range(len(actual) + 1))
    for i, left in enumerate(expected, 1):
        next_row = [i]
        for j, right in enumerate(actual, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1,
                                row[j - 1] + (left != right)))
        row = next_row
    return row[-1]


class SentenceTrial:
    def __init__(self, target, entries, *, clock=time.monotonic):
        if not target:
            raise ValueError('Target must not be empty')
        self.target = target
        self.codes = {}
        for char, code, *_ in entries:
            if code and char not in self.codes:
                self.codes[char] = code
        self.clock = clock
        self.started = clock()
        self.ended = None
        self.events = []
        self.actual = ''

    @property
    def active(self):
        return self.ended is None

    def event(self, kind, **values):
        if self.active:
            self.events.append(dict(kind=kind, elapsed_s=self.clock() - self.started, **values))

    def stroke(self, position, stroke_index, result, recognition_ms=0):
        char = self.target[position] if position < len(self.target) else None
        code = self.codes.get(char, '')
        expected = code[stroke_index] if stroke_index < len(code) else None
        category = result.get('category') if result else None
        # An extra stroke of a known character is an error; unsupported characters
        # are unscored, because no ground truth exists in this dictionary.
        scored = char in self.codes
        self.event('stroke', position=position, character=char, stroke_index=stroke_index,
                   expected=expected, recognized=category,
                   recognized_shape=result.get('shape') if result else None,
                   correct=(expected == category and expected is not None) if scored else None,
                   recognition_ms=recognition_ms)

    def commit(self, position, character, delivered, *, source='candidate', code=''):
        expected = self.target[position] if position < len(self.target) else None
        self.event('commit', position=position, expected=expected, character=character,
                   delivered=delivered, correct=delivered and character == expected,
                   source=source, code=code)

    def finish(self, actual):
        if self.active:
            self.actual = actual
            self.ended = self.clock()
        return self.summary(actual)

    def summary(self, actual):
        elapsed = max(0., (self.ended if self.ended is not None else self.clock()) - self.started)
        strokes = [e for e in self.events if e['kind'] == 'stroke']
        scored = [e for e in strokes if e['correct'] is not None]
        commits = [e for e in self.events if e['kind'] == 'commit']
        delivered = [e for e in commits if e['delivered']]
        counts = Counter(e['kind'] for e in self.events)
        tap_decisions = [e for e in self.events if e['kind'] == 'tap_confirmation']
        distance = edit_distance(self.target, actual)
        ratio = lambda n, d: n / d if d else None
        per_category = {}
        for category in 'hspnz':
            subset = [e for e in scored if e['expected'] == category]
            per_category[category] = dict(samples=len(subset), correct=sum(e['correct'] for e in subset),
                                          accuracy=ratio(sum(e['correct'] for e in subset), len(subset)))
        return dict(target=self.target, actual=actual, elapsed_s=elapsed,
                    completed=actual == self.target, edit_distance=distance,
                    character_error_rate=distance / len(self.target),
                    text_accuracy=max(0., 1 - distance / len(self.target)),
                    characters_per_minute=len(actual) * 60 / elapsed if elapsed else 0.,
                    correct_characters_per_minute=max(0, len(self.target) - distance) * 60 / elapsed if elapsed else 0.,
                    commit_attempts=len(commits), delivered_commits=len(delivered),
                    delivery_success_rate=ratio(len(delivered), len(commits)),
                    commit_accuracy=ratio(sum(e['correct'] for e in commits), len(commits)),
                    stroke_attempts=len(strokes), scored_strokes=len(scored),
                    unscored_strokes=len(strokes) - len(scored),
                    stroke_accuracy=ratio(sum(e['correct'] for e in scored), len(scored)),
                    per_category=per_category,
                    mean_recognition_ms=ratio(sum(e['recognition_ms'] for e in strokes), len(strokes)),
                    counts=dict(counts),
                    tap_confirmations_allowed=sum(e['allowed'] for e in tap_decisions),
                    tap_confirmations_suppressed=sum(not e['allowed'] for e in tap_decisions),
                    tap_suppression_reasons=dict(Counter(e['reason'] for e in tap_decisions if not e['allowed'])),
                    contains_manual_strokes=bool(counts['manual_stroke']),
                    measurement='five-class dictionary order; local editor delivery; timing from Start',
                    events=self.events)
