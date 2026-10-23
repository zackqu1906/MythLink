"""Independent four-direction hub and native IME parameter form.

Ranks map clockwise to up/right/down/left, frozen for this request. Exposure
requires 500 ms of real foreground visibility. Choosing opens a confirmation
form even for zero-parameter workflows; only a subsequent send executes.
"""
import time
import copy
from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QEvent, Signal, QRectF, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QGuiApplication, QCursor, QColor, QRegion, QPainterPath
from PySide6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QWidget,QScrollArea,QGridLayout,QPlainTextEdit,QFrame,QGraphicsDropShadowEffect,QSizePolicy)
from proximic_ring.ui.shared.overlay_stacking import OverlayVisibilityGuard
from proximic_ring.agent_workflow.ui.recommendation_wheel import RecommendationWheel, ACCENTS, DIRECTION_NAMES, icon_for
from .recommendation_focus import HubClickAway, HubFocusSession, focus_panel, has_native_focus, input_helper_active, owned_popup
from proximic_ring.agent_workflow.operations.operation_display import presentation
from .hub_sentence import SentenceCard
from .hub_pointer import HubPointer
from proximic_ring.agent_workflow.parameters.parameter_contract import required


class OverlayNotice(QLabel):
    changed=Signal()
    def setText(self,text):
        super().setText(text);self.setVisible(bool(text));self.changed.emit()
    def clear(self):self.setText('')


class ParameterEditor(QPlainTextEdit):
    def __init__(self, input_owner, name):
        super().__init__();self.input_owner=input_owner;self.name=name;self.selection_preview=True
        self.viewport().setAutoFillBackground(False)
        self.viewport().setAttribute(Qt.WA_TranslucentBackground)
        self.document().setDocumentMargin(0)
    def focusInEvent(self,event):
        super().focusInEvent(event);self.input_owner.editor_focused(self.name)
    def focusOutEvent(self,event):
        super().focusOutEvent(event)
        # Native input-source helpers briefly take focus during activation.
        QTimer.singleShot(0,self._settle_blur)
    def _settle_blur(self):
        from shiboken6 import isValid
        if not isValid(self):return
        # A folded/rebuilt form can have the same parameter names. Its delayed
        # blur must not revoke ownership from the replacement native editor.
        if self.input_owner.runtime.dialog.editors.get(self.name) is not self:return
        if not self.hasFocus() and not input_helper_active(self.input_owner.runtime.dialog):
            if self.input_owner.key.startswith(self.name+':'):
                self.input_owner.key='';self.input_owner._cancel_input_action()
    def paintEvent(self,event):
        # In the word layer the parent paints the chip and its arrows. Keeping
        # this native client (and its focus) allows the same IME/stroke path,
        # without painting a text box, placeholder or blinking insertion caret.
        if not self.selection_preview:super().paintEvent(event)

    def keyPressEvent(self,event):
        if self.selection_preview and event.key() in (Qt.Key_Up,Qt.Key_Down,Qt.Key_Left,Qt.Key_Right):
            direction={Qt.Key_Up:'up',Qt.Key_Down:'down',Qt.Key_Left:'left',Qt.Key_Right:'right'}[event.key()]
            self.input_owner.runtime.dialog.handle_gesture('swipe-'+direction);event.accept();return
        if self.selection_preview and (event.key()==Qt.Key_F2 or
                (event.text() and event.key() not in (Qt.Key_Escape,Qt.Key_Return,Qt.Key_Enter,Qt.Key_Tab))):
            self.input_owner.runtime.dialog.begin_word_input()
            if event.key()==Qt.Key_F2:event.accept();return
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            # Return belongs to the input method while candidates/preedit exist.
            if self.input_owner._composing or self.input_owner.owner._touchpad.strokeSession.engaged:
                super().keyPressEvent(event);return
            self.input_owner.request_submit();event.accept();return
        super().keyPressEvent(event)
    def inputMethodEvent(self,event):
        if self.selection_preview and (event.preeditString() or event.commitString()):
            self.input_owner.runtime.dialog.begin_word_input()
        self.input_owner._composing=bool(event.preeditString())
        super().inputMethodEvent(event)


