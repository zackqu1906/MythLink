"""One raw capture and optional prepared context per utterance, across all modes."""
from __future__ import annotations

from datetime import datetime, timezone


class VoiceCaptureMixin:
    def _init_voice_capture(self):
        self._voice_capture = None

    def _begin_voice_capture(self):
        inline = self._inline_enabled() and self._desktop_output
        target = self._inline_input.target_reference() if inline else self._speech_start_target
        capture = self._voice_capture
        if not inline or not capture or capture["key"] != self._automatic_key():
            self._prefetch_voice_capture(target, inline=inline, verified=True)
        elif inline:
            # Audio START can precede the native BEGIN acknowledgment. Reuse
            # its frame, then attach only the now-verified editor snapshot.
            capture["input_context"] = self._voice_input_context(inline=True, verified=True, target=target)
            if capture.get("interaction_id"):
                try:
                    self._modification_dataset.record_input_context(capture["interaction_id"], capture["input_context"])
                except Exception as exc:
                    self._append_log(f"输入上下文记录保存失败：{exc}")
        self._begin_automatic_voice()

    def _voice_input_context(self, *, inline, verified, target=None):
        view = self._inline_input._view if inline and verified else {}
        # Preserve the verified BEGIN snapshot, not a later/current foreground field.
        fields = ("application", "client_id", "epoch", "utterance_id", "revision", "phase",
                  "original", "edit_source_text", "context_complete", "context_scope",
                  "selection", "selected_range", "context_readable_range", "client_read_diagnostics")
        target_fields = ("window_handle", "window_title", "process_id", "process_name", "accessibility_id",
                         "screen_x", "screen_y", "screen_width", "screen_height",
                         "caret_x", "caret_y", "caret_width", "caret_height")
        return {"source": "input_method" if inline else "desktop_target",
                "status": ("captured" if verified else "pending") if inline else "reference_only",
                "captured_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "target_reference": {key: getattr(target, key) for key in target_fields if hasattr(target, key)},
                **{key: view[key] for key in fields if key in view}}

    def _prefetch_voice_capture(self, target, *, inline, verified):
        try:
            future = self._text_processing_worker.capture_context(target)
        except Exception as exc:
            from ...text_processing.screen_context import unavailable
            future = unavailable(str(exc))
        # Snapshot once, before either native BEGIN or ASR callbacks can finish.
        # Disabling model input must never disable the raw history capture.
        context_enabled = self._ocr_context_enabled
        model_context = (self._text_processing_worker.prepare_context(future, self._llm_settings())
                         if context_enabled else {})
        self._voice_capture = {
            "future": future,
            "model_context": model_context,
            "context_enabled": context_enabled,
            "key": self._automatic_key() if inline else None,
            "bound": False,
            "routing": self._inline_input.session_routing_mode if inline else self._input_routing_mode,
            "requested_mode": self._input_mode,
            "routed_edit_variant": self._routed_edit_variant,
            "input_context": self._voice_input_context(inline=inline, verified=verified, target=target),
        }

    def _bind_voice_capture(self, session_id):
        if int(session_id) <= 0:
            return
        inline = self._inline_enabled() and self._desktop_output
        capture = self._voice_capture
        if (not capture or (inline and capture["key"] != self._automatic_key())
                or (capture["bound"] and capture.get("session_id") != session_id)):
            # Capture even if the input method never becomes ready. It must not
            # make screenshot/OCR recording depend on successful text routing.
            self._prefetch_voice_capture(self._speech_start_target, inline=inline, verified=False)
            capture = self._voice_capture
        if capture["bound"]:
            return
        capture["interaction_id"] = self._modification_dataset.record_screen_capture(
            session_id, capture["future"], routing=capture["routing"],
            requested_mode=capture["requested_mode"], input_context=capture["input_context"])
        self._modification_dataset.record_context_preparation(
            capture["interaction_id"], capture["model_context"], enabled=capture["context_enabled"])
        capture["bound"] = True
        capture["session_id"] = session_id
