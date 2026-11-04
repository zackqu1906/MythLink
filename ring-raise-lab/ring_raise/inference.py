from __future__ import annotations

from collections import deque
import csv
from pathlib import Path

import numpy as np
import torch

from .common import CHANNELS, RAW_CHANNELS, LABELS
from .data import make_window, load_samples
from .metrics import Trigger
from .train import load_run


class OnlinePredictor:
    def __init__(self, run, allow_synthetic=False):
        self.model, self.mean, self.std, self.config = load_run(run)
        if self.config['synthetic'] and not allow_synthetic:
            raise ValueError('合成数据模型只能回放自检，不允许用于真实戒指')
        self.window = self.config['window']
        self.trigger = Trigger(self.config['threshold'], self.window['hop_s'])
        self.rows = deque()
        self.next_end = None
        self.origin = None

    def feed(self, row):
        t = row['t']
        if self.origin is None:
            self.origin = t
            self.next_end = t + self.window['seconds']
        self.rows.append(row)
        # Keep enough past support for interpolation at the left edge.
        while self.rows and self.rows[0]['t'] < t - self.window['seconds'] - .2:
            self.rows.popleft()
        results = []
        while self.next_end <= t + 1e-9:
            times = np.array([r['t'] for r in self.rows])
            values = np.array([[r[c] for c in CHANNELS] for r in self.rows])
            segments = np.array([r['segment'] for r in self.rows])
            saturated = np.array([any(abs(r[c]) >= 32760 for c in RAW_CHANNELS) for r in self.rows])
            x = make_window(times, values, segments, self.next_end, self.window, saturated)
            if x is not None:
                with torch.inference_mode():
                    p = torch.softmax(self.model(torch.from_numpy((x[None]-self.mean)/self.std)), 1).numpy()[0]
                trigger = self.trigger.step(self.next_end, p)
                results.append(dict(t=self.next_end, probabilities=p.tolist(),
                                    state=LABELS[int(p.argmax())], trigger=trigger))
            else:
                self.trigger.reset()
            self.next_end += self.window['hop_s']
        return results


def replay(run, session, output):
    output = Path(output)
    if output.exists():
        raise ValueError('回放输出已存在，使用新文件名')
    predictor = OnlinePredictor(run, allow_synthetic=True)
    samples, _, _ = load_samples(session)
    output.parent.mkdir(parents=True, exist_ok=True)
    events, count = [], 0
    with output.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['t', *LABELS, 'state', 'trigger'])
        writer.writeheader()
        for sample in samples:
            row = {k: float(sample[k]) for k in samples.dtype.names}
            for result in predictor.feed(row):
                writer.writerow(dict(t=result['t'], **dict(zip(LABELS, result['probabilities'])),
                                     state=result['state'], trigger=int(result['trigger'])))
                count += 1
                if result['trigger']:
                    events.append(result['t'])
    return dict(output=str(output), frames=count, candidate_times_s=events,
                synthetic_model=predictor.config['synthetic'])
