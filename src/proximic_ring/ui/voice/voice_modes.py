"""One explicit UI dispatch point for the three voice processing modes."""
from ...text_processing.model import (
    INPUT_ROUTING_AUTO, INPUT_ROUTING_MANUAL, INPUT_ROUTING_ROUTED_EDIT,
)


class VoiceModesMixin:
    def _configure_voice_mode(self):
        self._inline_input.automatic_mode = self._input_routing_mode == INPUT_ROUTING_AUTO
        self._inline_input.routed_edit_mode = self._input_routing_mode == INPUT_ROUTING_ROUTED_EDIT
        if self._input_routing_mode == INPUT_ROUTING_AUTO:
            self._text_processing_worker.warmup_router()

    def _current_voice_routing_mode(self):
        if self._inline_enabled() and self._inline_input.active:
            return self._inline_input.session_routing_mode
        if self._utterance_active:
            return self._session_routing_modes.get(self._latest_asr_session_id, self._input_routing_mode)
        return self._input_routing_mode

    def _submit_voice_text(self, routing_mode, text, session_id, mode, target):
        if routing_mode == INPUT_ROUTING_AUTO:
            self._submit_automatic_voice(text, session_id, target)
        elif routing_mode == INPUT_ROUTING_ROUTED_EDIT:
            self._submit_routed_voice(text, session_id, mode, target)
        else:
            self._prepare_manual_candidates(text, session_id, mode, target)

    def _inline_edit_options(self, key):
        routing = self._inline_input.session_routing_mode
        if routing == INPUT_ROUTING_AUTO:
            return self._automatic_inline_edit_options(key)
        if routing == INPUT_ROUTING_ROUTED_EDIT:
            return self._routed_inline_edit_options(key)
        return {"routing_mode": INPUT_ROUTING_MANUAL}
