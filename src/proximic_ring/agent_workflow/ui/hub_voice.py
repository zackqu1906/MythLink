"""Hub 词语语音替换，复用现有音频生产者和原生输入法。

tap 开始时对真实编辑器 selectAll；输入法 ACK 后才开音频。再次 tap 只结束
听写，settled 后确认词语回到动作层，绝不发送任务。取消恢复开始前的值。
每次回调检查 token、连接和编辑器所有权，避免迟到识别写入新选择。
"""
from PySide6.QtCore import QTimer,Slot

class HubVoice:
    def _voice_valid(self,token):
        from .recommendation_focus import input_helper_active
        dialog=self.runtime.dialog
        return (token is self._voice_token and self.owns_input_focus()
            and not dialog.closing and (dialog.has_panel_focus() or input_helper_active(dialog)) and token['key']==self.key
            and token['connection'] is self.owner._disconnect_event and not token['connection'].is_set())

    def start_voice(self):
        if self.interaction.mode!='word' or not self.owns_input_focus():return
        if self.runtime.agent._input_busy() or self._composing or self.owner._touchpad.strokeSession.engaged:return
        commands=getattr(self.owner,'_utterance_commands',None)
        if commands is None or commands.connection is not self.owner._disconnect_event or not self.owner._recognition_event.is_set():
            self._apply_error('请先连接设备并开启语音输入。');return
        self.runtime.dialog.begin_word_input()
        editor=self.runtime.dialog.editors[self.field]
        token=dict(key=self.key,connection=self.owner._disconnect_event,field=self.field,
                   previous=editor.toPlainText(),ready=False,finishing=False)
        self._voice_token=token;self.interaction.mode='voice';editor.selectAll()
        self._paint_voice();self._voice_timer.start(12000)
        if not commands.request('start',lambda complete:self._voicePrepare.emit(complete,token) if not self._closed else complete(False),
                                lambda action:self._voiceResult.emit(token,action) if not self._closed else None):
            self.cancel_voice();self._apply_error('语音连接已断开，请重新连接。')

    @Slot(object,object)
    def _prepare_voice(self,complete,token):
        if complete is None:return
        if not self._voice_valid(token):complete(False);return
        def ready(accepted):
            valid=accepted and self._voice_valid(token)
            complete(valid);self._voiceResult.emit(token,'ready' if valid else 'unavailable')
        self.owner._prepare_inline_gesture_start(ready,token['connection'])

    @Slot(object,str)
    def _voice_result(self,token,action):
        if token is not self._voice_token:return
        if not self._voice_valid(token):self.cancel_voice();return
        if action=='ready':token['ready']=True;self._voice_timer.stop()
        elif action in ('unavailable','cancel_start'):
            self.cancel_voice();self._apply_error('本次语音未能开始；原来的词已保留。')

    def end_voice(self):
        token=self._voice_token
        if not token or not self._voice_valid(token) or token['finishing']:return
        if not token['ready']:self.cancel_voice();return
        token['finishing']=True;self._paint_voice();self._voice_timer.start(12000)
        # Existing producer consumes this explicit END before final ASR. Never
        # replace it with an editor-only finish or send preedit to the Agent.
        self.owner._finish_utterance_event.set();self.owner._inline_input.finish()

    def _voice_settled(self,phase,text):
        token=self._voice_token
        if not token:return
        if phase!='dictated' or not str(text).strip():
            self.cancel_voice();self._apply_error('没有识别到可用文字，保留原来的词。');return
        self._voice_timer.start(12000)
        QTimer.singleShot(0,self,lambda:self._confirm_voice(token))

    def _confirm_voice(self,token):
        if not self._voice_valid(token):return
        if self.runtime.agent._input_busy() or self._composing:
            QTimer.singleShot(20,self,lambda:self._confirm_voice(token));return
        self._voice_timer.stop();self._voice_token=None;self.interaction.mode='word'
        self.runtime.dialog.confirm_word();self.runtime.dialog.message.clear()

    def _paint_voice(self):
        d=self.runtime.dialog;d.wheel.mode=self.interaction.mode
        d.wheel.voice_done=bool(self._voice_token and self._voice_token['finishing'])
        d.sentence.set_mode(self.interaction.mode,self.interaction.slot);d.wheel.update()

    def cancel_voice(self,*,to_hub=False):
        token=self._voice_token
        if not token:return
        self._voice_token=None;self._voice_timer.stop()
        self.owner._prepare_inline_gesture_start(None,token['connection'])
        self.owner._cancel_utterance_event.set();self.owner._inline_input.cancel()
        editor=self.runtime.dialog.editors.get(token['field'])
        if editor:editor.setPlainText(token['previous'])
        self.interaction.mode='action';self.runtime.dialog.sentence.editing=False;self._paint_voice()
        if self.runtime.dialog.has_panel_focus():self.runtime.dialog.wheel.setFocus()
        if to_hub:
            # Cancel ACK may take a moment. Return the UI after the producer
            # closes ownership; back() keeps its existing input-busy guard.
            self._return_after_cancel(token['connection'])

    def _return_after_cancel(self,connection,attempt=0):
        if connection is not self.owner._disconnect_event or self._closed:return
        if not self.runtime.dialog.back() and attempt<40:
            QTimer.singleShot(50,self,lambda:self._return_after_cancel(connection,attempt+1))

    def _voice_timeout(self):
        self.cancel_voice();self._apply_error('语音输入超时，已恢复原来的词。')
