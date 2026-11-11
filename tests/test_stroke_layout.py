"""Raw token records, whole-character capture and the offline layout check."""
import asyncio
import io
import json
from pathlib import Path
import struct
import tarfile
import tkinter as tk

import numpy as np
import pytest

from ring_python_sdk import RingSession
from ring_python_sdk.touchpad import TouchpadProcessor, TouchpadTokens


class Backbone:
    def reset(self):
        pass

    def step(self, token):
        return np.array([[.4, .1, 0.]]*5)


def packet(seq, count=10, value=0.):
    values = [value]*(count*6)
    return b'\x21\x05'+struct.pack('<HBIB', seq, count, seq*50, 1)+struct.pack('<'+'e'*(count*6), *values)


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))


def test_token_records_are_opt_in_and_share_event_steps():
    plain = TouchpadProcessor(backbone=Backbone())
    plain.feed(packet(0), arrival=1, now=1)
    assert plain.take_tokens() == [] and plain.token_records is None
    p = TouchpadProcessor(backbone=Backbone())
    p.enable_token_records()
    for n in range(25):
        t = 1+n*.05
        p.feed(packet(n, value=.5), arrival=t, now=t)
    records = p.take_tokens()
    assert len(records) == 25 and all(isinstance(r, TouchpadTokens) for r in records)
    assert records[0].steps == tuple(range(1, 11)) and records[-1].steps[-1] == p.post.n == 250
    assert records[0].values[0] == (.5,)*6 and records[0].epoch == 0
    assert p.take_tokens() == []
    p.feed(packet(30), arrival=3, now=3)  # sequence gap: reset, steps restart
    record, = p.take_tokens()
    assert record.epoch == 1 and record.steps[0] == 1


def test_session_delivers_every_token_record(tmp_path, monkeypatch):
    from ring_python_sdk.touchpad import stream
    monkeypatch.setattr(stream, 'TouchpadProcessor', lambda _: TouchpadProcessor(backbone=Backbone()))

    async def run():
        session = RingSession('test', 1, data_root=tmp_path)
        session.session_dir, session.rx_uuid = tmp_path, 'rx'

        class Client:
            is_connected = True
            async def write_gatt_char(self, *args, **kwargs): pass
            async def disconnect(self): self.is_connected = False
            async def stop_notify(self, *args): pass
        session.client = Client()
        records = []
        with pytest.raises(TypeError):
            await session.touchpad_on(on_event=lambda e: None, on_tokens=1)
        await session.touchpad_on(on_event=lambda e: None, on_tokens=records.append, duration_s=2)
        for seq in range(5):
            session._demux(None, packet(seq))
        for _ in range(100):
            if sum(len(r.steps) for r in records) == 50:
                break
            await asyncio.sleep(.01)
        await session.touchpad_off()
        assert [s for r in records for s in r.steps] == list(range(1, 51))
    asyncio.run(run())


def move(step, dx=1., dy=0.):
    return dict(kind='move', step=step, dx=dx, dy=dy, contact_probability=.9, timestamp=step*.005)


def contact(state, step):
    return dict(kind='contact', state=state, step=step, timestamp=step*.005, confirmed_step=step)


def test_character_capture_keeps_contacts_window_and_epoch(tools):
    from selection_capture import CharacterCapture
    capture = CharacterCapture()
    assert capture.finish() == '还没有完整笔画'
    capture.feed_tokens(dict(epoch=3))
    for step in range(5, 10):
        capture.feed(move(step, 0, 0))
    capture.feed(contact('down', 10))
    for step in range(10, 20):
        capture.feed(move(step))
    capture.feed(contact('up', 20))
    for step in range(20, 30):
        capture.feed(move(step, 0, 0))
    capture.feed(contact('down', 30))
    capture.feed(move(30, 0, 1))
    assert capture.touching and capture.finish() is None
    for step in range(31, 40):
        capture.feed(move(step, 0, 1))
    capture.feed(contact('up', 40))
    capture.feed(move(40, 0, 0))
    assert len(capture.preview()) == 2
    draft = capture.finish()
    assert [c['startStep'] for c in draft['contacts']] == [10, 30]
    assert draft['windowStartStep'] == 5 and draft['windowEndStep'] == 40 and draft['tokenEpoch'] == 3
    assert {p['stroke'] for p in draft['points']} == {0, 1}
    assert capture.contacts == [] and capture.epoch is None
    capture.feed_tokens(dict(epoch=3))
    assert capture.feed_tokens(dict(epoch=4)) == '信号重置；本字未保存'
    capture.feed(contact('down', 50))
    assert capture.feed(contact('reset', 51)) == '信号重置；本字未保存'


