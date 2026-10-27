"""Local handwriting review before a tentative clear gesture can erase input.

Geometry gates model use. Distances are engineering scores, not probabilities.
No intended character, task label or language context participates in the review.
"""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import time

from .stroke_input import _normalize

POLICY = dict(version=1, sampleCount=64, bodyStartFraction=.10, bodyEndFraction=.85,
              minVerticalSpanRatio=.50, minBodyDrop=.15, minBodyDownConsistency=.75,
              maxFoldDistance=.08, maxFoldGap=.01, uncertainAction='stroke')


def snapshot():
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return dict(schema='ring-clear-handwriting-review', policy=dict(POLICY), sha256=digest)


@lru_cache(maxsize=1)
def _dictionary():
    from .stroke_input import StrokeDictionary
    return StrokeDictionary(recognizer='dtw')


def vertical_structure(points):
    """Measure the middle of the path, separating initial motion and end hooks."""
    trace = _normalize(points, POLICY['sampleCount'])
    if trace is None:
        return dict(structuredDescent=False, verticalSpanRatio=0., bodyDrop=0.,
                    bodyDownConsistency=0., tailDy=0.)
    start = round((len(trace)-1)*POLICY['bodyStartFraction'])
    end = round((len(trace)-1)*POLICY['bodyEndFraction'])
    body = trace[start:end+1]
    changes = [b[1]-a[1] for a,b in zip(body,body[1:])]
    down, travel = sum(max(0.,dy) for dy in changes), sum(abs(dy) for dy in changes)
    xspan = max(p[0] for p in trace)-min(p[0] for p in trace)
    yspan = max(p[1] for p in trace)-min(p[1] for p in trace)
    ratio = yspan/max(xspan, 1e-12)
    # _normalize divides by original total arc length, so this is independent
    # of contact duration, absolute stroke size and duplicate stationary points.
    drop = body[-1][1]-body[0][1]
    consistency = down/travel if travel else 0.
    structured = (ratio >= POLICY['minVerticalSpanRatio']
                  and drop >= POLICY['minBodyDrop']
                  and consistency >= POLICY['minBodyDownConsistency'])
    return dict(structuredDescent=structured, verticalSpanRatio=ratio, bodyDrop=drop,
                bodyDownConsistency=consistency, tailDy=trace[-1][1]-body[-1][1])


def review_clear(points, dictionary=None):
    metrics = vertical_structure(points)
    review = dict(version=POLICY['version'], protect=False, status='clear',
                  reason='no-structured-descent', geometry=metrics,
                  modelAttempted=False, recognition=None, recognitionMs=0.)
    if not metrics['structuredDescent']:
        return review
    review['modelAttempted'] = True
    started = time.monotonic()
    try:
        result = (dictionary if dictionary is not None else _dictionary()).recognize(points)
    except Exception as error:
        # A failed review must never authorize erasing a handwriting-like path.
        review.update(protect=True, status='uncertain', reason='model-error',
                      error=f'{type(error).__name__}: {error}')
        return review
    finally:
        review['recognitionMs'] = (time.monotonic()-started)*1000
    review['recognition'] = result
    best = result.get('distance') if result else None
    folds = [item for item in (result or {}).get('category_scores', [])
             if item.get('category')=='z' and isinstance(item.get('distance'), (int,float))
             and math.isfinite(item['distance']) and item['distance']>=0]
    if result and result.get('category')=='z' and isinstance(best,(int,float)) and math.isfinite(best) and best>=0:
        folds.append(dict(shape=result.get('shape'),category='z',distance=best))
    fold = min(folds, key=lambda item:item['distance'], default=None)
    supported = bool(fold and isinstance(best,(int,float)) and math.isfinite(best) and best>=0
                     and fold['distance']<=POLICY['maxFoldDistance']
                     and fold['distance']-best<=POLICY['maxFoldGap'])
    review.update(protect=True, status='stroke' if supported else 'uncertain',
                  reason='structured-descent-and-fold' if supported else 'structured-descent-model-uncertain',
                  foldEvidence=fold)
    return review
