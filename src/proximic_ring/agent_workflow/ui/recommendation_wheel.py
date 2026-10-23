"""The reference Hub's 1300 × 720 glass wheel, with native mouse targets.

Painting has no recommendation or execution logic. The controller freezes ranks;
hover only changes presentation. Native editors are placed by the dialog so IME,
voice and stroke commits do not pass through a browser or a second input stack.
"""
import math
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import (QColor, QPainter, QPen, QLinearGradient, QRadialGradient,
                          QPainterPath, QFont, QFontMetricsF, QTextLayout, QTextOption, QRegion)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget, QAbstractButton

ACCENTS = ('#6366f1', '#0ea5e9', '#f59e0b', '#f43f5e')
VECTORS = ((0, -1), (1, 0), (0, 1), (-1, 0))
DIRECTION_NAMES = ('上', '右', '下', '左')
CENTER = QPointF(650,360)
CORE = QRectF(554,264,192,192)
CORE_TEXT = QRectF(574,346,152,44)
ICONS = {
    'food': '<path d="M4 3v6a2 2 0 0 0 4 0V3M6 3v18M20 3c-3 2-4 5-4 8h4M20 3v18"/>',
    'app': '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M17.5 14v7M14 17.5h7"/>',
    'calendar': '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18M8 15h2M14 15h2"/>',
    'send': '<path d="m22 2-7 20-4-9-9-4Z M22 2 11 13"/>',
    'compass': '<circle cx="12" cy="12" r="9"/><path d="m16 8-2.5 5.5L8 16l2.5-5.5Z"/>',
    'music': '<path d="M9 18V5l12-2v13M9 8l12-2"/><ellipse cx="6" cy="18" rx="3" ry="3"/><ellipse cx="18" cy="16" rx="3" ry="3"/>',
    'note': '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 8h16M8 12h8M8 16h5"/>',
}


def icon_for(text):
    text = text.lower()
    for words, icon in ((('餐', 'food', 'restaurant'), 'food'), (('音乐', 'music', '播放'), 'music'),
                        (('备忘录', '笔记', 'notes'), 'note'), (('日程', '日历', 'calendar'), 'calendar'),
                        (('消息', '回复', 'send', 'message'), 'send'), (('safari', '网页', '网站'), 'compass')):
        if any(word in text for word in words): return icon
    return 'app'


def draw_icon(painter, rect, name, color):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>'
    QSvgRenderer(svg.encode()).render(painter, rect)


def font(pixels, weight=QFont.Normal):
    result = QFont('PingFang SC'); result.setPixelSize(max(1, round(pixels))); result.setWeight(weight)
    return result


def _wrapped_lines(text, face, width):
    """Use Qt shaping/boundaries for CJK, long Latin words and emoji sequences."""
    layout=QTextLayout(text,face);option=QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere);layout.setTextOption(option)
    data=text.encode('utf-16-le');lines=[];layout.beginLayout()
    for _ in range(3):
        line=layout.createLine()
        if not line.isValid():break
        line.setLineWidth(width)
        start,end=line.textStart(),line.textStart()+line.textLength()
        lines.append((data[start*2:end*2].decode('utf-16-le').strip(),end,line.height()))
    layout.endLayout()
    return lines


def fitted_core_title(text):
    """At most two measured lines in an inscribed rectangle, never outside it.

    Keep the primary information large; only the display is elided when even
    the minimum readable size cannot fit. The full name remains in the tooltip.
    """
    text=' '.join(text.split()) or '······'
    for size in range(19,14,-1):
        face=font(size,QFont.DemiBold);lines=_wrapped_lines(text,face,CORE_TEXT.width()-2)
        if len(lines)<=2 and sum(line[2] for line in lines)<=CORE_TEXT.height():break
    if len(lines)>2:
        first,end,_=lines[0]
        rest=text.encode('utf-16-le')[end*2:].decode('utf-16-le').strip()
        last=QFontMetricsF(face).elidedText(rest,Qt.ElideRight,CORE_TEXT.width()-2)
        texts=[first,last]
    else:
        # Balance two lines instead of leaving a single character on the last
        # line. Qt still chooses word/grapheme boundaries, never Python slices.
        if len(lines)==2:
            metrics=QFontMetricsF(face)
            def difference(rows):return abs(metrics.horizontalAdvance(rows[0][0])-metrics.horizontalAdvance(rows[1][0]))
            for width in range(int(CORE_TEXT.width()-4),int(CORE_TEXT.width()/2),-2):
                candidate=_wrapped_lines(text,face,width)
                if len(candidate)==2 and difference(candidate)<difference(lines):lines=candidate
        texts=[line[0] for line in lines]
    height=QFontMetricsF(face).height()
    top=CORE_TEXT.center().y()-len(texts)*height/2
    return face,[(line,QRectF(CORE_TEXT.left(),top+i*height,CORE_TEXT.width(),height))
                 for i,line in enumerate(texts)]


