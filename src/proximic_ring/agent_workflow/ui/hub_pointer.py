"""原生指针的 tap / 长按 / 四向滑动；不修改窗口焦点策略。

延迟到抬起才处理点击，拖动一旦过阈值只产生一次方向事件，抬起不会再取消卡片。
原生编辑器保留文字选择、输入法和右键菜单；戒指的分层导航独立于指针。
"""
import math
from PySide6.QtCore import QObject,QEvent,Qt,QTimer,QPointF
from PySide6.QtWidgets import QWidget,QPlainTextEdit,QPushButton
from .recommendation_wheel import WheelTarget,CORE

class HubPointer(QObject):
    def __init__(self,dialog):
        super().__init__(dialog);self.dialog=dialog;self.drag=None
        self.longpress=QTimer(self);self.longpress.setSingleShot(True);self.longpress.setInterval(450)
        self.longpress.timeout.connect(self._long)
    def _long(self):
        if not self.drag or self.drag['slot'] is None:return
        self.drag['long']=True;self.dialog.enter_word(self.drag['slot']);self.dialog.runtime.input.start_voice()
    def eventFilter(self,watched,event):
        d=self.dialog
        if not isinstance(watched,QWidget) or watched.window() is not d:return False
        if event.type() not in (QEvent.MouseButtonPress,QEvent.MouseMove,QEvent.MouseButtonRelease):return False
        if d.closing or d.runtime.executing:return False
        node=watched
        while node and node is not d:
            if isinstance(node,QPlainTextEdit):
                if d.runtime.input.interaction.mode=='voice':
                    if event.type()==QEvent.MouseButtonRelease and event.button()==Qt.LeftButton:d.runtime.input.end_voice()
                    return True
                if not node.selection_preview:return False
            if isinstance(node,QPushButton):return False
            node=node.parentWidget()
        if event.type()==QEvent.MouseButtonPress:
            if event.button()!=Qt.LeftButton or not d.has_panel_focus():return False
            point=d.wheel.mapFromGlobal(event.globalPosition().toPoint())
            if not d.contains_artwork(event.globalPosition().toPoint()):return False
            slot=d.sentence.token_at(d.sentence.mapFromGlobal(event.globalPosition().toPoint())) if d.panel_direction else None
            self.drag=dict(start=point,fired=False,long=False,slot=slot,target=watched)
            if slot is not None and d.runtime.input.interaction.mode!='voice':self.longpress.start()
            return True
        if not self.drag:return False
        point=d.wheel.mapFromGlobal(event.globalPosition().toPoint());delta=point-self.drag['start'];s=d.wheel.scale
        length=math.hypot(delta.x(),delta.y())/s
        if length>6:
            d.wheel.trail=((QPointF(self.drag['start'])-d.wheel.origin)/s,(QPointF(point)-d.wheel.origin)/s);d.wheel.update()
        if length>12:self.longpress.stop()
        if length>56 and not self.drag['fired']:
            self.drag['fired']=True
            direction=('right' if delta.x()>0 else 'left') if abs(delta.x())>abs(delta.y()) else ('down' if delta.y()>0 else 'up')
            d.handle_gesture('swipe-'+direction)
        if event.type()==QEvent.MouseButtonRelease:
            self.longpress.stop();drag=self.drag;self.drag=None;d.wheel.trail=None;d.wheel.update()
            if drag['fired'] or drag['long'] or length>12:return True
            if isinstance(drag['target'],WheelTarget):d.choose(drag['target'].rank)
            elif drag['slot'] is not None:
                if d.runtime.input.interaction.mode=='voice':d.runtime.input.end_voice()
                elif d.runtime.input.interaction.mode=='word' and drag['slot']==d.runtime.input.interaction.slot:d.runtime.input.start_voice()
                else:d.enter_word(drag['slot'])
            elif d.panel_direction:
                design=(QPointF(point)-d.wheel.origin)/s
                if CORE.contains(design):d.cancel_to_hub()
                elif d.runtime.input.interaction.mode=='word':d.confirm_word()
                elif d.runtime.input.interaction.mode=='voice':d.runtime.input.end_voice()
                else:d.back()
            return True
        return True
