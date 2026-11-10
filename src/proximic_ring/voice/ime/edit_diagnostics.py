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


def edit_readback_metadata(values):
    return _metadata(values,
        integers=("expected_characters", "observed_characters", "source_expected_characters", "source_observed_characters",
                  "selected_characters", "source_common_prefix", "source_common_suffix", "instruction_characters",
                  "candidate_characters", "committed_caret", "tail_characters", "tail_utf16",
                  "caret_restore_limit", "key_count", "replacement_expected_characters",
                  "replacement_observed_characters"),
        booleans=("text_matches", "key_acknowledged", "instruction_matches", "write_sent", "grapheme_boundary", "replacement_text_matches"),
        ranges=("source_range", "target_selection", "expected_selection", "observed_selection", "expected_marked", "observed_marked"),
        durations=("operation_ms", "post_write_ms", "context_prepare_ms"),
        enums={
            "operation_stage": {"context_committed", "context_whole", "context_caret", "capture", "written",
                                "sentence_selected", "sentence_written", "native_written", "native_caret", "native_restore_caret", "verify"},
            "ack_source": {"text", "selection_collapse", "native_selection", "committed_range", "composition_end"},
            "source_match": {"exact", "terminalParagraphs", "different"},
            "source_relation": {"unavailable", "equal", "snapshot_prefix", "selected_prefix", "different"},
            "source_first_expected_kind": _CHARACTER_KINDS,
            "source_first_observed_kind": _CHARACTER_KINDS,
            "key_command": {"select_all", "select_to_start", "select_previous", "delete", "caret_from_end",
                            "caret_to_end", "caret_backward", "caret_forward"},
            "failure_code": {"unconfirmed", "key_delivery_failed", "caret_restore_limit_or_boundary",
                             "operation_timeout", "caret_outside_document", "instruction_mismatch",
                             "source_mismatch_restore_limit", "replacement_source_mismatch"},
        })


def snapshot_metadata(values):
    return _metadata(values, booleans=("context_complete",), durations=("snapshot_ms",),
        enums={"context_origin": {"document", "confirmed_hint", "bounded", "unavailable"}})
