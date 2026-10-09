"""Stroke asset, segmentation, and offline candidate behavior."""
from types import SimpleNamespace
import threading
import time

from proximic_ring.stroke_input import StrokeCollector, StrokeDictionary, SYMBOLS
from proximic_ring.touchpad_strokes import TouchpadStrokeOutput
from ring_python_sdk.touchpad import TouchpadMove, TouchpadContact, TouchpadClickVerdict


def test_trace_observer_is_bounded_and_cannot_mutate_or_interrupt_recognition():
    paths, views = [], []
    def display(view, finished):
        views.append((len(view), finished))
        view.clear()
        raise RuntimeError("display failure")
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=lambda: None,
        on_end=lambda *_: None, on_stroke=paths.append, on_tap=lambda: None,
        on_trace=display)
    path = [(float(step), 0.) for step in range(1000)]
    output._publish_stroke(path)
    assert views == [(201, True)]
    assert paths == [path] and len(path) == 1000


def test_prototype_templates_and_common_candidates_are_available():
    dictionary = StrokeDictionary()
    assert len(dictionary.templates) >= 200
    assert set(SYMBOLS) == {"h", "s", "p", "n", "z"}
    for category in SYMBOLS:
        template = next(item for item in dictionary.templates if item["category"] == category)
        assert dictionary.recognize(template["trace"])["category"] == category
    assert "一" in dictionary.candidates("h", 10)
    assert "你" in dictionary.candidates("pspzspn", 30)


def test_collector_uses_confirmed_edges_and_trims_already_received_lift():
    collector = StrokeCollector(min_length=1)
    for step in range(10): collector.feed(TouchpadMove(1, 0, .99, step, 0))
    assert not collector.points
    collector.feed(TouchpadContact('down', 2, 0))
    assert collector.points[-1] == (7., 0.)
    points = collector.feed(TouchpadContact('up', 7, 0))
    assert points[-1] == (4., 0.) and len(points) == 5
    assert not collector.touching and not collector.points


def test_contacts_can_arrive_before_moves_in_one_sdk_batch():
    collector = StrokeCollector()
    collector.feed(TouchpadContact('down', 1, 0))
    collector.feed(TouchpadContact('up', 8, 0))
    paths = []
    for step in range(12):
        result = collector.feed(TouchpadMove(1, 0, .01, step, 0))
        if result is not None: paths.append(result)
    assert len(paths) == 1 and paths[0][-1] == (6., 0.)
    assert not collector.touching


def test_stroke_worker_emits_completed_path_without_blocking_producer():
    ready, finished, completed = threading.Event(), threading.Event(), threading.Event()
    paths = []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: finished.set(), on_stroke=lambda path: (paths.append(path), completed.set()),
        on_tap=lambda: None)
    output.start()
    assert ready.wait(1)
    output.activate()
    now = time.monotonic()
    output.submit(TouchpadContact('down', 0, now))
    output.submit(TouchpadMove(0, 0, .01, 0, now))
    for step in range(1, 10):
        output.submit(TouchpadMove(1, 0, .9, step, now))
    output.submit(TouchpadContact('up', 10, now))
    output.submit(TouchpadClickVerdict(0, 10, False, now))
    assert completed.wait(1)
    output.stop()
    assert finished.wait(1)
    assert paths[0][-1] == (9., 0.)


def test_worker_reorders_contact_batch_and_original_sdk_click_wins_over_pixel_path():
    from ring_python_sdk.touchpad import TouchpadClick
    ready, done, written = threading.Event(), threading.Event(), threading.Event()
    paths, taps = [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: done.set(), on_stroke=lambda path: (paths.append(path), written.set()),
        on_tap=lambda: (taps.append(True), written.set()))
    output.start(); assert ready.wait(1); output.activate()
    now=time.monotonic()
    batch=[TouchpadContact('down',0,now),TouchpadContact('up',10,now),TouchpadClick(10,now)]
    batch += [TouchpadMove(1,0,.99,step,now) for step in range(13)]
    # Enqueue atomically to reproduce SDK contact-before-movement delivery.
    with output.lock: output.events.extend(batch)
    output.wake.set()
    assert written.wait(1)
    output.stop(); assert done.wait(1)
    assert paths==[] and taps==[True]


