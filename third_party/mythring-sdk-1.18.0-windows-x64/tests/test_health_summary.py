from dataclasses import replace
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import re
import unittest
from zoneinfo import ZoneInfo

from mythring.health_models import (
    HealthActivity, HealthBedtime, HealthLossRange, HealthMetric, HealthQuality,
    HealthReport, HealthRri, HealthSleepEpoch, HealthSource, HealthTemperatureSample,
    HealthValue, HealthWearEvent, HealthWearStateEvent,
)
from mythring.health_protocol import HealthCapabilities, HealthRecording, HealthState
from mythring.health_scoring import HealthScoringProfile, activity_load, empty_score, sleep_score
from mythring.health_summary import (
    AutomaticSyncResult, DailyHealthSummary, HealthBodyProfile, HealthNightSummary,
    HealthPageSnapshotCache, HealthSummaryOptions, PublicHealthReport, SyncError,
    SyncSummary, VitalHistoryValue, bridge_restarts, build_automatic_result,
    build_daily, build_hrv_timeline, build_vital_history, evaluate_hrv_artifacts,
    walking_calories,
)
from mythring.health_temperature import TemperatureReading, build_temperature, decode_temperature

Q = HealthQuality
ZONE = ZoneInfo("Asia/Shanghai")


def ms(value, zone=ZONE):
    return int(datetime.fromisoformat(value).replace(tzinfo=zone).timestamp() * 1000)


def report(start=None, **kwargs):
    start = ms("2026-10-16T20:00") if start is None else start
    recording = kwargs.pop("recording", HealthRecording(1, 1, 100, start, 14 * 3600000, 7))
    return HealthReport(recording, "fixture", **kwargs)


def epoch(start, duration, stage="LIGHT", **kwargs):
    return HealthSleepEpoch(start, stage, None, False, False, Q.VALID, duration, **kwargs)


class SerializationTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((Path(__file__).parent / "fixtures/health_schema.json").read_text(encoding="utf-8"))

    def assert_release_contract(self, value, node, path="$"):
        # The bundled release schema uses only refs, anyOf, primitive types and object/array rules.
        if "$ref" in node:
            target = self.schema
            for part in node["$ref"].split("/")[1:]:
                target = target[part]
            return self.assert_release_contract(value, target, path)
        if "anyOf" in node:
            failures = []
            for option in node["anyOf"]:
                try:
                    self.assert_release_contract(value, option, path)
                    return
                except AssertionError as error:
                    failures.append(str(error))
            self.fail(f"{path}: no schema variant matches: {failures}")
        kind = node.get("type")
        matches = {
            "object": isinstance(value, dict), "array": isinstance(value, list),
            "integer": type(value) is int, "number": type(value) in (int, float),
            "string": isinstance(value, str), "boolean": type(value) is bool, "null": value is None,
        }
        if isinstance(kind, list):
            compatible = [candidate for candidate in kind if matches[candidate]]
            self.assertTrue(compatible, f"{path}: expected one of {kind}, got {type(value).__name__}")
            kind = compatible[0]
        if kind is not None:
            self.assertTrue(matches[kind], f"{path}: expected {kind}, got {type(value).__name__}")
        if kind == "object":
            self.assertTrue(set(node.get("required", ())) <= value.keys(), path)
            if node.get("additionalProperties") is False:
                self.assertTrue(value.keys() <= node["properties"].keys(), f"{path}: unknown keys {value.keys() - node['properties'].keys()}")
            for key, child in value.items():
                if key in node["properties"]:
                    self.assert_release_contract(child, node["properties"][key], f"{path}.{key}")
        elif kind == "array":
            for index, child in enumerate(value):
                self.assert_release_contract(child, node["items"], f"{path}[{index}]")
        elif kind == "string" and "pattern" in node:
            self.assertIsNotNone(re.search(node["pattern"], value), path)

    def test_empty_snapshot_matches_release_schema_and_example_shape(self):
        original = json.loads((Path(__file__).parent / "fixtures/health_empty_example.json").read_text(encoding="utf-8"))
        result = AutomaticSyncResult(original["accountScope"], original["deviceAddress"], original["timeZone"])
        result = result.with_page_summary(now_utc_ms=original["page"]["generatedAtUtcMs"])
        value = result.to_map()
        self.assertEqual(value.keys(), original.keys())
        self.assertEqual(value["page"].keys(), original["page"].keys())
        self.assertEqual(value["page"]["today"]["date"], original["page"]["today"]["date"])
        self.assert_release_contract(value, self.schema)
        self.assertEqual(json.loads(result.to_json()), value)

    def test_full_snapshot_identity_strings_nulls_and_private_fields(self):
        start = ms("2026-10-16T23:00")
        values = [HealthValue(start + 60000, HealthMetric.HEART_RATE, 60.0, "bpm", Q.VALID, HealthSource.FIRMWARE, 90, 2**63 + 1)]
        source = report(values=values, rri=[HealthRri(start, 1000, 100, 0)], bedtimes=[HealthBedtime(start, start + 3600000)],
                        sleep=[epoch(start, 3600000)], temperature=[HealthTemperatureSample(start, 33.0, Q.VALID, 0)])
        snapshot = build_automatic_result(account_scope="a", device_address="b", time_zone="Asia/Shanghai", status="READY",
                                          device_state=HealthState(recording_id=2**63 + 2, boot_id=2**63 + 3),
                                          sync=SyncSummary(deferred_record_ids=[2**63 + 4]), reports=[source],
                                          loss_ranges=[HealthLossRange(5, 6, False)], errors=[SyncError("download", 2**63 + 5, "X", None, "x")],
                                          capabilities=HealthCapabilities(7, 50, 100, 60000, 100, 0, 20, 10), now_utc_ms=ms("2026-10-17T12:00"))
        wire = snapshot.to_map()
        self.assert_release_contract(wire, self.schema)
        self.assertEqual(wire["reports"][0]["recording"]["id"], "1")
        self.assertEqual(wire["reports"][0]["values"][0]["measurementId"], str(2**63 + 1))
        self.assertEqual(wire["deviceState"]["bootId"], str(2**63 + 3))
        self.assertEqual(wire["sync"]["deferredRecordIds"], [str(2**63 + 4)])
        self.assertNotIn("rri", wire["reports"][0])
        self.assertNotIn("temperature", wire["reports"][0])
        self.assertNotIn("probabilities", wire["reports"][0]["sleep"][0])
        self.assertIn("rejectionReason", wire["reports"][0]["values"][0])
        self.assertIsNone(wire["reports"][0]["values"][0]["rejectionReason"])
        public = PublicHealthReport.from_report(replace(source, values=[replace(values[0], value=math.nan)]))
        self.assertIsNone(replace(snapshot, reports=[public]).to_map()["reports"][0]["values"][0]["value"])


