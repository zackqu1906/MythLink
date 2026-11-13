import io
import json
from zipfile import ZipFile

from proximic_ring.diagnostics.export_scope import ExportScope, attributions, filtered_associations
from proximic_ring.diagnostics.diagnostic_log import RotatingDiagnosticLog
from proximic_ring.diagnostics.log_export import LogRange, export_diagnostics
from proximic_ring.voice.history_export import export_history
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_history_management import add_record, associate


def record(store, session, application, text):
    identity = add_record(store, session, text)
    store.record_application(session_id=session, action="applied", mode="dictation", application=application, final_text=text)
    return identity


def test_complete_record_media_and_associations_obey_export_application_allowlist(tmp_path):
    store = ModificationDatasetCollector(tmp_path / "data", "test")
    first = record(store, 1, "WeChat", "included-transcript")
    second = record(store, 2, "Safari", "excluded-secret")
    third = record(store, 3, "com.tencent.xinWeChat", "another-included")
    keep = associate(store, first, third)
    associate(store, first, second)
    path = export_history(store, tmp_path / "exports", scope=ExportScope(frozenset({"com.tencent.xinwechat"})))['path']
    with ZipFile(path) as archive:
        rows = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
        assert {r["interaction_id"] for r in rows} == {first, third}
        assert archive.read(f"interactions/{first}/audio.wav") == (store._interaction_dir(first) / "audio.wav").read_bytes()
        assert archive.read(f"interactions/{first}/record.json") == store._interaction_path(first).read_bytes()
        assert [json.loads(line)["association_id"] for line in archive.read("associations.jsonl").splitlines()] == [keep]
        assert not any(second in n for n in archive.namelist())
        assert b"excluded-secret" not in b"".join(archive.read(n) for n in archive.namelist())


def test_mixed_event_ownership_unknown_and_corrupt_sidecars_are_excluded(tmp_path):
    store = ModificationDatasetCollector(tmp_path / "data", "test")
    good = record(store, 1, "Safari", "allowed")
    mixed = record(store, 2, "WeChat", "earlier secret")
    store.record_application(session_id=2, action="applied", mode="dictation", application="Safari")
    unknown = add_record(store, 3)
    corrupt = record(store, 4, "Safari", "corrupt")
    with (store._interaction_dir(corrupt) / "events.jsonl").open("a") as stream:
        stream.write("{broken\n")
    result = export_history(store, tmp_path / "exports", scope=ExportScope(frozenset({"com.apple.safari"})))
    with ZipFile(result["path"]) as archive:
        assert [json.loads(line)["interaction_id"] for line in archive.read("records.jsonl").splitlines()] == [good]
        assert json.loads(archive.read("manifest.json"))["filtered_record_count"] == 3
        assert not any(i in str(archive.namelist()) for i in [mixed, unknown, corrupt])


def test_domains_use_structured_provenance_never_ocr_text_or_partial_names():
    scope = ExportScope(frozenset({"com.google.chrome|outlook.office.com"}))
    assert scope.accepts({"application": "Google Chrome", "website": "outlook.office.com"})
    assert not scope.accepts({"application": "Chrome", "ocr": "outlook.office.com"})
    assert not scope.accepts({"application": "Chrome", "website": "outlook.office.com.evil.test"})
    assert not scope.accepts({"application": "Chrome", "website": "outlook.office.com", "other": {"application": "Safari"}})
    assert not scope.accepts({})
    mixed = {"application": "Chrome", "website": "outlook.office.com", "foreground_application": "Safari"}
    selected_sites = ExportScope(frozenset({"com.google.chrome|outlook.office.com", "com.apple.safari|outlook.office.com"}))
    assert not selected_sites.accepts(mixed)  # A target's website is not evidence for another foreground app.


def test_diagnostics_exclude_unowned_logs_config_and_raw_ime_traces(tmp_path):
    writer = RotatingDiagnosticLog(tmp_path / "logs/diagnostic.log")
    writer.record("allowed-error\nallowed-stack", attribution={"application": "Safari"})
    writer.record("excluded-secret", attribution={"application": "WeChat"})
    writer.record("unowned-secret")
    writer.record("mixed-secret", attribution={"application": "Safari", "front_application": "WeChat"})
    ime = tmp_path / "ime"
    ime.mkdir()
    (ime / "caret-trace-test.jsonl").write_text('{"secret":"native-secret"}')
    result = export_diagnostics(writer, tmp_path / "result.zip", LogRange.create("all"),
        state={"settings": {"private_path": "configuration-secret"}}, ime_root=ime,
        scope=ExportScope(frozenset({"com.apple.safari"})))
    with ZipFile(result["path"]) as archive:
        text = b"".join(archive.read(name) for name in archive.namelist())
        assert b"allowed-error" in text and b"allowed-stack" in text
        assert not any(word in text for word in [b"excluded-secret", b"unowned-secret", b"mixed-secret", b"native-secret", b"configuration-secret"])
        assert json.loads(archive.read("manifest.json"))["log_coverage"]["excluded_scope_lines"] == 3


def test_association_member_list_is_part_of_filter():
    row = {"chosen": {"interaction_id": "a"}, "member_interaction_ids": ["a", "b"]}
    assert filtered_associations(json.dumps(row).encode(), {"a"}) == (b"", 1)
