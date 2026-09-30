"""Single-entry removal affects saved history, never other/live interactions."""
from pathlib import Path
from types import SimpleNamespace
import json
import threading

import numpy as np
import pytest

from proximic_ring.modification_dataset import ModificationDatasetCollector
from proximic_ring.text_processing import LLMSettings, TextProcessingRequest


def add_record(store, session, text="语音记录"):
    store.record_audio(session, np.zeros(160, dtype=np.float32))
    store.record_asr_update(SimpleNamespace(session_id=session, text=text, is_final=True,
        error=None, backend="test", model="test", latency_s=0.1, audio_duration_s=0.01))
    store.record_application(session_id=session, action="applied", mode="dictation", final_text=text)
    return store.interaction_id_for_session(session)


def associate(store, first, second):
    return store.create_association(kind="asr", subtype="dictation_retry",
        chosen={"interaction_id": first}, rejected=[{"interaction_id": second}], source="manual")


def test_delete_removes_only_selected_files_links_and_cached_row(tmp_path):
    store = ModificationDatasetCollector(tmp_path, "test")
    first, second, third = [add_record(store, n, f"语音 {n}") for n in (1, 2, 3)]
    removed_link = associate(store, first, second)
    kept_link = associate(store, second, third)
    rows = store.load_entries()
    third_before = store._interaction_path(third).read_bytes()
    second_audio = (store._interaction_dir(second) / "audio.wav").read_bytes()
    assert store.delete_entry(first)
    assert not store._interaction_dir(first).exists()
    assert not (store.user_root / ".deleted-history" / first).exists()
    assert store.load_entries() == [row for row in rows if row["interactionId"] != first]
    assert store._interaction_path(third).read_bytes() == third_before
    assert (store._interaction_dir(second) / "audio.wav").read_bytes() == second_audio
    assert [row["association_id"] for row in store.load_associations()] == [kept_link]
    second_record = json.loads(store._interaction_path(second).read_text())
    assert removed_link not in second_record["association_ids"]
    assert [row["association_id"] for row in second_record["association_memberships"]] == [kept_link]
    assert ModificationDatasetCollector(tmp_path, "test").load_entries() == store.load_entries()
    assert not store.delete_entry(first)  # Repeated confirmation cannot delete a neighbour.


def test_late_callbacks_cannot_recreate_deleted_record_or_change_other_record(tmp_path):
    saved = []
    store = ModificationDatasetCollector(tmp_path, "test", on_saved=saved.append)
    first, second = add_record(store, 1), add_record(store, 2)
    request = TextProcessingRequest(request_id=11, session_id=1, mode="edit", raw_text="修改",
        target_text="原文", settings=LLMSettings(enabled=True, model="test"))
    store.record_text_request(request)
    store.delete_entry(first)
    saved.clear()
    other = store._interaction_path(second).read_bytes()
    store.record_audio(1, np.ones(160, dtype=np.float32))
    store.record_asr_update(SimpleNamespace(session_id=1, text="延迟文本", is_final=True))
    store.record_inline_edit_intent(1)
    store.record_text_request(request)
    store.record_llm_result(11, SimpleNamespace(session_id=1, mode="edit", final_text="修改后", latency_s=0))
    store.record_application(session_id=1, action="undone", mode="edit")
    store.record_application(interaction_id=first, action="native_undo_sent")
    store.record_imu_samples(1, [{"x": 0}])
    store.record_runtime_event("[ASR] END", session_id=1)
    store.record_near_field_label(1, label="positive", source="test")
    store.record_asr_label(1, label="negative", source="test")
    assert not saved and not store._interaction_dir(first).exists()
    assert store._interaction_path(second).read_bytes() == other
    assert store.association_member_for_session(1) == {}
    with pytest.raises(ValueError):
        associate(store, first, second)
    store.reset_runtime()
    next_id = add_record(store, 1, "新的会话")
    assert next_id != first
    assert {row["interactionId"] for row in store.load_entries()} == {second, next_id}


