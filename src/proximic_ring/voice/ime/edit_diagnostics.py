"""Text-free IMK edit evidence. This module never reads or controls an editor."""

import math


def _metadata(values, *, integers=(), booleans=(), ranges=(), durations=(), enums=None):
    if not isinstance(values, dict):
        return {}
    result = {key: values[key] for key in integers if type(values.get(key)) is int}
    result.update({key: values[key] for key in booleans if type(values.get(key)) is bool})
    for key in ranges:
        value = values.get(key)
        if isinstance(value, list) and len(value) == 2 and all(type(n) is int for n in value):
            result[key] = value
    for key in durations:
        value = values.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            result[key] = round(value, 3)
    for key, allowed in (enums or {}).items():
        value = values.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    return result


def edit_request_metadata(values):
    result = _metadata(values,
        integers=("schema", "model_source_characters", "instruction_characters", "candidate_characters"),
        booleans=("source_complete", "document_access", "native_selection_path", "retry"),
        ranges=("source_range", "original_selection", "original_marked"),
        durations=("model_wait_ms",))
    if isinstance(values, dict):
        for key, value in edit_readback_metadata(values.get("verification")).items():
            result[f"verification_{key}"] = value
    return result


_CHARACTER_KINDS = {"end", "tab", "lf", "cr", "space", "nbsp", "line_separator",
                    "paragraph_separator", "object", "zero_width", "surrogate", "other"}


def body_read_metadata(values):
    """Keep exact-read provenance, including the failed IMK read before AX."""
    def one(read):
        return _metadata(read,
            integers=("returned_characters",),
            booleans=("text_queried", "ax_attempted", "read_selection_stable", "empty_context_from_imk"),
            ranges=("requested_range", "actual_range"),
            enums={"query": {"range", "probe", "snapshot"},
                   "read_source": {"imk", "ax", "unavailable"},
                   "snapshot_body_source": {"ax", "unavailable"}})
    result = one(values)
    if isinstance(values, dict):
        result.update({f"imk_{key}": value for key, value in one(values.get("imk_read")).items()})
    return result


def edit_readback_metadata(values):
    result = _metadata(values,
        integers=("expected_characters", "observed_characters", "source_expected_characters", "source_observed_characters",
                  "selected_characters", "source_common_prefix", "source_common_suffix", "instruction_characters",
                  "candidate_characters", "committed_caret", "tail_characters", "tail_utf16",
                  "caret_restore_limit", "key_count", "replacement_expected_characters",
                  "replacement_observed_characters", "model_characters", "baseline_characters",
                  "postwrite_caret_delta", "postwrite_key_count"),
        booleans=("text_matches", "key_acknowledged", "instruction_matches", "write_sent", "grapheme_boundary", "replacement_text_matches",
                  "selection_matches", "selection_read_available", "imk_selection_matches", "source_read_available",
                  "native_text_verified", "baseline_read_available", "baseline_stable", "postwrite_ax_read_available",
                  "postwrite_move_attempted", "postwrite_key_requested", "postwrite_key_acknowledged"),
        ranges=("source_range", "target_selection", "expected_selection", "observed_selection", "expected_marked", "observed_marked",
                "selection_target", "selection_caret", "verified_selection", "written_range", "baseline_range", "baseline_caret",
                "postwrite_imk_target", "postwrite_imk_selection", "postwrite_imk_after_ax", "postwrite_ax_selection",
                "postwrite_imk_after_move", "postwrite_ax_after_move", "postwrite_ax_before_move"),
        durations=("operation_ms", "post_write_ms", "context_prepare_ms"),
        enums={
            "operation_stage": {"context_committed", "context_whole", "context_caret", "capture", "written",
                                "sentence_selected", "sentence_written", "native_written", "native_caret", "native_restore_caret", "verify",
                                "postwrite_caret", "postwrite_baseline"},
            "ack_source": {"text", "selection_collapse", "native_selection", "committed_range", "composition_end",
                           "empty_noop", "imk_caret", "post_caret_imk", "post_caret_ax"},
            "undo_baseline_source": {"post_caret_imk", "post_caret_ax"},
            "baseline_read_method": {"imk", "ax", "unavailable"},
            "source_read_method": {"imk", "ax", "unavailable"},
            "selection_method": {"ax", "host_keyboard", "existing_caret"},
            "selection_verifier": {"imk", "ax"},
            "ax_fallback_reason": {"ax_driver_unavailable", "ax_permission_denied", "ax_focus_unavailable", "ax_range_unavailable",
                                   "ax_imk_caret_disagreement", "ax_range_not_writable", "ax_range_encoding_failed", "ax_set_rejected"},
            "postwrite_caret_status": {"moved_and_verified", "moved_but_disagreed", "ax_unavailable", "already_matched", "mismatch"},
            "postwrite_move_method": {"host_arrow"},
            "postwrite_key_command": {"caret_backward", "caret_forward"},
            "source_match": {"exact", "terminalParagraphs", "different"},
            "source_relation": {"unavailable", "equal", "snapshot_prefix", "selected_prefix", "different"},
            "source_first_expected_kind": _CHARACTER_KINDS,
            "source_first_observed_kind": _CHARACTER_KINDS,
            "key_command": {"select_all", "select_to_start", "select_previous", "delete", "caret_from_end",
                            "caret_to_end", "caret_backward", "caret_forward"},
            "failure_code": {"unconfirmed", "key_delivery_failed", "caret_restore_limit_or_boundary",
                             "operation_timeout", "caret_outside_document", "instruction_mismatch",
                             "source_mismatch_restore_limit", "replacement_source_mismatch", "source_mismatch",
                             "selection_state_changed", "prefix_fallback_range_unsupported", "target_changed",
                             "ax_target_pid_mismatch", "ax_caret_changed", "ax_set_outcome_unknown",
                             "source_read_unavailable", "prefix_caret_unconfirmed", "prewrite_selection_changed",
                             "invalid_result_caret", "postwrite_tail_boundary_unavailable", "body_read_unavailable",
                             "postwrite_baseline_range_unavailable", "postwrite_baseline_caret_changed",
                             "postwrite_baseline_unavailable", "postwrite_baseline_tail_changed", "postwrite_baseline_unstable",
                             "postwrite_imk_caret_changed", "postwrite_caret_read_unavailable", "postwrite_caret_sample_changed",
                             "postwrite_caret_not_aligned", "postwrite_caret_delta_invalid", "postwrite_key_ack_timeout"},
        })
    if isinstance(values, dict):
        for key in ("baseline_read", "source_imk_read"):
            result.update({f"{key}_{field}": value for field, value in body_read_metadata(values.get(key)).items()})
    return result


def snapshot_metadata(values):
    return _metadata(values, booleans=("context_complete",), durations=("snapshot_ms",),
        enums={"context_origin": {"document", "confirmed_hint", "bounded", "unavailable"}})
