"""Shared voice actions for Agent and recommendation parameter editors.

The hosting editor validates focus, field identity, connection and sentence.
Swipe up waits for the same native dictation commit before submitting; swipe
down polishes the current sentence. No second ASR/IME or gesture mapping.
"""
import time
from PySide6.QtCore import QTimer, Slot
from proximic_ring.gestures.app_gestures import action_phase
from proximic_ring.voice.voice_actions import voice_action_for_gesture


class EditorGestureActions:
    def _input_notice(self, message):
        self.owner._inline_input.show_notice(message)

    def _cancel_input_action(self, *_):
        self._pending_input_action = None
        self._send_timer.stop()
        capture=getattr(self,'auto_context',None)
        if capture:capture.cancel_prepared()

    def _input_action_timeout(self):
        self._cancel_input_action()
        self._input_notice('本句尚未完成定稿，文字已保留，请完成后再次上滑。')

    def _input_state_changed(self):
        pending = self._pending_input_action
        inline = self.owner._inline_input
        if pending and (not self._input_context_valid(*pending[1:]) or not inline.ready
                        or inline._view.get('phase') in {'editing','edited','polishing','undone','interrupted','error'}
                        or inline._view.get('edit_requested')):
            self._cancel_input_action()
        QTimer.singleShot(0, self._reconcile_input)

    def _input_settled(self, phase, text):
        pending = self._pending_input_action
        if pending is None: return
        if phase != 'dictated' or not str(text).strip():
            self._cancel_input_action()
            return
        self._pending_input_action=None
        self._send_timer.stop()
        # IME/QML commit notifications must finish before reading the editor.
        QTimer.singleShot(0, lambda: self._finish_input_action(pending))

    def _finish_input_action(self, pending):
        name, key, connection, context = pending
        try:
            if not self._input_context_valid(key, connection, context): return
            if action_phase('send', **self.owner._app_gestures.phase_state()) != 'dispatch': return
            if name == 'swipe-up': self._submit_from_gesture(key,name)
            else: self.owner._inline_input.polish(restore_raw_on_undo=False)
        finally:
            capture=getattr(self,'auto_context',None)
            if capture:capture.cancel_prepared()

    @Slot(str, str, object, float, object)
    def _input_gesture(self, name, key, connection, created, context):
        sentence, bindings = context
        if time.monotonic()-created > 1 or not self._input_context_valid(key, connection, context): return
        if name in {'swipe-up', 'swipe-down'}:
            phase = action_phase('send', **self.owner._app_gestures.phase_state())
            if self._pending_input_action: return
            if phase == 'finish_then_send':
                inline = self.owner._inline_input
                if not inline.ready or not inline._utterance_id:
                    self._input_notice('听写输入框尚未就绪，请稍后重新上滑。')
                    return
                self._pending_input_action = (name, key, connection, context)
                capture=getattr(self,'auto_context',None)
                if name=='swipe-up' and capture:capture.prepare(key)
                self._send_timer.start()
                self.owner._finish_utterance_event.set()
                inline.finish()
            elif phase == 'dispatch':
                if name == 'swipe-up': self._submit_from_gesture(key,name)
                elif not self.owner._inline_input.polish():
                    self._input_notice('当前没有可润色的本句听写。')
            else: self._input_notice(phase)
            return
        action=voice_action_for_gesture(name,
            interaction_active=self.owner.interactionCanCancel,
            correction_active=(self.owner.modeCorrectionHotkeyAvailable
                               or self.owner.processingModeCorrectionAvailable),
            undo_active=self.owner.nativeUndoAvailable, bindings=bindings)
        if action:
            self._cancel_input_action()
            self.owner._event_log('AGENT_INPUT_GESTURE',gesture=name,action=action,card=key)
            self.owner._apply_voice_action(action,_from_gesture=True)
