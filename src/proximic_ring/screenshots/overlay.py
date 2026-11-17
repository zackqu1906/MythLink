"""A translucent screenshot canvas shared by mouse and Ring clicks."""
from PySide6.QtCore import Qt, QRect, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QGuiApplication
from PySide6.QtWidgets import (QWidget, QPushButton, QLabel, QHBoxLayout,
                              QVBoxLayout, QFrame, QGraphicsDropShadowEffect)

from proximic_ring.ui.shared.overlay_stacking import OverlayVisibilityGuard


class SelectionIcon(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 210), 1.5, Qt.DashLine))
        painter.drawRoundedRect(QRectF(4, 6, 20, 16), 2, 2)


class CaptureOverlay(QWidget):
    def __init__(self, controller, index, spec):
        super().__init__()
        self.controller, self.index, self.spec = controller, index, spec
        self.image = None
        self._mouse_press = None
        self.setObjectName('mythlink-screen-capture')
        self.setWindowTitle('Mythlink · 截图')
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(*spec['geometry'])
        self.setCursor(Qt.CrossCursor)

        self.toolbar = QFrame(self)
        self.toolbar.setObjectName('captureToolbar')
        self.toolbar.setCursor(Qt.ArrowCursor)
        self.toolbar.setStyleSheet('''
            QFrame#captureToolbar {
                background: rgba(38, 38, 40, 232);
                border: 1px solid rgba(255, 255, 255, 46);
                border-radius: 14px;
            }
            QLabel { color: rgba(255, 255, 255, 230); background: transparent; border: 0; }
            QLabel#captureTitle { font-size: 13px; font-weight: 600; }
            QLabel#captureHint { color: rgba(255, 255, 255, 160); font-size: 11px; }
            QPushButton {
                color: #202023; background: #f2f2f4; border: 0;
                border-radius: 7px; font-size: 13px; font-weight: 500; padding: 8px 12px;
            }
            QPushButton:hover { background: white; }
            QPushButton:pressed { background: #cfcfd3; }
            QPushButton:disabled { background: rgba(255, 255, 255, 20); color: rgba(255, 255, 255, 95); }
        ''')
        shadow = QGraphicsDropShadowEffect(self.toolbar)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.toolbar.setGraphicsEffect(shadow)
        layout = QHBoxLayout(self.toolbar)
        layout.setContentsMargins(12, 12, 14, 12)
        layout.setSpacing(12)

        self.back = QPushButton()
        self.back.setObjectName('captureClose')
        self.back.setAccessibleName('退出截图')
        self.back.clicked.connect(controller.back)
        layout.addWidget(self.back)
        divider = QFrame()
        divider.setFixedSize(1, 26)
        divider.setStyleSheet('background: rgba(255, 255, 255, 30); border: 0;')
        layout.addWidget(divider)
        self.selection_icon = SelectionIcon(self.toolbar)
        layout.addWidget(self.selection_icon)
        information = QVBoxLayout()
        information.setSpacing(3)
        self.title = QLabel()
        self.title.setObjectName('captureTitle')
        information.addWidget(self.title)
        self.hint = QLabel()
        self.hint.setObjectName('captureHint')
        self.hint.setWordWrap(True)
        self.hint.setMinimumWidth(0)
        information.addWidget(self.hint)
        layout.addLayout(information, 1)
        self.confirm = QPushButton(controller.confirm_label)
        self.confirm.setObjectName('captureConfirm')
        self.confirm.setToolTip(controller.confirm_hint)
        self.confirm.clicked.connect(controller.confirm)
        layout.addWidget(self.confirm)
        for button in (self.back, self.confirm):
            button.setFocusPolicy(Qt.NoFocus)
        self.winId()
        self.guard = OverlayVisibilityGuard(self.windowHandle(), self, input_workspace=True, level_offset=2)
        self.refresh()

    def refresh(self):
        phase = self.controller.phase
        titles = {'preparing': '正在截取屏幕…', 'locating': '点击第一个角',
                  'selecting': '点击另一个角', 'preview': '选区已就绪'}
        exit_hint = self.controller.exit_hint
        self.back.setText('← ' + exit_hint)
        self.back.setToolTip(exit_hint + ' · Esc')
        hints = {'preparing': exit_hint,
                 'locating': '两次点击选择对角 · 支持任意方向',
                 'selecting': '移动光标预览 · 再次点击确定选区', 'preview': self.controller.preview_hint}
        self.title.setText('未能保存' if self.controller.notice and phase == 'preview' else titles.get(phase, ''))
        self.hint.setText(self.controller.notice or hints.get(phase, ''))
        self.confirm.setEnabled(phase == 'preview')
        sx, sy, _, _ = self.spec['geometry']
        work = QRect(*self.spec.get('available_geometry', self.spec['geometry'])).translated(-sx, -sy).intersected(self.rect())
        self.toolbar.setFixedWidth(min(640, max(280, work.width() - 32)))
        self.toolbar.adjustSize()
        # Keep controls above the Dock, including an auto-hiding Dock.
        bottom = min(work.y() + work.height() - 32, self.height() - 96)
        self.toolbar.move(max(work.x() + 8, work.x() + (work.width() - self.toolbar.width()) // 2),
                          max(work.y() + 8, bottom - self.toolbar.height()))
        self.update()

    def present(self, *, focus=False):
        self.show()
        self.guard.refresh()
        if focus:
            if QGuiApplication.platformName() == 'cocoa':
                from proximic_ring.agent_workflow.ui.recommendation_focus import native_window
                native_window(self).makeKeyWindow()
                self.windowHandle().requestActivate()
            else:
                self.raise_()
                self.activateWindow()
            self.setFocus(Qt.OtherFocusReason)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.image is not None:
            painter.drawImage(QRectF(self.rect()), self.image, QRectF(self.image.rect()))
        # A neutral translucent veil preserves the user's screen and its colors.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 64))
        selection = self.controller.selection
        if self.index != self.controller.selected_screen or not selection or not selection.bounds:
            return
        sx, sy, _, _ = self.spec['geometry']
        left, top, right, bottom = selection.bounds
        rect = QRectF(left - sx, top - sy, right - left, bottom - top)
        if self.image is not None and rect.width() > 0 and rect.height() > 0:
            source = QRectF(rect.x() * self.image.width() / self.width(),
                            rect.y() * self.image.height() / self.height(),
                            rect.width() * self.image.width() / self.width(),
                            rect.height() * self.image.height() / self.height())
            painter.drawImage(rect, self.image, source)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 100), 3))
        painter.drawRect(rect)
        painter.setPen(QPen(QColor(255, 255, 255, 245), 1))
        painter.drawRect(rect)
        # Short corner strokes make the boundary legible without resize handles.
        painter.setPen(QPen(Qt.white, 2, Qt.SolidLine, Qt.RoundCap))
        length = min(8, rect.width() / 3, rect.height() / 3)
        for x, y, dx, dy in ((rect.left(), rect.top(), 1, 1),
                             (rect.right(), rect.top(), -1, 1),
                             (rect.left(), rect.bottom(), 1, -1),
                             (rect.right(), rect.bottom(), -1, -1)):
            painter.drawLine(QPointF(x, y + dy * length), QPointF(x, y))
            painter.drawLine(QPointF(x, y), QPointF(x + dx * length, y))
        if self.controller.phase == 'selecting':
            # Keep the first corner visible even before there is a rectangle.
            anchor_x, anchor_y = selection.points[0]
            painter.setPen(QPen(QColor(0, 0, 0, 120), 2))
            painter.setBrush(Qt.white)
            painter.drawEllipse(QPointF(anchor_x - sx, anchor_y - sy), 3.5, 3.5)
        if selection.valid and self.image is not None:
            _, _, width, height = selection.pixels(self.image.width(), self.image.height())
            painter.setFont(QFont(QGuiApplication.font().family(), 11))
            text = f'{width} × {height}'
            label_width = painter.fontMetrics().horizontalAdvance(text) + 20
            x = min(max(8, rect.left()), max(8, self.width() - label_width - 8))
            y = rect.top() - 34 if rect.top() >= 42 else rect.top() + 10
            label = QRectF(x, y, label_width, 25)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(30, 30, 32, 210))
            painter.drawRoundedRect(label, 6, 6)
            painter.setPen(QColor(255, 255, 255, 235))
            painter.drawText(label, Qt.AlignCenter, text)

    def mouseMoveEvent(self, event):
        if self._mouse_press is not None:
            origin, _ = self._mouse_press
            if (event.globalPosition() - origin).manhattanLength() >= QGuiApplication.styleHints().startDragDistance():
                self._mouse_press = None
        self.controller.move(event.globalPosition().x(), event.globalPosition().y())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self.toolbar.geometry().contains(event.position().toPoint()):
                self._mouse_press = (event.globalPosition(), self.controller.pointer_context)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        # Qt replaces the second press with this event. It is still one point.
        self.mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            press, self._mouse_press = self._mouse_press, None
            if press is not None:
                origin, context = press
                point = event.globalPosition()
                if (context == self.controller.pointer_context
                        and (point - origin).manhattanLength() < QGuiApplication.styleHints().startDragDistance()
                        and not self.toolbar.geometry().contains(event.position().toPoint())):
                    self.controller.select_point(self.index, point.x(), point.y())
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Left): self.controller.cancel()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Right): self.controller.confirm()
        event.accept()

    def closeEvent(self, event):
        if self.controller.active: self.controller.cancel()
        event.accept()