def wedge_path(rank):
    center=CENTER;angle=(-90,0,90,180)[rank-1];path=QPainterPath(center)
    for a in range(angle-27,angle+28):
        path.lineTo(center+QPointF(230*math.cos(math.radians(a)),230*math.sin(math.radians(a))))
    path.closeSubpath()
    return path


def shadow(painter, rect, radius, color='#0f172a', strength=18, spread=16, dy=8):
    painter.setPen(Qt.NoPen)
    for i in range(spread, 0, -2):
        shade = QColor(color); shade.setAlpha(max(1, round(strength * (1-i/(spread+2)) / 4)))
        painter.setBrush(shade)
        painter.drawRoundedRect(rect.adjusted(-i, -i+dy, i, i+dy), radius+i, radius+i)


class WheelTarget(QAbstractButton):
    hovered = Signal(int)
    def __init__(self, wheel, rank, *, label=False):
        super().__init__(wheel)
        self.wheel=wheel; self.rank=rank; self.label=label; self.title='暂无推荐'; self.subtitle=''; self.icon='app'
        self.setCursor(Qt.PointingHandCursor); self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setObjectName(('recommendationLabel' if label else 'recommendationChoose')+str(rank))

    def enterEvent(self, event):
        if self.isEnabled(): self.hovered.emit(self.rank)
        super().enterEvent(event)

    def hitButton(self, pos):
        if self.label: return self.rect().contains(pos)
        return math.hypot(pos.x()-self.width()/2, pos.y()-self.height()/2) <= 43*self.wheel.scale

    def paintEvent(self, event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        s=self.wheel.scale; on=self.wheel.highlights[self.rank-1]
        opacity=.4 if self.wheel.active and self.wheel.active!=self.rank else (1. if self.isEnabled() else .45)
        if self.label:opacity*=max(0.,(self.wheel.reveal-.65)/.35)
        p.setOpacity(opacity)
        color=ACCENTS[self.rank-1] if on>.1 else '#475569'
        if self.label:
            align=Qt.AlignCenter if self.rank in (1,3) else (Qt.AlignLeft if self.rank==2 else Qt.AlignRight)
            p.setFont(font(13.5*s,QFont.DemiBold)); p.setPen(QColor(color))
            text=p.fontMetrics().elidedText(self.title,Qt.ElideRight,self.width())
            p.drawText(QRectF(0,0,self.width(),22*s),align|Qt.AlignVCenter,text)
            p.setFont(font(11*s));p.setPen(QColor(ACCENTS[self.rank-1] if on>.1 else '#94a3b8'))
            p.drawText(QRectF(0,22*s,self.width(),18*s),align|Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.subtitle,Qt.ElideRight,self.width()))
        else:
            # Nodes emerge from behind the center; no icon may pass over its
            # key information while the four children travel outward.
            clip=QPainterPath();clip.addRect(QRectF(self.rect()))
            core=QPainterPath();core.addEllipse(QRectF(self.wheel.map_rect(554,264,192,192)).translated(-self.x(),-self.y()))
            p.setClipPath(clip.subtracted(core))
            p.translate(self.width()/2,self.height()/2);p.scale(s,s)
            size=36*(1+.09*on if not self.wheel.active or self.wheel.active==self.rank else .94)
            if self.isDown():size*=.97
            rect=QRectF(-size,-size,2*size,2*size)
            shadow(p,rect,size,color if on else '#0f172a',24,14,7)
            if on:
                halo=QColor(ACCENTS[self.rank-1]);halo.setAlpha(round(24*on));p.setBrush(halo);p.setPen(Qt.NoPen)
                p.drawEllipse(rect.adjusted(-6,-6,6,6))
                halo.setAlpha(round(200*on));p.setPen(QPen(halo,1.5));p.setBrush(Qt.NoBrush)
                p.drawEllipse(rect.adjusted(-9,-9,9,9))
            p.setBrush(QColor(255,255,255,round(204+51*on)));p.setPen(QPen(QColor('#ffffff'),1))
            p.drawEllipse(rect);draw_icon(p,QRectF(-11.5,-11.5,23,23),self.icon,color)


