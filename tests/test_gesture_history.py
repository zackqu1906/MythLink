import json
from zipfile import ZipFile

from proximic_ring.gestures.history import GestureHistory, export_gestures
from proximic_ring.diagnostics.export_scope import ExportScope
from proximic_ring.diagnostics.log_export import LogRange


def test_history_merges_one_attempt_without_diagnostics_or_implicit_positive_labels(tmp_path):
    store = GestureHistory(tmp_path / "history.sqlite3")
    try:
        store.observe("one", gesture="tap", application="Safari", status="observed", stack="secret", confidence=.99)
        store.observe("one", focus="text", action="开始语音", status="triggered")
        store.observe("one", gesture="tap", status="observed")
        row, = store.records()
        assert row["status"] == "triggered" and row["feedback"] == ""
        assert not {"stack", "confidence", "screenshot", "ocr"} & row.keys()
        created = row["created_at"]
        store.feedback("one", "wrong_action", "结束语音")
        store.observe("one", status="confirmed")
        row, = store.records()
        assert row["feedback"] == "wrong_action" and row["expected_action"] == "结束语音"
        assert row["created_at"] == created
    finally:
        store.close()
    reopened = GestureHistory(tmp_path / "history.sqlite3")
    try:
        assert reopened.records()[0]["action"] == "开始语音"
        reopened.delete("one")
        assert reopened.records() == []
    finally:
        reopened.close()


def test_full_gesture_export_filters_apps_and_websites_and_preserves_annotations(tmp_path):
    store = GestureHistory(tmp_path / "history.sqlite3")
    try:
        store.observe("included", gesture="swipe-up", application="Chrome", website="outlook.office.com", action="Enter", status="triggered")
        store.observe("excluded", gesture="tap", application="Chrome", website="private.example", action="secret-action")
        store.observe("unknown", gesture="tap")
        store.feedback("included", "wrong_action", "=desired-action")
        result = export_gestures(store, tmp_path / "exports", scope=ExportScope(frozenset({"com.google.chrome|outlook.office.com"})), selection=LogRange.create("all"))
        with ZipFile(result["path"]) as archive:
            row, = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
            assert row["record_id"] == "included" and row["expected_action"] == "=desired-action"
            assert "'=desired-action" in archive.read("summary.csv").decode("utf-8-sig")
            assert "secret-action" not in archive.read("records.jsonl").decode()
    finally:
        store.close()
