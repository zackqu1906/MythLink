from dataclasses import replace

from proximic_ring.touchpad_diagnostics import TouchpadDiagnostics, note
from ring_python_sdk.touchpad import TouchpadContact, TouchpadMove, TouchpadStats


def test_background_rates_use_sensor_callback_time_despite_delayed_gui():
    diagnostics = TouchpadDiagnostics()
    first = TouchpadStats(tokens=100, packets=10)
    assert diagnostics.report(first, {}, 10., foreground=False, visibility_epoch=0, now=12.) is None
    for step in range(400):
        diagnostics.observe(TouchpadMove(0, 0, 0, step, 10. + step / 200))
    second = replace(first, tokens=500, packets=50, warmup_frames=200)
    report = diagnostics.report(second, diagnostics.snapshot(), 12., foreground=False, visibility_epoch=0, now=17.)
    assert report['token_hz'] == report['move_hz'] == 200
    assert report['ui_queue_ms'] == 5000 and report['foreground'] is False
    assert not report['mixed_visibility']


def test_diagnostics_distinguish_motion_expiry_from_stream_reset_and_visibility_transition():
    diagnostics = TouchpadDiagnostics()
    stats = TouchpadStats(tokens=100)
    diagnostics.report(stats, {}, 10., foreground=True, visibility_epoch=1, now=10.)
    diagnostics.observe(TouchpadContact('reset', 250, 10.2))
    diagnostics.observe(TouchpadContact('reset', 0, 10.3))
    diagnostics.note('stroke_verdict_timeout')
    report = diagnostics.report(stats, diagnostics.snapshot(), 10.5, foreground=False, visibility_epoch=2, now=10.5)
    assert report['mixed_visibility']  # Do not attribute a boundary interval to the background.
    assert report['events'] == dict(sdk_contact_reset_expired_motion=1, sdk_contact_reset_stream=1, stroke_verdict_timeout=1)
    clean = diagnostics.report(replace(stats, tokens=500), diagnostics.snapshot(), 12.5,
                               foreground=False, visibility_epoch=2, now=12.5)
    assert not clean['mixed_visibility'] and clean['events'] == {}


def test_diagnostic_failure_cannot_change_stroke_or_click_delivery():
    def failing(reason):
        raise RuntimeError('log unavailable')
    note(failing, 'stroke_delivered')
    from proximic_ring.touchpad_clicks import TouchpadClicks
    from ring_python_sdk.touchpad import TouchpadClick
    clicks = TouchpadClicks(on_diagnostic=failing)
    clicks.feed(TouchpadClick(1, 10.), now=10.)
    clicks.feed(TouchpadClick(2, 9.), now=10.1)  # Rejected old event must leave the first click pending.
    assert clicks.feed(TouchpadClick(3, 10.2), now=10.2)[0].kind == 'double'


def test_batch_timing_distinguishes_thread_cpu_from_elapsed_time(monkeypatch):
    import asyncio
    import time
    from ring_python_sdk.touchpad import stream
    class Processor:
        def __init__(self, _):
            self.stats = TouchpadStats()
            self.last_valid = None
        def feed(self, packet, arrival):
            time.sleep(.03)  # Wall-clock delay without consuming CPU.
            self.last_valid = time.monotonic()
            self.stats = replace(self.stats, packets=1, tokens=10)
        def poll(self): return []
    monkeypatch.setattr(stream, 'TouchpadProcessor', Processor)
    async def run():
        sample = asyncio.get_running_loop().create_future()
        pipeline = stream.TouchpadStream(lambda _: None, duration_s=None,
            on_stats=lambda stats: sample.set_result(stats) if not sample.done() else None)
        try:
            await pipeline.prepare()
            pipeline.submit(b'test')
            pipeline.start()
            stats = await asyncio.wait_for(sample, 2)
            # Empty polls preserve timing for the last nonempty batch.
            assert stats.inference_batch_packets == 1
            assert stats.inference_batch_ms >= 25
            assert stats.inference_thread_cpu_ms < stats.inference_batch_ms / 2
        finally:
            await pipeline.close()
    asyncio.run(run())
