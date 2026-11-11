"""Ground-truth single/two-finger collection: prompts, contact records, rule scoring.

No Qt or BLE here, so the plan, labeling and scoring can be tested offline.
Steps follow the Raw2 stream: step = model frame + 1, 200 per second.
"""
from collections import deque
import random

STROKES = (('横', 'h'), ('竖', 's'), ('撇', 'p'), ('捺', 'n'), ('折', 'z'))
DIRECTIONS = (('right', '→', '右'), ('down-right', '↘', '右下'), ('down', '↓', '下'),
              ('down-left', '↙', '左下'), ('left', '←', '左'), ('up-left', '↖', '左上'),
              ('up', '↑', '上'), ('up-right', '↗', '右上'))
STROKE_REPEATS, SINGLE_TAPS, SWIPE_REPEATS, TWO_FINGER_TAPS = 8, 8, 5, 6
STEPS_PER_SECOND = 200
EARLY_SECONDS = 80.
SHARE_THRESHOLDS = (.3, .4, .5, .6, .7)
CURRENT_RULE = '当前规则'


def make_plan(rounds=1, seed=None):
    """Shuffled so one-finger and two-finger contacts directly follow each other."""
    plan = []
    for _ in range(rounds):
        for name, code in STROKES:
            plan += [dict(fingers=1, gesture='stroke', target=code, prompt=f'单指 · 写「{name}」')] * STROKE_REPEATS
        plan += [dict(fingers=1, gesture='tap', target=None, prompt='单指 · 轻触一下')] * SINGLE_TAPS
        for direction, arrow, name in DIRECTIONS:
            plan += [dict(fingers=2, gesture='swipe', target=direction,
                          prompt=f'双指 · 向{name}滑 {arrow}')] * SWIPE_REPEATS
        plan += [dict(fingers=2, gesture='tap', target=None, prompt='双指 · 轻触一下')] * TWO_FINGER_TAPS
    random.Random(seed).shuffle(plan)
    return [dict(item) for item in plan]


class ContactAssembler:
    """Join per-frame model values, Raw2 finger tags and contact verdicts."""

    def __init__(self):
        self.touch, self.finger, self.move = {}, {}, {}
        self.two_finger_steps = set()

    def frame(self, step, touch, finger):
        self.touch[step], self.finger[step] = touch, finger

    def pointer(self, event):
        mode = getattr(event, 'mode', '')
        if event.kind == 'move' and str(getattr(mode, 'value', mode)).upper() == 'TWO_FINGER':
            self.two_finger_steps.add(event.step)

    def event(self, event):
        """Return one finished contact when its verdict arrives, else None."""
        if event.kind == 'move':
            self.move[event.step] = (event.dx, event.dy)
        elif event.kind == 'contact' and event.state == 'reset':
            self.__init__()
        elif event.kind == 'click_verdict':
            return self._finish(event.start_step, event.step, event.is_click)
        return None

    def _finish(self, start, end, is_click):
        steps = range(start, end)
        moves = [self.move.get(step, (0., 0.)) for step in steps]
        contact = dict(start_step=start, end_step=end, sdk_is_click=is_click,
                       elapsed_s=round(start / STEPS_PER_SECOND, 3),
                       touch=[round(self.touch.get(step, 0.), 3) for step in steps],
                       finger=[round(self.finger.get(step, 0.), 3) for step in steps],
                       net=[round(sum(m[0] for m in moves), 2), round(sum(m[1] for m in moves), 2)],
                       path_length=round(sum((m[0] ** 2 + m[1] ** 2) ** .5 for m in moves), 2),
                       sdk_two_finger_frames=sum(step in self.two_finger_steps for step in steps))
        for values in (self.touch, self.finger, self.move):
            for step in [step for step in values if step < end]:
                del values[step]
        self.two_finger_steps = {step for step in self.two_finger_steps if step >= end}
        return contact


class LabelSession:
    """Assign each finished contact to the prompt on screen."""

    def __init__(self, plan):
        self.pending = deque(plan)
        self.total = len(self.pending)
        self.trials = []
        self.touch_frames = 0

    @property
    def current(self):
        return self.pending[0] if self.pending else None

    @property
    def completed(self):
        return sum(not trial['discarded'] for trial in self.trials)

    def accept(self, contact):
        if not self.pending:
            return None
        item = self.pending.popleft()
        trial = dict(contact, label=item, index=len(self.trials), discarded=False,
                     touch_frames_before=self.touch_frames)
        self.touch_frames += len(contact['finger'])
        self.trials.append(trial)
        return trial

    def redo(self):
        """Discard the newest kept contact and ask for that prompt again."""
        for trial in reversed(self.trials):
            if not trial['discarded']:
                trial['discarded'] = True
                self.pending.appendleft(trial['label'])
                return trial
        return None

    def skip(self):
        """Drop the prompt on screen; the plan total shrinks by one."""
        if self.pending:
            self.pending.popleft()
            self.total -= 1


def finger_share(trial):
    finger = trial['finger']
    return sum(value >= .5 for value in finger) / len(finger) if finger else 0.


def rules():
    table = {CURRENT_RULE: lambda trial: trial['sdk_two_finger_frames'] > 0}
    for threshold in SHARE_THRESHOLDS:
        table[f'整次接触≥{threshold:.0%}'] = lambda trial, t=threshold: finger_share(trial) >= t
    return table


def score(trials):
    """Count both error directions for every candidate rule."""
    one = [trial for trial in trials if trial['label']['fingers'] == 1]
    two = [trial for trial in trials if trial['label']['fingers'] == 2]
    result = {}
    for name, rule in rules().items():
        result[name] = dict(single_n=len(one), two_n=len(two),
                            single_as_two=sum(bool(rule(trial)) for trial in one),
                            two_as_single=sum(not rule(trial) for trial in two))
    return result


def evaluate(trials, early_seconds=EARLY_SECONDS):
    kept = [trial for trial in trials if not trial['discarded']]
    periods = dict(all=kept, early=[t for t in kept if t['elapsed_s'] < early_seconds],
                   late=[t for t in kept if t['elapsed_s'] >= early_seconds])
    return dict(early_seconds=early_seconds, trials=len(kept), discarded=len(trials) - len(kept),
                periods={name: dict(moving=score([t for t in group if t['label']['gesture'] != 'tap']),
                                    tap=score([t for t in group if t['label']['gesture'] == 'tap']))
                         for name, group in periods.items()})


def format_report(report):
    names = dict(all='全程', early=f"前 {report['early_seconds']:.0f} 秒", late=f"{report['early_seconds']:.0f} 秒之后")
    lines = [f"有效接触 {report['trials']} 次，重做作废 {report['discarded']} 次", '']
    for period in ('all', 'early', 'late'):
        moving = report['periods'][period]['moving']
        first = next(iter(moving.values()))
        lines.append(f"{names[period]}（滑动/书写：单指 {first['single_n']} 次，双指 {first['two_n']} 次）")
        if first['single_n'] + first['two_n']:
            for name, row in moving.items():
                lines.append(f"  {name}：单指误判成双指 {row['single_as_two']}，双指漏判成单指 {row['two_as_single']}")
        lines.append('')
    return '\n'.join(lines).rstrip()
