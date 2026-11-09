"""Sequential automatic voice lane. Manual conversion candidates stay separate."""
from __future__ import annotations

from dataclasses import dataclass, replace

from ...text_processing.model import InputModeRoutingRequest, INPUT_MODE_DICTATION, INPUT_MODE_EDIT, INPUT_ROUTING_AUTO
from ...text_processing.task_routing import DICTATION, TASK_LABELS, EDIT_TASK_LABELS, EditPlan


@dataclass
class AutomaticUtterance:
    generation: int
    text: str
    session_id: int
    target: object
    snapshot: object = None
    inline_key: tuple = ()
    task_type: str = ""
    context: object = None
    edit_plan: EditPlan | None = None
    cleanup_dictation: bool = True


class AutomaticVoiceMixin:
    def _automatic_session_enabled(self):
        inline = self._inline_input
        if self._inline_enabled() and inline.active:
            return inline.session_automatic
        return self._input_routing_mode == INPUT_ROUTING_AUTO

    def _init_automatic_voice(self):
        self._automatic_generation = 0
        self._automatic_routes = {}
        self._automatic_dictations = {}
        self._automatic_inline_edit = None
        self._automatic_context = None
        self._automatic_submitted_key = None
        self._automatic_startup_final = None
        self._configure_voice_mode()

    def _automatic_inline_edit_options(self, key):
        pending = self._automatic_inline_edit
        if pending is None or tuple(key[:3]) != pending.inline_key:
            raise ValueError("自动编辑计划与输入法会话不匹配")
        self._inline_input.automatic_progress("generating", pending.edit_plan.tasks)
        return dict(task_type=pending.task_type, edit_plan=pending.edit_plan,
                    screen_context=pending.context, force_llm=True, routing_mode=INPUT_ROUTING_AUTO)

    def _automatic_key(self):
        inline = self._inline_input
        return (inline._epoch, inline._client_id, inline._utterance_id)

    def _begin_automatic_voice(self):
        buffered = self._automatic_startup_final
        self._cancel_automatic_voice()
        enabled = self._inline_input.session_automatic if self._inline_enabled() else self._input_routing_mode == INPUT_ROUTING_AUTO
        if not enabled:
            return
        target = self._inline_input.target_reference() if self._inline_enabled() else self._speech_start_target
        capture = getattr(self, "_voice_capture", None)
        if not capture:
            self._prefetch_voice_capture(target, inline=self._inline_enabled(), verified=False)
            capture = self._voice_capture
        self._automatic_context = capture["model_context"]
        if buffered and buffered[0] == self._automatic_key():
            self._submit_automatic_voice(buffered[1], buffered[2], target, inline=True)

    def _automatic_record(self, method, *args):
        try:
            getattr(self._modification_dataset, method)(*args)
        except Exception as exc:
            self._append_log(f"自动判断记录保存失败：{exc}")

    def _cancel_automatic_voice(self):
        if not hasattr(self, "_automatic_routes"):
            return
        self._automatic_generation += 1
        for request_id in (*self._automatic_routes, *self._automatic_dictations):
            self._text_processing_worker.cancel_request(request_id)
            self._pending_mode_routes.discard(request_id)
            self._pending_text_requests.discard(request_id)
            self._pending_interactions.pop(request_id, None)
        self._automatic_routes.clear()
        self._automatic_dictations.clear()
        self._automatic_inline_edit = None
        self._automatic_submitted_key = None
        self._automatic_startup_final = None
        # Raw capture belongs to the history record, even if routing is cancelled.
        self._automatic_context = None

    def _submit_automatic_voice(self, text, session_id, target, *, inline=False):
        # No _AutoInteraction is created: there is no alternate candidate and
        # no conversion button for this lane.
        key = self._automatic_key() if inline else (int(session_id),)
        if key == self._automatic_submitted_key:
            return
        if inline and (self._inline_input._startup_buffering or self._inline_input._view.get("phase") == "starting"):
            self._automatic_startup_final = (key, text, int(session_id))
            return
        if inline and (not self._inline_input.active or self._inline_input._final_sent):
            return
        self._automatic_submitted_key = key
        self._active_auto_interaction = None
        snapshot, source = None, ""
        if inline:
            source = str(self._inline_input._view.get("edit_source_text", ""))
        elif target is not None:
            try:
                adapter = self._desktop_target_adapter()
                capture = getattr(adapter, "capture_text_allowing_empty", adapter.capture_text)
                snapshot = capture(target)
                source = snapshot.text
            except Exception as exc:
                self._append_log(f"自动判断原文读取不可用：{exc}")
        if self._automatic_context is None:
            self._prefetch_voice_capture(target, inline=inline, verified=False)
            self._bind_voice_capture(session_id)
            self._automatic_context = self._voice_capture["model_context"]
        self._text_request_id += 1
        request_id = self._text_request_id
        pending = AutomaticUtterance(self._automatic_generation, text, int(session_id), target,
                                    snapshot=snapshot, inline_key=self._automatic_key() if inline else (),
                                    cleanup_dictation=self._automatic_dictation_cleanup_enabled)
        self._automatic_routes[request_id] = pending
        self._pending_mode_routes.add(request_id)
        request = InputModeRoutingRequest(request_id, int(session_id), text,
            replace(self._llm_settings(), enabled=True), router_backend="clef", target_text=source,
            screen_context=self._automatic_context)
        self._automatic_record("record_routing_request", request)
        if inline:
            self._inline_input.automatic_progress("routing")
        self._transcript_text = "正在自动判断"
        self._set_interaction_state("processing")
        self.textProcessingChanged.emit()
        self.transcriptChanged.emit()
        self._text_processing_worker.submit_routing(request)

    def _automatic_current(self, pending):
        return (pending.generation == self._automatic_generation and not self._quitting
                and not self._disconnect_event.is_set()
                and (not pending.inline_key or
                     (pending.inline_key == self._automatic_key() and self._inline_input._view.get("phase") in {"listening", "finishing"})))

    def _apply_automatic_voice_route(self, result):
        pending = self._automatic_routes.pop(result.request_id, None)
        if pending is None:
            return False
        self._pending_mode_routes.discard(result.request_id)
        self.textProcessingChanged.emit()
        if not self._automatic_current(pending):
            return True
        self._automatic_record("record_routing_result", result)
        if result.model_output:
            self._append_log(f"自动判断模型分数：{result.model_output}")
        if result.error:
            self._append_log(f"自动判断失败，保留本句听写：{result.error}")
            if pending.inline_key:
                self._inline_input.automatic_progress("route_failed")
                self._inline_input.update(pending.text, True, pending.session_id)
            else:
                from ...text_processing.model import TextProcessingResult
                self._commit_input_text(TextProcessingResult(0, pending.session_id, DICTATION, pending.text,
                    pending.text, 0, False), pending.target)
            return True
        pending.task_type, pending.context = result.task_type, result.screen_context
        pending.edit_plan = result.edit_plan
        self._append_log(f"自动判断：{TASK_LABELS[result.task_type]}（{result.latency_s:.3f}s）")
        if result.edit_plan:
            self._append_log("编辑需求：" + "、".join(EDIT_TASK_LABELS[k] for k in result.edit_plan.tasks))
        if result.task_type == DICTATION:
            self._automatic_record("record_dictation_cleanup", pending.session_id, pending.cleanup_dictation)
            if not pending.cleanup_dictation:
                self._append_log("听写整理已关闭，直接使用 ASR 文本")
                if pending.inline_key:
                    self._inline_input.automatic_progress("routed")
                    self._inline_input.update(pending.text, True, pending.session_id, automatic_task=DICTATION)
                else:
                    from ...text_processing.model import TextProcessingResult
                    self._commit_input_text(TextProcessingResult(0, pending.session_id, DICTATION,
                        pending.text, pending.text, 0, False, task_type=DICTATION), pending.target)
                return True
        if pending.inline_key and result.task_type != DICTATION:
            self._automatic_inline_edit = pending
            self._inline_input.automatic_progress("routed", result.edit_plan.tasks)
            self._inline_input.update(pending.text, True, pending.session_id, automatic_task=result.task_type)
        else:
            if result.task_type != DICTATION and pending.snapshot is None:
                self._reject_edit_request("没有取得原文本框，无法应用自动编辑")
                return True
            mode = INPUT_MODE_DICTATION if result.task_type == DICTATION else INPUT_MODE_EDIT
            if pending.inline_key:
                self._inline_input.automatic_progress("dictation")
            self._submit_text_processing(pending.text, pending.session_id, mode,
                target=pending.target, snapshot=pending.snapshot,
                target_text=pending.snapshot.text if pending.snapshot is not None else "",
                task_type=result.task_type, screen_context=result.screen_context,
                edit_plan=result.edit_plan, force_llm=True, routing_mode=INPUT_ROUTING_AUTO)
            if pending.inline_key:
                self._automatic_dictations[self._text_request_id] = pending
        return True

    def _apply_automatic_dictation(self, result):
        pending = self._automatic_dictations.pop(result.request_id, None)
        if pending is None:
            return False
        self._pending_text_requests.discard(result.request_id)
        self._pending_interactions.pop(result.request_id, None)
        self.textProcessingChanged.emit()
        self.llmTextProcessingChanged.emit()
        if not self._automatic_current(pending):
            return True
        self._automatic_record("record_llm_result", result.request_id, result)
        if result.error:
            self._append_log(f"听写整理失败，保留原始听写：{result.error}")
            self._inline_input.automatic_progress("dictation_failed")
        self._inline_input.update(pending.text if result.error else result.final_text,
                                  True, pending.session_id, automatic_task=DICTATION)
        return True
