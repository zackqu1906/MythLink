from datetime import datetime
import hashlib
import io
import json
import threading
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pytest

from proximic_ring.diagnostics.diagnostic_log import RotatingDiagnosticLog
from proximic_ring.diagnostics.log_export import LogRange, export_diagnostics, filter_logs, safe_configuration
from proximic_ring.voice.history_export import ExportCancelled
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_history_management import add_record


NOW = datetime(2026, 11, 10, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.mark.parametrize("period,expected", [
    ("today", ["utc-today", "today", "stack detail", "second stack line"]),
    ("week", ["week-start", "yesterday", "utc-today", "today", "stack detail", "second stack line"]),
    ("all", ["old", "week-start", "yesterday", "utc-today", "today", "stack detail", "second stack line"]),
])
def test_ranges_use_record_time_and_keep_multiline_rotations(tmp_path, period, expected):
    first, second = tmp_path / "old.log", tmp_path / "new.log"
    first.write_text("[2026-11-03T23:59:59+08:00] old\n"
        "[2026-11-04T00:00:00+08:00] week-start\n"
        "[2026-11-09T23:59:59+08:00] yesterday\n"
        "[2026-11-09T16:00:00Z] utc-today\n"
        "[2026-11-10T10:00:00+08:00] today\nstack detail\n")
    second.write_text("second stack line\n[2026-11-10T13:00:00+08:00] future\nfuture stack\n")
    output = io.BytesIO()
    stats = filter_logs([first, second], output, LogRange.create(period, NOW))
    rows = output.getvalue().decode().splitlines()
    assert [row.partition("] ")[2] if row.startswith("[") else row for row in rows] == expected
    assert stats["selected_lines"] == len(expected)
    assert stats["available_start"].startswith("2026-11-03")
    assert stats["unknown_time_lines"] == 0


def test_calendar_week_crosses_month_and_dst():
    selection = LogRange.create("week", datetime(2026, 11, 3, 12, tzinfo=ZoneInfo("America/New_York")))
    assert selection.start.isoformat() == "2026-10-28T00:00:00-04:00"
    assert selection.end.isoformat() == "2026-11-03T12:00:00-05:00"
    with pytest.raises(ValueError):
        LogRange.create("month", NOW)


def test_full_export_reads_every_rotation_beyond_live_view_limit(tmp_path):
    writer = RotatingDiagnosticLog(tmp_path / "logs/diagnostic.log", max_bytes=18000, backup_count=5)
    for index in range(1400):
        writer.append(f"[2026-11-10T09:00:00+08:00] event-{index}")
    originals = {p: p.read_bytes() for p in writer.files() if p.exists()}
    assert len(originals) > 1
    result = export_diagnostics(writer, tmp_path / "all.zip", LogRange.create("all", NOW))
    with ZipFile(result["path"]) as archive:
        rows = archive.read("diagnostic.log").decode().splitlines()
        assert len(rows) == 1400
        assert [int(row.rsplit("-", 1)[1]) for row in rows] == list(range(1400))
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def test_archive_includes_full_interaction_evidence_and_reports_coverage(tmp_path):
    writer = RotatingDiagnosticLog(tmp_path / "logs/diagnostic.log")
    writer.append("orphan crash stack")
    writer.append("[2026-11-10T09:00:00+08:00] trace=full " + "原始错误" * 400)
    store = ModificationDatasetCollector(tmp_path / "history", "test")
    today, old = [add_record(store, index) for index in (1, 2)]
    for identity, stamp in ((today, "2026-11-10T01:00:00+08:00"), (old, "2026-11-01T01:00:00+08:00")):
        path = store._interaction_path(identity)
        record = json.loads(path.read_text())
        record.update(created_at=stamp, updated_at=stamp)
        record["llm"] = {"requests": [{"request_input": {"text": "完整模型输入" * 500}, "latency_ms": 124}]}
        record["screen_evidence"] = {"screenshot_file": "screen.png", "ocr_lines": ["原文"]}
        (path.parent / "screen.png").write_bytes(b"saved screenshot")
        path.write_text(json.dumps(record, ensure_ascii=False))
    originals = {p: p.read_bytes() for p in store.interactions_root.rglob("*") if p.is_file()}
    ime = tmp_path / "ime"
    ime.mkdir()
    (ime / "caret-trace-123.jsonl").write_text(json.dumps({"timestamp": NOW.timestamp() - 10, "pid": 123}) + "\n")
    path = tmp_path / "support.zip"
    result = export_diagnostics(writer, path, LogRange.create("today", NOW), store=store, ime_root=ime,
        state={"settings": {"apiKey": "secret-value", "gestureBindings": {"tap": "start"}}})
    assert result["record_count"] == 1 and result["log_lines"] == 2
    with ZipFile(path) as archive:
        assert archive.testzip() is None
        assert "interactions/" + old + "/record.json" not in archive.namelist()
        base = "interactions/" + today + "/"
        for name in ("record.json", "audio.wav", "screen.png", "events.jsonl", "asr_updates.jsonl"):
            assert archive.read(base + name) == originals[store._interaction_dir(today) / name]
        assert "ime/caret-trace-123.jsonl" in archive.namelist()
        assert archive.read("diagnostic.log") == writer.path.read_bytes()
        runtime = archive.read("runtime.json").decode()
        assert "secret-value" not in runtime and "gestureBindings" in runtime
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["log_coverage"]["unknown_time_lines"] == 1
        assert manifest["requested_start"] == "2026-11-10T00:00:00+08:00"
        for entry in manifest["files"]:
            data = archive.read(entry["path"])
            assert entry["bytes"] == len(data) and entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def test_missing_corrupt_files_are_reported_and_empty_range_still_exports(tmp_path):
    writer = RotatingDiagnosticLog(tmp_path / "logs/diagnostic.log")
    writer.append("[2026-11-01T09:00:00+08:00] old")
    store = ModificationDatasetCollector(tmp_path / "history", "test")
    identity = add_record(store, 1)
    store._interaction_path(identity).write_text("broken json")
    result = export_diagnostics(writer, tmp_path / "empty.zip", LogRange.create("today", NOW), store=store)
    assert result["log_lines"] == 0 and result["warning_count"] == 1
    with ZipFile(result["path"]) as archive:
        assert archive.read("diagnostic.log") == b""
        assert json.loads(archive.read("manifest.json"))["warnings"][0]["source"] == identity


def test_missing_attachment_preserves_record_and_reports_missing_evidence(tmp_path):
    writer = RotatingDiagnosticLog(tmp_path / "diagnostic.log")
    store = ModificationDatasetCollector(tmp_path / "history", "test")
    identity = add_record(store, 1)
    record_path = store._interaction_path(identity)
    record = json.loads(record_path.read_text())
    record.update(created_at=NOW.isoformat(), updated_at=NOW.isoformat())
    record_path.write_text(json.dumps(record))
    (record_path.parent / "audio.wav").unlink()
    result = export_diagnostics(writer, tmp_path / "missing.zip", LogRange.create("today", NOW), store=store)
    assert result["record_count"] == 1
    with ZipFile(result["path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert any(warning.get("file") == "audio.wav" for warning in manifest["warnings"])
        assert "interactions/" + identity + "/record.json" in archive.namelist()


def test_cancel_and_failure_never_publish_partial_or_overwrite_source(tmp_path, monkeypatch):
    writer = RotatingDiagnosticLog(tmp_path / "diagnostic.log")
    writer.append("evidence")
    output = tmp_path / "support.zip"
    output.write_bytes(b"previous export")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ExportCancelled):
        export_diagnostics(writer, output, LogRange.create("all", NOW), cancel=cancel)
    assert output.read_bytes() == b"previous export"
    assert not list(tmp_path.glob(".mythlink-diagnostics-*"))
    with pytest.raises(ValueError):
        export_diagnostics(writer, writer.path, LogRange.create("all", NOW))
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(ZipFile, "writestr", fail)
    with pytest.raises(OSError, match="disk full"):
        export_diagnostics(writer, output, LogRange.create("all", NOW))
    assert output.read_bytes() == b"previous export"
    assert not list(tmp_path.glob(".mythlink-diagnostics-*"))


def test_configuration_omits_nested_credentials_without_losing_bindings():
    assert safe_configuration({"nested": '{"password":"secret","tap":"click"}', "privateKeyPath": "/private/key"}) == {
        "nested": {"password": "[omitted]", "tap": "click"}, "privateKeyPath": "[omitted]"}
