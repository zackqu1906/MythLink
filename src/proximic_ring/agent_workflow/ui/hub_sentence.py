"""模型句式的原生展示：可换词保留原参数键，长正文只缩略显示、不截断提交。

选词层只画词语 chip；明确输入时才显示原生编辑器。长正文仅在输入时展开。
没有句式的历史文案临时显示标签，后台补齐后在下次唤起读取新句式。
"""
import math
from PySide6.QtCore import Qt,QRectF,QPoint,QTimer,QVariantAnimation,QEasingCurve
from PySide6.QtGui import QPainter,QColor,QPen,QFontMetricsF
from PySide6.QtWidgets import QWidget,QLabel
from .recommendation_wheel import font,ACCENTS


def sentence_parts(display):
    if display.get('sentence'):return display['sentence']
    result=[dict(text=display['title']+'：' if display['parameters'] else display['title'])]
    for name,field in display['parameters'].items():
        if len(result)>1:result.append(dict(text='，'))
        result.extend([dict(text=field['label']+' '),dict(parameter=name)])
    return result


class SentenceCard(QWidget):
    def __init__(self,dialog,display,editors,candidates):
        super().__init__(dialog.parameters);self.dialog=dialog;self.display=display;self.editors=editors
        self.parts=sentence_parts(display);self.names=[part['parameter'] for part in self.parts if 'parameter' in part]
        self.candidates=candidates;self.rects={};self.pieces=[];self.s=1.;self.mode='action';self.slot=0;self.offset=0.;self.editing=False
        self.setMouseTracking(True);self.setFocusPolicy(Qt.NoFocus)
        # A child of the canvas can sit beside a chip without being clipped by
        # the sentence card. It never becomes a window or takes input focus.
        self.candidate_notice=QLabel('无其他候选词',dialog.wheel)
        self.candidate_notice.setTextFormat(Qt.PlainText)
        self.candidate_notice.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.candidate_notice.setFocusPolicy(Qt.NoFocus);self.candidate_notice.hide()
        self.destroyed.connect(self.candidate_notice.deleteLater)
        self.notice_timer=QTimer(self);self.notice_timer.setSingleShot(True)
        self.notice_timer.setInterval(1600);self.notice_timer.timeout.connect(self.clear_candidate_notice)
        self.layout_timer=QTimer(self);self.layout_timer.setSingleShot(True);self.layout_timer.timeout.connect(dialog._arrange_overlays)
        self.bounce=QVariantAnimation(self);self.bounce.setDuration(360);self.bounce.setEasingCurve(QEasingCurve.OutCubic)
        self.bounce.valueChanged.connect(self._offset)
        for name,editor in editors.items():
            editor.setParent(self);editor.hide()
            editor.textChanged.connect(self.relayout)
        self.relayout()

    def _offset(self,value):self.offset=float(value);self.relayout()
    def exit_flash(self,direction):
        self.bounce.stop();self.bounce.setStartValue(direction*13.);self.bounce.setEndValue(0.);self.bounce.start()
    def token_at(self,point):
        return next((self.names.index(name) for name,rect in self.rects.items() if rect.contains(point)),None)
    def value(self,name):return self.editors[name].toPlainText()
    def height_units(self):return self.content_bottom+14+(58 if self.expanded else 0)+24
    def set_mode(self,mode,slot):
        self.clear_candidate_notice()
        self.mode=mode;self.slot=slot;self.relayout()
    def clear_candidate_notice(self):
        self.notice_timer.stop();self.candidate_notice.hide();self.dialog._update_shape()
    def show_candidate_notice(self):
        if self.mode!='word' or self.editing:return
        self.candidate_notice.show();self.arrange_candidate_notice();self.notice_timer.start()
        self.dialog._update_shape()
    def arrange_candidate_notice(self):
        notice=self.candidate_notice
        if notice.isHidden() or not self.names:return
        s=self.s;rect=self.rects[self.names[self.slot]]
        anchor=self.mapTo(self.dialog.wheel,rect.topRight().toPoint())
        notice.setStyleSheet(f'color:#475569;font-size:{max(10,round(11*s))}px;background:#ffffff;border:1px solid #e2e8f0;border-radius:{round(6*s)}px;padding:{round(5*s)}px {round(9*s)}px;')
        notice.adjustSize();area=self.dialog.wheel.rect();gap=round(8*s)
        x=max(gap,min(anchor.x()+gap,area.right()-notice.width()-gap))
        y=max(gap,min(anchor.y()-notice.height()-gap,area.bottom()-notice.height()-gap))
        notice.move(QPoint(x,y));notice.raise_()
    def hideEvent(self,event):
        self.clear_candidate_notice();super().hideEvent(event)
    def relayout(self):
        if self.editing:self.clear_candidate_notice()
        self.s=self.dialog.wheel.scale;s=self.s;metrics=QFontMetricsF(font(15*s));width=max(40,self.width()-36*s)
        self.rects={};self.pieces=[];x=0.;y=22*s;rows=[];row=[]
        # Reserve room for fixed text; cap unusually long parameter values in
        # the sentence only. The complete native document is never shortened.
        for part in self.parts:
            name=part.get('parameter');text=(self.value(name) or self.display['parameters'][name]['label']) if name else part['text']
            text=' '.join(text.split())
            if name:
                options=[text]+[' '.join(c['value'].split()) for c in self.candidates.get(name,[])]
                w=min(140*s,max(48*s,max(metrics.horizontalAdvance(v)+12*s for v in options)))
            else:w=min(width,math.ceil(metrics.horizontalAdvance(text))+2*s)
            if row and x+w>width:rows.append((row,x,y));row=[];x=0.;y+=32*s
            row.append((name,text,QRectF(x,y,w,26*s)));x+=w
        if row:rows.append((row,x,y))
        for row,w,y in rows:
            for name,text,rect in row:
                rect.translate((self.width()-w)/2+self.offset*s,0)
                self.pieces.append((name,text,rect))
                if name:self.rects[name]=rect
        self.content_bottom=(y+26*s)/s if rows else 48
        current=self.names[self.slot] if self.names and self.slot<len(self.names) else None
        self.expanded=bool(current and (self.mode=='voice' or self.editing) and
            ('\n' in self.value(current) or metrics.horizontalAdvance(self.value(current))>128*s))
        accent=ACCENTS[self.dialog.position-1]
        for name,editor in self.editors.items():
            editor.selection_preview=self.mode=='word' and not self.editing
            active=name==current and self.mode in ('word','voice')
            if active:
                rect=self.rects[name]
                if self.expanded:rect=QRectF(18*s,(self.content_bottom+6)*s,self.width()-36*s,48*s)
                editor.setGeometry(rect.toRect())
                background='transparent' if editor.selection_preview else '#f1f5ff'
                editor.setStyleSheet(f'QPlainTextEdit {{font-size:{round(15*s)}px;color:{accent};border:0;border-radius:{round(5*s)}px;background:{background};padding:{round(2*s)}px;selection-background-color:{accent};selection-color:white;}}')
            editor.setVisible(active);editor.viewport().update()
        self.setToolTip(''.join(self.value(p['parameter']) or self.display['parameters'][p['parameter']]['label'] if 'parameter' in p else p['text'] for p in self.parts))
        self.update()
        self.layout_timer.start(0)

    def candidate_hint(self):
        if not self.names:return ''
        name=self.names[self.slot];options=[c['value'] for c in self.candidates.get(name,[])]
        if self.editing:return '输入中 · Enter 确认 · 上下回到候选'
        if not options:return '暂无历史候选 · tap 语音输入'
        value=self.value(name)
        if not value and any(c.get('omitted') for c in self.candidates.get(name,[])):
            return '可选输入留空 · 上下选择历史值 · tap 语音'
        position=f'{options.index(value)+1}/{len(options)}' if value in options else f'自填 · {len(options)}个历史值'
        return f'候选 {position} · 上下切换 · tap 语音'

    def resizeEvent(self,event):super().resizeEvent(event);self.relayout()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);s=self.s;accent=QColor(ACCENTS[self.dialog.position-1])
        p.setFont(font(15*s))
        for name,text,rect in self.pieces:
            active=name and self.names.index(name)==self.slot and self.mode in ('word','voice')
            if active:
                tint=QColor(accent);tint.setAlpha(24 if self.mode=='word' else 50)
                p.setBrush(tint);p.setPen(QPen(accent,1));p.drawRoundedRect(rect,5*s,5*s)
                p.setFont(font(10*s));p.drawText(rect.adjusted(0,-14*s,0,-24*s),Qt.AlignCenter,'↑ ↓');p.setFont(font(15*s))
            p.setPen(accent if name else QColor('#475569'))
            p.drawText(rect.adjusted(4*s if name else 0,0,-4*s if name else 0,0),Qt.AlignVCenter|Qt.AlignLeft,
                p.fontMetrics().elidedText(text,Qt.ElideRight,math.ceil(rect.width()-(8*s if name else 0))))
            if name:
                p.setPen(QPen(accent,1.5*s));p.drawLine(rect.bottomLeft(),rect.bottomRight())
        p.setFont(font(20*s));p.setPen(accent if self.offset else QColor('#94a3b8'))
        if self.mode=='word' or self.offset:
            p.drawText(QRectF(1*s,22*s,16*s,26*s),Qt.AlignCenter,'‹');p.drawText(QRectF(self.width()-17*s,22*s,16*s,26*s),Qt.AlignCenter,'›')
        p.setFont(font(10*s));p.setPen(QColor('#94a3b8'))
        hint={'action':'点词修改 · 向外提交 · 向内返回','word':self.candidate_hint(),'voice':'正在听写 · tap 结束并确认'}.get(self.mode,'')
        p.drawText(QRectF(18*s,self.height()-28*s,self.width()-36*s,20*s),Qt.AlignCenter,hint)
