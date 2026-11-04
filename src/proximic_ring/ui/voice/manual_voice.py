"""Entry for explicit manual mode, alongside automatic_voice.py.

Gesture mapping is shared by both modes. This entry uses the operation already
selected by the user and does not invoke Clef or collect OCR context.
"""
from __future__ import annotations
import time
from ...desktop.desktop_target import DesktopTargetRef
from ...text_processing.model import normalize_input_mode


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

