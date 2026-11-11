import asyncio
import io
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from mythring.health import HealthClient, HealthError
from mythring.health_analysis import HealthFeatureSegment, analyze_features, analyze_raw, day_activity
from mythring.health_inference import GapwideHead, StepRfModel
from mythring.health_models import HealthActivity, HealthQuality, HealthRecording, HealthReport
from mythring.health_protocol import read_block
from mythring.health_sleep import BedtimeDetector, decode_stages, gap_features, stage_features
from mythring.health_store import HealthStore
from test_health import raw_record, capabilities, reply


def feature_session(start=100, boot=123, index=0, utc=1700000000000):
    p = bytearray(48)
    p[:4] = b"\x07\x04\x10\x01"
    struct.pack_into("<QIHHHHQIHB", p, 4, utc, start, 640, 80, 2048, 512, boot, index, 18, 1)
    struct.pack_into("<II", p, 40, 100, 1800000)
    return raw_record(1, p, uptime=start)


def feature_data():
    heart = struct.pack("<ffHHBBI", 77.5, 42.0, 0, 0, 0, 1, 55)
    return b"".join((feature_session(),
        raw_record(6, struct.pack("<BI", 30, 10), sequence=1, uptime=100),
        raw_record(6, struct.pack("<BI", 19, 2), sequence=2, uptime=101),
        raw_record(4, struct.pack("<ii", 32000, 0), sequence=3, uptime=102),
        raw_record(11, heart, sequence=4, uptime=103),
        raw_record(7, b"\x01"+bytes(7), sequence=5, uptime=60100)))


class HealthAnalysisTests(unittest.TestCase):
    def test_rf_frozen_forest_and_step_quality(self):
        data = struct.pack("<5I", 0x31465253, 1, 42, 50, 1)+bytes(32)+bytes(50*4)+struct.pack("<iiidd", -1, -1, 0, 0., .7)
        model = StepRfModel(data)
        self.assertAlmostEqual(model.probability_walk([0.]*42), .7)
        payload = struct.pack("<4sBBBBIIIi", b"SRW2", 12, 2, 0, 1, 7, 100, 20000, 8)+struct.pack("<12e", *([.5]*12))
        block = read_block(io.BytesIO(raw_record(13, payload, uptime=100)))
        result = day_activity(block, 1000, 123, predict=lambda f: .7)
        self.assertEqual((result.steps, result.candidate_steps, result.window_key), (8, 8, "123:7"))
        self.assertEqual(day_activity(block, 1000, 123, power_test=True).steps, 0)
        with self.assertRaises(ValueError):
            model.probability_walk([math.nan]*42)

    def test_gapwide_frozen_model_and_quality_gates(self):
        data = struct.pack("<4I4d", 0x47483152, 1, 1883, 1, 0., 0., 0., 0.)+struct.pack("<ii", 2, 1)+struct.pack("<iddiiBB", 0, 0., 1., 0, 0, 0, 1)
        probabilities = GapwideHead(data).predict([math.nan]*1883)
        self.assertAlmostEqual(sum(probabilities), 1.)
        self.assertGreater(probabilities[2], probabilities[0])
        rows = decode_stages([0, 300000, 600000], [probabilities, None, probabilities],
            [HealthQuality.VALID, HealthQuality.INSUFFICIENT_DATA, HealthQuality.GAP], 900000, True)
        self.assertEqual(rows[0].stage, "REM")
        self.assertIsNone(rows[1].stage)
        self.assertTrue(rows[2].estimated)
        self.assertEqual(rows[2].quality, HealthQuality.GAP)

    def test_feature_analysis_real_wire_values_and_monitoring(self):
        data = feature_data()
        segment = HealthFeatureSegment(data)
        record = HealthRecording(5, 1, len(data), 1700000000000, 60000, 7)
        report = analyze_features([segment], record)
        self.assertEqual([value.value for value in report.values], [77.5, 42.])
        self.assertEqual([value.measurement_id for value in report.values], [55, 55])
        self.assertEqual(report.temperature[0].celsius, 32.)
        self.assertFalse(report.sleep)
        self.assertFalse(report.activity_available)
        self.assertIn("HEALTH_DISABLED_SLEEP", report.warnings)
        self.assertEqual(HealthReport.from_dict(report.to_dict()), report)

    def test_raw_profile_temperature_and_missing_sleep(self):
        header = struct.pack("<BBBBqIHHHH", 6, 3, 16, 1, 1700000000000, 100, 640, 80, 2048, 512)
        data = b"".join((raw_record(1, header, schema=3),
            raw_record(6, struct.pack("<BI", 3, 1), sequence=1, schema=3, uptime=101),
            raw_record(4, struct.pack("<ii", 31000, 0), sequence=2, schema=3, uptime=102),
            raw_record(7, b"\x01"+bytes(7), sequence=3, schema=3, uptime=1000)))
        record = HealthRecording(5, 1, len(data), 1700000000000, 900, 6)
        report = analyze_raw(data, record)
        self.assertEqual(report.recording.duration_ms, 900)
        self.assertEqual(report.temperature[0].celsius, 31.)
        self.assertIn("SLEEP_DATA_INSUFFICIENT", report.warnings)

    def test_store_crc_resume_account_isolation_and_report_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = HealthStore(directory, "AA", "account/one"), HealthStore(directory, "AA", "account/two")
            data = feature_data()
            record = HealthRecording(5, 1, len(data), 1700000000000, 60000, 7)
            first.save(record)
            first.append(5, 0, data[:80])
            self.assertEqual(first.recover(5), 72)
            first.append(5, 72, data[72:])
            self.assertEqual(first.require_verified(5), record)
            self.assertEqual(second.recordings(), [])
            report = HealthReport(record, "test", activity=[HealthActivity(1700000000000, 20000, 8, 8, .7, HealthQuality.VALID)])
            first.save_report(report)
            first.reset_bedtime_steps(1700000000000, 1700000060000)
            self.assertEqual(first.report(5).activity, [])
            self.assertEqual(first.raw(5).read_bytes(), data)
            first.mark_acknowledged(record)
            self.assertTrue(first.is_acknowledged(record))
            first.raw(5).write_bytes(data[:-1])
            with self.assertRaises(ValueError):
                first.require_verified(5)


class HealthArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_automatic_sync_coalesces_and_saves_analysis_before_batch_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            data, commands, updates = feature_data(), [], []
            record = HealthRecording(5, 1, len(data), 1700000000000, 60000, 7)
            async def send(packet):
                commands.append((packet[0], packet[1]))
                if packet[0] == 0x23:
                    utc = struct.unpack_from("<q", packet, 2)[0]
                    client.accept(b"\x23\x02\x01"+struct.pack("<qq", utc, 100))
                    return
                op = packet[1]
                error = 0
                if op == 0:
                    payload = capabilities((1 << 22) | (1 << 23) | (1 << 27))
                elif op == 1:
                    state = bytearray(30)
                    state[17] = 1
                    struct.pack_into("<QI", state, 18, 123, 100)
                    payload = bytes(state)
                elif op == 9:
                    after = struct.unpack_from("<I", packet, 8)[0]
                    if after == 0:
                        payload = struct.pack("<IBBIqII", record.id, 1, 7, record.bytes, record.start_utc_ms, record.duration_ms, 10)
                    else:
                        payload, error = b"", -2
                elif op == 5:
                    _, offset, _, maximum = struct.unpack("<IIIH", packet[8:])
                    payload = struct.pack("<IIIB", 5, offset, len(data), 1)+data[offset:offset+maximum]
                elif op == 18:
                    payload = b"\x00"
                elif op == 20:
                    self.assertEqual(client.store.require_verified(5), record)
                    self.assertEqual(client.store.report(5).recording, record)
                    self.assertEqual(packet[8:10], b"\x00\x01")
                    payload = b""
                elif op == 10:
                    payload, error = b"", -2
                else:
                    self.fail(f"Unexpected health command {op}")
                client.accept(reply(packet, payload, error))
            client = HealthClient(send, address="AA", storage_path=directory)
            first, second = await asyncio.gather(client.sync_automatic("alice", "UTC", on_update=updates.append),
                                                client.sync_automatic("alice", "UTC"))
            self.assertEqual(first.status, "SUCCESS", first.errors)
            self.assertEqual(second.status, "SUCCESS")
            self.assertEqual(first.sync.downloaded_record_count, 1)
            self.assertEqual(commands.count((0x3A, 20)), 1)
            self.assertEqual(commands.count((0x23, 0)), 1)
            self.assertIn("DATA_READY", [value.stage for value in updates])
            self.assertTrue(client.store.is_acknowledged(record))
            self.assertFalse(client.store.pending_batch())
            self.assertEqual(client._sync_callers, 0)

    async def test_sync_resumes_last_crc_boundary_and_analyzes_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            data, offsets = feature_data(), []
            record = HealthRecording(5, 1, len(data), 1700000000000, 60000, 7)
            async def send(packet):
                if packet[1] == 0:
                    payload = capabilities()
                else:
                    self.assertEqual(packet[1], 5)
                    _, offset, total, maximum = struct.unpack("<IIIH", packet[8:])
                    offsets.append(offset)
                    payload = struct.pack("<IIIB", 5, offset, len(data), 1)+data[offset:offset+min(maximum, 40)]
                client.accept(reply(packet, payload))
            client = HealthClient(send, address="AA", storage_path=directory)
            client.store.save(record)
            client.store.append(5, 0, data[:80])
            result = await client.sync(5, record)
            self.assertEqual(result, record)
            self.assertEqual(offsets[:2], [0, 72])
            client.disconnected()
            report = await client.analyze(5)
            self.assertEqual(report.values[0].value, 77.5)
            self.assertEqual(len(await client.local_reports()), 1)

    async def test_ack_rechecks_disk_and_rejects_deletion_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            data, commands = feature_data(), []
            record = HealthRecording(5, 1, len(data), 1700000000000, 60000, 7)
            delete = True
            async def send(packet):
                commands.append(packet[1])
                payload = capabilities((1 << 22) | (1 << 23)) if packet[1] == 0 else bytes((int(delete),)) if packet[1] == 18 else b""
                client.accept(reply(packet, payload))
            client = HealthClient(send, address="AA", storage_path=directory)
            client.store.save(record)
            client.store.append(5, 0, data)
            with self.assertRaisesRegex(ValueError, "delete"):
                await client.acknowledge(record)
            self.assertNotIn(8, commands)
            delete = False
            await client.acknowledge(record)
            self.assertIn(8, commands)
            self.assertTrue(client.store.is_acknowledged(record))

    async def test_automatic_offline_uses_account_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            async def send(packet):
                self.fail("Offline automatic sync must not send")
            client = HealthClient(send, address="AA", storage_path=directory)
            client.disconnected()
            result = await client.sync_automatic("alice", "UTC")
            self.assertEqual(result.status, "CACHE_ONLY")
            self.assertEqual(result.account_scope, "alice")
            self.assertEqual(client._sync_callers, 0)


if __name__ == "__main__":
    unittest.main()