class RecommendationWheel(QWidget):
    chosen=Signal(int)
    backgroundClicked=Signal()
    resized=Signal()
    shapeChanged=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        self.tiles=[];self.labels=[];self.active=None;self.hover=None
        self.context='当前场景';self.context_icon='app';self.highlights=[0.]*4
        self.mode='hub';self.voice_done=False;self.trail=None
        self.scale=1.;self.origin=QPointF();self.setMouseTracking(True)
        # Programmatic keyboard ownership without taking focus from a native
        # editor when its NoFocus Done/Cancel button is clicked.
        self.setFocusPolicy(Qt.TabFocus)
        self.setAutoFillBackground(False)
        self.animation=QVariantAnimation(self);self.animation.setDuration(280)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self._animate)
        self.animation.finished.connect(self.shapeChanged)
        self._start=[0.]*4;self._end=[0.]*4
        self.reveal=1.
        self.entrance=QVariantAnimation(self);self.entrance.setDuration(420)
        self.entrance.setEasingCurve(QEasingCurve.OutCubic)
        self.entrance.valueChanged.connect(self._reveal)

    def open_from_center(self):
        self.entrance.stop()
        reduce_motion=False
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.platformName()=='cocoa':
            from AppKit import NSWorkspace
            reduce_motion=bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
        if reduce_motion:self._reveal(1.);return
        self._reveal(0.)
        self.entrance.setStartValue(0.);self.entrance.setEndValue(1.);self.entrance.start()

    def _reveal(self,value):
        self.reveal=float(value);self.arrange();self.shapeChanged.emit()
        for target in self.tiles+self.labels:target.update()

    def clear(self):
        self.animation.stop();self.entrance.stop();self.reveal=1.
        for child in self.tiles+self.labels:child.hide();child.deleteLater()
        self.tiles=[];self.labels=[];self.active=None;self.hover=None;self.highlights=[0.]*4;self.mode='hub'

    def add_item(self, rank, title, subtitle, tooltip, available):
        node=WheelTarget(self,rank);label=WheelTarget(self,rank,label=True)
        for target in (node,label):
            target.title=title;target.subtitle=subtitle;target.icon=icon_for(title)
            target.setAccessibleName(title);target.setAccessibleDescription(subtitle)
            target.setToolTip(tooltip);target.setEnabled(available)
            target.hovered.connect(self.set_hover)
            target.clicked.connect(lambda checked=False,r=rank:self.chosen.emit(r));target.show()
        self.tiles.append(node);self.labels.append(label);self.arrange()
        return node,label

    def set_hover(self,rank):
        if not self.active and self.hover!=rank:self.hover=rank;self._transition()

    def set_active(self,rank):
        self.active=rank;self.hover=None;self._transition()
        for i,label in enumerate(self.labels,1):label.setVisible(i!=rank)

    def _transition(self):
        self.animation.stop();self._start=self.highlights[:]
        self._end=[float(i== (self.active or self.hover)) for i in range(1,5)]
        self.animation.setStartValue(0.);self.animation.setEndValue(1.);self.animation.start();self.update()
        current=self.active or self.hover
        self.setToolTip(self.tiles[current-1].title if current else '选择一个方向 · Esc 关闭')
        self.shapeChanged.emit()

    def _animate(self,value):
        self.highlights=[a+(b-a)*value for a,b in zip(self._start,self._end)]
        self.update()
        for target in self.tiles+self.labels:target.update()

    def map_rect(self,x,y,w,h):
        return QRectF(self.origin.x()+x*self.scale,self.origin.y()+y*self.scale,w*self.scale,h*self.scale).toRect()

    def visible_region(self):
        """Native window shape follows the floating artwork, not its canvas."""
        from PySide6.QtGui import QTransform
        transform=QTransform();transform.translate(self.origin.x(),self.origin.y());transform.scale(self.scale,self.scale)
        def region(path):return QRegion(transform.map(path).toFillPolygon().toPolygon())
        path=QPainterPath();path.addEllipse(CORE.adjusted(-20,-20,20,20));result=region(path)
        for radius in (140,210) if self.reveal>.05 else ():
            outer=QRegion(self.map_rect(650-radius-2,360-radius-2,2*radius+4,2*radius+4),QRegion.Ellipse)
            inner=QRegion(self.map_rect(650-radius+2,360-radius+2,2*radius-4,2*radius-4),QRegion.Ellipse)
            result|=outer-inner
        for rank,(dx,dy) in enumerate(VECTORS,1):
            node=self.tiles[rank-1] if rank<=len(self.tiles) else None
            if node:result|=QRegion(node.geometry(),QRegion.Ellipse)
            if self.reveal>.65 and rank<=len(self.labels) and not self.labels[rank-1].isHidden():result|=QRegion(self.labels[rank-1].geometry())
            if self.reveal>.05:
                if dx:result|=QRegion(self.map_rect(650+min(dx*96,dx*118),358,22,4))
                else:result|=QRegion(self.map_rect(648,360+min(dy*96,dy*118),4,22))
            if rank in (self.active,self.hover) or self.highlights[rank-1]>.01:result|=region(wedge_path(rank))
        return result

    def arrange(self):
        self.scale=max(.1,min(1.,(self.width()-32)/1300,(self.height()-32)/720))
        self.origin=QPointF((self.width()-1300*self.scale)/2,(self.height()-720*self.scale)/2)
        for rank,((dx,dy),node,label) in enumerate(zip(VECTORS,self.tiles,self.labels),1):
            x,y=650+158*dx*self.reveal,360+158*dy*self.reveal
            node.setGeometry(self.map_rect(x-52,y-52,104,104))
            # Label hit areas follow their visible text, not a wide invisible
            # rectangle that would intercept clicks on the desktop underneath.
            title_width=QFontMetricsF(font(13.5*self.scale,QFont.DemiBold)).horizontalAdvance(label.title)
            sub_width=QFontMetricsF(font(11*self.scale)).horizontalAdvance(label.subtitle)
            width=min(300 if rank in (1,3) else 212,math.ceil(max(title_width,sub_width)/self.scale)+4)
            if rank==1:rect=(x-width/2,y-90,width,40)
            elif rank==3:rect=(x-width/2,y+46,width,40)
            elif rank==2:rect=(x+50,y-20,width,40)
            else:rect=(x-50-width,y-20,width,40)
            label.setGeometry(self.map_rect(*rect))
        self.resized.emit();self.update()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.arrange()

    def leaveEvent(self,event):
        if not self.active:self.hover=None;self._transition()
        super().leaveEvent(event)

    def mouseMoveEvent(self,event):
        if not self.active and self.hover:self.hover=None;self._transition()
        super().mouseMoveEvent(event)

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:self.backgroundClicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.origin);p.scale(self.scale,self.scale)
        center=CENTER
        p.save();p.setOpacity(self.reveal)
        for i,(dx,dy) in enumerate(VECTORS):
            if self.highlights[i]:
                path=wedge_path(i+1);grad=QRadialGradient(center,230)
                c=QColor(ACCENTS[i]);c.setAlpha(round(33*self.highlights[i]));grad.setColorAt(0,c)
                c.setAlpha(round(20*self.highlights[i]));grad.setColorAt(.62,c);c.setAlpha(0);grad.setColorAt(1,c)
                p.fillPath(path,grad)
        p.setBrush(Qt.NoBrush)
        pen=QPen(QColor(15,23,42,18),1);pen.setDashPattern([2,9]);p.setPen(pen);p.drawEllipse(center,210,210)
        p.setPen(QPen(QColor(15,23,42,23),1));p.drawEllipse(center,140,140)
        for i,(dx,dy) in enumerate(VECTORS):
            p.setPen(QPen(QColor(ACCENTS[i]) if self.highlights[i]>.1 else QColor(15,23,42,30),1.5+1.5*self.highlights[i]))
            p.drawLine(center+QPointF(dx*96,dy*96),center+QPointF(dx*118,dy*118))
        p.restore()
        core=CORE;shadow(p,core,96,strength=19,spread=24,dy=15)
        p.setPen(QPen(QColor('#ffffff'),1.5));p.setBrush(QColor(255,255,255,209));p.drawEllipse(core)
        current=self.active
        if current:
            halo=QColor(ACCENTS[current-1]);halo.setAlpha(80)
            p.setBrush(Qt.NoBrush);p.setPen(QPen(halo,1.5));p.drawEllipse(core.adjusted(-10,-10,10,10))
        p.save();clip=QPainterPath();clip.addEllipse(core.adjusted(8,8,-8,-8));p.setClipPath(clip)
        badge=QRectF(632,278,36,36);gradient=QLinearGradient(badge.topLeft(),badge.bottomRight())
        gradient.setColorAt(0,QColor('#38bdf8'));gradient.setColorAt(1,QColor('#2563eb'))
        p.setPen(Qt.NoPen);p.setBrush(gradient);p.drawRoundedRect(badge,10,10)
        draw_icon(p,QRectF(640.5,286.5,19,19),self.context_icon,'#ffffff')
        p.setPen(QColor('#94a3b8'));p.setFont(font(11))
        p.drawText(QRectF(580,318,140,17),Qt.AlignCenter,p.fontMetrics().elidedText(self.context,Qt.ElideRight,140))
        p.setFont(font(12.5,QFont.Medium));p.drawText(QRectF(580,334,140,17),Qt.AlignCenter,'你想')
        title=self.tiles[current-1].title if current and current<=len(self.tiles) else '······'
        face,lines=fitted_core_title(title)
        p.setFont(face);p.setPen(QColor(ACCENTS[current-1] if current else '#0f172a'))
        for text,rect in lines:p.drawText(rect,Qt.AlignCenter|Qt.TextSingleLine,text)
        arrows=('↑','→','↓','←');opposites=('↓','←','↑','→')
        if self.mode=='hub':legend=['朝任一方向滑动']
        elif self.mode=='word':legend=['← →  换到别的词','↑ ↓  换这个词','tap 语音 · 两端退出']
        elif self.mode=='voice':legend=['文字正在落定…' if self.voice_done else '正在听…','tap 结束并确认']
        else:
            r=(current or 1)-1
            legend=[arrows[r]+' 继续'+DIRECTION_NAMES[r]+'滑 · 确认执行',opposites[r]+' 反向滑 · 返回中心','tap 进入选词']
        for index,text in enumerate(legend):
            p.setFont(font(10.5,QFont.Medium if index==0 and current else QFont.Normal))
            p.setPen(QColor(ACCENTS[current-1] if index==0 and current else '#94a3b8'))
            p.drawText(QRectF(570,392+16*index,160,16),Qt.AlignCenter,text)
        p.restore()
        if self.trail:
            start,end=self.trail;delta=end-start;length=math.hypot(delta.x(),delta.y())
            if length>6:
                p.setBrush(Qt.NoBrush);pen=QPen(QColor(15,23,42,45),1.5);pen.setDashPattern([3,7]);p.setPen(pen)
                p.drawEllipse(start,56,56);p.setPen(QPen(QColor('#64748b'),2.5));p.drawLine(start,end)
                p.setBrush(QColor('#64748b'));p.setPen(Qt.NoPen);p.drawEllipse(start,4,4)
