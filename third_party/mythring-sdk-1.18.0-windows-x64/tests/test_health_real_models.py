"""Reproducible real-model smoke checks; no device or user health data is used."""
import sys
import unittest

import numpy as np

from mythring.health_inference import HealthDayModel
from mythring.health_models import HealthBedtime, HealthQuality
from mythring.health_sleep import HealthSleepModel


class RealHealthModelTests(unittest.TestCase):
    def test_original_rf42_and_rf12_forests(self):
        model = HealthDayModel()
        self.assertEqual(model.predict([0.0]*42), 0.)
        self.assertAlmostEqual(model.predict([0.5]*12), .44860743019731336, places=12)

    def test_original_sleep_graph_full_forward(self):
        start, end = 1700000100000, 1700001900000
        times = np.arange(start+30000, end+1, 30000, dtype=np.int64)
        beat_times = np.arange(start+1000, end, 1000, dtype=np.int64)
        motion = np.full((len(times), 6), .01, dtype=np.float32)
        ibi = np.column_stack((np.full(len(beat_times), 1000., np.float32),
                               np.full(len(beat_times), np.nan, np.float32),
                               np.ones(len(beat_times), np.float32)))
        temperature = np.full(len(times), 32., dtype=np.float32)
        rows = HealthSleepModel().run(start, end, motion, times, ibi, beat_times, temperature, times,
            [], [], [HealthBedtime(start, end)], True, lambda a, b: False)
        self.assertEqual(len(rows), 5)
        self.assertEqual([row.start_utc_ms for row in rows], list(range(start+300000, end, 300000)))
        for row in rows:
            self.assertEqual(row.quality, HealthQuality.VALID)
            self.assertFalse(row.estimated)
            self.assertEqual(len(row.probabilities), 4)
            self.assertAlmostEqual(sum(row.probabilities), 1., places=12)
            self.assertTrue(all(np.isfinite(value) and 0 <= value <= 1 for value in row.probabilities))

    @unittest.skipUnless(sys.platform in ("darwin", "win32"), "Requires the desktop native library")
    def test_native_motion_and_ppg_original_sources(self):
        from mythring import health_native
        xyz = np.tile([0, 0, 2048], (48, 1)).astype(np.int16)
        motion = health_native.motion_lowrate(xyz, 47*640)
        self.assertEqual(motion.shape, (8,))
        self.assertTrue(np.isfinite(motion).all())
        self.assertEqual(motion[-1], 0.)
        times = np.arange(375, dtype=np.int64)*80+1700000100000
        raw = (100000+10000*np.sin(np.arange(375)*2*np.pi/12.5)).astype(np.int32)
        output = health_native.ppg_lowrate(raw, times, 0.)
        self.assertEqual(output[1], 12.5)
        beats = output[18:].reshape(-1, 3)
        self.assertEqual(len(beats), 29)
        self.assertTrue((np.diff(beats[:, 0]) > 0).all())
        self.assertGreater(beats[0, 0], times[0])
        self.assertLess(beats[-1, 0], times[-1])


if __name__ == "__main__":
    unittest.main()