def test_worker_delays_tap_until_zero_length_contact_is_complete():
    from ring_python_sdk.touchpad import TouchpadClick
    ready, done, tapped = threading.Event(), threading.Event(), threading.Event()
    paths=[]
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: done.set(), on_stroke=paths.append,on_tap=tapped.set)
    output.start(); assert ready.wait(1); output.activate()
    now=time.monotonic()
    output.submit(TouchpadContact('down',0,now))
    output.submit(TouchpadContact('up',5,now))
    output.submit(TouchpadClick(5,now))
    time.sleep(.03)
    assert not tapped.is_set()
    for step in range(5): output.submit(TouchpadMove(0,0,.01,step,time.monotonic()))
    assert tapped.wait(1)
    output.stop(); assert done.wait(1)
    assert paths==[]


def test_original_detector_tap_with_amplified_movement_confirms_without_adding_stroke():
    from ring_python_sdk.touchpad import TouchpadClick
    from ring_python_sdk.touchpad.core import ClickDetector
    detector = ClickDetector()
    batch = []
    now = time.monotonic()
    for step, probability in enumerate([.01]*25 + [.99]*16 + [.01]*25):
        clicks = detector.probability(step, probability)
        for down, boundary, confirmed in detector.contact_edges:
            batch.append(TouchpadContact('down' if down else 'up', boundary, now, confirmed))
        detector.contact_edges.clear()
        # Raw displacement satisfies main's original click rule, while screen
        # gain makes the pixel path exceed the stroke collector's minimum.
        clicks += detector.movement(step, (.1, 0.))
        batch.append(TouchpadMove(.3, 0., probability, step, now))
        batch.extend(TouchpadClick(click['step'], now) for click in clicks)
        batch.extend(TouchpadClickVerdict(start, end, is_click, now)
                     for start, end, is_click in detector.contact_verdicts)
        detector.contact_verdicts.clear()
    assert sum(event.kind == 'click' for event in batch) == 1
    ready, done, tapped = threading.Event(), threading.Event(), threading.Event()
    paths = []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: done.set(), on_stroke=paths.append, on_tap=tapped.set)
    output.start(); assert ready.wait(1); output.activate()
    with output.lock: output.events.extend(batch)
    output.wake.set()
    assert tapped.wait(1)
    output.stop(); assert done.wait(1)
    assert paths == []


def test_short_written_stroke_without_sdk_click_is_preserved():
    ready, done, written = threading.Event(), threading.Event(), threading.Event()
    paths, taps = [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: done.set(), on_stroke=lambda path: (paths.append(path), written.set()),
        on_tap=lambda: taps.append(True))
    output.start(); assert ready.wait(1); output.activate()
    now = time.monotonic()
    batch = [TouchpadContact('down', 0, now), TouchpadContact('up', 10, now)]
    batch += [TouchpadMove(1, 0, .99, step, now) for step in range(10)]
    batch.append(TouchpadClickVerdict(0, 10, False, now))
    with output.lock: output.events.extend(batch)
    output.wake.set()
    assert written.wait(1)
    output.stop(); assert done.wait(1)
    assert paths[0][-1] == (9., 0.) and taps == []


