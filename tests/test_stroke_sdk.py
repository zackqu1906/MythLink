"""New stroke observations preserve the original click and pointer contract."""
from types import SimpleNamespace
import threading
import time

import pytest

from proximic_ring.touchpad_mouse import TouchpadMouseOutput
from ring_python_sdk.touchpad import TouchpadClick, TouchpadClickVerdict, TouchpadContact, TouchpadProcessor
from ring_python_sdk.touchpad.core import ClickDetector


@pytest.mark.parametrize("end,is_click", [(30, True), (70, False)])
def test_contact_edges_and_final_verdict_observe_original_click_rule(end, is_click):
    detector = ClickDetector()
    clicks = []
    for step in range(100):
        clicks += detector.probability(step, .99 if 20 <= step < end else .01)
        clicks += detector.movement(step, (.1, 0))
    assert list(detector.contact_edges) == [(True, 20, 22), (False, end, end + 2)]
    assert list(detector.contact_verdicts) == [(20, end, is_click)]
    assert clicks == ([{"kind": "click", "step": end}] if is_click else [])


def test_processor_reset_emits_only_one_contact_reset_without_false_click():
    processor = TouchpadProcessor(backbone=SimpleNamespace(reset=lambda: None))
    processor.reset()
    assert processor.poll(1.) == [TouchpadContact("reset", 0, 1.)]
    assert processor.poll(2.) == []


def test_contact_metadata_does_not_displace_pointer_events():
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True,
        invert_y=False, on_ready=lambda: None, on_end=lambda *_: None)
    output.activate()
    now = time.monotonic()
    click = TouchpadClick(1, now)
    output.submit(click)
    for _ in range(50):
        output.submit(TouchpadContact("down", 1, now))
        output.submit(TouchpadClickVerdict(1, 2, False, now))
    assert list(output.events) == [click]
    output.stop()
