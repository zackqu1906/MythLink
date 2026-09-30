"""Synchronous engine for recorded packets and a single inference worker.

feed() and poll() must run on one thread. This module never controls a mouse.
"""
from dataclasses import replace
from pathlib import Path
import time
from .core import Postprocessor, parse_tokens
from .events import TouchpadMove, TouchpadClick, TouchpadStats
from .model import TouchpadBackbone


class TouchpadProcessor:
    def __init__(self, model_path: Path | None = None, *, backbone=None):
        self.backbone = backbone if backbone is not None else TouchpadBackbone(model_path)
        self.post = Postprocessor()
        self.stats = TouchpadStats()
        self.last_valid = None
        self.sequence = self.uptime = self.arrival = None

    def reset(self):
        self.backbone.reset()
        self.post.reset()
        self.sequence = self.uptime = self.arrival = None
        self.stats = replace(self.stats, resets=self.stats.resets+1, warmup_frames=0)

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
            if not reboot and not gap and (delta == 0 or delta >= 32768):
                self.stats = replace(self.stats, duplicate_packets=self.stats.duplicate_packets+1)
                return
            if delta != 1 or reboot or gap:
                self.reset()
        self.sequence, self.uptime, self.arrival = seq, uptime, arrival
        for index, token in enumerate(tokens):
            self.post.add(self.backbone.step(token), arrival, arrival-(len(tokens)-1-index)*.005)
        self.last_valid = now
        self.stats = replace(self.stats, packets=self.stats.packets+1, tokens=self.stats.tokens+len(tokens))

    def poll(self, now: float | None = None):
        now = time.monotonic() if now is None else now
        if self.arrival is not None and now-self.arrival > .25:
            self.reset()
        events = []
        for event in self.post.drain(now):
            if event['kind'] == 'move':
                events.append(TouchpadMove(event['dx'], event['dy'], event['contact'], event['step'], now))
            else:
                events.append(TouchpadClick(event['step'], now))
        self.stats = replace(self.stats, warmup_frames=min(self.post.n, 200),
                             contact_probability=self.post.contact,
                             last_data_age_s=None if self.last_valid is None else now-self.last_valid)
        return events
