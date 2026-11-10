"""Entry for explicit manual mode, alongside automatic_voice.py.

Gesture mapping is shared by both modes. This entry uses the operation already
selected by the user and does not invoke Clef or filter the shared raw OCR.
"""
from __future__ import annotations
import time
import json
from dataclasses import dataclass, field, replace
from ...desktop.desktop_target import DesktopTargetRef, DesktopTextSnapshot
from ...text_processing.model import (normalize_input_mode, validate_edit_target_text,
    INPUT_MODE_DICTATION, INPUT_MODE_EDIT, TextProcessingResult, InputModeRoutingResult)
from .voice_results import _is_explicit_emptying_edit_response


_DICTATION_CORRECTION_GRACE_MS = 700

@dataclass
class _AutoInteraction:
    """Legacy/manual switchable candidates; not the Clef automatic pipeline."""

    route_id: int
    session_id: int
    raw_text: str
    target: DesktopTargetRef | None
    selected_mode: str
    routed_at: float
    snapshot: DesktopTextSnapshot | None = None
    request_ids: dict[str, int] = field(default_factory=dict)
    results: dict[str, TextProcessingResult] = field(default_factory=dict)
    candidate_errors: dict[str, str] = field(default_factory=dict)
    preparing: bool = True
    classified: bool = False
    routed_by_model: bool = True
    prepared_at: float = field(default_factory=time.monotonic)


