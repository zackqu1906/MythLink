"""Instance-only observation for the complete trial; never decides input."""
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import time
import uuid


class ContactDiagnostics:
    def __init__(self, path, session_log, *, synthetic=False):
        self.path = Path(path)
        self.stream_id = uuid.uuid4().hex
        self.epoch = 0
        self.dropped = 0
        self.error = None
        self.closed = False
        self.lock = threading.Lock()
        self.queue = queue.Queue(maxsize=20000)
        self.file = self.path.open('x', encoding='utf-8')
        self.thread = threading.Thread(target=self._write, name='ContactDiagnosticLog', daemon=True)
        self.thread.start()
        self.record('session', schema='stroke-contact-diagnostics-v1',
                    session_log=str(session_log), synthetic=synthetic, sample_hz=200,
                    probability_source='finalized-model-frame-before-contact-detector',
                    movement_probability='sdk-batch-snapshot-not-per-frame')

    def record(self, kind, **value):
        # No filesystem access or Qt signals on the inference path.
        with self.lock:
            if self.closed or self.error:
                return
            row = dict(kind=kind, mono=time.monotonic(), stream_id=self.stream_id,
                       epoch=self.epoch, **value)
            try:
                self.queue.put_nowait(row)
            except queue.Full:
                self.dropped += 1

    def _write(self):
        try:
            flushed = time.monotonic()
            while True:
                try:
                    row = self.queue.get(timeout=.25)
                except queue.Empty:
                    self.file.flush()
                    flushed = time.monotonic()
                    continue
                if row is None:
                    break
                self.file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                if row['kind'] in {'session', 'annotation', 'trial_start', 'session_end'} or time.monotonic()-flushed >= .25:
                    self.file.flush()
                    flushed = time.monotonic()
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.file.close()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        # Producers have stopped before close. Drain without dropping the footer.
        footer = dict(kind='session_end', mono=time.monotonic(), stream_id=self.stream_id,
                      epoch=self.epoch, dropped_records=self.dropped)
        for row in (footer, None):
            while self.thread.is_alive():
                try:
                    self.queue.put(row, timeout=.1)
                    break
                except queue.Full:
                    continue
        self.thread.join()

    def instrument(self, processor):
        """Run on the inference executor, before labeled input; survives reset."""
        original_reset, original_poll = processor.reset, processor.poll

        def observe_detector():
            detector = processor.post.click
            original_probability, original_movement = detector.probability, detector.movement
            original_ready = detector.ready
            original_detector_reset = detector.reset

            def detector_reset():
                self.record('detector_reset', start_step=detector.start,
                            last_probability_step=detector.last_prob, last_movement_step=detector.last_move)
                return original_detector_reset()

            def probability(index, p):
                self.record('raw_contact_probability', step=index, probability=float(p))
                return original_probability(index, p)

            def movement(index, velocity):
                self.record('raw_velocity', step=index, vx=float(velocity[0]), vy=float(velocity[1]))
                return original_movement(index, velocity)

            def ready():
                pending = list(detector.pending)
                result = original_ready()
                for start, end in pending[:len(pending)-len(detector.pending)]:
                    missing = any(k not in detector.raw for k in range(start, end))
                    is_click = any(item['step'] == end for item in result)
                    self.record('sdk_click_verdict', start_step=start, end_step=end,
                                duration_ms=(end-start)*5, is_click=is_click,
                                reason=('duration' if not 0 < end-start < 40 else
                                        'missing-frames' if missing else 'click' if is_click else 'movement'))
                return result

            detector.probability, detector.movement, detector.ready = probability, movement, ready
            detector.reset = detector_reset

        def reset():
            original_reset()
            self.epoch += 1
            observe_detector()
            self.record('processor_reset', stats=asdict(processor.stats))

        def poll(*args, **kwargs):
            result = original_poll(*args, **kwargs)
            if result:
                self.record('sdk_batch', events=[asdict(event) for event in result])
            return result

        observe_detector()
        processor.reset, processor.poll = reset, poll
        self.record('processor_attached', warmup_frames=processor.stats.warmup_frames)

    def observe_collector(self, collector):
        original = collector._finish_if_ready
        original_feed, original_reset = collector.feed, collector.reset

        def reset():
            self.record('collector_reset', start_step=collector.start_step,
                        end_step=collector.end_step, last_step=collector.last_step,
                        point_count=len(collector.points))
            return original_reset()

        def feed(event):
            start, last = collector.start_step, collector.last_step
            count = len(collector.points)
            result = original_feed(event)
            if start is not None:
                reason = None
                if event.kind == 'contact' and event.state == 'down':
                    reason = 'replaced-by-next-contact'
                elif event.kind == 'move' and collector.start_step is None and collector.end_step is None:
                    # Normal completion is already described by collector_result.
                    if last is not None and event.step != last+1:
                        reason = 'movement-gap'
                    elif count >= collector.max_points:
                        reason = 'max-points'
                if reason:
                    self.record('collector_discard', start_step=start, event_step=event.step,
                                point_count=count, reason=reason)
            return result

        def finish():
            ready = (collector.end_step is not None and collector.last_step is not None
                     and collector.last_step >= collector.end_step-1)
            detail = dict(start_step=collector.start_step, end_step=collector.end_step,
                          point_count=len(collector.points), path_length=collector.length,
                          min_points=5, min_length=collector.min_length)
            result = original()
            if ready:
                self.record('collector_result', **detail, accepted=result is not None,
                            reason=('accepted' if result is not None else
                                    'too-few-points' if detail['point_count'] < 5 else 'too-short-path'))
            return result

        collector._finish_if_ready = finish
        collector.feed, collector.reset = feed, reset

    def observe_packets(self, stream):
        original = stream.submit

        def submit(packet):
            self.record('token_packet', hex=bytes(packet).hex(), arrival=time.monotonic())
            return original(packet)

        stream.submit = submit


def marker_value(task, outcome, note, *, group_id=None, code='', focus_since=None):
    return dict(time=datetime.now().isoformat(), task=task, outcome=outcome,
                note=note, group_id=group_id, code=code, focus_since=focus_since,
                timing='human-retrospective-marker-not-physical-edge')
