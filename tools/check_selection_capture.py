"""Regression checks for real SDK contact timing and collection persistence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from selection_capture import ContactCapture

TEST_ROOT = Path(__file__).resolve().parents[1]


def move(step, dx=1., dy=0.):
    return dict(kind="move", step=step, dx=dx, dy=dy, contact_probability=.9, timestamp=step*.005)


def contact(state, step, confirmed=None):
    return dict(kind="contact", state=state, step=step, timestamp=(confirmed or step)*.005, confirmed_step=confirmed)


def trace():
    collector = ContactCapture()
    collector.feed(contact("down", 10))
    for i in range(10, 20):
        collector.feed(move(i, 0 if i < 15 else 2, -2 if i < 15 else 0))
    return collector.feed(contact("up", 20, 23))


class ContactTests(unittest.TestCase):
    def test_reversals_keep_signed_end_position_and_all_frames(self):
        for sign in (1, -1):
            for horizontal, expected in (([2, 2, 2, -2, -2], 2),
                                         ([2, 2, 2, -2, -2, -2, -2, -2], -4),
                                         ([2, 2, 2, -2, -2, 2], 4)):
                with self.subTest(sign=sign, horizontal=horizontal):
                    capture = ContactCapture()
                    capture.feed(contact("down", 1))
                    movements = [(0, 0)] + [(0, -2)] * 3 + [(sign*dx, 0) for dx in horizontal]
                    for step, (dx, dy) in enumerate(movements, 1):
                        capture.feed(move(step, dx, dy))
                    result = capture.feed(contact("up", len(movements)+1))
                    self.assertEqual(result["points"][-1]["x"], sign*expected)
                    self.assertEqual(result["points"][-1]["y"], -6)
                    self.assertEqual(len(result["points"]), len(movements)+1)
                    saved_dx = [e["dx"] for e in result["rawEvents"] if e["kind"] == "move"]
                    self.assertEqual(saved_dx, [dx for dx, _ in movements])
                    self.assertGreater(result["pathLength"], abs(expected)+6)

    def test_delayed_edges_recover_start_and_trim_lift(self):
        capture = ContactCapture()
        for step in range(1, 7):
            capture.feed(move(step, 999 if step == 3 else 2, -1))
        capture.feed(contact("down", 3, 6))
        for step in range(7, 11):
            capture.feed(move(step, 100 if step >= 8 else 2, -1))
        result = capture.feed(contact("up", 8, 10))
        self.assertEqual(result["points"][-1]["x"], 8.)
        self.assertEqual(result["points"][-1]["y"], -4.)
        self.assertEqual(result["durationMs"], 25)
        self.assertEqual([e["step"] for e in result["rawEvents"] if e["kind"] == "move"], list(range(3, 8)))

    def test_edges_before_moves_wait_for_all_frames(self):
        capture = ContactCapture()
        capture.feed(contact("down", 10))
        self.assertIsNone(capture.feed(contact("up", 15, 17)))
        for i in range(10, 14):
            self.assertIsNone(capture.feed(move(i)))
        result = capture.feed(move(14))
        self.assertEqual(result["points"][-1]["x"], 4.)
        self.assertIsNone(capture.feed(move(15)))

    def test_stationary_tap_is_retained(self):
        capture = ContactCapture()
        capture.feed(contact("down", 1))
        for i in range(1, 8):
            capture.feed(move(i, 0, 0))
        result = capture.feed(contact("up", 8))
        self.assertEqual(result["pathLength"], 0)
        self.assertTrue(result["quality"]["stationary"])

    def test_gap_and_reset_never_complete_partial_sample(self):
        for interruption in (move(4), contact("reset", 0)):
            capture = ContactCapture()
            capture.feed(contact("down", 1))
            capture.feed(move(1))
            capture.feed(move(2))
            self.assertIsInstance(capture.feed(interruption), str)
            self.assertIsNone(capture.feed(contact("up", 5)))

    def test_duplicate_frame_does_not_change_distance(self):
        capture = ContactCapture()
        capture.feed(contact("down", 1))
        capture.feed(move(1))
        capture.feed(move(2))
        capture.feed(move(2, 999))
        capture.feed(move(3))
        result = capture.feed(contact("up", 4))
        self.assertEqual(result["points"][-1]["x"], 2)

    def test_arrival_jitter_does_not_reverse_frame_time(self):
        capture = ContactCapture()
        capture.feed(contact("down", 1))
        for i in range(1, 7):
            capture.feed({**move(i), "timestamp": 100-i*.01})
        result = capture.feed(contact("up", 7))
        times = [p["t"] for p in result["points"]]
        self.assertEqual(times, sorted(times))
        self.assertGreater(result["points"][0]["arrivalTime"], result["points"][1]["arrivalTime"])

    def test_missing_start_and_nonfinite_are_rejected(self):
        capture = ContactCapture()
        capture.feed(contact("down", 1))
        capture.feed(move(2))
        self.assertIsInstance(capture.feed(contact("up", 3)), str)
        self.assertIsInstance(capture.feed(move(4, float("nan"))), str)



class WorkerTests(unittest.TestCase):
    def test_worker_disconnects_on_stream_failure(self):
        import asyncio
        import queue
        import sys
        from types import SimpleNamespace
        from ring_selection_collector import RingWorker
        class FakeSession:
            disconnected = False
            def __init__(self, *args):
                pass
            async def connect_target(self, device):
                return True
            async def touchpad_on(self, **kwargs):
                raise RuntimeError("model failed")
            async def disconnect(self):
                FakeSession.disconnected = True
        with patch.dict(sys.modules, {"ring_python_sdk": SimpleNamespace(RingSession=FakeSession)}):
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                asyncio.run(RingWorker("test", Path("."), queue.Queue()).acquire())
        self.assertTrue(FakeSession.disconnected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
