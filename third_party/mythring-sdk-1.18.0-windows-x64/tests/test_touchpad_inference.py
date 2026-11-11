import asyncio
from dataclasses import replace
import importlib.util
import json
import math
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest

from mythring.touchpad import TouchpadToken
from mythring.touchpad_inference import (
    Button, Click, ContactPhase, ContactUpdate, EpisodeMode, InteractionEngine,
    Motion, MotionMode, Raw2Model, STATE_WIDTHS, TouchpadInference,
    TouchpadBacklogWarning, TouchpadRecognizer, _ClickPairing, _ContactTimes, motion_delta,
)


class GoldenSink:
    def __init__(self):
        self.events = []

    def motion(self, frame, mode, episode, source, dx, dy, now):
        self.events.append(["M", frame, mode.name, episode, source, dx, dy])

    def contact(self, episode, phase, mode, contact, source, now):
        self.events.append(["C", episode, phase.name, mode.name, contact.start_frame,
                            contact.start_time_ns, contact.end_frame_exclusive, contact.end_time_ns, source])

    def click(self, click_id, button, contacts, now):
        self.events.append(["K", click_id, button.name, len(contacts), contacts[0].up_time_ns, False])

    def preview(self, click_id, contact, now):
        self.events.append(["K", click_id, "LEFT", 1, contact.up_time_ns, True])


class InteractionTests(unittest.TestCase):
    def test_matches_unmodified_android_java_golden_traces(self):
        data = json.loads((Path(__file__).parent / "fixtures/touchpad_android_golden.json").read_text())
        for case in data["cases"]:
            with self.subTest(case=case["name"]):
                sink = GoldenSink()
                ids = iter(range(1, 1000))
                engine = InteractionEngine(sink, lambda: next(ids))
                frames = [(x, y, touch, finger) for count, touch, finger, x, y in case["segments"] for _ in range(count)]
                for index in range(0, len(frames), 2):
                    arrival = 1_000_000_000 + index // 2 * 10_000_000
                    now = arrival + case["delay_ns"]
                    # Java model predictions are float32. Round Python fixture inputs accordingly.
                    import struct
                    heads = struct.unpack("8f", struct.pack("8f", *frames[index], *frames[index + 1]))
                    engine.publish(index + 1, arrival, heads, now)
                    engine.round(lambda: now)
                now += 1_200_000_000
                engine.round(lambda: now)
                self.assertEqual(len(sink.events), len(case["expected"]))
                for actual, expected in zip(sink.events, case["expected"]):
                    self.assertEqual(len(actual), len(expected))
                    for left, right in zip(actual, expected):
                        if isinstance(right, float):
                            self.assertAlmostEqual(left, right, places=12)
                        else:
                            self.assertEqual(left, right)

    def test_motion_curve_deadzone_knots_and_finite_values(self):
        self.assertEqual(motion_delta(.01, 0), (0.0, 0.0))
        self.assertAlmostEqual(motion_delta(.1, 0)[0], .151875)
        self.assertAlmostEqual(motion_delta(.2, 0)[0], .33)
        self.assertAlmostEqual(motion_delta(4, 0)[0], 33.3)
        self.assertEqual(motion_delta(-.2, 0), (-.33, 0))
        for value in (math.inf, math.nan):
            with self.assertRaises(ValueError):
                motion_delta(value, 0)

    def test_publication_is_atomic_and_bounded(self):
        sink = GoldenSink()
        engine = InteractionEngine(sink, lambda: 1)
        values = (0, 0, -8, -8) * 2
        engine.publish(1, 0, values, 0)
        with self.assertRaises(ValueError):
            engine.publish(4, 0, values, 0)
        self.assertEqual(engine.published, 1)
        self.assertEqual(len(engine.recognition), 2)
        for index in range(1, 300):
            engine.publish(index * 2 + 1, 0, values, 0)
        with self.assertRaises(ValueError):
            engine.publish(601, 0, values, 0)
        self.assertEqual(len(engine.recognition), 600)
        engine.clear()
        engine.publish(1, 0, values, 0)
        self.assertEqual(len(engine.recognition), 2)

    def test_round_processes_at_most_two_frames_and_respects_validity(self):
        sink = GoldenSink()
        engine = InteractionEngine(sink, lambda: 1)
        for index in range(3):
            engine.publish(index * 2 + 1, 0, (.1, 0, -8, -8) * 2, 0)
        engine.round(lambda: 0, lambda: False)
        self.assertEqual(len(engine.recognition), 6)
        engine.round(lambda: 0)
        self.assertEqual(len(engine.recognition), 4)
        self.assertEqual(engine.timing.round_frames, 2)

    def test_late_down_cannot_extend_double_click_window(self):
        sink = GoldenSink()
        ids = iter(range(1, 10))
        pairing = _ClickPairing(sink, lambda: next(ids))
        first = _ContactTimes(1_000_000_000, 1_050_000_000, 0, 50_000_000)
        pairing.candidate(Button.LEFT, first, 1_050_000_000)
        pairing.down(100_000_000, 1_300_000_001)
        self.assertEqual([row[3] for row in sink.events], [1, 1])
        self.assertTrue(sink.events[0][-1])
        self.assertFalse(sink.events[1][-1])
        self.assertIsNone(pairing.first)

    def test_double_click_wait_uses_host_and_logical_clocks_separately(self):
        sink = GoldenSink()
        ids = iter(range(1, 10))
        pairing = _ClickPairing(sink, lambda: next(ids))
        contact = _ContactTimes(10_000_000_000, 10_050_000_000, 0, 50_000_000)
        pairing.candidate(Button.LEFT, contact, 10_060_000_000)
        pairing.down(100_000_000, 10_100_000_000)
        self.assertEqual(pairing.next(10_100_000_000), 11_060_000_000)
        pairing.progress(300_000_001, 10_400_000_000)
        self.assertIsNone(pairing.first)
        self.assertEqual(sink.events[-1], ["K", 1, "LEFT", 1, 10_050_000_000, False])


@unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy is required for ONNX tensor contract tests")
class Raw2ModelTests(unittest.TestCase):
    def test_bundled_model_first_prediction(self):
        # The x64 and arm64 CPU providers differ by ~0.001 on the contact/finger
        # logits for this reset input. Keep the tighter check on coordinates.
        prediction = Raw2Model().step([0.] * 8)
        expected = (.049265, .121755, 1.016825, -4.135263,
                    .046626, .117180, 1.155314, -4.161733)
        tolerances = (1e-4, 1e-4, 2e-3, 2e-3) * 2
        for value, reference, tolerance in zip(prediction, expected, tolerances, strict=True):
            self.assertAlmostEqual(value, reference, delta=tolerance)

    def make_session(self):
        import numpy as np

        class Session:
            def __init__(self):
                self.calls = []
                self.invalid = False

            def get_inputs(self):
                return [SimpleNamespace(name=name, type="tensor(float)", shape=[1, width])
                        for name, width in [("features", 8), *[(f"h{i}", w) for i, w in enumerate(STATE_WIDTHS)]]]

            def get_outputs(self):
                return [SimpleNamespace(name=name, type="tensor(float)", shape=[1, width])
                        for name, width in zip(Raw2Model.output_names, (4, 2, 2, *STATE_WIDTHS))]

            def run(self, names, feed):
                self.calls.append({name: value.copy() for name, value in feed.items()})
                results = [np.array([[1, 2, 3, 4]], dtype=np.float32),
                           np.array([[5, 6]], dtype=np.float32), np.array([[7, 8]], dtype=np.float32)]
                results.extend(feed[f"h{i}"] + np.float32(i + 1) for i in range(7))
                if self.invalid:
                    results[-1][0, 0] = np.nan
                return results

        return Session()

    def test_exact_head_mapping_state_progression_and_warmup_reset(self):
        session = self.make_session()
        model = Raw2Model(session=session, warmup_steps=2)
        self.assertTrue(all(value == 0 for value in model.backend_state()))
        self.assertEqual(model.step([0] * 8), (1, 2, 5, 7, 3, 4, 6, 8))
        model.step([0] * 8)
        self.assertEqual(session.calls[-1]["h0"][0, 0], 1)
        self.assertEqual(session.calls[-1]["h6"][0, 0], 7)
        self.assertEqual(len(model.backend_state()), 1216)
        model.reset()
        self.assertTrue(all(value == 0 for value in model.backend_state()))

    def test_rejects_wrong_output_order_or_shape(self):
        session = self.make_session()
        outputs = session.get_outputs()
        session.get_outputs = lambda: list(reversed(outputs))
        with self.assertRaisesRegex(ValueError, "contract"):
            Raw2Model(session=session)
        session = self.make_session()
        inputs = session.get_inputs()
        inputs[1].shape = [1, 64]
        session.get_inputs = lambda: inputs
        with self.assertRaisesRegex(ValueError, "h0"):
            Raw2Model(session=session)

    def test_bad_features_or_outputs_do_not_advance_state(self):
        session = self.make_session()
        model = Raw2Model(session=session)
        for values in ([0] * 7, [math.nan] + [0] * 7):
            with self.assertRaises(ValueError):
                model.step(values)
        session.invalid = True
        with self.assertRaisesRegex(ValueError, "finiteness"):
            model.step([0] * 8)
        self.assertTrue(all(value == 0 for value in model.backend_state()))