def test_multiple_strokes_in_one_batch_keep_separate_sdk_boundaries():
    ready, done, written = threading.Event(), threading.Event(), threading.Event()
    paths=[]
    def stroke(path):
        paths.append(path)
        if len(paths)==2: written.set()
    output = TouchpadStrokeOutput(connection=threading.Event(),on_ready=ready.set,
        on_end=lambda *_: done.set(),on_stroke=stroke,on_tap=lambda: None)
    output.start(); assert ready.wait(1); output.activate()
    now=time.monotonic()
    batch=[TouchpadContact('down',0,now),TouchpadContact('up',10,now),
           TouchpadContact('down',14,now),TouchpadContact('up',24,now)]
    batch += [TouchpadMove(1,0,.5,step,now) for step in range(27)]
    batch += [TouchpadClickVerdict(0,10,False,now),TouchpadClickVerdict(14,24,False,now)]
    with output.lock: output.events.extend(batch)
    output.wake.set(); assert written.wait(1)
    output.stop(); assert done.wait(1)
    assert [path[-1] for path in paths]==[(9.,0.),(9.,0.)]


def test_late_click_with_already_received_lift_frames_never_publishes_a_stroke():
    """The recorded third failure: up is backdated and click arrives next batch."""
    from ring_python_sdk.touchpad import TouchpadClick
    ready, traced, tapped, written = (threading.Event() for _ in range(4))
    paths, taps = [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: None, on_stroke=lambda p: (paths.append(p), written.set()),
        on_tap=lambda: (taps.append(True), tapped.set()),
        on_trace=lambda p, finished: traced.set() if len(p) == 14 else None)
    output.start(); assert ready.wait(1); output.activate()
    try:
        now = time.monotonic()
        with output.lock:
            output.events.extend([TouchpadContact('down', 0, now)] +
                [TouchpadMove(.3, 0., .99, step, now) for step in range(14)])
        output.wake.set(); assert traced.wait(1)
        output.submit(TouchpadContact('up', 10, time.monotonic()))
        # Wait beyond the old 30 ms guess. Subsequent movement is not a verdict.
        assert not written.wait(.08)
        output.submit(TouchpadClick(10, time.monotonic()))
        output.submit(TouchpadClickVerdict(0, 10, True, time.monotonic()))
        assert tapped.wait(1)
        assert paths == [] and taps == [True]
    finally:
        output.stop(); output.thread.join(1)


def test_negative_verdict_keeps_short_stroke_and_does_not_consume_next_click():
    from ring_python_sdk.touchpad import TouchpadClick
    ready, written, tapped = (threading.Event() for _ in range(3))
    paths, taps = [], []
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: None, on_stroke=lambda p: (paths.append(p), written.set()),
        on_tap=lambda: (taps.append(True), tapped.set()))
    output.start(); assert ready.wait(1); output.activate()
    try:
        now = time.monotonic()
        batch = [TouchpadContact('down', 0, now), TouchpadContact('up', 10, now),
                 TouchpadContact('down', 14, now), TouchpadContact('up', 24, now)]
        batch += [TouchpadMove(.3, 0., .99, step, now) for step in range(27)]
        with output.lock: output.events.extend(batch)
        output.wake.set()
        assert not written.wait(.05)
        output.submit(TouchpadClickVerdict(0, 10, False, time.monotonic()))
        assert written.wait(1)
        output.submit(TouchpadClick(24, time.monotonic()))
        output.submit(TouchpadClickVerdict(14, 24, True, time.monotonic()))
        assert tapped.wait(1)
        assert len(paths) == 1 and len(taps) == 1
    finally:
        output.stop(); output.thread.join(1)


def test_missing_verdict_expires_without_turning_contact_into_stroke():
    ready, traced, written = (threading.Event() for _ in range(3))
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda *_: None, on_stroke=lambda p: written.set(), on_tap=lambda: None,
        on_trace=lambda p, finished: traced.set() if len(p) > 1 else None)
    output.start(); assert ready.wait(1); output.activate()
    try:
        now = time.monotonic()
        with output.lock:
            output.events.extend([TouchpadContact('down', 0, now), TouchpadContact('up', 10, now)] +
                [TouchpadMove(1., 0., .99, step, now) for step in range(14)])
        output.wake.set()
        assert not written.wait(.6)
        assert output.pending_strokes == {}
    finally:
        output.stop(); output.thread.join(1)