class DailyProjectionTests(unittest.TestCase):
    def test_midnight_split_dedup_overlap_and_missing_steps(self):
        start, midnight = ms("2026-10-16T23:55"), ms("2026-10-17T00:00")
        activity = HealthActivity(start, 20000, 20, 20, .8, Q.VALID, "boot:1")
        source = report(activity=[activity, activity], bedtimes=[HealthBedtime(start, midnight + 300000)],
                        sleep=[epoch(start, 420000), epoch(midnight, 300000)])
        daily = build_daily([source], ZONE, ms("2026-10-17T12:00"))
        self.assertEqual([day.steps for day in daily], [20, None])
        self.assertEqual([day.valid_sleep_minutes for day in daily], [5, 5])
        self.assertEqual(sum(e.duration_ms for day in daily for e in day.sleep), 600000)

    def test_cross_boot_bridge_requires_shared_trusted_context(self):
        start = ms("2026-10-16T23:00")
        bed = HealthBedtime(start, start + 660000)
        before = report(recording=HealthRecording(1, 1, 1, start, 300000, 7), bedtimes=[bed], sleep=[epoch(start, 300000)], warnings=["BEDTIME_CROSS_BOOT_CONTEXT"])
        after = report(recording=HealthRecording(2, 1, 1, start + 360000, 300000, 7), bedtimes=[bed], sleep=[epoch(start + 360000, 300000, "DEEP")], warnings=["BEDTIME_CROSS_BOOT_CONTEXT"])
        bridged = bridge_restarts(before.sleep + after.sleep, [before, after])
        self.assertEqual(len(bridged), 3)
        self.assertEqual((bridged[1].stage, bridged[1].duration_ms, bridged[1].quality, bridged[1].estimated), ("LIGHT", 60000, Q.GAP, True))
        self.assertEqual(len(bridge_restarts(before.sleep + after.sleep, [before, replace(after, warnings=[])])), 2)

    def test_dst_hour_bins_include_repeated_hour_and_skip_missing_hour(self):
        for day, expected in (("2026-11-01", 25), ("2026-03-08", 23)):
            zone = ZoneInfo("America/New_York")
            now = ms(day + "T23:59", zone)
            snapshot = AutomaticSyncResult("a", "b", str(zone)).with_page_summary(now_utc_ms=now)
            hours = snapshot.page.today.hourly_activity
            self.assertEqual(len(hours), expected)
            self.assertTrue(all(b.start_utc_ms == a.end_utc_ms for a, b in zip(hours, hours[1:])))
            if expected == 25:
                repeated = [hour for hour in hours if hour.local_hour == 1]
                self.assertEqual(len(repeated), 2)
                self.assertNotEqual(repeated[0].utc_offset_seconds, repeated[1].utc_offset_seconds)

    def test_hrv_buckets_split_at_bedtime_and_keep_null_gaps(self):
        start = ms("2026-10-17T01:05")
        source = report(bedtimes=[HealthBedtime(start, start + 20 * 60000)])
        value = HealthValue(start + 60000, HealthMetric.HRV, 40, "ms", Q.VALID, HealthSource.FIRMWARE)
        snapshot = AutomaticSyncResult("a", "b", "Asia/Shanghai", reports=[PublicHealthReport.from_report(source)],
                                        vital_history=[VitalHistoryValue(value, "2026-10-17", "night", False)])
        timeline = build_hrv_timeline(snapshot, date(2026, 10, 17), ZONE, ms("2026-10-17T02:00"))
        nights = [point for point in timeline.points if point.period == "NIGHT"]
        self.assertEqual([point.end_utc_ms - point.start_utc_ms for point in nights], [300000, 600000, 300000])
        self.assertEqual([point.median_rmssd_ms for point in nights], [40, None, None])

    def test_page_cache_keeps_caller_options_and_demo_label(self):
        now = ms("2026-10-17T12:00")
        snapshot = AutomaticSyncResult("a", "b", "Asia/Shanghai")
        cache = HealthPageSnapshotCache(HealthSummaryOptions(body_profile=HealthBodyProfile(170, 60)))
        first = cache.project(snapshot, now)
        second = cache.project(replace(snapshot, synced_at_utc_ms=now), now + 1000)
        self.assertIs(first.page, second.page)
        demo = replace(snapshot, status="DEMO").with_page_summary(now_utc_ms=now)
        self.assertTrue(demo.page.today.sleep_score.is_mock)
        self.assertEqual(demo.page.today.sleep_score.label, "演示数据")


class HrvTests(unittest.TestCase):
    def test_screening_keeps_genuine_variability_and_breaks_removed_adjacency(self):
        start = ms("2026-10-16T23:00")
        rows, current = [], start
        for index in range(20):
            interval = 800 if index % 2 == 0 else 1000
            current += interval
            rows.append(HealthRri(current, interval, 100, 0))
        result = evaluate_hrv_artifacts(rows)
        self.assertEqual(result.rejected, 0)
        self.assertEqual(result.rmssd_ms, 200)
        bad = list(rows)
        bad[10] = replace(bad[10], milliseconds=1790)
        screened = evaluate_hrv_artifacts(bad)
        self.assertGreaterEqual(screened.rejected, 1)
        self.assertLess(screened.pairs, 19)

    def test_night_rri_derives_missing_vitals_but_not_day_values(self):
        start = ms("2026-10-16T23:00")
        rows = [HealthRri(start + (index + 1) * 1000, 1000, 100, 0) for index in range(20)]
        source = report(rri=rows, bedtimes=[HealthBedtime(start, start + 3600000)])
        values = build_vital_history([source], ZONE, start + 3600000)
        self.assertEqual({item.measurement.metric for item in values}, {HealthMetric.HRV, HealthMetric.HEART_RATE})
        self.assertTrue(all(item.derived_from_rri and item.period == "night" for item in values))
        self.assertEqual(build_vital_history([replace(source, bedtimes=[])], ZONE, start + 3600000), [])