class RecommendationDialog(QDialog):
    def __init__(self,runtime):
        super().__init__();self.runtime=runtime;self.cards=[];self.editors={};self.position=None;self.guard=None
        self.drafts={};self.panel_direction=None;self.closing=False;self._dismissed=False
        self.pending_choice=None;self._activation_until=0.
        self.focus_session=HubFocusSession(self)
        self.setWindowTitle('Mythlink · 推荐 Hub');self.setObjectName('workflowRecommendationDialog')
        self.setWindowFlags(Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground);self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_ShowWithoutActivating);self.setAttribute(Qt.WA_QuitOnClose,False)
        self.resize(1332,752)
        self.setStyleSheet("""
            QWidget {color:#0f172a;font-family:'PingFang SC';font-size:13px;}
            QLabel {background:transparent;border:0;}
            QFrame#recommendationParameters {background:rgba(255,255,255,230);border:1px solid white;border-radius:16px;}
            QPushButton {background:rgba(255,255,255,180);border:1px solid #e2e8f0;border-radius:10px;color:#475569;padding:0 12px;}
            QPushButton:hover {background:white;border-color:#cbd5e1;}
            QPushButton:pressed {background:#e2e8f0;}
            QPushButton:disabled {color:#94a3b8;background:#e2e8f0;}
            QScrollArea {background:transparent;border:0;}
            QScrollArea>QWidget>QWidget {background:transparent;}
            QScrollBar:vertical {width:5px;background:transparent;}
            QScrollBar::handle:vertical {background:#cbd5e1;border-radius:2px;min-height:16px;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
        """)
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0)
        self.wheel=RecommendationWheel(self);outer.addWidget(self.wheel)
        self.wheel.chosen.connect(self.choose);self.wheel.backgroundClicked.connect(self.back)
        self.wheel.resized.connect(self._arrange_overlays)
        self.wheel.shapeChanged.connect(self._update_shape)
        self.parameters=QFrame(self.wheel);self.parameters.setObjectName('recommendationParameters')
        self.form=QVBoxLayout(self.parameters);self.form.setContentsMargins(0,0,0,0);self.form.setSpacing(0)
        shadow=QGraphicsDropShadowEffect(self.parameters);shadow.setBlurRadius(36);shadow.setOffset(0,12);shadow.setColor(QColor(15,23,42,35))
        self.parameters.setGraphicsEffect(shadow);self.parameters.hide()
        self.refresh_button=QPushButton('↻',self.wheel);self.refresh_button.setToolTip('刷新推荐')
        self.refresh_button.setAccessibleName('刷新推荐');self.refresh_button.clicked.connect(runtime.refresh)
        self.close_button=QPushButton('×',self.wheel);self.close_button.setToolTip('关闭推荐 Hub（Esc）')
        self.close_button.setAccessibleName('关闭推荐 Hub');self.close_button.clicked.connect(self.reject)
        for button in (self.refresh_button,self.close_button):
            button.setCursor(Qt.PointingHandCursor);button.setFocusPolicy(Qt.NoFocus);button.setAutoDefault(False)
        self.message=OverlayNotice(self.wheel);self.message.setWordWrap(True);self.message.setTextFormat(Qt.PlainText)
        self.update_notice=OverlayNotice(self.wheel);self.update_notice.setWordWrap(True)
        for notice in (self.message,self.update_notice):
            notice.setAlignment(Qt.AlignCenter);notice.setTextFormat(Qt.PlainText)
            notice.setStyleSheet('color:#475569;font-size:12px;background:rgba(255,255,255,235);border-radius:12px;padding:8px;')
            notice.hide();notice.changed.connect(self._update_shape)
        self.pipeline_notice=QLabel(self);self.pipeline_notice.hide()
        self.hint=QLabel(self.wheel);self.hint.setAlignment(Qt.AlignCenter);self.hint.setStyleSheet('color:#94a3b8;font-size:11px;')
        self.hint.hide()  # Input hints live on the controls; no peripheral footer.
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.check_exposures);self.timer.start()
        self.focus_timer=QTimer(self);self.focus_timer.setSingleShot(True);self.focus_timer.timeout.connect(self._settle_focus)
        self.fade=QPropertyAnimation(self,b'windowOpacity',self);self.fade.setDuration(500)
        self.fade.setEasingCurve(QEasingCurve.InOutCubic);self.fade.finished.connect(self.finish_dismissal)
        self.click_away=HubClickAway(self)
        self.focus_session.released.connect(self._focus_released)
        self.finished.connect(self._did_finish)
        QApplication.instance().installEventFilter(self)
        QGuiApplication.instance().focusWindowChanged.connect(self._window_focus_changed)
        self.pointer=HubPointer(self);QApplication.instance().installEventFilter(self.pointer)

    def has_panel_focus(self):
        # QWidget.isActiveWindow() also reports True for a Tool while another
        # main window in the process is active on macOS. Ownership must follow
        # the actual key window, not process/application activation.
        return bool(self.isVisible() and not self.closing and has_native_focus(self))

    def _window_focus_changed(self,window):
        if self.has_panel_focus():
            self._activation_until=0.
            self.focus_timer.stop();self.runtime.input.set_focus(True)
            editor=self.focusWidget()
            if isinstance(editor,ParameterEditor):self.runtime.input.editor_focused(editor.name)
        else:self.focus_timer.start(100)

    def eventFilter(self,watched,event):
        if (event.type()==QEvent.MouseButtonPress and self.closing
                and isinstance(watched,QWidget) and watched.window() is not self):
            self.focus_session.discard()
        if event.type()==QEvent.MouseButtonPress and self.isVisible() and not self.closing:
            if isinstance(watched,QWidget) and watched.window() is self:
                if not self.contains_artwork(event.globalPosition().toPoint()):self.dismiss_animated()
                elif not self.has_panel_focus():self.activate_panel()
            elif isinstance(watched,QWidget) and watched.window() is not owned_popup(self):self.dismiss_animated(focus_reason='local_click')
        return False

    def event(self,event):
        if hasattr(self,'runtime'):
            if event.type()==QEvent.WindowActivate:
                if self.has_panel_focus():
                    self.runtime.input.set_focus(True)
                    editor=self.focusWidget()
                    if isinstance(editor,ParameterEditor):self.runtime.input.editor_focused(editor.name)
            elif event.type()==QEvent.WindowDeactivate:self.focus_timer.start(100)
        return super().event(event)

    def _settle_focus(self):
        if not self.isVisible() or self.closing:
            self.runtime.input.set_focus(False);return
        if input_helper_active(self) or owned_popup(self):
            self.focus_timer.start(25);return
        if not self.has_panel_focus() and time.monotonic()<self._activation_until:
            # Native window creation/key-window notifications can arrive after
            # Qt's first focus event. Never permanently disable this new panel.
            focus_panel(self);self.focus_timer.start(25);return
        self.runtime.input.set_focus(self.has_panel_focus())
        if not self.runtime.input.focused:
            self.dismiss_animated()

    def contains_artwork(self, global_point):
        return self.mask().contains(self.mapFromGlobal(global_point))

    def present(self):
        opening=not self.isVisible() or self.closing
        self.focus_session.begin()
        self.fade.stop();self.closing=False;self._dismissed=False;self.setWindowOpacity(1.)
        self._activation_until=time.monotonic()+.35
        screen=QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen:
            area=screen.availableGeometry();self.resize(min(1332,area.width()-24),min(752,area.height()-24))
            self.move(screen.geometry().center()-self.rect().center())
        self.show()
        # Stay above the Agent's floating cards without covering IME palettes.
        if self.guard is None:self.guard=OverlayVisibilityGuard(self.windowHandle(),self,input_workspace=True,level_offset=1)
        self.click_away.start()
        if opening:self.wheel.open_from_center()
        self.activate_panel()

    def activate_panel(self):
        if self.closing:return
        self._activation_until=time.monotonic()+.35
        focus_panel(self)
        if not self.panel_direction:self.wheel.setFocus(Qt.OtherFocusReason)
        self.runtime.input.set_focus(self.has_panel_focus())
        if self.runtime.input.focused:self._activation_until=0.
        self.focus_timer.start(25)

    def _arrange_overlays(self):
        if not hasattr(self,'close_button'):return
        s=self.wheel.scale
        self.refresh_button.setGeometry(self.wheel.map_rect(626,420,22,22))
        self.close_button.setGeometry(self.wheel.map_rect(652,420,22,22))
        for button in (self.refresh_button,self.close_button):
            button.setStyleSheet(f'QPushButton {{font-size:{max(10,round(15*s))}px;border:0;border-radius:{round(11*s)}px;padding:0;background:transparent;color:#94a3b8;}} QPushButton:hover {{background:#edf2f7;color:#475569;}}')
        self.message.setGeometry(self.wheel.map_rect(440,660,420,48))
        self.update_notice.setGeometry(self.wheel.map_rect(20,660,380,48))
        if self.panel_direction is not None and hasattr(self,'sentence'):
            rank=self.panel_direction;height=self.sentence.height_units();width=420
            x,y=((650,146),(864,360),(650,574),(436,360))[rank-1]
            if rank==1:x-=width/2;y-=height
            elif rank==3:x-=width/2
            elif rank==2:y-=height/2
            else:x-=width;y-=height/2
            # Long real parameters can add rows. Stay within the available
            # canvas while keeping the selected direction and native editor.
            y=max(4,min(y,716-height))
            self.parameters.setGeometry(self.wheel.map_rect(x,y,width,height))
            self.parameters.raise_()
            self.sentence.arrange_candidate_notice()
        self._update_shape()

    def _update_shape(self):
        if not hasattr(self,'update_notice'):return
        region=self.wheel.visible_region()
        if self.panel_direction is not None:
            path=QPainterPath();path.addRoundedRect(QRectF(self.parameters.geometry()).adjusted(-20,-20,20,32),30,30)
            region|=QRegion(path.toFillPolygon().toPolygon())
        for notice in (self.message,self.update_notice):
            if notice.text():region|=QRegion(notice.geometry())
        if hasattr(self,'sentence') and not self.sentence.candidate_notice.isHidden():
            region|=QRegion(self.sentence.candidate_notice.geometry())
        if region!=self.mask():self.setMask(region)

    def populate(self,result):
        self.fade.stop();self.closing=False;self._dismissed=False;self.pending_choice=None
        self.setWindowOpacity(1.)
        self.runtime.input.release();self.parameters.hide();self.panel_direction=None
        for card in self.cards:card['reject'].deleteLater()
        self.wheel.clear();self.cards=[];self.editors={};self.drafts={};self.position=None
        self.runtime.input.set_stage('hub')
        self.message.setText('' if result['items'] else '还没有可用操作。完成 Agent 任务并生成通用 Workflow 后，再打开推荐。')
        self.update_notice.clear();self.refresh_button.setEnabled(True);self.refresh_button.show();self.close_button.show()
        bundle=result.get('context',{}).get('app')
        names={'com.apple.Safari':'Safari','com.apple.Notes':'备忘录','com.apple.Music':'苹果音乐'}
        self.wheel.context=names.get(bundle,bundle.rsplit('.',1)[-1] if bundle else '当前应用未知')
        self.wheel.context_icon=icon_for(bundle or '')
        for rank in range(1,5):
            item=result['items'][rank-1] if rank<=len(result['items']) else None
            display=presentation(item['workflow'],item.get('display')) if item else None
            name=display['title'] if item else '暂无推荐'
            params=item['workflow']['parameters'] if item else []
            mandatory=sum(required(p) for p in params)
            subtitle=(f'{mandatory} 项必填 · {len(params)-mandatory} 项可选' if params else '确认后执行') if item else ''
            tooltip=display['summary'] if item else ''
            node,label=self.wheel.add_item(rank,name,subtitle,tooltip,bool(item))
            if item:
                reject=QPushButton('现在不需要',self.wheel);reject.hide();reject.setEnabled(False)
                reject.clicked.connect(lambda checked=False,p=rank:self.runtime.reject(p))
                self.cards.append(dict(widget=node,title=label,choose=node,reject=reject,exposure=None,pending=False,since=None,rejected=False))
        self.hint.setText('上 / 右 / 下 / 左选择 · Esc 关闭')
        self._arrange_overlays()

    def check_exposures(self):
        foreground=self.has_panel_focus() and not self.isMinimized() and self.runtime.input.stage=='hub' and self.wheel.reveal==1.
        for position,card in enumerate(self.cards,1):
            title=card['title'];rect=QRect(title.mapTo(self.wheel,QPoint(0,0)),title.size());visible=rect.intersected(self.wheel.rect())
            enough=foreground and title.isVisible() and visible.width()*visible.height()>=max(1,rect.width()*rect.height())*.5
            if not enough:card['since']=None;continue
            if card['exposure'] or card['pending']:continue
            if card['since'] is None:card['since']=time.monotonic()
            elif time.monotonic()-card['since']>=.5:card['pending']=True;self.runtime.expose(position)

    def choose(self,position):
        if (self.closing or not self.has_panel_focus() or not self.runtime.snapshot or not 1<=position<=len(self.cards) or self.runtime.executing):return
        card=self.cards[position-1]
        if card['rejected']:return
        if not card['exposure']:
            # Keep an early click/swipe during entrance/exposure instead of
            # silently discarding it. It can only open this invocation's form.
            self.pending_choice=position;self.wheel.set_hover(position);return
        self.pending_choice=None
        if self.panel_direction:
            previous=self.position
            if not self.back():return
            if previous==position:return
        item=self.runtime.snapshot['items'][position-1]
        display=copy.deepcopy(presentation(item['workflow'],item.get('display')))
        while self.form.count():
            child=self.form.takeAt(0)
            if child.widget():child.widget().hide();child.widget().deleteLater()
        self.position=position;self.panel_direction=position;self.editors={};self.field_labels=[]
        self.runtime.input.set_stage('parameters');self.wheel.set_active(position)
        candidates=copy.deepcopy(item.get('parameter_candidates',{}))
        for parameter in item['workflow']['parameters']:
            name=parameter['name'];field_copy=display['parameters'][name]
            optional=not required(parameter)
            if optional:
                field_copy['label']+='（可选）'
                field_copy['placeholder']='可留空；'+field_copy['placeholder']
                candidates[name]=[dict(value='',omitted=True)]+[p for p in candidates.get(name,[]) if p['value']]
            editor=ParameterEditor(self.runtime.input,name)
            editor.setPlaceholderText(field_copy['placeholder']);editor.setAccessibleName(field_copy['label'])
            editor.setAccessibleDescription(field_copy['placeholder']);editor.setToolTip(field_copy['placeholder'])
            editor.setObjectName('recommendationParameter_'+name);editor.setTabChangesFocus(True)
            options=candidates.get(name,[])
            editor.setPlainText(self.drafts.get(position,{}).get(name,options[0]['value'] if options else ''))
            self.editors[name]=editor
        self.sentence=SentenceCard(self,display,self.editors,candidates);self.form.addWidget(self.sentence)
        self.runtime.input.interaction.select(position,len(self.sentence.names))
        self.wheel.mode='action';self.refresh_button.setEnabled(False);self.refresh_button.hide();self.close_button.hide()
        self.parameters.show();self._arrange_overlays();self.wheel.setFocus();self.wheel.update()

    def enter_word(self,slot):
        if self.runtime.executing or self.runtime.input.interaction.mode=='voice':return
        if not 0<=slot<len(self.sentence.names):return
        if self.runtime.agent._input_busy() or self.runtime.input._composing:return
        state=self.runtime.input.interaction;state.slot=slot;state.mode='word';self.runtime.input.generation+=1
        self.wheel.mode='word';self.sentence.editing=False;self.sentence.set_mode('word',slot)
        self.editors[self.sentence.names[slot]].setFocus(Qt.OtherFocusReason)
        self.wheel.update();self._arrange_overlays()

    def begin_word_input(self):
        """Only an explicit voice/keyboard/stroke input opens the native editor.

        Entering a word or cycling candidates remains a selection preview.
        The editor object, document and focus identity are retained throughout.
        """
        if self.runtime.executing or self.runtime.input.interaction.mode not in ('word','voice'):return
        self.sentence.editing=True;self.sentence.relayout();self._arrange_overlays()

    def confirm_word(self):
        if self.runtime.input.interaction.mode=='voice':self.runtime.input.end_voice();return
        if self.runtime.agent._input_busy() or self.runtime.input._composing:return
        state=self.runtime.input.interaction;state.mode='action';self.runtime.input.generation+=1
        self.sentence.editing=False;self.sentence.set_mode('action',state.slot);self.wheel.mode='action';self.wheel.setFocus();self.wheel.update()
        self._arrange_overlays()

    def cycle_word(self,delta):
        state=self.runtime.input.interaction;name=self.sentence.names[state.slot];editor=self.editors[name]
        options=[row['value'] for row in self.sentence.candidates.get(name,[])]
        if not options:
            self.sentence.editing=False;self.sentence.relayout();return
        if self.runtime.agent._input_busy() or self.runtime.input._composing:return
        current=editor.toPlainText();index=options.index(current) if current in options else (-1 if delta>0 else 0)
        self.sentence.editing=False
        value=options[(index+delta)%len(options)]
        if current!=value:editor.setPlainText(value)
        self.sentence.relayout();self.message.clear()
        if len(options)==1:self.sentence.show_candidate_notice()
        else:self.sentence.clear_candidate_notice()

    def handle_gesture(self,name):
        if self.closing or not self.has_panel_focus() or self.runtime.executing:return
        intent=self.runtime.input.interaction.gesture(name)
        if isinstance(intent,tuple):
            action,value=intent
            if action=='select':self.choose(value)
            elif action=='word':self.enter_word(value)
            elif action=='cycle':self.cycle_word(value)
            elif action=='exit_word':self.confirm_word();self.sentence.exit_flash(value)
        elif intent=='submit':self.runtime.input.submit()
        elif intent=='back':self.back()
        elif intent=='confirm_word':self.confirm_word()
        elif intent=='start_voice':self.runtime.input.start_voice()
        elif intent=='end_voice':self.runtime.input.end_voice()

    def cancel_to_hub(self):
        if self.runtime.input.interaction.mode=='voice':self.runtime.input.cancel_voice(to_hub=True)
        else:self.back()

    def back(self):
        self.pending_choice=None
        if not self.panel_direction:return True
        if self.runtime.executing:return False
        if self.runtime.agent._input_busy() or self.runtime.input._composing:
            self.message.setText('请先结束当前输入，再收起参数。');return False
        self.drafts[self.position]={k:e.toPlainText() for k,e in self.editors.items()}
        self.runtime.input.release();self.runtime.input.set_stage('hub');self.runtime.input.set_focus(self.has_panel_focus())
        self.parameters.hide();self.panel_direction=None;self.wheel.set_active(None);self.wheel.mode='hub'
        self.refresh_button.setEnabled(True);self.refresh_button.show();self.close_button.show();self.message.clear();self.hint.setText('上 / 右 / 下 / 左选择 · Esc 关闭')
        self.wheel.setFocus();return True

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape and self.panel_direction:
            if self.runtime.input.interaction.mode=='voice':self.runtime.input.cancel_voice()
            else:self.handle_gesture('escape')
            event.accept();return
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and self.panel_direction:
            self.runtime.input.request_submit();event.accept();return
        super().keyPressEvent(event)

    def set_execution_busy(self,busy):
        for card in self.cards:
            enabled=not busy and not card['rejected'] and not self.closing
            card['choose'].setEnabled(enabled);card['title'].setEnabled(enabled)
            card['reject'].setEnabled(enabled and bool(card['exposure']))
        if hasattr(self,'run_button'):self.run_button.setEnabled(not busy)
        if hasattr(self,'cancel_button'):self.cancel_button.setEnabled(not busy)
        for editor in self.editors.values():editor.setReadOnly(busy)
        if busy:self.message.setText('正在提交本次选定的流程…')
        elif self.pending_choice and self.has_panel_focus():
            position=self.pending_choice
            if self.cards[position-1]['exposure']:self.choose(position)

    def dismiss_animated(self,*,focus_reason='dismiss'):
        if self.closing or not self.isVisible():return
        self.closing=True;self.pending_choice=None;self._activation_until=0.
        self.timer.stop();self.focus_timer.stop()
        self.wheel.entrance.stop()
        # Invalidate queued gestures and asynchronous submissions immediately;
        # the remaining half second is presentation only.
        self.runtime.dismiss();self._dismissed=True
        # Drop the Hub's transient application activation before resigning its
        # native key window. Otherwise AppKit may promote the host main window.
        self.focus_session.release(focus_reason)
        if QGuiApplication.platformName()=='cocoa':
            from .recommendation_focus import native_window
            native=native_window(self);native.setIgnoresMouseEvents_(True)
            if native.isKeyWindow():native.resignKeyWindow()
        self.fade.setStartValue(self.windowOpacity());self.fade.setEndValue(0.);self.fade.start()

    def finish_dismissal(self):
        self.fade.stop()
        if self.closing:super().done(QDialog.Rejected)

    def _did_finish(self,*_):
        if not self._dismissed:self.runtime.dismiss()
        self._dismissed=True

    def done(self,result):
        if result==QDialog.Rejected and not self.runtime.closed:
            self.dismiss_animated();return
        self.focus_session.discard()
        self.runtime.input.release(sent=result==QDialog.Accepted)
        super().done(result)

    def closeEvent(self,event):
        if self.runtime.closed:self.focus_session.discard();event.accept();return
        event.ignore();self.dismiss_animated()

    def showEvent(self,event):
        super().showEvent(event)
        if hasattr(self,'timer'):self.timer.start()

    def _focus_released(self):
        if self.closing or not self.isVisible():self.click_away.stop()

    def hideEvent(self,event):
        self.timer.stop();self.focus_timer.stop();self.runtime.input.set_focus(False)
        # Keep explicit local-click detection until pending native activation
        # settles, including the short tail after the fade has hidden the Hub.
        if not self.focus_session.return_pending:self.click_away.stop()
        self.fade.stop();self.wheel.entrance.stop();self.pending_choice=None
        super().hideEvent(event)
