"""Diagnostic traces preserve detector output, including misses and resets."""
from dataclasses import asdict
import json
import math
from pathlib import Path
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from ring_python_sdk.touchpad import TouchpadProcessor, TouchpadContact, TouchpadMove
from proximic_ring.touchpad.stroke.stroke_input import StrokeCollector


@pytest.fixture
def diagnostics_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    import stroke_contact_diagnostics
    return stroke_contact_diagnostics


class Backbone:
    def __init__(self):
        self.reset()

    def reset(self):
        self.n = 0

    def step(self, token):
        self.n += 1
        def probability(frame):
            # A short contact followed by slow release, then stable low values.
            if 225 <= frame < 231:
                return .99
            if 250 <= frame < 280:
                return .99
            if 280 <= frame < 310:
                return .99-.98*(frame-279)/30
            return .01
        return np.array([[.4, .1, math.log(p/(1-p))]
                         for p in [probability(self.n+j-2) for j in range(5)]])


def replay(processors, *, base=100.):
    results = [[] for _ in processors]
    for seq in range(38):
        packet = b'\x21\x05'+struct.pack('<HBIB', seq, 10, seq*50, 1)+struct.pack('<60e', *([0.]*60))
        now = base+seq*.05
        for index, processor in enumerate(processors):
            processor.feed(packet, arrival=now, now=now)
            results[index].extend(asdict(e) for e in processor.poll(now))
    return results


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_observer_preserves_events_and_captures_unended_contact_after_reset(tmp_path, diagnostics_module):
    log = diagnostics_module.ContactDiagnostics(tmp_path/'contact.jsonl', tmp_path/'main.jsonl')
    baseline, observed = [TouchpadProcessor(backbone=Backbone()) for _ in range(2)]
    log.instrument(observed)
    before, after = replay([baseline, observed])
    assert before == after
    assert observed.post.click.start is not None  # Diagnostic recording must not fix the simulated miss.
    for processor in (baseline, observed):
        processor.reset()
    before, after = replay([baseline, observed], base=200.)
    assert before == after
    log.close()
    saved = rows(log.path)
    for epoch in (0, 1):
        probabilities = [r for r in saved if r['kind']=='raw_contact_probability' and r['epoch']==epoch]
        assert len(probabilities) > 170
        assert len({r['step'] for r in probabilities}) == len(probabilities)
        assert probabilities[-1]['probability'] == pytest.approx(.01)
        assert any(r['probability'] == pytest.approx(.99) for r in probabilities)
        assert any(r['kind']=='raw_velocity' and r['epoch']==epoch for r in saved)
        assert any(r['kind']=='sdk_click_verdict' and r['epoch']==epoch for r in saved)
    assert any(r['kind']=='processor_reset' for r in saved)
    assert saved[-1]['kind']=='session_end' and saved[-1]['dropped_records']==0
    # Every frame contains its own probability, rather than a copied batch value.
    values = {r['step']:r['probability'] for r in saved if r['kind']=='raw_contact_probability' and r['epoch']==0}
    assert values[280] != values[281]


def test_packet_and_short_contact_rejections_are_retained(tmp_path, diagnostics_module):
    log = diagnostics_module.ContactDiagnostics(tmp_path/'contact.jsonl', tmp_path/'main.jsonl')
    packets = []
    stream = SimpleNamespace(submit=packets.append)
    log.observe_packets(stream)
    stream.submit(b'\x21\x05\x00')
    collector = StrokeCollector()
    log.observe_collector(collector)
    for start, count, dx in [(0, 3, 1.), (10, 8, .01), (30, 8, 1.)]:
        collector.reset()
        collector.feed(TouchpadContact('down', start, 0.))
        for step in range(start, start+count):
            collector.feed(TouchpadMove(dx, 0., .99, step, 0.))
        collector.feed(TouchpadContact('up', start+count, 0.))
    collector.reset()
    collector.feed(TouchpadContact('down', 50, 0.))
    collector.feed(TouchpadMove(1., 0., .99, 50, 0.))
    collector.feed(TouchpadContact('down', 60, 0.))
    collector.feed(TouchpadMove(1., 0., .99, 60, 0.))
    log.close()
    saved = rows(log.path)
    assert packets == [b'\x21\x05\x00']
    assert next(r for r in saved if r['kind']=='token_packet')['hex']=='210500'
    assert [r['reason'] for r in saved if r['kind']=='collector_result'] == [
        'too-few-points', 'too-short-path', 'accepted']
    assert [r['reason'] for r in saved if r['kind']=='collector_discard'] == [
        'replaced-by-next-contact', 'movement-gap']


def test_complete_trial_markers_pair_with_raw_log_without_resetting_input(tmp_path, monkeypatch, diagnostics_module):
    from test_complete_stroke_input import load_window
    app, window = load_window(tmp_path, monkeypatch)
    log = diagnostics_module.ContactDiagnostics(tmp_path/'contact.jsonl', window.log_path)
    try:
        window.add_manual('h')
        before = window.code, list(window.candidates), window.editor.text()
        window.contact_diagnostics = log
        window.worker = SimpleNamespace()
        window.diagnostic_task.setCurrentText('快速三横')
        window.diagnostic_note.setText('漏了第2笔')
        window.mark_diagnostic('start')
        window.mark_diagnostic('missing-stroke')
        assert (window.code, window.candidates, window.editor.text()) == before
        assert window.diagnostic_group is None
        window.record('contact_intent', start_step=1, end_step=8, intent_decision={'action':'stroke'})
        log.close()
        saved = rows(log.path)
        start = next(r for r in saved if r['kind']=='trial_start')
        end = next(r for r in saved if r['kind']=='annotation')
        assert start['group_id'] == end['group_id']
        assert end['task']=='快速三横' and end['note']=='漏了第2笔'
        assert end['timing']=='human-retrospective-marker-not-physical-edge'
        assert any(r['kind']=='application_event' and r['event_kind']=='contact_intent' for r in saved)
        main = rows(window.log_path)
        assert len([r for r in main if r['kind']=='contact_diagnostic_marker'])==2
        assert all('monotonic' in r for r in main)
        window.show(); app.processEvents()
        window.statistics_scroll.ensureWidgetVisible(window.diagnostic_buttons[-1])
        app.processEvents()
        assert window.grab().save(str(tmp_path/'contact-diagnostics.png'))
    finally:
        window.worker = None
        log.close()
        window.close(); app.processEvents()