class TemperatureAndScoringTests(unittest.TestCase):
    def test_temperature_wear_policy_and_sensor_errors(self):
        readings = [TemperatureReading(index, 33000, 0, 0) for index in range(1, 5)]
        states = [HealthWearStateEvent(1, 2, 0), HealthWearStateEvent(3, 0, 0)]
        optical = [HealthWearEvent(1, 2)]
        values = decode_temperature(readings, states, optical)
        self.assertEqual([item.quality for item in values], [Q.VALID, Q.VALID, Q.NON_WEAR, Q.NON_WEAR])
        self.assertTrue(all(item.quality == Q.NON_WEAR for item in decode_temperature(readings, [], optical)))
        self.assertEqual(decode_temperature([TemperatureReading(1, 33000, -1, 0)], [], [])[0].quality, Q.INSUFFICIENT_DATA)

    def test_temperature_initial_latest_and_seven_night_baselines(self):
        reports = []
        for index in range(8):
            day = date(2026, 10, 10) + timedelta(days=index)
            start = ms(day.isoformat() + "T00:00")
            samples = [HealthTemperatureSample(start + minute * 60000, 33.0 + index * .1, Q.VALID, 0) for minute in range(30)]
            reports.append(report(start=start, recording=HealthRecording(index + 1, 1, 1, start, 3600000, 7),
                                  bedtimes=[HealthBedtime(start, start + 3600000)], sleep=[epoch(start, 3600000)], temperature=samples))
        now = ms("2026-10-17T12:00")
        daily = build_daily(reports, ZONE, now)
        temperature = build_temperature(reports, daily, ZONE, now)
        self.assertEqual(temperature.nightly[0].baseline_source, "INITIAL_SLEEP")
        self.assertEqual(temperature.nightly[0].deviation_celsius, 0)
        self.assertEqual(temperature.nightly[1].baseline_source, "LATEST_SLEEP")
        self.assertAlmostEqual(temperature.nightly[1].deviation_celsius, .1)
        self.assertEqual(temperature.nightly[-1].baseline_source, "ROLLING_7_NIGHTS")
        self.assertAlmostEqual(temperature.nightly[-1].deviation_celsius, .4)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{16}", row.baseline_id) for row in temperature.nightly))

    def test_walking_calories_missing_profile_zero_activity_and_limits(self):
        now = ms("2026-10-17T12:00")
        zero = HealthActivity(now - 20000, 20000, 0, 0, .5, Q.VALID)
        fast = replace(zero, steps=1000)
        body = HealthBodyProfile(170, 60)
        self.assertEqual(walking_calories([zero], None, now).status, "PROFILE_REQUIRED")
        self.assertEqual(walking_calories([zero], body, now).kcal, 0)
        self.assertEqual(walking_calories([fast], body, now).status, "OUT_OF_RANGE")
        self.assertEqual(walking_calories([zero, fast], body, now).status, "PARTIAL")
        self.assertIsNone(activity_load([zero], now).load_units)

    def test_sleep_score_coverage_and_today_requires_all_inputs(self):
        start = ms("2026-10-16T23:00")
        stages = [epoch(start, 96 * 60000, "DEEP"), epoch(start + 96 * 60000, 384 * 60000)]
        night = HealthNightSummary("2026-10-17", start, start + 480 * 60000, 480 * 60000, 480, start, start + 480 * 60000,
                                    False, stages, None, 0, None, 0)
        score = sleep_score(night, [night], None, ZONE, HealthScoringProfile())
        self.assertEqual(score.value, 100)
        self.assertEqual(score.status, "PARTIAL")
        self.assertAlmostEqual(score.coverage, .60)
        self.assertIn("HRV", score.missing_inputs)
        snapshot = AutomaticSyncResult("a", "b", "Asia/Shanghai").with_page_summary(now_utc_ms=ms("2026-10-17T12:00"))
        self.assertIsNone(snapshot.page.today.today_score.value)


if __name__ == "__main__":
    unittest.main()
