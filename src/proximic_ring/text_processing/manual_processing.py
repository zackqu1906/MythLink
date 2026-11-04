"""Explicit user-selected processing: dictation, gesture edit and polish.

No intent classifier runs here. The caller has already selected the operation.
The existing fragment/full edit race remains exclusive to this processing path.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import threading
import time
from typing import Callable
from .edit_tool import (DEFAULT_EDIT_MODE, EDIT_MODE_FRAGMENT, EDIT_MODE_FULL,
                        EDIT_MODE_RACE, normalize_edit_mode)
from .model import (INPUT_MODE_EDIT, LLMBranchTrace, LLM_PROVIDER_LOCAL, LLMSettings,
                    normalize_llm_provider, normalize_input_mode, validate_edit_target_text)
from .prompts import DICTATION_PROMPT, EDIT_FRAGMENT_PROMPT, EDIT_FULL_TEXT_PROMPT, POLISH_PROMPT
from .processing_common import LLMResponseProcessingError, _text_output_budget


class ManualTextProcessingMixin:
    def polish_with_trace(self, text: str, settings: LLMSettings) -> tuple[str, str]:
        """Polish only this utterance; never enter instruction editing/racing."""
        settings.validate()
        if not settings.enabled:
            raise ValueError("润色需要启用大模型")
        raw_text = str(text or "").strip()
        if not raw_text:
            raise ValueError("本句没有可润色的文字")
        if normalize_llm_provider(settings.provider) == LLM_PROVIDER_LOCAL and settings.local_auto_start:
            self._ensure_local_server(settings)
        response = self._request_chat(
            settings, system_prompt=POLISH_PROMPT,
            user_content=f"<语音转写>\n{raw_text}\n</语音转写>",
            temperature=0.2, max_tokens=_text_output_budget(settings), edit_tool=None,
        )
        output = self._model_output_text(response)
        if not isinstance(response, str) or not response.strip():
            raise LLMResponseProcessingError("润色未返回正文，已保留听写", output)
        return response.strip(), output

    def process(
        self,
        text: str,
        mode: str,
        settings: LLMSettings,
        target_text: str = "",
        edit_mode: str = DEFAULT_EDIT_MODE,
    ) -> str:
        final_text, _model_output = self.process_with_trace(
            text,
            mode,
            settings,
            target_text,
            edit_mode,
        )
        return final_text

    def process_with_trace(
        self,
        text: str,
        mode: str,
        settings: LLMSettings,
        target_text: str = "",
        edit_mode: str = DEFAULT_EDIT_MODE,
    ) -> tuple[str, str]:
        """Return both the applied text and the model's unmodified response."""

        final_text, model_outputs = self.process_with_attempts(
            text,
            mode,
            settings,
            target_text,
            edit_mode,
        )
        return final_text, model_outputs[-1] if model_outputs else ""

    def process_with_collection_trace(
        self,
        text: str,
        mode: str,
        settings: LLMSettings,
        target_text: str = "",
        edit_mode: str = DEFAULT_EDIT_MODE,
        *,
        on_collection_complete: (
            Callable[[tuple[LLMBranchTrace, ...], str], None] | None
        ) = None,
    ) -> tuple[str, str, tuple[LLMBranchTrace, ...], str]:
        """Return the first valid edit and optionally finish traces in background.

        Without a callback this method remains synchronous for diagnostics.
        The UI supplies a callback so its accepted candidate is returned
        immediately while the slower branch completes the persisted trace.
        """

        normalized_mode = normalize_input_mode(mode)
        if normalized_mode != INPUT_MODE_EDIT:
            final_text, model_output = self.process_with_trace(
                text, mode, settings, target_text, edit_mode
            )
            return final_text, model_output, (), ""
        normalized_edit_mode = normalize_edit_mode(edit_mode)
        if normalized_edit_mode != EDIT_MODE_RACE:
            final_text, model_output = self.process_with_trace(
                text, mode, settings, target_text, edit_mode
            )
            return final_text, model_output, (), ""

        settings.validate()
        raw_text = str(text or "").strip()
        if not raw_text:
            return "", "", (), ""
        if not settings.enabled:
            return raw_text, "", (), ""
        target = validate_edit_target_text(target_text)
        user_content = (
            "<待修改文本>\n"
            f"{target}\n"
            "</待修改文本>\n\n"
            "<修改要求>\n"
            f"{raw_text}\n"
            "</修改要求>"
        )
        max_tokens = _text_output_budget(settings)
        return self._process_edit_race_with_collection(
            settings,
            target=target,
            user_content=user_content,
            max_tokens=max_tokens,
            instruction=raw_text,
            on_collection_complete=on_collection_complete,
        )

    def process_with_attempts(
        self,
        text: str,
        mode: str,
        settings: LLMSettings,
        target_text: str = "",
        edit_mode: str = DEFAULT_EDIT_MODE,
    ) -> tuple[str, tuple[str, ...]]:
        """Return the result and every model output, including retries."""

        settings.validate()
        raw_text = str(text or "").strip()
        if not raw_text:
            return "", ()
        if not settings.enabled:
            return raw_text, ()

        normalized_mode = normalize_input_mode(mode)
        if (
            normalize_llm_provider(settings.provider) == LLM_PROVIDER_LOCAL
            and settings.local_auto_start
            and normalized_mode != INPUT_MODE_EDIT
        ):
            self._ensure_local_server(settings)

        if normalized_mode == INPUT_MODE_EDIT:
            normalized_edit_mode = normalize_edit_mode(edit_mode)
            target = validate_edit_target_text(target_text)
            user_content = (
                "<待修改文本>\n"
                f"{target}\n"
                "</待修改文本>\n\n"
                "<修改要求>\n"
                f"{raw_text}\n"
                "</修改要求>"
            )
            max_tokens = _text_output_budget(settings)
            if normalized_edit_mode == EDIT_MODE_RACE:
                return self._process_edit_race(
                    settings,
                    target=target,
                    user_content=user_content,
                    max_tokens=max_tokens,
                    instruction=raw_text,
                )
            system_prompt = (
                EDIT_FRAGMENT_PROMPT
                if normalized_edit_mode == EDIT_MODE_FRAGMENT
                else EDIT_FULL_TEXT_PROMPT
            )
        else:
            system_prompt = DICTATION_PROMPT
            user_content = raw_text
            max_tokens = _text_output_budget(settings)
        if normalized_mode == INPUT_MODE_EDIT:
            return self._process_edit_with_retry(
                settings,
                target=target,
                system_prompt=system_prompt,
                user_content=user_content,
                max_tokens=max_tokens,
                edit_mode=normalized_edit_mode,
                instruction=raw_text,
            )
        response = self._request_chat(
            settings,
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=0.0,
            max_tokens=max_tokens,
            edit_tool=None,
        )
        if not isinstance(response, str):
            model_output = self._model_output_text(response)
            raise LLMResponseProcessingError(
                "大模型听写响应不是文本",
                model_output,
            )
        return response, (response,)

    def _process_edit_race(
        self,
        settings: LLMSettings,
        *,
        target: str,
        user_content: str,
        max_tokens: int,
        instruction: str = "",
    ) -> tuple[str, tuple[str, ...]]:
        """Race both edit contracts and return the first valid response."""

        race_settings = settings
        if (
            normalize_llm_provider(settings.provider) == LLM_PROVIDER_LOCAL
            and settings.local_auto_start
        ):
            # Start the shared server once before worker threads enter it.
            self._ensure_local_server(settings)
            race_settings = replace(settings, local_auto_start=False)

        jobs = (
            (EDIT_MODE_FRAGMENT, EDIT_FRAGMENT_PROMPT),
            (EDIT_MODE_FULL, EDIT_FULL_TEXT_PROMPT),
        )
        executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="ProxiMicEditRace",
        )
        futures = {
            executor.submit(
                self._process_edit_with_retry,
                race_settings,
                target=target,
                system_prompt=prompt,
                user_content=user_content,
                max_tokens=max_tokens,
                edit_mode=mode,
                # The parallel protocols already provide two independent
                # attempts. Retrying both would double the user's wait time.
                max_attempts=1,
                instruction=instruction,
            ): mode
            for mode, prompt in jobs
        }
        errors: dict[str, BaseException] = {}
        failed_outputs: list[str] = []
        for future in as_completed(futures):
            edit_mode = futures[future]
            try:
                result = future.result()
            except BaseException as exc:
                errors[edit_mode] = exc
                outputs = tuple(getattr(exc, "model_outputs", ()) or ())
                if outputs:
                    failed_outputs.extend(str(item) for item in outputs)
                else:
                    output = str(getattr(exc, "model_output", "") or "")
                    if output:
                        failed_outputs.append(output)
                continue

            if result[0] == target:
                errors[edit_mode] = LLMResponseProcessingError(
                    "大模型未找到可可靠执行的修改",
                    result[1][-1] if result[1] else "",
                    result[1],
                )
                failed_outputs.extend(result[1])
                continue

            for other in futures:
                if other is not future:
                    other.cancel()
            # urllib cannot stop a request already in progress. Return without
            # waiting; the losing result is intentionally ignored.
            executor.shutdown(wait=False, cancel_futures=True)
            return result

        executor.shutdown(wait=True, cancel_futures=True)
        details = []
        for edit_mode, _prompt in jobs:
            error = errors.get(edit_mode)
            if error is None:
                continue
            label = "片段替换" if edit_mode == EDIT_MODE_FRAGMENT else "完整文本"
            details.append(f"{label}：{error}")
        message = "两种编辑协议均失败"
        if details:
            message += "（" + "；".join(details) + "）"
        model_output = failed_outputs[-1] if failed_outputs else ""
        raise LLMResponseProcessingError(
            message,
            model_output,
            tuple(failed_outputs),
        )

    def _process_edit_race_with_collection(
        self,
        settings: LLMSettings,
        *,
        target: str,
        user_content: str,
        max_tokens: int,
        instruction: str = "",
        on_collection_complete: (
            Callable[[tuple[LLMBranchTrace, ...], str], None] | None
        ) = None,
    ) -> tuple[str, str, tuple[LLMBranchTrace, ...], str]:
        race_settings = settings
        if (
            normalize_llm_provider(settings.provider) == LLM_PROVIDER_LOCAL
            and settings.local_auto_start
        ):
            self._ensure_local_server(settings)
            race_settings = replace(settings, local_auto_start=False)

        jobs = (
            (EDIT_MODE_FRAGMENT, EDIT_FRAGMENT_PROMPT),
            (EDIT_MODE_FULL, EDIT_FULL_TEXT_PROMPT),
        )

        def run_branch(edit_mode: str, prompt: str):
            started = time.perf_counter()
            try:
                candidate, outputs = self._process_edit_with_retry(
                    race_settings,
                    target=target,
                    system_prompt=prompt,
                    user_content=user_content,
                    max_tokens=max_tokens,
                    edit_mode=edit_mode,
                    max_attempts=1,
                    instruction=instruction,
                )
                if candidate == target:
                    return (
                        "",
                        outputs,
                        "大模型未找到可可靠执行的修改",
                        time.perf_counter() - started,
                    )
                return candidate, outputs, None, time.perf_counter() - started
            except BaseException as exc:
                outputs = tuple(getattr(exc, "model_outputs", ()) or ())
                if not outputs:
                    output = str(getattr(exc, "model_output", "") or "")
                    outputs = (output,) if output else ()
                return "", outputs, str(exc), time.perf_counter() - started

        completed: dict[str, tuple[str, tuple[str, ...], str | None, float]] = {}
        winner = ""
        winner_candidate = ""
        winner_output = ""
        executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="ProxiMicEditCollection",
        )
        futures = {
            executor.submit(run_branch, edit_mode, prompt): edit_mode
            for edit_mode, prompt in jobs
        }

        def traces_from_completed() -> tuple[LLMBranchTrace, ...]:
            return tuple(
                LLMBranchTrace(
                    branch=edit_mode,
                    raw_returns=completed[edit_mode][1],
                    validation=(
                        "valid" if completed[edit_mode][2] is None else "invalid"
                    ),
                    candidate_text=completed[edit_mode][0],
                    latency_s=max(0.0, completed[edit_mode][3]),
                    error=completed[edit_mode][2],
                    won=edit_mode == winner,
                )
                for edit_mode, _prompt in jobs
            )

        def finish_collection_in_background() -> None:
            try:
                for pending in as_completed(futures):
                    edit_mode = futures[pending]
                    if edit_mode not in completed:
                        completed[edit_mode] = pending.result()
                traces = traces_from_completed()
                callback = on_collection_complete
                if callback is not None:
                    try:
                        callback(traces, winner)
                    except BaseException:
                        pass
            finally:
                executor.shutdown(wait=True, cancel_futures=False)

        for future in as_completed(futures):
            edit_mode = futures[future]
            branch_result = future.result()
            completed[edit_mode] = branch_result
            candidate, outputs, error, _latency = branch_result
            if not winner and error is None:
                winner = edit_mode
                winner_candidate = candidate
                winner_output = outputs[-1] if outputs else ""
                if on_collection_complete is not None:
                    threading.Thread(
                        target=finish_collection_in_background,
                        name="ProxiMicEditTraceCollection",
                        daemon=True,
                    ).start()
                    # The accepted candidate reaches the UI immediately. The
                    # slower branch only completes the persisted trace.
                    return winner_candidate, winner_output, (), winner

        executor.shutdown(wait=True, cancel_futures=False)
        traces = traces_from_completed()
        if winner:
            return winner_candidate, winner_output, traces, winner

        details = "；".join(
            f"{trace.branch}：{trace.error}" for trace in traces if trace.error
        )
        outputs = tuple(
            output for trace in traces for output in trace.raw_returns
        )
        error = LLMResponseProcessingError(
            f"两种编辑协议均失败（{details}）" if details else "两种编辑协议均失败",
            outputs[-1] if outputs else "",
            outputs,
        )
        error.branch_traces = traces
        raise error
