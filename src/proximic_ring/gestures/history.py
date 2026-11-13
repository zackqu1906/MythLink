"""Small context/action samples. Diagnostic details remain in diagnostic.log."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import threading


def observe_gesture(owner, identity, **fields):
    try:
        store = getattr(owner, "_gesture_history", None)
        if store is not None:
            store.observe(identity, **fields)
    except Exception:
        pass  # History must never affect gesture delivery.


class GestureHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="GestureHistory")
        self._closed = False
        self._deleted = set()
        self._recent_attribution = {}
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS gestures (id TEXT PRIMARY KEY, created TEXT NOT NULL, data TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def observe(self, identity, **fields):
        if not identity:
            return
        # Copy only the learning schema, never retain diagnostic dictionaries.
        allowed = {"gesture", "application", "website", "scene", "focus", "voice_state", "action", "status", "foreground_application", "voice_interaction_id", "voice_application"}
        fields = {key: str(value)[:256] for key, value in fields.items() if key in allowed and value is not None and value != ""}
        with self._lock:
            if not self._closed:
                attribution = self._recent_attribution.pop(str(identity), {})
                attribution.update({key: value for key, value in fields.items()
                    if key in {"application", "foreground_application", "voice_application", "website"}})
                self._recent_attribution[str(identity)] = attribution
                while len(self._recent_attribution) > 256:
                    self._recent_attribution.pop(next(iter(self._recent_attribution)))
                future = self._worker.submit(self._update, str(identity), fields, datetime.now().astimezone().isoformat(timespec="milliseconds"))
                future.add_done_callback(self._report_write_error)

    def attribution(self, identity):
        """Use the same captured owner for log export, without disk IO on Qt."""
        with self._lock:
            return dict(self._recent_attribution.get(str(identity), {}))

    @staticmethod
    def _report_write_error(future):
        if future.exception() is not None:
            import logging
            logging.getLogger(__name__).error("Gesture history write failed: %s", future.exception())

    def _update(self, identity, fields, created):
        if identity in self._deleted:
            return
        with self._connect() as db:
            old = db.execute("SELECT data FROM gestures WHERE id=?", (identity,)).fetchone()
            if old is None and not fields.get("gesture"):
                return
            record = json.loads(old[0]) if old else dict(record_id=identity, created_at=created,
                gesture="", application="", website="", scene="", focus="unknown", voice_state="",
                action="", status="observed", feedback="", expected_action="", schema_version=1)
            # Late received/recognition events must not undo a completed route.
            if fields.get("status") == "observed" and old:
                fields = {k: v for k, v in fields.items() if k != "status"}
            record.update(fields)
            db.execute("INSERT OR REPLACE INTO gestures VALUES (?, ?, ?)",
                       (identity, record["created_at"], json.dumps(record, ensure_ascii=False)))

    def flush(self):
        with self._lock:
            if self._closed:
                return
            future = self._worker.submit(lambda: None)
        future.result(timeout=10)

    def records(self, limit=None):
        self.flush()
        with self._connect() as db:
            query = "SELECT data FROM gestures ORDER BY created DESC, id DESC"
            rows = db.execute(query + (" LIMIT ?" if limit is not None else ""), (limit,) if limit is not None else ())
            return [json.loads(row[0]) for row in rows]

    def feedback(self, identity, feedback, expected_action=""):
        if feedback not in {"", "correct", "accidental", "wrong_action"}:
            raise ValueError("无效标注")
        with self._lock:
            if self._closed:
                return
            future = self._worker.submit(self._feedback, identity, feedback, expected_action)
        future.result(timeout=10)

    def _feedback(self, identity, feedback, expected_action):
        with self._connect() as db:
            row = db.execute("SELECT data FROM gestures WHERE id=?", (identity,)).fetchone()
            if row is None:
                return
            record = json.loads(row[0])
            record.update(feedback=feedback, expected_action=str(expected_action)[:256])
            db.execute("UPDATE gestures SET data=? WHERE id=?", (json.dumps(record, ensure_ascii=False), identity))

    def delete(self, identity):
        with self._lock:
            if self._closed:
                return
            future = self._worker.submit(self._delete, identity)
        future.result(timeout=10)

    def _delete(self, identity):
        self._deleted.add(identity)
        with self._connect() as db:
            db.execute("DELETE FROM gestures WHERE id=?", (identity,))

    def close(self):
        with self._lock:
            self._closed = True
        self._worker.shutdown(wait=True)


def export_gestures(store, destination, *, scope, selection, cancel=None):
    import csv
    import io
    import os
    import tempfile
    import uuid
    from zipfile import ZipFile, ZIP_DEFLATED
    from ..voice.history_export import _check_cancel, _csv_value
    records = store.records()
    selected = [r for r in records if scope.accepts(r) and selection.overlaps(r)]
    if not selected:
        raise ValueError("所选应用和时间内没有可导出的手势记录")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    name = f"Mythlink-gestures-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.zip"
    with tempfile.TemporaryDirectory(prefix=".mythlink-gestures-", dir=destination) as temporary:
        archive = Path(temporary) / name
        with ZipFile(archive, "w", ZIP_DEFLATED) as output:
            lines, table = io.StringIO(), io.StringIO()
            columns = ["record_id", "created_at", "gesture", "application", "website", "scene", "focus", "voice_state", "action", "status", "feedback", "expected_action"]
            csv_writer = csv.DictWriter(table, columns, extrasaction="ignore")
            csv_writer.writeheader()
            for record in selected:
                _check_cancel(cancel)
                lines.write(json.dumps(record, ensure_ascii=False) + "\n")
                csv_writer.writerow({k: _csv_value(v) for k, v in record.items()})
            output.writestr("records.jsonl", lines.getvalue())
            output.writestr("summary.csv", table.getvalue().encode("utf-8-sig"))
            output.writestr("manifest.json", json.dumps(dict(format="mythlink-gesture-history", export_version=1,
                record_count=len(selected), filtered_count=len(records)-len(selected), range=selection.key,
                export_scope=scope.as_dict()), ensure_ascii=False, indent=2))
            output.writestr("README.txt", "每行是一次离散手势的场景与动作样本。record_id 可关联诊断日志。\n"
                "observed=已识别但未确认派发；triggered=已触发；confirmed=执行端已确认；not_triggered=未触发。\n"
                "触发不代表达到用户目的；未标注不代表正确。未知场景不补猜。无截图、正文或原始传感器数据。\n")
        _check_cancel(cancel)
        os.link(archive, destination / name)
    return dict(path=str(destination / name), record_count=len(selected), filtered_count=len(records)-len(selected), skipped_count=0, missing_file_count=0)