class ConstantModel:
    def __init__(self, prediction=(.1, 0, -8, -8) * 2):
        self.prediction = prediction
        self.resets = 0

    def reset(self):
        self.resets += 1

    def step(self, features):
        return self.prediction


def token(sequence=0, reset=True):
    return TouchpadToken(1, 1, sequence, sequence * 10, reset, (0,) * 8)


class InferenceTests(unittest.TestCase):
    def test_two_heads_share_packet_ingress_and_reset_generation(self):
        model = ConstantModel()
        inference = TouchpadInference(model, clock=lambda: 1_000_000_000, session_id="fixture")
        events = inference.process((token(), token(1, False)), 1_000_000_000)
        self.assertEqual([event.frame_id for event in events], [0, 1, 2, 3])
        self.assertTrue(all(isinstance(event, Motion) and event.mode == MotionMode.SINGLE for event in events))
        self.assertEqual([event.meta.source_time_ns for event in events], [1_000_000_000] * 4)
        self.assertEqual([event.meta.sequence for event in events], [1, 2, 3, 4])
        reset = inference.process((replace(token(2), epoch=2),), 1_000_000_000)
        self.assertEqual([event.frame_id for event in reset], [0, 1])
        self.assertTrue(all(event.meta.interaction_generation == 2 for event in reset))
        self.assertEqual(model.resets, 2)

    def test_backlog_preserves_model_inputs_and_recovers_without_reset(self):
        inputs = []

        class Model(ConstantModel):
            def step(self, features):
                inputs.append(features)
                return super().step(features)

        model = Model()
        inference = TouchpadInference(model, clock=lambda: 1_000_000_000)
        tokens = tuple(replace(token(i, i == 0), features=(i,) * 8) for i in range(4))
        self.assertEqual(inference.process(tokens[:2], 799_999_999), ())
        self.assertIsInstance(inference.take_warning(), TouchpadBacklogWarning)
        self.assertEqual(inference.process(tokens[2:3], 799_999_999), ())
        self.assertIsNone(inference.take_warning())
        events = inference.process(tokens[3:], 1_000_000_000)
        self.assertEqual([event.frame_id for event in events], [6, 7])
        self.assertTrue(all(event.meta.interaction_generation == 2 for event in events))
        self.assertEqual(inputs, [item.features for item in tokens])
        self.assertEqual(model.resets, 1)
        self.assertEqual(inference.ordinal, 4)
        inference.process((token(4, False),), 799_999_999)
        self.assertIsInstance(inference.take_warning(), TouchpadBacklogWarning)
        self.assertEqual(inference.generation, 3)

    def test_slow_step_discards_earlier_events_in_the_same_batch(self):
        now, calls = [1_000_000_000], []

        class Model(ConstantModel):
            def step(self, features):
                calls.append(features)
                if len(calls) == 2:
                    now[0] += 201_000_000
                return super().step(features)

        inference = TouchpadInference(Model(), clock=lambda: now[0])
        self.assertEqual(inference.process((token(), token(1, False)), now[0]), ())
        self.assertEqual(inference.ordinal, 2)
        self.assertIsInstance(inference.take_warning(), TouchpadBacklogWarning)
        events = inference.process((token(2, False),), now[0])
        self.assertEqual([event.frame_id for event in events], [4, 5])

    def test_backlog_cancels_pending_click_but_normal_click_timer_still_fires(self):
        for delayed in (False, True):
            with self.subTest(delayed=delayed):
                now = [1_000_000_000]
                inference = TouchpadInference(ConstantModel(), clock=lambda: now[0])
                contact = _ContactTimes(now[0] - 50_000_000, now[0], 0, 50_000_000)
                inference.interaction.clicks.candidate(Button.LEFT, contact, now[0])
                self.assertTrue(inference._drain()[0].is_preview)
                now[0] += 300_000_000
                events = inference.round(1_000_000_000 if delayed else now[0])
                if delayed:
                    self.assertEqual(events, ())
                    self.assertIsInstance(inference.take_warning(), TouchpadBacklogWarning)
                    self.assertEqual(inference.round(now[0]), ())
                else:
                    self.assertEqual(len(events), 1)
                    self.assertFalse(events[0].is_preview)

    def test_future_arrival_clock_still_fails_before_model_call(self):
        model = ConstantModel()
        inference = TouchpadInference(model, clock=lambda: 1_000_000_000)
        with self.assertRaisesRegex(RuntimeError, "clock is invalid"):
            inference.process((token(),), 1_000_000_001)
        self.assertEqual(inference.ordinal, 0)
        self.assertEqual(model.resets, 0)


