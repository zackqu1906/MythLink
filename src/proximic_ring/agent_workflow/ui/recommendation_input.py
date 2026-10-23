"""Focus-scoped hub navigation and the shared native voice/stroke input path.

BLE callbacks only inspect cached ownership and enqueue signals. Qt checks the
connection, page/field generation and age again, so an up gesture selecting the
first recommendation cannot also send the newly opened parameter form.
"""
import time
from PySide6.QtCore import QObject, Signal, Slot, QTimer, Qt
from proximic_ring.agent_workflow.ui.editor_gestures import EditorGestureActions
from .hub_interaction import HubInteraction,DIRECTIONS
from .hub_voice import HubVoice
from proximic_ring.agent_workflow.parameters.parameter_contract import missing_required


class RecommendationInput(QObject, HubVoice, EditorGestureActions):
    _voicePrepare = Signal(object,object)
    _voiceResult = Signal(object,str)
    _navigation = Signal(str, object, float, int)
    _inputGestureReceived = Signal(str, str, object, float, object)

    def __init__(self, runtime):
        super().__init__(runtime)
        self.runtime=runtime; self.owner=runtime.agent.owner
        self._closed=False; self.focused=False; self.stage='hub'; self.key=''; self.generation=0
        self.field='';self.interaction=HubInteraction();self._voice_token=None
        self._voice_timer=QTimer(self);self._voice_timer.setSingleShot(True);self._voice_timer.timeout.connect(self._voice_timeout)
        self._voicePrepare.connect(self._prepare_voice,Qt.QueuedConnection)
        self._voiceResult.connect(self._voice_result,Qt.QueuedConnection)
        self._pending_input_action=None; self._composing=False; self._stroke_drawing=False
        self._send_timer=QTimer(self); self._send_timer.setSingleShot(True); self._send_timer.setInterval(12000)
        self._send_timer.timeout.connect(self._input_action_timeout)
        self._navigation.connect(self.navigate,Qt.QueuedConnection)
        self._inputGestureReceived.connect(self._input_gesture,Qt.QueuedConnection)
        inline=self.owner._inline_input
        inline.settled.connect(self._input_settled)
        inline.settled.connect(self._voice_settled)
        inline.actionRequested.connect(self._cancel_input_action)
        inline.changed.connect(self._input_state_changed)
        self.owner._touchpad.strokeTraceChanged.connect(self._trace)

    def _trace(self, points, finished):
        self._stroke_drawing=bool(points) and not finished

    def set_focus(self, focused):
        if self.focused!=focused:
            if not focused and self._voice_token:self.cancel_voice()
            self.focused=focused; self.generation+=1; self._cancel_input_action()
            if not focused:self.key='';self.field=''

    def set_stage(self, stage):
        if stage in ('hub','closed'):self.interaction=HubInteraction()
        self.stage=stage; self.key=''; self.field=''; self.generation+=1; self._composing=False
        self._cancel_input_action()

    def editor_focused(self, name):
        if self.field!=name or not self.key:
            self.field=name; self.generation+=1; self.key=f'{name}:{self.generation}'
            self._cancel_input_action()
        # These are real native IME clients, just like the Agent editor.
        self.owner._touchpad.setLocalStrokeFocus(False)
        self.owner._touchpad.setLocalStrokeInput(False)

    def owns_input_focus(self):
        return bool(not self._closed and self.focused and self.stage=='parameters'
                    and self.key and not self.runtime.executing)

    def _reconcile_input(self):
        pass  # Fields are frozen for this selection, unlike Agent question cards.

    def _apply_error(self, message):
        if self.runtime.dialog:self.runtime.dialog.message.setText(message)

    def _input_context_valid(self, key, connection, context):
        sentence,bindings=context
        return (self.owns_input_focus() and self.runtime.dialog.has_panel_focus() and key==self.key
                and connection is self.owner._disconnect_event and not connection.is_set()
                and bindings is self.owner._gesture_bindings
                and sentence==self.owner._app_gestures.sentence()
                and not self.owner._ring_gestures.session_blocked())

    def _submit_from_gesture(self, key, name):
        self.submit()

    def request_submit(self):
        self.runtime.dialog.handle_gesture('enter')

    def submit(self):
        """Read all committed fields once; never send preedit or stroke candidates."""
        if (not self.focused or not self.runtime.dialog.has_panel_focus()
                or self.stage!='parameters' or self.runtime.executing):return
        if self.runtime.agent._input_busy() or self._composing:
            self._apply_error('请先完成当前输入，再向外滑提交。'); return
        tp=self.owner._touchpad
        if tp.strokeSession.engaged:
            if tp.strokeSession.phase!='running' or tp.strokeCode or tp._pending_stroke_commit or self._stroke_drawing:
                self._apply_error('请先完成笔画输入，再向外滑提交。'); return
            tp.strokeSession.cancel('已提交推荐参数')
        dialog=self.runtime.dialog
        values={k:e.toPlainText().strip() for k,e in dialog.editors.items()}
        flow=self.runtime.snapshot['items'][dialog.position-1]['workflow']
        missing=next(iter(missing_required(flow,values)),None)
        if missing:
            self.runtime.dialog.enter_word(self.runtime.dialog.sentence.names.index(missing))
            self._apply_error('请填写必填参数后再发送。'); return
        self.runtime.execute(dialog.position,values)

    def filter_gesture(self,event,connection):
        if not self.focused or self._closed:return False
        name=getattr(event,'name','');bindings=self.owner._gesture_bindings
        voice={g for pair in bindings.as_dict().values() for g in pair if g}
        if self.runtime.executing:return name in set(DIRECTIONS)|voice|{'tap'}
        # Native stroke candidates own ALL their navigation, including tap and
        # four swipes. Leaving stroke mode hands control back to this layer.
        if self.owns_input_focus() and self.owner._touchpad.strokeSession.engaged:return False
        if name in set(DIRECTIONS)|{'tap'}:
            self._navigation.emit(name,connection,time.monotonic(),self.generation);return True
        if self.interaction.mode=='word' and self.key and name in set(bindings.undo+bindings.switch_mode):
            self._inputGestureReceived.emit(name,self.key,connection,time.monotonic(),
                (self.owner._app_gestures.sentence(),bindings));return True
        return name in voice  # Hub tap semantics replace only this surface's voice endpoints.

    @Slot(str,object,float,int)
    def navigate(self,name,connection,created,generation):
        if (self._closed or not self.focused or not self.runtime.dialog.has_panel_focus() or generation!=self.generation
                or connection is not self.owner._disconnect_event or connection.is_set()
                or time.monotonic()-created>1 or self.owner._ring_gestures.session_blocked()):return
        self.runtime.dialog.handle_gesture(name)

    def release(self, *, sent=False):
        if self._voice_token:self.cancel_voice()
        owned=self.owns_input_focus()
        self.set_focus(False); self.set_stage('closed')
        if owned:
            if self.runtime.agent._input_busy():
                self.owner._finish_utterance_event.set(); self.owner._inline_input.cancel()
            if self.owner._touchpad.strokeSession.engaged:
                self.owner._touchpad.strokeSession.cancel('已离开推荐参数')
            self.owner._inline_input.release_completed_shortcut(reset_native=True,sent=sent)

    def close(self):
        self.release(); self._closed=True; self._send_timer.stop();self._voice_timer.stop()
