"""Experimental local evidence for an ambiguous pie versus one-stroke deletion.

This is a bounded adjustment to a frozen geometric recognizer, not a calibrated
intent probability. No expected action, prompt or intended character is accepted.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
from importlib.resources import files
import json
import math
from pathlib import Path

from .stroke_context import StrokeContext
from .stroke_input import StrokeDictionary, SYMBOLS

POLICY = dict(version=1, ambiguityDegrees=6., maxShiftDegrees=4., degreesPerLogRatio=2.,
              minContextSupport=20, minFollowerSupport=3, minProbability=.01,
              minContextLift=3., minEvidenceRatio=2.5)


class StrokeGestureContext:
    def __init__(self, dictionary=None, predictor=None, *, allowed_characters=None):
        self.dictionary = dictionary if dictionary is not None else StrokeDictionary()
        self.predictor = predictor if predictor is not None else StrokeContext()
        self.allowed_characters = None if allowed_characters is None else frozenset(allowed_characters)

    def snapshot(self):
        digest = hashlib.sha256()
        for name in ('stroke-context.json.gz', 'strokes.json.gz'):
            digest.update(name.encode())
            digest.update(files('proximic_ring').joinpath('assets', name).read_bytes())
        digest.update(Path(__file__).read_bytes())
        digest.update(Path(__file__).with_name('stroke_context.py').read_bytes())
        digest.update(json.dumps(POLICY, sort_keys=True).encode())
        scope = None
        if self.allowed_characters is not None:
            scope = dict(count=len(self.allowed_characters),
                         sha256=hashlib.sha256(''.join(sorted(self.allowed_characters)).encode('utf-8')).hexdigest())
            digest.update(scope['sha256'].encode())
        return dict(schema='ring-stroke-context-assist', policy=dict(POLICY), sha256=digest.hexdigest(),
                    characterScope=scope)

    @lru_cache(maxsize=256)
    def _prefix_characters(self, code):
        return frozenset(char for char, full_code, _ in self.dictionary.entries if full_code.startswith(code)
                         and (self.allowed_characters is None or char in self.allowed_characters))

    @lru_cache(maxsize=128)
    def language_evidence(self, before_text, code):
        continuing = self._prefix_characters(code + 'p')
        # A shorter prefix is a superset, so comparing its total probability
        # would always reward deletion. Only newly restored alternatives count.
        repairing = self._prefix_characters(code[:-1]) - self._prefix_characters(code)
        evidence = self.predictor.character_evidence(before_text, continuing | repairing)
        rows = evidence['characters']
        best = lambda chars: max((rows[c] for c in chars),
                                  key=lambda row: (row['probability'], row['char']), default=None)
        return dict(hasContext=evidence['hasContext'], contextSupport=evidence['contextSupport'],
                    write=best(continuing), repair=best(repairing))

    def decide(self, metrics, baseline_action, *, before_text, stroke_code, horizontal_segments, delete_config):
        result = dict(action=baseline_action, baselineAction=baseline_action, changed=False,
                      contextUsed=False, reason='geometry-outside-ambiguity', shiftDegrees=0.,
                      effectiveBoundary=delete_config['maxAngle'], language=None)
        def unchanged(reason):
            result['reason'] = reason
            return result
        if baseline_action == 'clear-all':
            return unchanged('clear-priority')
        if horizontal_segments != 1:
            return unchanged('not-a-single-left-run')
        angle, boundary = metrics['leftAngle'], delete_config['maxAngle']
        if (angle is None or metrics['dx'] >= 0 or abs(angle-boundary) > POLICY['ambiguityDegrees']
                or metrics['length'] < delete_config['minLength']
                or metrics['straightness'] < delete_config['minStraightness']
                or metrics['leftConsistency'] < delete_config['minLeftConsistency']
                or angle < delete_config.get('minAngle', -boundary)):
            return result
        # An empty composition could mean deleting a committed character; that
        # operation has no comparable next-stroke hypothesis in this experiment.
        if not stroke_code:
            return unchanged('no-composition')
        if len(stroke_code) > 40 or any(c not in SYMBOLS for c in stroke_code):
            return unchanged('invalid-composition')
        language = self.language_evidence(before_text[-32:], stroke_code)
        result['language'] = language
        if not language['hasContext'] or language['contextSupport'] < POLICY['minContextSupport']:
            return unchanged('insufficient-context')
        write, repair = language['write'], language['repair']
        credible = lambda item: bool(item and item['support'] >= POLICY['minFollowerSupport']
                                    and item['probability'] >= POLICY['minProbability']
                                    and item['lift'] >= POLICY['minContextLift'])
        write_p = write['probability'] if write else 0.
        repair_p = repair['probability'] if repair else 0.
        ratio = (write_p + 1e-9) / (repair_p + 1e-9)
        if ratio >= POLICY['minEvidenceRatio'] and credible(write):
            shift = -min(POLICY['maxShiftDegrees'], POLICY['degreesPerLogRatio'] * math.log(ratio))
            reason = 'context-supports-writing'
        elif 1 / ratio >= POLICY['minEvidenceRatio'] and credible(repair):
            shift = min(POLICY['maxShiftDegrees'], -POLICY['degreesPerLogRatio'] * math.log(ratio))
            reason = 'context-supports-repair'
        else:
            return unchanged('competing-or-weak-candidates')
        action = 'delete-one' if angle <= boundary + shift else 'none'
        result.update(action=action, changed=action != baseline_action, contextUsed=True,
                      reason=reason, shiftDegrees=shift, effectiveBoundary=boundary + shift)
        return result
