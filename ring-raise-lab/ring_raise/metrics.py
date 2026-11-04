from __future__ import annotations

import math
import numpy as np
from scipy.stats import chi2


class Trigger:
    """Near-mouth candidate only; never launches voice or sends OS input."""
    def __init__(self, threshold, hop_s=.1):
        self.threshold, self.hop_s = float(threshold), hop_s
        self.reset()

    def reset(self):
        self.previous = None
        self.high_count = self.low_count = 0
        self.armed = True
        self.last_event = -math.inf

    def step(self, t, p):
        if self.previous is not None and (t <= self.previous or t - self.previous > self.hop_s * 1.5):
            self.reset()
        self.previous = t
        self.high_count = self.high_count + 1 if p[2] >= self.threshold else 0
        self.low_count = self.low_count + 1 if p[0] >= .6 else 0
        if self.low_count >= 5 and t - self.last_event >= 1.:
            self.armed = True
        if self.armed and self.high_count >= 3:
            self.armed = False
            self.last_event = t
            return True
        return False


def wilson(success, total):
    if total == 0:
        return None
    z, p = 1.95996398454, success / total
    c = (p + z*z / (2*total)) / (1 + z*z / total)
    d = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / (1+z*z/total)
    return [max(0., c-d), min(1., c+d)]


def event_metrics(package, probabilities, sessions, threshold, config):
    hits, misses, false, excluded = [], [], [], []
    negative_s = 0.
    hop = config['hop_s']
    for session in np.unique(package['session']):
        indices = np.flatnonzero(package['session'] == session)
        ts, ps = package['t'][indices], probabilities[indices]
        labels = package['y'][indices]
        trial_phases = {}
        for interval in sessions[str(session)]['annotations']['intervals']:
            if interval.get('trial_id') and interval['label'] in ('raising', 'near_mouth', 'lowering'):
                trial_phases.setdefault(interval['trial_id'], []).append(interval)
        events = []
        for trial, phases in trial_phases.items():
            raising, near, lowering = phases
            start, end = raising['start_s'], near['end_s']
            subset = ts[(ts >= start) & (ts < end)]
            coverage = len(subset) > 0 and subset[0] - start <= hop * 1.51 and end - subset[-1] <= hop * 1.51
            coverage = coverage and (len(subset) == 1 or np.max(np.diff(subset)) <= hop * 1.51)
            event = dict(session=str(session), trial_id=trial, raise_start_s=start,
                         near_start_s=near['start_s'], near_end_s=end, detected=False)
            if coverage:
                events.append(event)
            else:
                excluded.append({**event, 'reason': '缺口/未知标注/预热导致目标事件覆盖不完整'})
        detector = Trigger(threshold, hop)
        negative_s += float(np.count_nonzero((labels == 0) | (labels == 3)) * hop)
        for local, (t, p) in enumerate(zip(ts, ps)):
            if not detector.step(float(t), p):
                continue
            matching = next((e for e in events if e['raise_start_s'] <= t < e['near_end_s']), None)
            if matching is not None and not matching['detected']:
                matching['detected'] = True
                hits.append({**matching, 'trigger_s': float(t), 'latency_from_raise_s': float(t - matching['raise_start_s']),
                             'latency_from_near_s': float(t - matching['near_start_s'])})
            elif matching is not None:
                false.append(dict(session=str(session), t=float(t), activity='duplicate_target_trigger',
                                  reason='同一次抬手重复触发', negative=False))
            elif any(e['session'] == session and e['raise_start_s'] <= t < e['near_end_s'] for e in excluded):
                continue
            else:
                false.append(dict(session=str(session), t=float(t), activity=str(package['activity'][indices[local]]),
                                  reason='非目标时段触发', negative=True))
        misses.extend(e for e in events if not e['detected'])
    total = len(hits) + len(misses)
    negative_fp = sum(e['negative'] for e in false)
    hours = negative_s / 3600
    confusion = np.zeros((4, 4), dtype=int)
    for y, pred in zip(package['y'], probabilities.argmax(axis=1)):
        confusion[y, pred] += 1
    return dict(threshold=float(threshold), threshold_disables_detector=threshold > 1,
                target_events=total, matched=len(hits), missed=len(misses),
                event_recall=len(hits)/total if total else None, recall_wilson95=wilson(len(hits), total),
                negative_seconds=negative_s, false_triggers=negative_fp,
                negative_exposure_note='有效背景/放下预测帧乘 100 ms；边界存在一个步长的量化误差，未知和缺口帧不计',
                false_triggers_per_hour=negative_fp/hours if hours else None,
                poisson_upper95_per_hour=float(.5 * chi2.ppf(.95, 2*(negative_fp+1))/hours) if hours else None,
                duplicate_triggers=sum(not e['negative'] for e in false),
                latency_from_near_p50_s=float(np.median([e['latency_from_near_s'] for e in hits])) if hits else None,
                latency_from_near_p90_s=float(np.quantile([e['latency_from_near_s'] for e in hits], .9)) if hits else None,
                latency_note='负值表示到嘴边前触发；设备时间，无 BLE/显示延迟；置信区间未校正同人事件相关性',
                confusion_matrix=confusion.tolist(), hits=hits, misses=misses, false_events=false, excluded_events=excluded)


def choose_threshold(package, probabilities, sessions, config, budget=1.):
    candidates = [event_metrics(package, probabilities, sessions, t, config)
                  for t in (.5, .6, .7, .8, .9, .95, .98, .995, 1.01)]
    feasible = [r for r in candidates if r['false_triggers_per_hour'] is not None
                and r['false_triggers_per_hour'] <= budget and r['duplicate_triggers'] == 0]
    if not feasible or not any(r['target_events'] for r in candidates):
        raise ValueError('验证集缺少可评估的完整目标事件或负例时长')
    best = max(feasible, key=lambda r: (r['event_recall'] or 0, -r['false_triggers_per_hour'], r['threshold']))
    return best, candidates