@pytest.mark.parametrize("value", ["", "../elsewhere", "/tmp/other", "interaction_fake", "interaction_2026-01-01_00-00-00-000/../other"])
def test_delete_rejects_non_record_identity(tmp_path, value):
    store = ModificationDatasetCollector(tmp_path, "test")
    identity = add_record(store, 1)
    with pytest.raises(ValueError):
        store.delete_entry(value)
    assert store._interaction_path(identity).exists()


def test_delete_never_follows_directory_symlink(tmp_path):
    store = ModificationDatasetCollector(tmp_path, "test")
    target = tmp_path / "outside"
    target.mkdir()
    marker = target / "important.txt"
    marker.write_text("keep")
    store.interactions_root.mkdir(parents=True)
    identity = "interaction_2026-01-01_00-00-00-000"
    store._interaction_dir(identity).symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        store.delete_entry(identity)
    assert marker.read_text() == "keep"


def test_metadata_failure_restores_record_and_related_memberships(tmp_path, monkeypatch):
    store = ModificationDatasetCollector(tmp_path, "test")
    first, second = add_record(store, 1), add_record(store, 2)
    associate(store, first, second)
    before = {str(path.relative_to(store.user_root)): path.read_bytes()
              for path in store.user_root.rglob("*") if path.is_file()}
    write = store._write_jsonl
    def fail_index(path, rows):
        if path == store.association_index_path:
            raise OSError("test write failure")
        return write(path, rows)
    monkeypatch.setattr(store, "_write_jsonl", fail_index)
    with pytest.raises(OSError):
        store.delete_entry(first)
    after = {str(path.relative_to(store.user_root)): path.read_bytes()
             for path in store.user_root.rglob("*") if path.is_file()}
    assert before == after
    assert not store.is_deleted(first)


def test_cleanup_failure_stays_hidden_and_can_retry(tmp_path, monkeypatch):
    import proximic_ring.modification_dataset as module
    store = ModificationDatasetCollector(tmp_path, "test")
    identity = add_record(store, 1)
    assert len(store.load_entries()) == 1
    remove = module.shutil.rmtree
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(OSError):
        store.delete_entry(identity)
    assert store.is_deleted(identity) and store.load_entries() == []
    assert ModificationDatasetCollector(tmp_path, "test").load_entries() == []
    monkeypatch.setattr(module.shutil, "rmtree", remove)
    assert store.delete_entry(identity)
    assert not (store.user_root / ".deleted-history" / identity).exists()


def test_delete_serializes_with_audio_writer(tmp_path, monkeypatch):
    store = ModificationDatasetCollector(tmp_path, "test")
    identity = add_record(store, 1)
    started, release, deleted = threading.Event(), threading.Event(), threading.Event()
    write = store._write_wav
    errors = []
    def paused_write(path, audio):
        started.set()
        assert release.wait(2)
        write(path, audio)
    monkeypatch.setattr(store, "_write_wav", paused_write)
    def run(action):
        try:
            action()
        except BaseException as exc:
            errors.append(exc)
    writer = threading.Thread(target=lambda: run(lambda: store.record_audio(1, np.zeros(160))))
    remover = threading.Thread(target=lambda: run(lambda: (store.delete_entry(identity), deleted.set())))
    writer.start()
    assert started.wait(2)
    remover.start()
    assert not deleted.wait(0.03)
    release.set()
    writer.join(2); remover.join(2)
    assert not errors and deleted.is_set()
    assert not store._interaction_dir(identity).exists()


def test_unlimited_history_can_search_beyond_previous_hundred(tmp_path):
    store = ModificationDatasetCollector(tmp_path, "test")
    for session in range(1, 103):
        add_record(store, session, str(session))
    assert len(store.load_entries()) == 100
    assert len(store.load_entries(limit=None)) == 102