def test_plan_codes_match_dictionary_and_window_saves_characters(tools, tmp_path):
    import stroke_layout_collection as study
    from ring_selection_collector import CollectionWindow
    dictionary, _ = study.baseline()
    assert len(study.inventory(1)) == 25
    tasks = study.inventory()
    assert len(tasks) == 26
    for task in study.inventory(1) + tasks:
        codes = {entry[1] for entry in dictionary.by_character[task['character']]}
        assert task['expectedCode'] in codes and task['character'] in task['group']
    shared = {}
    for task in tasks:
        # Inside a group the compared prefix is one code, so only layout differs.
        shared.setdefault(task['group'], set()).add(task['expectedCode'][:task['compareStrokes']])
    assert all(len(codes) == 1 for codes in shared.values())
    assert next(t for t in tasks if t['character'] == '寺')['compareStrokes'] == 5
    session = study.new_session('P01', rounds=1)
    assert session['planVersion'] == 2 and study.validate_session(session) is session
    legacy = study.new_session('P01', rounds=1)
    legacy['planVersion'] = 1
    legacy['tasks'] = [{**t, 'round': 1} for t in study.inventory(1)]
    assert study.validate_session(legacy) is legacy
    root = tk.Tk()
    root.withdraw()
    try:
        window = CollectionWindow(root, tmp_path, profile=study)
        window.install(session)
        window.connected = window.warm = True
        window.arm()
        window.tokens(dict(epoch=0, steps=[1], frame_times=[0.], values=[[0.]*6], timestamp=0., kind='tokens'))
        step = 100
        for _ in range(session['tasks'][0]['expectedLogicalStrokes']):
            window.event(contact('down', step))
            for s in range(step, step+20):
                window.event(move(s, 1, 1))
            window.event(contact('up', step+20))
            for s in range(step+20, step+30):
                window.event(move(s, 0, 0))
            step += 30
        assert window.armed and window.session['draft'] is None
        window.next_sample()  # finishes the character
        assert not window.armed and window.session['draft']['tokenEpoch'] == 0
        window.save_sample()
        restored = study.validate_session(json.loads(window.path.read_text(encoding='utf-8')))
        saved = restored['samples'][0]
        assert saved['strokeCountMatches'] and len(saved['perStrokeBaseline']) == len(saved['contacts'])
        assert '每轮 26 字' in window.plan_help.get()
        window.raw_stream.close()
        window.token_stream.close()
        logged = window.path.with_suffix('.tokens.jsonl').read_text(encoding='utf-8')
        assert json.loads(logged)['tokens']['steps'] == [1]
    finally:
        root.destroy()


MEDIANS = {  # Synthetic layouts in the hanzi-writer frame (y grows upward).
    '人': [[[500, 800], [200, 100]], [[480, 600], [850, 100]]],
    '八': [[[350, 700], [150, 150]], [[600, 700], [850, 150]]],
    '入': [[[550, 700], [200, 100]], [[350, 800], [850, 100]]],
}


def glyph_archive(path):
    with tarfile.open(path, 'w:gz') as archive:
        for character, medians in MEDIANS.items():
            raw = json.dumps(dict(strokes=['M 0 0'], medians=medians)).encode()
            info = tarfile.TarInfo(f'package/{character}.json')
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))


def synthetic_sample(character, index, step0, bias, gyro_of):
    """Gyro and model displacement generated from one known velocity path."""
    strokes = [np.array(s, float)*(1, -1)/100 for s in MEDIANS[character]]
    velocities, spans = [np.zeros(2)]*20, []
    position = strokes[0][0]
    for stroke in strokes:
        hover = stroke[0]-position
        velocities += [hover/30]*30
        begin = len(velocities)
        velocities += [(stroke[1]-stroke[0])/40]*40
        spans.append((begin, len(velocities)))
        position = stroke[1]
    velocities += [np.zeros(2)]*20
    steps = list(range(step0, step0+len(velocities)))
    tokens = [[0., 0., 0., *(gyro_of(v)+bias)] for v in velocities]
    contacts = []
    for begin, end in spans:
        x = y = 0.
        points = [dict(x=0., y=0., t=0, step=steps[begin])]
        for k in range(begin+1, end):
            x, y = x+velocities[k][0], y+velocities[k][1]
            points.append(dict(x=x, y=y, t=(k-begin)*5, step=steps[k]))
        points.append(dict(x=x, y=y, t=(end-begin)*5, step=steps[end]))
        contacts.append(dict(points=points, startStep=steps[begin], endStep=steps[end]))
    task = dict(character=character, group='人八入', expectedLogicalStrokes=2)
    sample = dict(taskIndex=index, task=task, contacts=contacts, tokenEpoch=0,
                  windowStartStep=steps[0], windowEndStep=steps[-1])
    return sample, dict(epoch=0, steps=steps, values=tokens)


def test_layout_analysis_recovers_synthetic_characters(tools, tmp_path):
    from analyze_stroke_layout import analyze
    archive = tmp_path/'glyphs.tgz'
    glyph_archive(archive)
    mapping = np.array([[0., 2.], [1.5, 0.], [.3, -.4]])  # unknown ring orientation
    samples, records, step = [], [], 1
    for index, character in enumerate('人八入'*3):
        sample, record = synthetic_sample(character, index, step, np.array([.05, -.02, .01]),
                                          lambda v: mapping @ v)
        samples.append(sample)
        records.append(record)
        step = record['steps'][-1]+1
    path = tmp_path/'stroke-layout-test.json'
    path.write_text(json.dumps(dict(sessionId='s', phase='train', samples=samples)), encoding='utf-8')
    path.with_suffix('.tokens.jsonl').write_text(
        ''.join(json.dumps(dict(tokens=r))+'\n' for r in records), encoding='utf-8')
    summary = analyze([path], archive)['summary']
    assert summary['evaluated'] == 9
    assert summary['methods']['gyroOnly']['top1'] == 1 and summary['methods']['boundaryX']['top1'] == 1
    assert summary['correctedGapAxes']['x']['medianAbsError'] < .05
    assert summary['chanceTop1'] == pytest.approx(1/3)
    assert summary['contactAngleErrorDeg']['p90'] < 5
    assert summary['contactScaleRatio']['median'] == pytest.approx(1, abs=.1)
