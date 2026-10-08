"""Synchronous engine for recorded packets and a single inference worker.

feed() and poll() must run on one thread. This module never controls a mouse.
"""
from dataclasses import replace
from pathlib import Path
import time
from .core import Postprocessor, parse_tokens
from .events import TouchpadMove, TouchpadClick, TouchpadContact, TouchpadClickVerdict, TouchpadStats
from .model import TouchpadBackbone


class TouchpadProcessor:
    def __init__(self, model_path: Path | None = None, *, backbone=None):
        self.backbone = backbone if backbone is not None else TouchpadBackbone(model_path)
        self.post = Postprocessor()
        self.stats = TouchpadStats()
        self.last_valid = None
        self.sequence = self.uptime = self.arrival = None
        self.contact_reset_pending = False

    def reset(self):
        self.backbone.reset()
        self.post.reset()
        self.sequence = self.uptime = self.arrival = None
        self.stats = replace(self.stats, resets=self.stats.resets+1, warmup_frames=0)
        self.contact_reset_pending = True

    def feed(self, packet: bytes, *, arrival: float | None = None, now: float | None = None):
        now = time.monotonic() if now is None else now
        arrival = now if arrival is None else arrival
        if packet[:2] != b'\x21\x05':
            return
        if now-arrival > .25:
            self.stats = replace(self.stats, stale_packets=self.stats.stale_packets+1)
            self.reset()
            return
        try:
            seq, uptime, tokens = parse_tokens(packet)
        except ValueError:
            self.stats = replace(self.stats, invalid_packets=self.stats.invalid_packets+1)
            self.reset()
            return
        if self.sequence is not None:
            delta = (seq-self.sequence) & 65535
            reboot = uptime < self.uptime and self.uptime-uptime < 0x80000000
            gap = arrival-self.arrival > .25
            if not reboot and (delta == 0 or delta >= 32768):
                self.stats = replace(self.stats, duplicate_packets=self.stats.duplicate_packets+1)
                return
            if gap:
                self.stats = replace(self.stats, arrival_gaps=self.stats.arrival_gaps+1)
            # Windows can deliver a complete, ordered stream after a >250 ms
            # notification pause. Preserve the recurrent state when the packet
            # sequence proves no tokens were lost.
            if delta != 1 or reboot:
                if reboot:
                    self.stats = replace(self.stats, reboots=self.stats.reboots+1)
                elif delta != 1:
                    self.stats = replace(self.stats, sequence_gaps=self.stats.sequence_gaps+1,
                                         missing_packets=self.stats.missing_packets+max(0, delta-1))
                self.reset()
        self.sequence, self.uptime, self.arrival = seq, uptime, arrival
        for index, token in enumerate(tokens):
            self.post.add(self.backbone.step(token), arrival, arrival-(len(tokens)-1-index)*.005)
        self.last_valid = now
        self.stats = replace(self.stats, packets=self.stats.packets+1, tokens=self.stats.tokens+len(tokens))

    def poll(self, now: float | None = None):
        now = time.monotonic() if now is None else now
        if self.arrival is not None and now-self.arrival > 2.:
            self.stats = replace(self.stats, idle_resets=self.stats.idle_resets+1)
            self.reset()
        events = []
        if self.contact_reset_pending:
            events.append(TouchpadContact('reset', 0, now))
            self.contact_reset_pending = False
        for event in self.post.drain(now):
            if event['kind'] == 'move':
                events.append(TouchpadMove(event['dx'], event['dy'], event['contact'], event['step'], now))
            elif event['kind'] == 'contact':
                events.append(TouchpadContact(event['state'], event['step'], now,
                                              event['confirmed_step']))
            elif event['kind'] == 'click_verdict':
                events.append(TouchpadClickVerdict(event['start_step'], event['step'], event['is_click'], now))
            else:
                events.append(TouchpadClick(event['step'], now))
        self.stats = replace(self.stats, warmup_frames=min(self.post.n, 200),
                             contact_probability=self.post.contact,
                             last_data_age_s=None if self.last_valid is None else now-self.last_valid)
        return events
