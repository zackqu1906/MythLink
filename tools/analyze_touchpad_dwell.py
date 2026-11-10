"""Summarize labelled SDK contact windows without assuming silence means contact."""
import argparse
import collections
import json
import math
from pathlib import Path


def measure(frames):
    x = y = path = radius = max_step = 0.
    nonzero = 0
    recent, max_window = collections.deque(), 0.
    for f in frames:
        x += f['dx']; y += f['dy']
        step = math.hypot(f['dx'], f['dy'])
        path += step
        radius = max(radius, math.hypot(x, y))
        max_step = max(max_step, step)
        nonzero += bool(step)
        recent.append(f)
        while recent and recent[0]['source'] <= f['source']-100_000_000:
            recent.popleft()
        max_window = max(max_window, math.hypot(sum(r['dx'] for r in recent), sum(r['dy'] for r in recent)))
    return dict(frames=len(frames), nonzero=nonzero, max_radius=radius, path=path,
                max_step=max_step, max_100ms_displacement=max_window)


def analyze(path):
    rows = [json.loads(s) for s in Path(path).read_text().splitlines()]
    frames = collections.defaultdict(list)
    for row in rows:
        if row['type'] == 'frame':
            frames[row.get('session_id'), row.get('generation')].append(row)
    starts, episodes = {}, []
    for row in rows:
        if row['type'] != 'ContactUpdate':
            continue
        meta = row['meta']
        key = meta['session_id'], meta['generation'], row['episode_id']
        if row['phase'] == 'BEGIN':
            starts[key] = row
        elif row['phase'] in ('END', 'CANCEL') and key in starts:
            start = starts.pop(key)
            group = frames[key[:2]]
            observed = next((f for f in group if f['episode_id'] == key[2] and
                             f['frame'] >= start['range']['start_frame']), None)
            if observed is None:
                continue
            selected = [f for f in group if observed['frame'] <= f['frame'] < row['range']['end_frame_exclusive']]
            entry = dict(label=start['label'], episode_id=key[2], mode=row['mode'], ended=row['phase'],
                         same_label=start['label'] == row['label'],
                         duration_ms=(row['range']['end_time_ns']-start['meta']['source_time_ns'])/1e6,
                         total=measure(selected), windows={})
            for ms in (300, 400, 600):
                if entry['duration_ms'] >= ms:
                    samples = [f for f in selected if f['source'] < start['meta']['source_time_ns']+ms*1e6]
                    entry['windows'][str(ms)] = measure(samples)
            episodes.append(entry)
    summary = {}
    for label in ('still', 'slow', 'normal'):
        subset = [e for e in episodes if e['label'] == label and e['same_label'] and e['mode'] == 'SINGLE' and e['ended'] == 'END']
        summary[label] = dict(contacts=len(subset), windows={},
            max_100ms_displacement=max((e['total']['max_100ms_displacement'] for e in subset), default=None))
        for ms in ('300', '400', '600'):
            windows = [e['windows'][ms] for e in subset if ms in e['windows']]
            summary[label]['windows'][ms] = dict(count=len(windows),
                maxima={metric: max((w[metric] for w in windows), default=None)
                        for metric in ('max_radius', 'path', 'max_step')},
                nonzero_frames=sum(w['nonzero'] for w in windows), frames=sum(w['frames'] for w in windows))
    return dict(summary=summary, episodes=episodes,
                row_counts=dict(collections.Counter(row['type'] for row in rows)))


def replay(path):
    from proximic_ring.touchpad.touchpad_drag import TouchpadDrag
    from ring_python_sdk.touchpad.events import Raw2PointerContact, Raw2PointerMove
    rows = [json.loads(s) for s in Path(path).read_text().splitlines()]
    results = {}
    for ms in (300, 400, 600):
        drag = TouchpadDrag(lambda: None, lambda: None)
        episodes, current = [], None
        for row in rows:
            if row['type'] not in {'ContactUpdate', 'Motion'}:
                continue
            meta = row['meta']
            stamp = meta['source_time_ns']/1e9
            fields = dict(session_id=meta['session_id'], generation=meta['generation'], sequence=meta['sequence'])
            if row['type'] == 'ContactUpdate':
                contact, phase = row['range'], row['phase']
                event = Raw2PointerContact(phase, row['mode'], row['episode_id'], 0, stamp,
                    contact['start_time_ns']/1e9, end_timestamp=None if contact['end_time_ns'] is None
                    else contact['end_time_ns']/1e9, **fields)
                if phase == 'BEGIN':
                    current = dict(label=row['label'], episode=row['episode_id'], dragged=False,
                                   moved=False, ready=False, start=stamp)
                drag.tick(stamp)
                if current:
                    current['ready'] |= drag.state == 'ready'
                drag.contact(event, enabled=True, dwell_ms=ms)
                if phase in {'END', 'CANCEL'} and current:
                    current.update(duration_ms=(stamp-current.pop('start'))*1000,
                        valid=phase == 'END' and row['mode'] == 'SINGLE' and current['label'] == row['label'])
                    episodes.append(current)
                    current = None
            else:
                event = Raw2PointerMove(row['dx'], row['dy'], 0, row['frame_id']+1, stamp,
                    mode=row['mode'], episode_id=row['episode_id'], **fields)
                value = drag.move(event)
                if current:
                    current['ready'] |= drag.state == 'ready'
                    current['dragged'] |= bool(value and value[3])
                    current['moved'] |= drag.state == 'moving'
        summary = {}
        for label in ('still', 'slow'):
            selected = [e for e in episodes if e['label'] == label and e['valid'] and e['duration_ms'] >= 600]
            summary[label] = dict(n=len(selected), ready=sum(e['ready'] for e in selected),
                dragged=sum(e['dragged'] for e in selected), ordinary_move=sum(e['moved'] for e in selected))
        results[ms] = dict(summary=summary, episodes=episodes)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('samples')
    parser.add_argument('--output')
    parser.add_argument('--replay', action='store_true')
    args = parser.parse_args()
    report = analyze(args.samples)
    if args.replay:
        report['replay'] = replay(args.samples)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
