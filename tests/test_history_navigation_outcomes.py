"""History rows must follow the current step, including return through an edit instruction."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from proximic_ring.text_processing import LLMSettings, TextProcessingRequest, TextProcessingResult
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector


def make_edit(store, session=1, *, applied=True):
    store.record_audio(session, np.zeros(160, dtype=np.float32))
    store.record_asr_update(SimpleNamespace(session_id=session, text="扩写", is_final=True, error=None))
    store.record_inline_edit_intent(session)
    request = TextProcessingRequest(session, session, "edit", "扩写", LLMSettings(), "原文")
    store.record_text_request(request)
    store.record_llm_result(request.request_id, TextProcessingResult(request.request_id, session, "edit", "扩写", "结果", .1, True))
    if applied:
        store.record_application(session_id=session, action="applied", mode="edit", final_text="结果", method="input_method")
    return request


def state(store, expected, candidate="", session=1):
    identity = store.interaction_id_for_session(session)
    entry = next(row for row in store.load_entries() if row["interactionId"] == identity)
    assert entry["outcome"] == expected
    assert entry["candidateText"] == candidate
    assert entry["candidateAvailable"] is (expected == "applied")
    return entry


@pytest.mark.parametrize("delete_instruction", [False, True])
def test_edit_instruction_remains_undone_when_restored_again(tmp_path, delete_instruction):
    store = ModificationDatasetCollector(tmp_path, "test")
    make_edit(store)
    state(store, "applied", "结果")
    # Left: remove model output, restore the spoken edit instruction.
    store.record_application(session_id=1, action="applied", mode="dictation", final_text="扩写", method="input_method")
    state(store, "undone")
    if delete_instruction:
        store.record_application(session_id=1, action="undone", mode="dictation", final_text="", method="input_method")
        state(store, "undone")
    # Next restores the instruction stage (or a repeat receipt confirms that stage).
    store.record_application(session_id=1, action="applied", mode="dictation", final_text="扩写", method="input_method")
    state(store, "undone")
    store.record_application(session_id=1, action="applied", mode="edit", final_text="结果", method="input_method")
    state(store, "applied", "结果")


def test_three_dictations_undo_and_next_update_only_their_original_record(tmp_path):
    from test_history_management import add_record
    store = ModificationDatasetCollector(tmp_path, "test")
    for session, text in enumerate(("甲", "乙", "丙"), 1):
        add_record(store, session, text)
    for session in (3, 2, 1):
        store.record_application(session_id=session, action="undone", mode="dictation", final_text="", method="input_method")
        for check in (1, 2, 3):
            state(store, "applied" if check < session else "undone", ("甲乙丙"[check - 1] if check < session else ""), check)
    for session, text in enumerate(("甲", "乙", "丙"), 1):
        store.record_application(session_id=session, action="applied", mode="dictation", final_text=text, method="input_method")
        for check in (1, 2, 3):
            state(store, "applied" if check <= session else "undone", ("甲乙丙"[check - 1] if check <= session else ""), check)
    assert ModificationDatasetCollector(tmp_path, "test").load_entries() == store.load_entries()


def test_polish_undo_next_show_the_current_text_not_the_last_model_output(tmp_path):
    from test_history_management import add_record
    store = ModificationDatasetCollector(tmp_path, "test")
    add_record(store, 1, "原句")
    request = TextProcessingRequest(2, 1, "polish", "原句", LLMSettings())
    store.record_text_request(request)
    store.record_llm_result(2, TextProcessingResult(2, 1, "polish", "原句", "润色句", .1, True))
    for action, text in [("applied", "润色句"), ("applied", "原句"), ("undone", ""),
                         ("applied", "原句"), ("applied", "润色句")]:
        store.record_application(session_id=1, action=action, mode="dictation", final_text=text, method="input_method")
        state(store, action, text if action == "applied" else "")


@pytest.mark.parametrize("result", ["", "重做的新结果"])
def test_retry_success_undo_and_next_keep_new_result_and_reexecuted_tag(tmp_path, result):
    store = ModificationDatasetCollector(tmp_path, "test")
    first = make_edit(store)
    store.record_application(session_id=1, action="applied", mode="dictation", final_text="扩写", method="input_method")
    state(store, "undone")
    request = replace(first, request_id=2, reexecuted=True)
    store.record_text_request(request)
    store.record_llm_result(2, TextProcessingResult(2, 1, "edit", "扩写", result, .1, True))
    # A generated answer is not an applied answer until native readback confirms it.
    state(store, "undone")
    for action, mode, text, expected in [
        ("applied", "edit", result, "applied"),
        ("applied", "dictation", "扩写", "undone"),
        ("undone", "dictation", "", "undone"),
        ("applied", "dictation", "扩写", "undone"),
        ("applied", "edit", result, "applied"),
    ]:
        store.record_application(session_id=1, action=action, mode=mode, final_text=text, method="input_method")
        row = state(store, expected, result if expected == "applied" else "")
        assert row["reexecuted"] is True
        assert row["candidateEmpty"] is (expected == "applied" and result == "")
    assert ModificationDatasetCollector(tmp_path, "test").load_entries() == store.load_entries()


@pytest.mark.parametrize("error", ["模型超时", "输入框未确认选区或替换结果，已停止本次操作"])
def test_retry_failure_is_not_shown_as_success_or_hidden_by_old_undo(tmp_path, error):
    store = ModificationDatasetCollector(tmp_path, "test")
    first = make_edit(store)
    store.record_application(session_id=1, action="applied", mode="dictation", final_text="扩写", method="input_method")
    store.record_text_request(replace(first, request_id=2, reexecuted=True))
    store.record_application(session_id=1, action="applied", mode="dictation", final_text="扩写", method="input_method", error=error)
    row = state(store, "apply_failed")
    assert row["error"] == error and row["reexecuted"]
    assert ModificationDatasetCollector(tmp_path, "test").load_entries()[0]["outcome"] == "apply_failed"


def test_next_restoring_a_never_applied_edit_instruction_preserves_cancellation(tmp_path):
    store = ModificationDatasetCollector(tmp_path, "test")
    make_edit(store, applied=False)
    store.record_application(session_id=1, action="applied", mode="dictation", final_text="扩写", method="input_method")
    state(store, "cancelled")
    store.record_application(session_id=1, action="undone", mode="dictation", final_text="", method="input_method")
    state(store, "undone")
    store.record_application(session_id=1, action="applied", mode="dictation", final_text="扩写", method="input_method")
    state(store, "cancelled")


@pytest.mark.parametrize("applied", [True, False])
def test_legacy_record_without_evidence_marker_uses_application_receipts(tmp_path, applied):
    store = ModificationDatasetCollector(tmp_path, "test")
    make_edit(store, applied=applied)
    store.record_application(session_id=1, action="applied", mode="dictation", final_text="扩写", method="input_method")
    store.record_application(session_id=1, action="undone", mode="dictation", final_text="", method="input_method")
    identity = store.interaction_id_for_session(1)
    record = store._interaction_data(identity)
    record["mode"].pop("edit_ever_applied", None)
    store._write_json(store._interaction_path(identity), record)
    store.record_application(session_id=1, action="applied", mode="dictation", final_text="扩写", method="input_method")
    state(store, "undone" if applied else "cancelled")
    assert store._interaction_data(identity)["mode"]["edit_ever_applied"] is applied
