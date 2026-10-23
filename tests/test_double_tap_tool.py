"""Standalone test entry point: packet replay, default target and BLE cleanup."""

import asyncio
import csv
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ring_python_sdk.swipe import SwipeProcessor


spec = importlib.util.spec_from_file_location(
    "double_tap_tool", Path(__file__).parents[1] / "tools/test_double_tap.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_demo_replays_packets_and_logs_only_double_taps(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tool.firmware, "RingSession", lambda **_kw: pytest.fail("demo touched BLE"))
    path = tmp_path / "events.csv"
    assert tool.main(["--demo", "--csv", str(path)]) == 0
    summary = json.loads(path.with_suffix(".summary.json").read_text())
    assert summary["source"] == "demo"
    assert summary["double_tap"] == {
        "count": 2, "gesture_name": "tap", "min_interval_ms": 100, "max_interval_ms": 200,
    }
    assert "double_pinch" not in summary
    with path.open(encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    pairs = [row for row in rows if row["double_tap_index"]]
    assert [row["double_tap_interval_ms"] for row in pairs] == ["150", "150"]
    assert [row["double_tap_first_seq"] for row in pairs] == ["1", "7"]
    assert not any(row["double_pinch_index"] for row in rows)
    output = capsys.readouterr().out
    assert "DOUBLE TAP #0002" in output and "设备：8F56" in output
    assert "DOUBLE PINCH" not in output


@pytest.mark.parametrize("args", [
    ["--min-interval-ms", "0"], ["--max-interval-ms", "nan"],
    ["--min-interval-ms", "700", "--max-interval-ms", "600"],
    ["--double-pinch"],
])
def test_invalid_timing_or_conflicting_mode_never_starts_ble(args, monkeypatch):
    monkeypatch.setattr(tool.firmware, "RingSession", lambda **_kw: pytest.fail("invalid options touched BLE"))
    with pytest.raises(SystemExit) as error:
        tool.main(args)
    assert error.value.code == 2


@pytest.mark.parametrize("outcome", ["completed", "not_found", "disconnect", "cancel"])
def test_default_target_is_8f56_and_only_swipe_starts(tmp_path, monkeypatch, outcome):
    calls = []

    class Session:
        def __init__(self, **kwargs):
            assert kwargs["name_keyword"] == "8F56"
            assert not kwargs["auto_reconnect"] and not kwargs["battery_poll_enabled"]
            self.client = SimpleNamespace(is_connected=True)
            self.device_info = SimpleNamespace(fw_version="test", hw_rev=1, components=())
            self.target_name, self.target_address = "Ringo_8F56", "TEST-ID"
            self.swipe = None

        async def connect(self):
            calls.append("connect")
            return outcome != "not_found"

        async def swipe_on(self, **kwargs):
            calls.append("swipe")
            self.swipe = SwipeProcessor(tmp_path / "sdk.csv", **kwargs)
            for seq, uptime in [(1, 1000), (2, 1150)]:
                self.swipe.handle_notification(None, tool.firmware.demo_packet(
                    5, seq=seq, trigger=True, uptime_ms=uptime))
            if outcome == "disconnect":
                self.client.is_connected = False
            elif outcome == "cancel":
                raise asyncio.CancelledError()

        def drain_live_logs(self):
            return []

        async def disconnect(self):
            calls.append("disconnect")
            if self.swipe:
                self.swipe.close()

    monkeypatch.setattr(tool.firmware, "RingSession", Session)
    path = tmp_path / "events.csv"
    arguments = ["--duration", "0.01", "--csv", str(path)]
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            tool.main(arguments)
    else:
        assert tool.main(arguments) == {"completed": 0, "not_found": 1, "disconnect": 1}[outcome]
    assert calls == (["connect", "disconnect"] if outcome == "not_found" else
                     ["connect", "swipe", "disconnect"])
    summary = json.loads(path.with_suffix(".summary.json").read_text())
    assert summary["double_tap"]["count"] == (0 if outcome == "not_found" else 1)
    assert (summary["stop_reason"] == "completed") == (outcome == "completed")


def test_name_override_and_dry_run_create_no_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tool.firmware, "RingSession", lambda **_kw: pytest.fail("dry run touched BLE"))
    path = tmp_path / "events.csv"
    assert tool.main(["--dry-run", "--name", "ABCD", "--csv", str(path)]) == 0
    assert "设备：ABCD" in capsys.readouterr().out
    assert not path.exists()
