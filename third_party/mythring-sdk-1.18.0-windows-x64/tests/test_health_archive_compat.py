import hashlib
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from mythring.health import HealthClient
from mythring.health_demo import HealthDemoData
from mythring.health_models import HealthRecording, HealthReport
from mythring.health_store import HealthStore
from test_health import raw_record
from test_health_analysis import feature_data


class HealthArchiveCompatibilityTests(unittest.TestCase):
    def test_android_metadata_receipts_and_read_only_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent"
            self.assertEqual(HealthClient.read_local_reports(missing, "AA", "alice"), [])
            self.assertFalse(missing.exists())
            store = HealthStore(directory, "AA", "alice")
            android = {"id": 5, "status": 1, "bytes": 128, "start": 1000, "duration": 60000, "mode": 7}
            record = store.decode_recording(android)
            store.atomic("5.json", android)
            store.atomic("sync-progress-v1", {"confirmed": [{"record": android, "reclaimed": True}], "observed": [android]})
            self.assertEqual(store.recording(5), record)
            self.assertTrue(store.is_acknowledged(record))
            store.save_sync_progress([record])
            self.assertTrue(store.receipts()["5"]["reclaimed"])
            store.atomic("5.report", {"schema": 1, "algorithm": "legacy", "recording": android,
                "acc": 0, "green": 0, "warnings": [], "values": [], "rri": [], "activity": [], "sleep": [], "bedtimes": []})
            self.assertEqual(store.report(5).recording, record)

    def test_recovered_schema5_is_validated_separately_without_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HealthStore(directory, "AA")
            original = raw_record(1, b"\x07\x05"+bytes(46), schema=5)+raw_record(7, b"\x01"+bytes(7), sequence=1, schema=5)
            replay = feature_data()
            source = HealthRecording(5, 1, len(original), 1700000000000, 60000, 7)
            derived = replace(source, id=100005, bytes=len(replay))
            store.save(derived)
            store.append(derived.id, 0, replay)
            store.save_report(HealthReport(derived, "test", warnings=["OFFLINE_SCHEMA5_REPLAY", "OFFLINE_SOURCE_RECORD_5"]))
            path = store.root / "5.source.rh"
            path.write_bytes(original)
            store.atomic("5.recovered.json", {"schema": 1, "source": asdict(source), "derivedId": derived.id,
                "sourceSha256": hashlib.sha256(original).hexdigest(), "derivedSha256": hashlib.sha256(replay).hexdigest()})
            self.assertTrue(store.has_recovered_source(source))
            self.assertFalse(store.raw(5).exists())
            path.write_bytes(original[:-1]+b"\xff")
            self.assertFalse(store.has_recovered_source(source))
            self.assertEqual(path.stat().st_size, len(original))

    def test_refusal_identity_and_legacy_activation_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            client = HealthClient(None, storage_path=directory)
            record = HealthRecording(5, 1, 100, 1, 60000, 6)
            self.assertFalse(client.is_analysis_eligible(record))
            cutoff = client._analysis_activation_utc_ms
            self.assertTrue(client.is_analysis_eligible(replace(record, start_utc_ms=cutoff)))
            self.assertTrue(client.is_analysis_eligible(replace(record, mode=7)))
            second = HealthClient(None, storage_path=directory)
            self.assertFalse(second.is_analysis_eligible(record))
            self.assertEqual(second._analysis_activation_utc_ms, cutoff)
            client.store.save_refusal(record, 123, 4, 18, -134)
            self.assertEqual(client.store.cached_refusal(record, 123, 4, 18), -134)
            self.assertIsNone(client.store.cached_refusal(record, 124, 4, 18))
            self.assertIsNone(client.store.cached_refusal(replace(record, bytes=101), 123, 4, 18))

    def test_demo_is_explicit_complete_and_deterministic(self):
        result = HealthDemoData.yesterday("Asia/Shanghai", now_utc_ms=1700000000000)
        self.assertEqual(result.status, "DEMO")
        self.assertEqual(result.account_scope, "demo")
        self.assertEqual(len(result.reports), 1)
        self.assertEqual(result.daily[0].steps, 3437)
        self.assertTrue(all(day.today_score.is_mock and day.sleep_score.is_mock for day in result.page.days))
        self.assertIsNone(result.synced_at_utc_ms)
