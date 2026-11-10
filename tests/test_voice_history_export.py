import csv
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import threading
from zipfile import ZipFile

import numpy as np
import pytest

from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from proximic_ring.voice.history_export import export_history, ExportCancelled
from proximic_ring.text_processing.screen_context import ScreenContext
from test_history_management import add_record, associate


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def test_export_round_trips_all_modes_full_context_assets_and_associations(tmp_path):
    store = ModificationDatasetCollector(tmp_path / "data", "test")
    ids = []
    for session, mode in enumerate(("manual", "auto", "manual_routed_edit"), 1):
        identity = add_record(store, session, "=1+1\n中文语音，完整保留")
        ids.append(identity)
        context = ScreenContext(dict(status="ok", application="test.app", capture_id="a" * 32), evidence={
            "screenshot_png": b"example-png-bytes", "ocr_lines": [{"text": "原始上文"}], "captured_at": "2026-11-10T01:00:00+00:00"})
        store.record_screen_capture(session, context, routing=mode, input_context={
            "status": "captured", "original": "", "context_complete": False, "selection": [0, 0]})
        store.record_context_preparation(identity, ScreenContext({"status": "ok", "context": "整理上文"},
            evidence={"context_preparation": {"input": {"lines": ["原始上文"]}, "raw_output": "模型返回", "model": "test"}}), enabled=True)
        store.record_imu_samples(session, [{"x": 1, "y": 2}], sample_rate_hz=100)
    associate(store, ids[0], ids[1])
    before = snapshot(store.user_root)
    result = export_history(store, tmp_path / "exports")
    assert result["record_count"] == 3 and result["missing_file_count"] == 0
    assert snapshot(store.user_root) == before
    with ZipFile(result["path"]) as archive:
        assert archive.testzip() is None
        rows = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
        assert rows == [json.loads(before[f"interactions/{identity}/record.json"]) for identity in sorted(ids)]
        for relative, data in before.items():
            assert archive.read(relative) == data
        csv_rows = list(csv.DictReader(io.StringIO(archive.read("summary.csv").decode("utf-8-sig"))))
        assert {r["routing_mode"] for r in csv_rows} == {"manual", "auto", "manual_routed_edit"}
        assert all(r["asr_text"].startswith("'=1+1") and r["editable_text_present"] == "True" for r in csv_rows)
        assert all(r["editable_text"] == "" and r["context_complete"] == "False" for r in csv_rows)
        assert all(r["screenshot_in_archive"] == "True" for r in csv_rows)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["association_missing_interaction_ids"] == []
        assert manifest["context_coverage"]["prepared_context_ok"] == 3
        for entry in manifest["files"]:
            data = archive.read(entry["path"])
            assert len(data) == entry["size"] and hashlib.sha256(data).hexdigest() == entry["sha256"]


def test_export_includes_all_records_beyond_ui_limit_and_incomplete_cancelled_sessions(tmp_path):
    store = ModificationDatasetCollector(tmp_path / "data", "test")
    for session in range(1, 103):
        store.begin_session(session)
    store.record_application(session_id=1, action="cancelled")
    result = export_history(store, tmp_path / "exports")
    assert result["record_count"] == 102
    with ZipFile(result["path"]) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("summary.csv").decode("utf-8-sig"))))
        assert len(rows) == 102
        assert all(r["editable_text_present"] == "False" and r["context_complete"] == "" for r in rows)
        assert sum(r["outcome_status"] == "cancelled" for r in rows) == 1


def test_missing_unsafe_and_corrupt_records_are_reported_without_following_links(tmp_path):
    store = ModificationDatasetCollector(tmp_path / "data", "test")
    first, deleted, corrupt, linked = [add_record(store, n) for n in range(1, 5)]
    store.delete_entry(deleted)
    store._interaction_path(corrupt).write_text("broken JSON")
    outside = tmp_path / "private.txt"
    outside.write_text("must not export")
    audio = store._interaction_dir(first) / "audio.wav"
    audio.unlink()
    audio.symlink_to(outside)
    record = store._interaction_data(first)
    record["screen_evidence"] = {"screenshot_file": "../../private.txt"}
    store._write_json(store._interaction_path(first), record)
    store._interaction_path(linked).unlink()
    store._interaction_path(linked).symlink_to(outside)
    result = export_history(store, tmp_path / "exports")
    assert result["record_count"] == 1 and result["skipped_count"] == 2 and result["missing_file_count"] == 2
    with ZipFile(result["path"]) as archive:
        assert all("must not export" not in archive.read(name).decode("utf-8", errors="ignore") for name in archive.namelist())
        assert not any(deleted in name or ".." in name for name in archive.namelist())


def test_compression_does_not_hold_writer_lock_and_archive_uses_pinned_versions(tmp_path, monkeypatch):
    import proximic_ring.voice.history_export as module
    store = ModificationDatasetCollector(tmp_path / "data", "test")
    identity = add_record(store, 1, "原来的语音")
    before = snapshot(store.user_root)
    original = module._record_snapshot
    @contextmanager
    def changing_snapshot(*args):
        with original(*args) as value:
            done = threading.Event()
            def update():
                store.record_audio(1, np.ones(320, dtype=np.float32))
                store.record_application(session_id=1, action="undone", final_text="新的正文")
                done.set()
            writer = threading.Thread(target=update)
            writer.start()
            assert done.wait(2), "Export must release the store lock before compression"
            writer.join()
            yield value
    monkeypatch.setattr(module, "_record_snapshot", changing_snapshot)
    result = export_history(store, tmp_path / "exports")
    with ZipFile(result["path"]) as archive:
        for name in ("record.json", "audio.wav", "events.jsonl"):
            relative = f"interactions/{identity}/{name}"
            assert archive.read(relative) == before[relative]
    assert snapshot(store.user_root) != before


@pytest.mark.parametrize("reason", ["cancel", "disk_full"])
def test_interrupted_exports_leave_no_partial_archive_and_do_not_change_source(tmp_path, monkeypatch, reason):
    store = ModificationDatasetCollector(tmp_path / "data", "test")
    add_record(store, 1)
    add_record(store, 2)
    before = snapshot(store.user_root)
    cancel = threading.Event()
    if reason == "disk_full":
        original = ZipFile.open
        def failing_open(self, name, mode="r", *args, **kwargs):
            if mode == "w":
                raise OSError("disk full")
            return original(self, name, mode, *args, **kwargs)
        monkeypatch.setattr(ZipFile, "open", failing_open)
    with pytest.raises(ExportCancelled if reason == "cancel" else OSError):
        export_history(store, tmp_path / "exports", cancel=cancel, on_progress=lambda *_: cancel.set())
    assert list((tmp_path / "exports").iterdir()) == []
    assert snapshot(store.user_root) == before


def test_empty_store_reports_no_history_and_repeated_exports_are_distinct(tmp_path):
    store = ModificationDatasetCollector(tmp_path / "data", "test")
    with pytest.raises(ValueError, match="暂无"):
        export_history(store, tmp_path / "exports")
    add_record(store, 1)
    first = export_history(store, tmp_path / "exports")
    data = Path(first["path"]).read_bytes()
    second = export_history(store, tmp_path / "exports")
    assert first["path"] != second["path"] and Path(first["path"]).read_bytes() == data
