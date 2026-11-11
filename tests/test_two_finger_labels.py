"""Prompted two-finger label collection: plan, labeling, scoring and window flow."""
import json
from pathlib import Path
import sys

import pytest

from ring_python_sdk.touchpad import TouchpadClickVerdict, TouchpadMove
from ring_python_sdk.touchpad.events import Raw2PointerMove

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from two_finger_labels import (CURRENT_RULE, ContactAssembler, LabelSession, evaluate, finger_share,  # noqa: E402
                               format_report, make_plan)


def contact(finger, two_frames=0, elapsed=10.):
    return dict(start_step=int(elapsed * 200), end_step=int(elapsed * 200) + len(finger), sdk_is_click=False,
                elapsed_s=elapsed, touch=[1.] * len(finger), finger=list(finger), net=[0., 0.], path_length=0.,
                sdk_two_finger_frames=two_frames)


def test_plan_covers_every_prompt_and_seed_is_repeatable():
    plan = make_plan(seed=7)
    assert len(plan) == 94
    assert sum(item['fingers'] == 1 for item in plan) == 48
    assert sum(item['gesture'] == 'swipe' for item in plan) == 40
    assert sum(item['gesture'] == 'tap' and item['fingers'] == 2 for item in plan) == 6
    assert len({item['target'] for item in plan if item['gesture'] == 'swipe'}) == 8
    assert plan == make_plan(seed=7) and plan != make_plan(seed=8)
    assert len(make_plan(2, seed=7)) == 188


def test_assembler_joins_frames_tags_and_verdict():
    assembler = ContactAssembler()
    for step in range(100, 110):
        assembler.frame(step, .9, .1 if step < 105 else .8)
        assert assembler.event(TouchpadMove(1., 0., .9, step, 0.)) is None
    assembler.pointer(Raw2PointerMove(1., 0., 0., 106, 0., mode='TWO_FINGER'))
    assembler.pointer(Raw2PointerMove(1., 0., 0., 107, 0., mode='SINGLE'))
    assembler.pointer(Raw2PointerMove(1., 0., 0., 300, 0., mode='TWO_FINGER'))
    result = assembler.event(TouchpadClickVerdict(100, 110, False, 0.))
    assert result['finger'] == [.1] * 5 + [.8] * 5 and result['net'] == [10., 0.]
    assert result['sdk_two_finger_frames'] == 1 and result['elapsed_s'] == .5
    assert not assembler.finger and assembler.two_finger_steps == {300}  # later contacts keep their tags


def test_session_labels_in_order_and_redo_requeues_the_prompt():
    plan = make_plan(seed=1)
    session = LabelSession(plan)
    first = session.accept(contact([.1] * 20))
    assert first['label'] == plan[0] and first['touch_frames_before'] == 0
    second = session.accept(contact([.1] * 10))
    assert second['touch_frames_before'] == 20 and session.completed == 2
    assert session.redo() is second and second['discarded'] and session.current == plan[1]
    assert session.completed == 1
    assert session.accept(contact([.1] * 5))['label'] == plan[1]
    total = session.total
    session.skip()
    assert session.total == total - 1


def trial(fingers, gesture, finger, two_frames=0, elapsed=10., discarded=False):
    return dict(contact(finger, two_frames, elapsed), discarded=discarded,
                label=dict(fingers=fingers, gesture=gesture, target=None, prompt=''))


def test_evaluate_counts_both_errors_by_period_and_gesture():
    trials = [trial(1, 'stroke', [.9, .9, 0, 0, 0, 0, 0, 0, 0, 0], two_frames=5),  # landing spike
              trial(2, 'swipe', [0.] * 3 + [.9] * 17, two_frames=0),               # late rise
              trial(2, 'swipe', [.9] * 20, two_frames=15, elapsed=100.),
              trial(1, 'tap', [0.] * 10),
              trial(1, 'stroke', [.9] * 20, two_frames=20, discarded=True)]
    assert finger_share(trials[1]) == .85
    report = evaluate(trials)
    assert report['trials'] == 4 and report['discarded'] == 1
    early = report['periods']['early']['moving']
    assert early[CURRENT_RULE] == dict(single_n=1, two_n=1, single_as_two=1, two_as_single=1)
    assert early['整次接触≥50%'] == dict(single_n=1, two_n=1, single_as_two=0, two_as_single=0)
    assert report['periods']['late']['moving'][CURRENT_RULE]['two_as_single'] == 0
    assert report['periods']['all']['tap'][CURRENT_RULE]['single_n'] == 1
    assert '单指误判成双指 1' in format_report(report)


@pytest.fixture
def window(tmp_path):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    import collect_two_finger_labels as tool
    app = QApplication.instance() or QApplication(['labels', '-platform', 'offscreen'])

    class FakeWorker(QObject):
        status, ready, contact, stopped = Signal(str), Signal(), Signal(object), Signal(str)

        def __init__(self, device, packet_path):
            super().__init__()
            self.stop_calls = 0

        def start(self):
            pass

        def stop(self):
            self.stop_calls += 1

        def wait(self, timeout):
            return True

    class TestWindow(tool.Window):
        worker_type = FakeWorker

    view = TestWindow('offline', seed=3, output_root=tmp_path)
    view.show()
    app.processEvents()
    yield view
    view.close()
    app.processEvents()


def test_window_collects_redoes_and_saves_report(window, tmp_path):
    window.start()
    window.accept_contact(contact([.9] * 10))   # before ready: must not be labeled
    assert not window.session.trials
    window.worker.ready.emit()
    assert window.prompt.text() == window.session.current['prompt']
    first = window.session.current
    window.worker.contact.emit(contact([.1] * 10))
    assert window.session.completed == 1 and ('✓' in window.last.text() or '✗' in window.last.text())
    window.redo()
    assert window.session.completed == 0 and window.session.current == first
    window.worker.contact.emit(contact([.1] * 10))
    window.finish()
    assert window.worker.stop_calls == 1 and window.prompt.text() == '采集结束'
    folder = next(path for path in tmp_path.iterdir() if path.is_dir())
    lines = [json.loads(line) for line in (folder / 'trials.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [line.get('kind') for line in lines] == [None, 'discard', None]
    assert lines[0]['label'] == first
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    assert report['trials'] == 1 and report['discarded'] == 1
    assert '有效接触 1 次' in window.report.toPlainText()
    assert window.grab().save(str(tmp_path / 'window.png'))
    window.worker.stopped.emit('')
    assert window.worker is None


def test_worker_hook_records_frames_and_packets(tmp_path):
    from types import SimpleNamespace
    import collect_two_finger_labels as tool
    worker = tool.RingWorker('offline', tmp_path / 'packets.jsonl')
    forwarded = []
    engine = SimpleNamespace(frame=lambda *values: forwarded.append(values))
    stream = SimpleNamespace(receiver=SimpleNamespace(engine=engine), submit=lambda packet: forwarded.append(packet))
    packets = tool.PacketLog(tmp_path / 'packets.jsonl')
    worker.hook(stream, packets)
    engine.frame(SimpleNamespace(frame=41, touch=.9, finger=.7), 1, 2)
    stream.submit(b'\x3c\x02')
    packets.close()
    assert worker.assembler.finger == {42: .7} and len(forwarded) == 2
    assert json.loads((tmp_path / 'packets.jsonl').read_text(encoding='utf-8'))['hex'] == '3c02'