class ManualVoiceMixin:
    def _prepare_manual_candidates(
        self,
        text: str,
        session_id: int,
        mode: str,
        target: DesktopTargetRef | None,
    ) -> None:
        """Use the same two-candidate pipeline without invoking the router."""
        self._text_request_id += 1
        route_id = self._text_request_id
        selected = normalize_input_mode(mode)
        self._prepare_auto_candidates(
            route_id,
            int(session_id),
            str(text),
            target,
            selected,
        )
        interaction = self._active_auto_interaction
        if interaction is None:
            return
        interaction.selected_mode = selected
        interaction.classified = True
        interaction.routed_by_model = False
        interaction.routed_at = time.monotonic()
        self._present_auto_selection(target)


    def _prepare_auto_candidates(
        self,
        route_id: int,
        session_id: int,
        raw_text: str,
        target: DesktopTargetRef | None,
        fallback_mode: str,
    ) -> None:
        """Prepare the legacy/manual switchable candidates; Clef never calls this."""
        interaction = _AutoInteraction(
            route_id=route_id,
            session_id=session_id,
            raw_text=raw_text,
            target=target,
            selected_mode=normalize_input_mode(fallback_mode),
            routed_at=0.0,
        )
        self._active_auto_interaction = interaction
        # The ASR final is always a complete, immediately usable dictation
        # candidate. Optional LLM cleanup may replace it if it finishes inside
        # the short correction window, but must never delay injection.
        interaction.results[INPUT_MODE_DICTATION] = TextProcessingResult(
            request_id=0,
            session_id=session_id,
            mode=INPUT_MODE_DICTATION,
            raw_text=raw_text,
            final_text=raw_text,
            latency_s=0.0,
            used_llm=False,
        )
        if target is None:
            interaction.candidate_errors[INPUT_MODE_EDIT] = (
                "没有锁定外部文本框，无法按编辑指令执行"
            )
        else:
            try:
                adapter = self._desktop_target_adapter()
                capture = getattr(adapter, "capture_text_allowing_empty", None)
                if not callable(capture):
                    capture = adapter.capture_text
                # Dictation needs a pre-application snapshot even when the
                # field is empty. Without that empty snapshot the bottom stack
                # item falls back to the application's unrelated native undo
                # history and can reinsert a sentence removed moments earlier.
                interaction.snapshot = capture(target)
                validate_edit_target_text(interaction.snapshot.text)
                self._log_edit_target_snapshot(interaction.snapshot)
            except BaseException as exc:
                interaction.candidate_errors[INPUT_MODE_EDIT] = str(exc)

        for mode in (INPUT_MODE_DICTATION, INPUT_MODE_EDIT):
            if mode == INPUT_MODE_DICTATION and not self._llm_enabled:
                continue
            if mode == INPUT_MODE_EDIT and (
                interaction.snapshot is None
                or INPUT_MODE_EDIT in interaction.candidate_errors
            ):
                continue
            self._submit_text_processing(
                raw_text,
                session_id,
                mode,
                target_text=(
                    interaction.snapshot.text
                    if mode == INPUT_MODE_EDIT and interaction.snapshot is not None
                    else ""
                ),
                target=target,
                snapshot=(interaction.snapshot if mode == INPUT_MODE_EDIT else None),
                auto_route_id=route_id,
                update_interaction=False,
            )

        interaction.preparing = False


    def _start_auto_candidates(
        self,
        route: InputModeRoutingResult,
        target: DesktopTargetRef | None,
    ) -> None:
        """Select one of the two candidates already running for this utterance."""
        interaction = self._active_auto_interaction
        if interaction is None or interaction.route_id != route.request_id:
            # Compatibility for callers that deliver a route result directly.
            self._prepare_auto_candidates(
                route.request_id,
                route.session_id,
                route.raw_text,
                target,
                route.mode,
            )
            interaction = self._active_auto_interaction
        if interaction is None:
            return
        selected = normalize_input_mode(route.mode)
        interaction.selected_mode = selected
        interaction.classified = True
        interaction.routed_by_model = not bool(route.error)
        interaction.routed_at = time.monotonic()

        self._transcript_mode = ""
        if selected == INPUT_MODE_EDIT or self._llm_enabled:
            self._transcript_text = self._processing_status_text(
                selected, interaction.raw_text
            )
            self._transcript_final = False
            self._transcript_active = True
            self._set_interaction_state("processing")
            self.transcriptChanged.emit()
            self.interactionChanged.emit()
        if selected in interaction.results or selected in interaction.candidate_errors:
            self._present_auto_selection(target)
        if not self.textProcessing:
            self.textProcessingChanged.emit()


    def _accept_auto_candidate(
        self,
        route_id: int,
        result: TextProcessingResult,
        target: DesktopTargetRef | None,
    ) -> None:
        expected_switch = (int(route_id), result.mode)
        pending_switch_keys = [
            key
            for key, pending in self._pending_applied_mode_switches.items()
            if pending == expected_switch
        ]
        interaction = self._active_auto_interaction
        if interaction is None or interaction.route_id != int(route_id):
            matched = self._find_operation_for_route(route_id)
            interaction = matched[1].auto_context if matched is not None else None
        if interaction is None:
            # A late worker result must never leave the mode control spinning
            # forever after its operation was replaced or removed.
            if pending_switch_keys:
                for key in pending_switch_keys:
                    self._pending_applied_mode_switches.pop(key, None)
                self.interactionChanged.emit()
            return
        interaction.results[result.mode] = result
        matched = self._find_operation_for_route(route_id)
        matched_key = matched[0] if matched is not None else ""
        if pending_switch_keys:
            if result.error:
                # Both edit contracts can fail together. Resolve that terminal
                # result immediately instead of relying on a later QTimer to
                # clear the busy marker.
                for key in pending_switch_keys:
                    self._pending_applied_mode_switches.pop(key, None)
                operation = matched[1] if matched is not None else None
                if operation is not None:
                    reason = self._short_text(str(result.error), limit=34)
                    operation.mode_switch_error = reason
                    operation.summary = f"另一种处理方式不可用：{reason}"
                feedback_key = matched_key or pending_switch_keys[0]
                if (
                    operation is not None
                    and self._operation_target_is_focused(operation)
                ):
                    self._activate_operation_target(feedback_key)
                else:
                    self.interactionChanged.emit()
                return
            self._schedule_alternate_result(
                result.mode,
                target_key=matched_key or pending_switch_keys[0],
                route_id=route_id,
            )
            return
        if (
            interaction is self._active_auto_interaction
            and
            result.mode == interaction.selected_mode
            and not interaction.preparing
            and interaction.classified
        ):
            self._present_auto_selection(target)


    def _present_auto_selection(
        self, target: DesktopTargetRef | None = None
    ) -> None:
        interaction = self._active_auto_interaction
        if interaction is None:
            return
        mode = interaction.selected_mode
        error = interaction.candidate_errors.get(mode, "")
        if error:
            self._transcript_mode = ""
            self._transcript_text = (
                f"修改失败：{self._short_text(error, limit=48)}"
                if mode == INPUT_MODE_EDIT
                else "未能处理文本"
            )
            self._transcript_final = True
            self._transcript_active = True
            self._set_interaction_state("error")
            self.transcriptChanged.emit()
            self.interactionChanged.emit()
            self._append_log(f"文本处理失败：{error}")
            if self._recognition_enabled:
                if self._failed_edit_fallback_interaction() is not None:
                    self._set_status(
                        "修改未完成",
                        "原文本保持不变；可使用转换手势改为听写输入",
                        "error",
                    )
                else:
                    self._set_status("自动监听中", error, "running")
            # Processing is terminal. Keep the fallback visible until its
            # timeout or the next utterance, without blocking recognition.
            self._resume_recognition_after_interaction()
            return
        result = interaction.results.get(mode)
        if result is None:
            self._transcript_mode = ""
            self._transcript_text = self._processing_status_text(
                mode, interaction.raw_text
            )
            self._transcript_final = False
            self._set_interaction_state("processing")
            self.transcriptChanged.emit()
            self.interactionChanged.emit()
            if self._recognition_enabled:
                label = "修改" if mode == INPUT_MODE_EDIT else "输入"
                self._set_status(
                    "正在处理文本",
                    f"大模型正在处理{label}内容",
                    "starting",
                )
            return
        if mode == INPUT_MODE_DICTATION:
            commit_target = target if target is not None else interaction.target
            if not self._llm_enabled:
                # The raw ASR final is already complete.  With dictation LLM
                # disabled there is no correction candidate to wait for.
                self._dictation_commit_timer.stop()
                self._pending_dictation_result = None
                self._commit_input_text(result, commit_target)
                return
            self._pending_dictation_result = (
                result,
                commit_target,
            )
            self._transcript_mode = ""
            self._transcript_text = "正在处理文本"
            self._transcript_final = True
            self._set_interaction_state("processing")
            self.transcriptChanged.emit()
            self.interactionChanged.emit()
            elapsed_ms = int(
                max(0.0, time.monotonic() - interaction.routed_at) * 1000
            )
            remaining_ms = max(0, _DICTATION_CORRECTION_GRACE_MS - elapsed_ms)
            if remaining_ms:
                self._dictation_commit_timer.start(remaining_ms)
            else:
                self._commit_pending_dictation()
            return

        snapshot = interaction.snapshot
        if snapshot is None:
            self._reject_edit_request("修改目标快照已经失效")
            return
        parsed: object = None
        if result.model_output:
            try:
                parsed = json.loads(result.model_output)
            except (TypeError, json.JSONDecodeError):
                pass
        if result.error or result.final_text == result.target_text:
            self._begin_failed_edit_review(
                replace(
                    result,
                    error=result.error or "大模型未找到可可靠执行的修改",
                ),
                snapshot,
            )
            return
        self._begin_edit_review(
            result,
            snapshot,
            allow_empty=_is_explicit_emptying_edit_response(
                parsed, result.target_text
            ),
        )


    def _finish_auto_interaction(self, used_mode: str, *, retain: bool = False) -> None:
        interaction = self._active_auto_interaction
        if interaction is None:
            return
        if retain:
            self._active_auto_interaction = None
            self.interactionChanged.emit()
            return
        for mode, request_id in tuple(interaction.request_ids.items()):
            if request_id in self._pending_text_requests:
                self._pending_text_requests.discard(request_id)
                self._pending_interactions.pop(request_id, None)
                cancel_request = getattr(
                    self._text_processing_worker, "cancel_request", None
                )
                if callable(cancel_request):
                    cancel_request(request_id)
            if mode == INPUT_MODE_EDIT and mode != used_mode:
                try:
                    self._modification_dataset.abandon_request(
                        request_id, "alternate mode was not selected"
                    )
                except BaseException:
                    pass
        if used_mode != INPUT_MODE_EDIT and interaction.snapshot is not None:
            try:
                self._desktop_target_adapter().release_selection(
                    interaction.snapshot.target
                )
            except BaseException:
                pass
        self._active_auto_interaction = None
        self.interactionChanged.emit()
        self.textProcessingChanged.emit()

