"""Focusable per-screen frozen canvas. Mouse and Ring share one selection state."""
from PySide6.QtCore import Qt,QRect,QRectF,QPointF
from PySide6.QtGui import QPainter,QColor,QPen,QPainterPath,QFont
from PySide6.QtWidgets import QWidget,QPushButton,QLabel,QHBoxLayout,QFrame
from proximic_ring.ui.shared.overlay_stacking import OverlayVisibilityGuard


class CaptureOverlay(QWidget):
    def __init__(self,controller,index,spec):
        super().__init__();self.controller=controller;self.index=index;self.spec=spec;self.image=None
        self.setObjectName('agent-screen-capture');self.setWindowTitle('Mythlink · 截图')
        self.setWindowFlags(Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating);self.setAttribute(Qt.WA_QuitOnClose,False)
        self.setAttribute(Qt.WA_TranslucentBackground);self.setMouseTracking(True);self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(*spec['geometry']);self.setCursor(Qt.CrossCursor)
        self.toolbar=QFrame(self);self.toolbar.setObjectName('captureToolbar')
        self.toolbar.setStyleSheet('QFrame#captureToolbar {background:#172333;border:1px solid #476078;border-radius:14px;} QLabel{color:#edf5ff;background:transparent;font-size:13px;} QPushButton{background:#31445b;color:white;border:0;border-radius:7px;padding:8px 14px;font-size:13px;} QPushButton:hover{background:#45627e;} QPushButton:disabled{color:#7e8b99;background:#243344;}')
        layout=QHBoxLayout(self.toolbar);layout.setContentsMargins(18,12,12,12);layout.setSpacing(12)
        self.hint=QLabel();layout.addWidget(self.hint)
        self.back=QPushButton('← 退出截图');self.back.clicked.connect(controller.back);layout.addWidget(self.back)
        self.confirm=QPushButton('发送 →');self.confirm.clicked.connect(controller.confirm);layout.addWidget(self.confirm)
        self.confirm.setToolTip('将截图加入任务输入框')
        for button in (self.back,self.confirm):button.setFocusPolicy(Qt.NoFocus)
        self.winId();self.guard=OverlayVisibilityGuard(self.windowHandle(),self,input_workspace=True,level_offset=2)
        self.refresh()

    def refresh(self):
        phase=self.controller.phase
        hints={'preparing':'正在截取屏幕 · 左滑退出','locating':'tap 开始圈选 · 左滑退出',
            'drawing':'移动画圈 · 再 tap 完成 · 左滑清除选区','preview':'右滑加入输入框 · 左滑清除选区'}
        self.hint.setText(self.controller.notice or hints.get(phase,''))
        self.confirm.setEnabled(phase=='preview')
        self.back.setText('← 取消选区' if phase in ('drawing','preview') else '← 退出截图')
        self.toolbar.adjustSize()
        sx,sy,_,_=self.spec['geometry']
        work=QRect(*self.spec.get('available_geometry',self.spec['geometry'])).translated(-sx,-sy).intersected(self.rect())
        # Sit 32 pt above the usable area's bottom (above a visible Dock),
        # with at least 96 pt below the toolbar even when the Dock auto-hides.
        bottom=min(work.y()+work.height()-32,self.height()-96)
        self.toolbar.move(max(work.x()+8,work.x()+(work.width()-self.toolbar.width())//2),
                          max(work.y()+8,bottom-self.toolbar.height()))
        self.update()

    def present(self,*,focus=False):
        self.show();self.guard.refresh()
        if focus:
            from PySide6.QtGui import QGuiApplication
            if QGuiApplication.platformName()=='cocoa':
                from proximic_ring.agent_workflow.ui.recommendation_focus import native_window
                native_window(self).makeKeyWindow();self.windowHandle().requestActivate()
            else:self.raise_();self.activateWindow()
            self.setFocus(Qt.OtherFocusReason)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        if self.image is not None:p.drawImage(QRectF(self.rect()),self.image,QRectF(self.image.rect()))
        p.fillRect(self.rect(),QColor(8,15,25,125))
        selection=self.controller.selection
        if self.index!=self.controller.selected_screen or not selection or not selection.bounds:return
        sx,sy,_,_=self.spec['geometry'];l,t,r,b=selection.bounds;rect=QRectF(l-sx,t-sy,r-l,b-t)
        if self.image is not None and rect.width()>0 and rect.height()>0:
            source=QRectF(rect.x()*self.image.width()/self.width(),rect.y()*self.image.height()/self.height(),
                          rect.width()*self.image.width()/self.width(),rect.height()*self.image.height()/self.height())
            p.drawImage(rect,self.image,source)
        p.setPen(QPen(QColor('#83cdff'),2));p.setBrush(Qt.NoBrush);p.drawRect(rect)
        if self.controller.phase=='drawing' and selection.points:
            path=QPainterPath(QPointF(selection.points[0][0]-sx,selection.points[0][1]-sy))
            for x,y in selection.points[1:]:path.lineTo(x-sx,y-sy)
            p.setPen(QPen(QColor('#d1f3ff'),1.5));p.drawPath(path)
        if selection.valid and self.image is not None:
            _,_,w,h=selection.pixels(self.image.width(),self.image.height())
            p.setFont(QFont('PingFang SC',11));p.setPen(QColor('white'))
            label=QRectF(max(8,rect.x()),max(8,rect.y()-30),240,25)
            p.fillRect(label,QColor(12,23,38,220));p.drawText(label.adjusted(8,0,0,0),Qt.AlignVCenter,f'{w} × {h} px')

    def mouseMoveEvent(self,event):
        self.controller.move(event.globalPosition().x(),event.globalPosition().y());event.accept()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton:
            self.controller.tap(self.index,event.globalPosition().x(),event.globalPosition().y());event.accept()

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape:self.controller.cancel()
        elif event.key() in (Qt.Key_Return,Qt.Key_Enter):self.controller.confirm()
        elif event.key()==Qt.Key_Space:
            from PySide6.QtGui import QCursor
            point=QCursor.pos();self.controller.tap(self.index,point.x(),point.y())
        elif event.key()==Qt.Key_Left:self.controller.back()
        elif event.key()==Qt.Key_Right:self.controller.confirm()
        event.accept()

    def closeEvent(self,event):
        if self.controller.active:self.controller.cancel()
        event.accept()
