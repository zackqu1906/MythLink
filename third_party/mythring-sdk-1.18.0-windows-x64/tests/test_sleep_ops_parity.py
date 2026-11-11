"""Compare ported operators with the delivered ARM64 numerical instructions.

Only ATen allocation/conversion is bypassed: the bounds, timestamp arithmetic,
binary searches, plateau scans, and output writes run from sleep_native.so.
These tests need Unicorn's executable-memory permission on macOS.
"""
import struct
import unittest

import numpy as np
import torch
from unicorn import arm64_const as reg

from mythring.health_sleep_ops import _classifier, _indices, _peaks, _windows


class OriginalNumericalLoops:
    def __init__(self):
        self.runtime = _classifier()
        self.uc = self.runtime.uc
        # The numerical blocks have no function calls or stack allocations.
        self.input = self.runtime.stack + 0x1000
        self.second = self.runtime.stack + 0x5000
        self.output = self.runtime.stack + 0x9000
        self.ends = self.runtime.stack + 0x19000

    def registers(self, **values):
        for name, value in values.items():
            self.uc.reg_write(getattr(reg, "UC_ARM64_REG_" + name), int(value))

    def run(self, start, end):
        self.uc.emu_start(self.runtime.base + start, self.runtime.base + end,
                          timeout=2_000_000, count=2_000_000)
        if self.uc.reg_read(reg.UC_ARM64_REG_PC) != self.runtime.base + end:
            raise RuntimeError("Original numerical loop exceeded execution budget")

    def read(self, address, length):
        return np.frombuffer(self.uc.mem_read(address, length * 8), dtype="<i8").copy()

    def windows(self, values, seconds):
        values = np.asarray(values, dtype="<i8")
        with self.runtime.lock:
            self.uc.mem_write(self.input, values.tobytes())
            self.registers(CPACR_EL1=3 << 20, X20=self.input,
                           X25=self.input + values.nbytes,
                           D8=struct.unpack("<Q", struct.pack("<d", seconds))[0])
            # seconds -> integral milliseconds, then original origin/count.
            self.run(0x191E4, 0x191F8)
            self.run(0x1923C, 0x19278)
            count = self.uc.reg_read(reg.UC_ARM64_REG_X27)
            if not 1 <= count <= 2048:
                raise ValueError("Test buffer only accepts 1..2048 windows")
            self.registers(X0=self.output, X22=self.ends, X21=len(values))
            self.run(0x19320, 0x19490)
            return self.read(self.ends, count).reshape(-1, 1), self.read(self.output, count * 4).reshape(-1, 4)

    def accepts_window_count(self, count):
        with self.runtime.lock:
            self.registers(X27=count)
            # Stop immediately after the conditional branch, before either
            # ATen allocation or the source's exception construction.
            self.uc.emu_start(self.runtime.base + 0x19278,
                              self.runtime.base + 0x19290, count=6)
            return self.uc.reg_read(reg.UC_ARM64_REG_PC) == self.runtime.base + 0x19290

    def indices(self, values, new_values):
        values = np.asarray(values, dtype="<f8")
        new_values = np.asarray(new_values, dtype="<f8")
        with self.runtime.lock:
            self.uc.mem_write(self.input, values.tobytes())
            self.uc.mem_write(self.second, new_values.tobytes())
            self.registers(CPACR_EL1=3 << 20, X0=self.output,
                           X20=self.input, X21=self.second, X22=len(values),
                           X23=len(new_values), X25=self.input + values.nbytes)
            self.run(0x19D3C, 0x19DAC)
            return self.read(self.output, len(new_values))

    def peaks(self, values):
        values = np.asarray(values, dtype="<f8")
        if len(values) < 3:
            return np.array([], dtype=np.int64)
        with self.runtime.lock:
            self.uc.mem_write(self.input, values.tobytes())
            # Reserve the C++ vector capacity so its allocation branch is not
            # needed; writes and plateau midpoint selection are original code.
            self.registers(CPACR_EL1=3 << 20, X8=1,
                           X20=self.input, X23=len(values) - 1, X24=self.input + 8,
                           X25=self.output, X22=self.output,
                           X19=self.output + values.nbytes,
                           X29=self.runtime.stack + 0x1FFF0)
            self.run(0x1A280, 0x1A42C)
            count = (self.uc.reg_read(reg.UC_ARM64_REG_X22) - self.output) // 8
            return self.read(self.output, count)


class SleepOpsOriginalParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = OriginalNumericalLoops()

    def assert_windows(self, values, seconds):
        expected = self.original.windows(values, seconds)
        actual = _windows(torch.tensor(values, dtype=torch.int64).reshape(-1, 1), seconds)
        for output, source in zip(actual, expected):
            np.testing.assert_array_equal(output.numpy(), source)

    def test_windows_boundaries_duplicate_timestamps_and_missing_intervals(self):
        vectors = [
            [0], [29999], [30000], [0, 0, 1, 29999, 30000, 30000, 59999, 60000, 300000],
            [1780000012345, 1780000030000, 1780000030000, 1780000100000, 1780000612345],
            [-60001, -60000, -30001, -30000, -1, 0, 1, 30000, 300001],
            [-60001, -60000], [-60000], [-1],
        ]
        for values in vectors:
            with self.subTest(values=values):
                self.assert_windows(values, 30.0)

    def test_windows_fractional_seconds_and_random_sorted_inputs(self):
        rng = np.random.default_rng(20261104)
        for seconds in (0.001, 0.0019, 0.3333, 1.1257, 30., 47.9999, 60., 300.):
            period = int(seconds * 1000)
            for origin in (-1234567, 0, 1780000000000):
                values = np.sort(rng.integers(origin, origin + period * 25, size=120))
                with self.subTest(seconds=seconds, origin=origin):
                    self.assert_windows(values, seconds)

    def test_window_count_limit_matches_original_guard(self):
        for count, accepted in ((0, False), (1, True), (99999, True),
                                (100000, False), (100001, False)):
            with self.subTest(count=count):
                self.assertEqual(self.original.accepts_window_count(count), accepted)
        valid = torch.tensor([[0], [99998]], dtype=torch.int64)
        self.assertEqual(len(_windows(valid, .001)[0]), 99999)
        for final_timestamp in (99999, 100000):
            with self.assertRaises(ValueError):
                _windows(torch.tensor([[0], [final_timestamp]], dtype=torch.int64), .001)

    def test_indices_exact_hits_duplicate_values_and_outside_lower_endpoint(self):
        cases = [([0, 1], []), ([0, 1], [-2, 0, 0.1, 1]),
                 ([0, 1, 1, 3], [-1, 0, 1, 1.00000001, 2, 3]),
                 ([1, 1, 1, 1], [-1, 0, 1]),
                 ([-2, -1, -1, 0], [-3, -2, -1.5, -1, -0.5, 0])]
        for values, new_values in cases:
            with self.subTest(values=values, new_values=new_values):
                expected = self.original.indices(values, new_values)
                actual = _indices(torch.tensor(values, dtype=torch.float64),
                                  torch.tensor(new_values, dtype=torch.float64))
                np.testing.assert_array_equal(actual.numpy(), expected)

    def test_indices_random_sorted_vectors_preserve_float64_precision(self):
        rng = np.random.default_rng(20261105)
        for origin in (-1780000000000, 0, 1780000000000):
            for size in (2, 3, 16, 127, 257):
                values = origin + np.sort(rng.integers(0, 1000, size)) / 8.0
                new_values = np.sort(np.concatenate((values, values[:-1] + 0.003, [values[0] - 3])))
                new_values = new_values[new_values <= values[-1]]
                expected = self.original.indices(values, new_values)
                actual = _indices(torch.from_numpy(values), torch.from_numpy(new_values))
                np.testing.assert_array_equal(actual.numpy(), expected)

    def test_peaks_plateau_midpoint_edges_and_nonfinite_values(self):
        cases = [[], [1], [1, 2], [0, 1, 0], [0, 1, 1, 0],
                 [0, 1, 1, 1, 0], [0, 1, 1, 1], [1, 1, 1, 0],
                 [1, 1, 1, 1], [3, 2, 1, 2, 3], [0, 1, 0, 1, 0],
                 [0, np.inf, 0, -np.inf, 0],
                 [0, 1, np.nan, 2, 0, 1, 0],
                 [np.nan, 1, 0, 1, np.nan, 0, 1, 0]]
        for values in cases:
            with self.subTest(values=values):
                expected = self.original.peaks(values)
                actual = _peaks(torch.tensor(values, dtype=torch.float64))
                np.testing.assert_array_equal(actual.numpy(), expected)

    def test_peaks_random_plateaus_match_original_index_scan(self):
        rng = np.random.default_rng(20261106)
        for size in (3, 4, 15, 128, 1024):
            for _ in range(8):
                values = rng.integers(-3, 4, size).astype(np.float64)
                expected = self.original.peaks(values)
                actual = _peaks(torch.from_numpy(values))
                np.testing.assert_array_equal(actual.numpy(), expected)


if __name__ == "__main__":
    unittest.main()