class RecognizerTests(unittest.IsolatedAsyncioTestCase):
    async def test_warning_runs_on_loop_and_fresh_input_continues(self):
        events, warnings, errors = [], [], []
        warned, resumed = asyncio.Event(), asyncio.Event()
        main_thread = threading.get_ident()

        def on_warning(warning):
            self.assertEqual(threading.get_ident(), main_thread)
            warnings.append(warning)
            warned.set()

        def on_event(event):
            events.append(event)
            resumed.set()

        recognizer = TouchpadRecognizer(on_event, errors.append, on_warning=on_warning,
                                        model_factory=ConstantModel)
        await recognizer.start()
        self.addAsyncCleanup(recognizer.stop)
        recognizer.submit((token(),), time.monotonic_ns() - 250_000_000)
        await asyncio.wait_for(warned.wait(), 1)
        self.assertTrue(recognizer.running)
        self.assertEqual(events, [])
        self.assertEqual(errors, [])
        recognizer.submit((token(1, False),), time.monotonic_ns())
        await asyncio.wait_for(resumed.wait(), 1)
        self.assertEqual([event.frame_id for event in events], [2, 3])
        self.assertEqual(len(warnings), 1)

    async def test_slow_callback_discards_remaining_events_and_recovers(self):
        events, warnings, errors = [], [], []
        offset = [0]
        warned, resumed = asyncio.Event(), asyncio.Event()

        def on_event(event):
            events.append(event)
            if len(events) == 1:
                offset[0] = 250_000_000
            else:
                resumed.set()

        def on_warning(warning):
            warnings.append(warning)
            warned.set()

        recognizer = TouchpadRecognizer(on_event, errors.append, on_warning=on_warning,
                                        model_factory=ConstantModel)
        await recognizer.start()
        self.addAsyncCleanup(recognizer.stop)
        recognizer._engine.clock = lambda: time.monotonic_ns() + offset[0]
        recognizer.submit((token(), token(1, False)), time.monotonic_ns())
        await asyncio.wait_for(warned.wait(), 1)
        self.assertEqual(len(events), 1)
        self.assertTrue(recognizer.running)
        offset[0] = 0
        recognizer.submit((token(2, False),), time.monotonic_ns())
        await asyncio.wait_for(resumed.wait(), 1)
        self.assertEqual([event.frame_id for event in events], [0, 4, 5])
        self.assertEqual(events[-1].meta.interaction_generation, 2)
        self.assertEqual(errors, [])

    async def test_stop_during_model_call_suppresses_warning(self):
        begun, release = threading.Event(), threading.Event()
        warnings, events = [], []

        class Model(ConstantModel):
            def step(self, features):
                begun.set()
                release.wait(1)
                return super().step(features)

        recognizer = TouchpadRecognizer(events.append, on_warning=warnings.append, model_factory=Model)
        await recognizer.start()
        recognizer.submit((token(),), time.monotonic_ns() - 250_000_000)
        try:
            await asyncio.to_thread(begun.wait, 1)
            await recognizer.stop()
        finally:
            release.set()
        await recognizer._inflight
        self.assertEqual(warnings, [])
        self.assertEqual(events, [])

    async def test_model_runs_off_loop_and_stop_discards_queued_events(self):
        events, seen = [], asyncio.Event()
        main_thread = threading.get_ident()

        class Model(ConstantModel):
            def step(self, features):
                if threading.get_ident() == main_thread:
                    raise AssertionError("Native model blocked the asyncio thread")
                return super().step(features)

        def on_event(event):
            self.assertEqual(threading.get_ident(), main_thread)
            events.append(event)
            seen.set()

        recognizer = TouchpadRecognizer(on_event, model_factory=Model)
        await recognizer.start()
        recognizer.submit((token(),), time.monotonic_ns())
        await asyncio.wait_for(seen.wait(), 1)
        await recognizer.stop()
        count = len(events)
        recognizer.submit((token(1, False),), time.monotonic_ns())
        await asyncio.sleep(.02)
        self.assertEqual(len(events), count)
        self.assertFalse(recognizer.running)

    async def test_failure_is_reported_once_and_stops_recognition(self):
        errors, failed = [], asyncio.Event()

        class Model(ConstantModel):
            def step(self, features):
                raise ValueError("bad output")

        def on_error(error):
            errors.append(error)
            failed.set()

        recognizer = TouchpadRecognizer(lambda event: None, on_error, model_factory=Model)
        await recognizer.start()
        recognizer.submit((token(),), time.monotonic_ns())
        await asyncio.wait_for(failed.wait(), 1)
        await recognizer.stop()
        self.assertEqual(len(errors), 1)
        self.assertIn("bad output", str(errors[0]))

    async def test_queue_capacity_and_loading_generation(self):
        errors = []
        recognizer = TouchpadRecognizer(lambda event: None, errors.append, model_factory=ConstantModel)
        await recognizer.start()
        for index in range(65):
            recognizer.submit((token(index, index == 0),), time.monotonic_ns())
        self.assertEqual(len(errors), 1)
        self.assertIn("queue", str(errors[0]))
        await recognizer.stop()

        gate, begun = threading.Event(), threading.Event()

        def delayed_model():
            begun.set()
            gate.wait(1)
            return ConstantModel()

        recognizer = TouchpadRecognizer(lambda event: None, model_factory=delayed_model)
        task = asyncio.create_task(recognizer.start())
        await asyncio.to_thread(begun.wait, 1)
        recognizer.invalidate()
        gate.set()
        with self.assertRaises(ConnectionError):
            await task
        self.assertFalse(recognizer.running)


if __name__ == "__main__":
    unittest.main()
