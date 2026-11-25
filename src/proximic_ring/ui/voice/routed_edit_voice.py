"""Manual gestures and the selected voice editor. Never submits a Clef request."""
from ...text_processing.model import INPUT_MODE_DICTATION, INPUT_MODE_EDIT, INPUT_ROUTING_ROUTED_EDIT


class RoutedEditVoiceMixin:
    def _finish_routed_edit_utterance(self):
        if self._utterance_active and self._latest_asr_session_id:
            self._session_input_modes[self._latest_asr_session_id] = INPUT_MODE_EDIT
            self._finish_utterance_event.set()
        # Unlike legacy candidate conversion, this mode only chooses intent
        # while recording; a settled sentence cannot silently send a new call.

    def _routed_inline_edit_options(self, key):
        capture = getattr(self, "_voice_capture", None)
        capture = capture if capture and capture.get("key") == tuple(key[:3]) else {}
        variant = capture.get("routed_edit_variant", self._routed_edit_variant)
        return {"routing_mode": INPUT_ROUTING_ROUTED_EDIT,
                "screen_context": capture.get("model_context", {}), "force_llm": True,
                "saved_context": self._ring_gestures.screenshot.saved_context_snapshot(),
                "routed_edit_variant": variant}

    def _submit_routed_voice(self, text, session_id, mode, target):
        # Desktop adapter fallback; the native input method already owns the
        # original snapshot and invokes _submit_inline_edit after right-swipe.
        self._active_auto_interaction = None
        if mode == INPUT_MODE_DICTATION:
            self._submit_text_processing(text, session_id, mode, target=target,
                                         routing_mode=INPUT_ROUTING_ROUTED_EDIT)
            return
        if target is None:
            self._reject_edit_request("没有锁定原文本框，无法执行编辑路由")
            return
        try:
            adapter = self._desktop_target_adapter()
            capture = getattr(adapter, "capture_text_allowing_empty", adapter.capture_text)
            snapshot = capture(target)
        except Exception as exc:
            self._reject_edit_request(f"原文读取不可用，未发送编辑请求：{exc}")
            return
        capture = getattr(self, "_voice_capture", None)
        capture = capture if capture and capture.get("session_id") == session_id else {}
        variant = capture.get("routed_edit_variant", self._routed_edit_variant)
        self._submit_text_processing(text, session_id, INPUT_MODE_EDIT, target=target,
            snapshot=snapshot, target_text=snapshot.text, screen_context=capture.get("model_context", {}),
            saved_context=self._ring_gestures.screenshot.saved_context_snapshot(),
            routing_mode=INPUT_ROUTING_ROUTED_EDIT, force_llm=True,
            routed_edit_variant=variant)
