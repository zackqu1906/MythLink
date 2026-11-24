"""Compact native desktop layout for the sentence trial window."""
from PySide6.QtCore import Qt, QSize, QObject, QEvent, QEasingCurve, QVariantAnimation, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QFont
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
                              QHBoxLayout, QGridLayout, QSizePolicy, QProgressBar, QScrollArea,
                              QGraphicsOpacityEffect)
from shiboken6 import delete
import stroke_trial_menu as trial_menu
from proximic_ring.touchpad.stroke.stroke_menu import MORE_PUNCTUATION_CELLS


class SquareArea(QWidget):
    """Fit a square child inside the available space without growing the window."""
    def __init__(self, child, alignment=Qt.AlignHCenter):
        super().__init__()
        self.child = child
        self.alignment = alignment
        child.setParent(self)
        child.setMinimumSize(0, 0)
        child.show()
        self.setMinimumSize(140, 140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self):
        return QSize(280, 280)

    def resizeEvent(self, event):
        side = min(self.width(), self.height())
        x = self.width() - side if self.alignment == Qt.AlignRight else 0 if self.alignment == Qt.AlignLeft else (self.width() - side) // 2
        self.child.setGeometry(x, (self.height() - side) // 2, side, side)
        super().resizeEvent(event)


class TraceMenu(QFrame):
    """Keep a click-through ink layer above the native candidate buttons."""
    def __init__(self, trace):
        super().__init__()
        self.trace = trace
        trace.setParent(self)
        trace.setMinimumSize(0, 0)
        trace.set_overlay_mode(True)
        trace.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.trace.setGeometry(self.contentsRect())
        self.trace.raise_()


class TrialPullSlider(QWidget):
    def __init__(self, parent, name, direction, caption):
        super().__init__(parent)
        self.setObjectName(name)
        self.setFixedSize(60, 58)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.direction = direction
        self.caption = caption
        self.progress = 0
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0)
        self.setGraphicsEffect(self.effect)
        self.fade = QVariantAnimation(self)
        self.fade.setDuration(120)
        self.fade.setEasingCurve(QEasingCurve.OutCubic)
        self.fade.valueChanged.connect(self.effect.setOpacity)
        self.fade.finished.connect(self._finish_fade)
        self.shown = False
        self.hide()

    def set_shown(self, shown):
        if shown == self.shown:
            return
        self.shown = shown
        self.fade.stop()
        self.fade.setStartValue(self.effect.opacity())
        self.fade.setEndValue(1. if shown else 0.)
        if shown:
            self.show()
            self.raise_()
        self.fade.start()

    def _finish_fade(self):
        if not self.shown:
            self.hide()

    def set_progress(self, value):
        self.progress = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        destructive = self.direction == 'left'
        armed = self.progress >= 1
        edge = QColor('#c2413b' if destructive else '#345ee5')
        ink = QColor('white' if armed else '#a6322c' if destructive else '#234cdb')
        bounds = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        shape = QPainterPath()
        shape.addRoundedRect(bounds, 16, 16)
        painter.fillPath(shape, QColor('#fce9e6' if destructive else '#edf2fb'))
        painter.save()
        painter.setClipPath(shape)
        height = bounds.height() * self.progress
        width = bounds.width() * self.progress
        if destructive:
            fill = QRectF(bounds.right() - width, bounds.top(), width, bounds.height())
        else:
            y = bounds.bottom() - height if self.direction == 'up' else bounds.top()
            fill = QRectF(bounds.left(), y, bounds.width(), height)
        painter.fillRect(fill, QColor(('#9c2f2a' if armed else '#edaba5') if destructive
                                     else ('#234cdb' if armed else '#aabcf2')))
        painter.restore()
        painter.setPen(edge)
        painter.drawPath(shape)
        painter.setPen(ink)
        painter.save()
        painter.translate(30, 20)
        painter.rotate({'left': 0, 'up': 90, 'down': -90}[self.direction])
        for offset in (-3, 3) if destructive else (0,):
            arrow = QPainterPath()
            arrow.moveTo(3 + offset, -4)
            arrow.lineTo(-2 + offset, 0)
            arrow.lineTo(3 + offset, 4)
            painter.drawPath(arrow)
        painter.restore()
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(QRectF(0, 32, 60, 17), Qt.AlignCenter, self.caption)


class TrialPunctuationButton(QPushButton):
    def __init__(self, symbol, parent):
        super().__init__(parent)
        self.setAccessibleName(symbol)
        self.ink = QPainterPath()
        font = QFont(self.font())
        font.setPixelSize(20)
        self.ink.addText(0, 0, font, symbol)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.ink.boundingRect().center()
        painter.translate(self.width() / 2 - center.x(), self.height() / 2 - center.y())
        painter.fillPath(self.ink, QColor('white' if self.isChecked() else '#24334d'))


class TrialPunctuationOverlay(QWidget):
    """Show the same submenu above the standalone trial's candidate grid."""

    def __init__(self, window, menu):
        super().__init__(window)
        self.window = window
        self.menu = menu
        self.slider = TrialPullSlider(window, 'strokePunctuationPull', 'up', '更多')
        self.cancel_slider = TrialPullSlider(window, 'strokeCancelPunctuation', 'down', '取消')
        self.clear_slider = TrialPullSlider(window, 'strokeClearPull', 'left', '清空')
        self.clear_slider.hide()
        self.setObjectName('strokeMorePunctuation')
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(
            'QWidget#strokeMorePunctuation {background:rgba(243,246,252,235);'
            'border:1px solid #aabcf2; border-radius:14px}'
            'QPushButton {background:rgba(255,255,255,205); border:1px solid #dce4f0;'
            'border-radius:8px; color:#24334d; font-size:20px; padding:0}'
            'QPushButton:checked {background:#234cdb; color:white; border-color:#1739ac}')
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(4)
        self.buttons = {}
        for index, cell in enumerate(MORE_PUNCTUATION_CELLS):
            row = QHBoxLayout()
            row.setSpacing(1)
            for symbol in cell:
                button = TrialPunctuationButton(symbol, self)
                button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
                button.setFocusPolicy(Qt.NoFocus)
                button.setCheckable(True)
                button.clicked.connect(lambda checked=False, symbol=symbol: window.punctuate(symbol))
                self.buttons[symbol] = button
                row.addWidget(button)
            grid.addLayout(row, index // 3, index % 3)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.animation = QVariantAnimation(self)
        self.animation.valueChanged.connect(self.animate)
        self.animation.finished.connect(self.animation_finished)
        self.opened = False
        self.hide()
        self.slider.hide()
        self.cancel_slider.hide()

    def animate(self, progress):
        self.effect.setOpacity(min(1., progress))
        side = round(self.side * (.66 + .34 * progress))
        self.setGeometry(round(self.x_center - side / 2), round(self.y_center - side / 2), side, side)
        self.cancel_slider.move(round(self.x_center - 30), self.geometry().bottom() + 5)

    def animation_finished(self):
        if not self.opened:
            self.hide()

    def update_selection(self, state):
        clear = state.get('phase') == 'selecting' and state.get('direction') == 'left'
        if clear:
            delete = self.window.direction_buttons['left']
            start = delete.mapTo(self.window, delete.rect().topLeft())
            self.clear_slider.move(max(0, start.x() - 60), start.y() + (delete.height() - 58) // 2)
            self.clear_slider.caption = '清空' if self.window.code else '删除前文'
            self.clear_slider.set_progress(1 if state.get('clear_armed') else state.get('clear_progress', 0))
            self.clear_slider.raise_()
        self.clear_slider.set_shown(clear)
        pull = state.get('punctuation_pull')
        if pull:
            anchor = self.window.direction_buttons[pull['direction']]
            start = anchor.mapTo(self.window, anchor.rect().center())
            grid_top = self.menu.mapTo(self.window, self.menu.rect().topLeft()).y()
            self.side = round(self.menu.width() * .72)
            half = self.side / 2
            self.x_center = min(max(start.x(), half + 8), self.window.width() - half - 8)
            self.y_center = min(max(grid_top + 8, half + 8), self.window.height() - half - 70)
            progress = pull['progress']
            x = start.x() + (self.x_center - start.x()) * progress
            y = start.y() + (self.y_center - start.y()) * progress
            self.slider.move(round(x - 30), round(y - 29))
            self.slider.set_progress(progress)
        opened = bool(pull and pull['opened'])
        cancel_progress = pull.get('cancel_progress', 0) if opened else 0
        self.cancel_slider.set_progress(cancel_progress)
        self.cancel_slider.set_shown(opened)
        if opened:
            self.cancel_slider.raise_()
        self.slider.set_shown(bool(pull) and not opened)
        if self.slider.isVisible():
            self.slider.raise_()
        if opened != self.opened:
            start = self.animation.currentValue() if self.isVisible() else 0.
            self.opened = opened
            self.setEnabled(opened)
            self.animation.stop()
            self.animation.setDuration(220 if opened else 140)
            self.animation.setEasingCurve(QEasingCurve.OutBack if opened else QEasingCurve.InCubic)
            self.animation.setStartValue(start)
            self.animation.setEndValue(1. if opened else 0.)
            self.show()
            self.raise_()
            self.animation.start()
        elif opened:
            self.animate(self.animation.currentValue())
        if opened:
            for symbol, button in self.buttons.items():
                button.setChecked(symbol == pull['symbol'])


class AdaptiveTrialLayout(QObject):
    """Give the menu height first on short, high-DPI desktop work areas."""
    def __init__(self, window, root, task_layout, field_layout, subtitle, footer):
        super().__init__(window)
        self.window, self.root = window, root
        self.task_layout, self.field_layout, self.subtitle = task_layout, field_layout, subtitle
        self.footer = footer
        self.mode = None
        window.installEventFilter(self)
        self.apply()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.apply(event.size())
        return False

    def apply(self, size=None):
        size = size or self.window.size()
        compact = size.height() < 780
        self.subtitle.setVisible(not compact and size.width() >= 1320)
        if compact == self.mode:
            return
        previous = self.mode
        self.mode = compact
        self.window.setProperty('compactTrial', compact)
        self.root.setContentsMargins(16 if compact else 20, 10 if compact else 14,
                                     16 if compact else 20, 8 if compact else 12)
        self.root.setSpacing(8 if compact else 10)
        self.task_layout.setContentsMargins(12 if compact else 14, 8 if compact else 10,
                                            12 if compact else 14, 8 if compact else 10)
        self.task_layout.setSpacing(4 if compact else 6)
        self.field_layout.setSpacing(8 if compact else 10)
        self.window.target_input.setFixedHeight(32 if compact else 36)
        self.window.target_preview.setFixedHeight(42 if compact else 52)
        self.window.editor.setFixedHeight(42 if compact else 52)
        self.window.target_preview.setStyleSheet(
            'background:#f3f6fc; border-radius:8px; padding:6px 10px; font-size:17px;' if compact else
            'background:#f3f6fc; border-radius:8px; padding:8px 12px; font-size:20px;')
        self.window.editor.setStyleSheet('font-size:17px; padding:6px 10px;' if compact else
                                        'font-size:19px; padding:8px 12px;')
        self.window.progress.setFixedHeight(3 if compact else 5)
        self.window.trial_hint.setFixedHeight(18 if compact else 30)
        self.window.trial_hint.setWordWrap(not compact)
        if compact:
            self.task_layout.removeWidget(self.window.trial_hint)
            self.root.addWidget(self.window.trial_hint)
            self.footer.hide()
            self.window.trial_hint.show()
        elif previous is True:
            self.root.removeWidget(self.window.trial_hint)
            self.task_layout.addWidget(self.window.trial_hint)
            self.window.trial_hint.show()
            self.footer.show()
        self.window.result.setFixedHeight(36 if compact else 48)


def compact_layout(window):
    widgets = []

    def dismantle(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.layout():
                dismantle(item.layout())
                delete(item.layout())
            elif item.widget():
                widgets.append(item.widget())
                item.widget().hide()

    old = window.layout()
    dismantle(old)
    delete(old)
    root = QVBoxLayout(window)
    root.setContentsMargins(20, 14, 20, 12)
    root.setSpacing(10)

    def label(text, name='sectionTitle'):
        widget = QLabel(text)
        widget.setObjectName(name)
        return widget

    def add(layout, widget, *args):
        layout.addWidget(widget, *args)
        widget.show()

    def card():
        frame = QFrame()
        frame.setObjectName('card')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        return frame, layout

    def button(text):
        return next(w for w in widgets if isinstance(w, QPushButton) and w.text() == text)

    header = QHBoxLayout()
    header.addWidget(label('Ring 书写实验', 'appTitle'))
    subtitle = label('自定义文本 · 连续输入 · 逐笔评估', 'muted')
    header.addWidget(subtitle)
    header.addStretch()
    window.status.setObjectName('muted')
    window.status.setMinimumWidth(0)
    window.status.setMaximumWidth(300)
    window.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    add(header, window.status, 1)
    window.device.setMaximumWidth(190)
    add(header, window.device)
    add(header, window.connect_button)
    root.addLayout(header)
    body = QHBoxLayout()
    body.setSpacing(12)
    root.addLayout(body, 1)
    left = QVBoxLayout()
    left.setSpacing(10)
    body.addLayout(left, 1)

    text_card, text_layout = card()
    text_header = QHBoxLayout()
    text_header.addWidget(label('输入任务'))
    window.target_input.setFixedHeight(36)
    window.target_input.document().setDocumentMargin(0)
    window.target_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    add(text_header, window.target_input, 1)
    window.start_trial_button.setObjectName('primary')
    add(text_header, window.start_trial_button)
    add(text_header, window.finish_trial_button)
    text_layout.addLayout(text_header)
    window.target_preview.setMinimumHeight(0)
    window.target_preview.setFixedHeight(52)
    window.target_preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    window.target_preview.setStyleSheet('background:#f3f6fc; border-radius:8px; padding:8px 12px; font-size:20px;')
    text_fields = QHBoxLayout()
    text_fields.setSpacing(10)
    target_column = QHBoxLayout()
    target_column.setSpacing(8)
    target_column.addWidget(label('待输入', 'muted'))
    add(target_column, window.target_preview, 1)
    text_fields.addLayout(target_column, 1)
    input_column = QHBoxLayout()
    input_column.setSpacing(8)
    input_column.addWidget(label('已输入', 'muted'))
    window.editor.setMinimumHeight(0)
    window.editor.setFixedHeight(52)
    window.editor.setStyleSheet('font-size:19px; padding:8px 12px;')
    add(input_column, window.editor, 1)
    text_fields.addLayout(input_column, 1)
    text_layout.addLayout(text_fields)
    window.progress.setFixedHeight(5)
    window.progress.setTextVisible(False)
    add(text_layout, window.progress)
    window.trial_hint.setObjectName('muted')
    window.trial_hint.setFixedHeight(30)
    window.trial_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    add(text_layout, window.trial_hint)
    left.addWidget(text_card)

    selection_card, selection = card()
    left.addWidget(selection_card, 1)
    selection_header = QHBoxLayout()
    selection_header.addWidget(label('双指八向选字 · 笔画轨迹' if trial_menu.TWO_FINGER else '八向选字 · 笔画轨迹'))
    window.selection_hint.setObjectName('directionHint')
    window.selection_hint.setMinimumWidth(0)
    window.selection_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    add(selection_header, window.selection_hint, 1)
    selection.addLayout(selection_header)
    menu = TraceMenu(window.trace_view)
    window.trace_view.direction_buttons = window.direction_buttons
    menu.setObjectName('selectionSquare')
    grid = QGridLayout(menu)
    grid.setContentsMargins(10, 10, 10, 10)
    grid.setSpacing(8)
    for i in range(3):
        grid.setRowStretch(i, 1)
        grid.setColumnStretch(i, 1)
    for index, (_, row, col, _) in enumerate(trial_menu.CANDIDATE_CELLS):
        candidate = window.buttons[index]
        candidate.setMinimumSize(0, 0)
        candidate.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        candidate.setObjectName('candidate')
        add(grid, candidate, row, col)
    for direction, col, _, _, _ in trial_menu.PUNCTUATION_CELLS:
        punctuation = window.direction_buttons[direction]
        punctuation.setMinimumHeight(0)
        punctuation.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        punctuation.setObjectName('directionAction')
        add(grid, punctuation, 0, col)
    for direction, row, col, *_ in trial_menu.ACTION_CELLS:
        action = window.direction_buttons[direction]
        action.setMinimumHeight(0)
        action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        action.setObjectName('deleteAction')
        add(grid, action, row, col)
    window.selection_square = SquareArea(menu)
    if trial_menu.TWO_FINGER:
        window.punctuation_overlay = TrialPunctuationOverlay(window, menu)
    window.selection_square.setMinimumSize(260, 260)
    selection.addWidget(window.selection_square, 1)
    legend = QLabel('<span style="color:#171C28">●</span> 已输入　'
                    '<span style="color:#98A1B2">◇</span> 未输入　'
                    '<span style="color:#C79600">●</span> 模糊　'
                    '<span style="color:#DC2626">●</span> 疑似错笔　'
                    '<span style="color:#779CF2">●</span> 下一笔')
    legend.setObjectName('glyphLegend')
    legend.setAlignment(Qt.AlignCenter)
    window.selection_progress = QProgressBar()
    window.selection_progress.setRange(0, 100)
    window.selection_progress.setValue(0)
    window.selection_progress.setFixedHeight(5)
    window.selection_progress.setTextVisible(False)
    selection.addWidget(window.selection_progress)
    pages = QHBoxLayout()
    add(pages, button('上一页'))
    add(pages, window.page_label)
    add(pages, button('下一页'))
    window.page_label.setAlignment(Qt.AlignCenter)
    window.page_label.setMinimumWidth(88)
    window.page_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    selection_header.addLayout(pages)
    window.composition.setFixedHeight(36)
    window.composition.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    window.composition.setObjectName('composition')

    controls = QHBoxLayout()
    add(controls, window.composition, 1)
    controls.addWidget(legend)
    add(controls, button('退一笔'))
    add(controls, button('清空笔画'))
    selection.addLayout(controls)
    window.result.setFixedHeight(48)
    window.result.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    window.result.setObjectName('muted')

    sidebar, side = card()
    side.setSpacing(4)
    scroll = QScrollArea()
    scroll.setObjectName('statisticsScroll')
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setFixedWidth(276)
    scroll.setWidget(sidebar)
    window.statistics_scroll = scroll
    body.addWidget(scroll)
    side.addWidget(label('实验统计'))
    # Native labels let Qt measure each row at the current DPI. Rich-text
    # tables in fixed-height labels overflowed at Windows display scaling.
    stats = QGridLayout()
    stats.setVerticalSpacing(5)
    stats.setHorizontalSpacing(8)
    window.metric_values = {}
    for row, title in enumerate(('总用时', '输入速度', '文本准确率', '笔画准确率',
                                 '出字成功率', '提交正确率', '评分 / 未评分', '平均识别耗时')):
        stats.addWidget(label(title, 'metricName'), row, 0)
        value = label('—', 'metricValue')
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        stats.addWidget(value, row, 1)
        window.metric_values[title] = value
    side.addLayout(stats)
    side.addWidget(label('各类笔画准确率'))
    categories = QGridLayout()
    categories.setVerticalSpacing(4)
    window.category_values = {}
    for row, (category, title) in enumerate(zip('hspnz', ('横', '竖', '撇', '点／捺', '折'))):
        categories.addWidget(label(title, 'metricName'), row, 0)
        value = label('—  0/0', 'categoryValue')
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        categories.addWidget(value, row, 1)
        window.category_values[category] = value
    side.addLayout(categories)
    side.addWidget(label('抬落／漏笔诊断'))
    add(side, window.diagnostic_task)
    add(side, window.diagnostic_note)
    diagnostic_actions = QGridLayout()
    for index, action in enumerate(window.diagnostic_buttons):
        add(diagnostic_actions, action, index//2, index%2)
    side.addLayout(diagnostic_actions)
    add(side, window.diagnostic_status)
    settings = QHBoxLayout()
    add(settings, window.common_toggle)
    add(settings, window.fuzzy_toggle)
    side.addLayout(settings)
    add(side, window.joined_toggle)
    add(side, window.joined_status)
    if hasattr(window, 'vector_toggle'):
        add(side, window.vector_toggle)
    if hasattr(window, 'fill_toggle'):
        add(side, window.fill_toggle)
    strength_header = QHBoxLayout()
    window.fuzzy_strength_label.setMinimumWidth(0)
    window.fuzzy_strength_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    add(strength_header, window.fuzzy_strength_label, 1)
    add(strength_header, window.fuzzy_strength_reset)
    side.addLayout(strength_header)
    strength = QHBoxLayout()
    strength.addWidget(label('低', 'muted'))
    add(strength, window.fuzzy_strength_slider, 1)
    strength.addWidget(label('高', 'muted'))
    side.addLayout(strength)
    window.adaptive_status.setMinimumHeight(30)
    window.adaptive_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    add(side, window.adaptive_status)
    side.addWidget(label('个人写法'))
    writer = QHBoxLayout()
    add(writer, window.personal_toggle)
    add(writer, window.personal_profile)
    side.addLayout(writer)
    add(side, window.personal_status)
    writer_actions = QHBoxLayout()
    add(writer_actions, button('撤回学习'))
    add(writer_actions, button('清空档案'))
    side.addLayout(writer_actions)
    add(side, window.tap_guard_toggle)
    window.tap_guard_status.setFixedHeight(30)
    window.tap_guard_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    add(side, window.tap_guard_status)
    add(side, window.correction_toggle)
    window.correction_status.setMinimumHeight(30)
    window.correction_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    add(side, window.correction_status)
    add(side, window.once_toggle)
    add(side, window.once_status)
    add(side, window.llm_toggle)
    window.llm_status.setMinimumHeight(30)
    window.llm_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    add(side, window.llm_status)
    side.addWidget(label('连接与识别状态'))
    add(side, window.result)
    for widget in (window.contact_state, window.metrics):
        widget.setObjectName('muted')
        widget.setWordWrap(True)
        widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        add(side, widget)
    window.contact_state.setFixedHeight(30)
    window.metrics.setFixedHeight(48)
    side.addStretch()
    side.addWidget(label('手动笔画 · 仅用于调试', 'muted'))
    manual = QHBoxLayout()
    manual.setSpacing(4)
    for title in ('横', '竖', '撇', '点／捺', '折'):
        add(manual, button(title))
    side.addLayout(manual)
    examples = next(w for w in widgets if isinstance(w, QLabel) and w.text().startswith('笔画对照：'))
    examples.setObjectName('muted')
    examples.setWordWrap(True)
    examples.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    add(side, examples)
    footer = label('双指滑向目标格子，抬手确认 · 双指左滑退笔或删字，继续外拉清空笔画'
                   if trial_menu.TWO_FINGER else
                   '选字：静止 0.333 秒后向目标滑动，抬手确认 · 使用按钮退笔或清空笔画', 'muted')
    root.addWidget(footer)
    window.setStyleSheet('''
        QWidget { font-family:"Microsoft YaHei UI"; font-size:12px; color:#24334d; }
        Window { background:#edf1f7; }
        QFrame#card { background:#ffffff; border:1px solid #e0e6f0; border-radius:12px; }
        QLabel { background:transparent; border:none; }
        QLabel#appTitle { font-size:21px; font-weight:700; color:#203257; }
        QLabel#sectionTitle { font-size:13px; font-weight:700; padding:3px 0px; }
        QLabel#muted, QLabel#metricName { color:#738099; font-size:11px; }
        QLabel#metricValue { font-size:12px; font-weight:600; }
        QLabel#categoryValue { font-size:11px; }
        QLabel#directionHint { color:#738099; font-size:11px; }
        QLabel#glyphLegend { color:#738099; font-size:11px; padding:3px 0; }
        QLabel#composition { background:#f3f6fc; border-radius:6px; padding:4px 8px; font-size:17px; }
        QLineEdit, QPlainTextEdit { background:#f8faff; border:1px solid #dce4ef; border-radius:6px; padding:5px 8px; }
        QLineEdit:focus, QPlainTextEdit:focus { border:1px solid #708de8; }
        QPushButton { background:#f3f6fb; border:1px solid #dfe6f0; border-radius:6px; padding:6px 10px; }
        QPushButton:hover { background:#e7edfb; border-color:#aabcf2; }
        QPushButton:pressed { background:#d8e3fa; }
        QPushButton:disabled { color:#a6afbf; background:#f8f9fc; border-color:#edf0f5; }
        QPushButton#primary { background:#345ee5; color:white; border-color:#345ee5; font-weight:700; }
        QPushButton#primary:hover { background:#264dc9; }
        QPushButton#primary:disabled { background:#e9edfa; color:#9aa7c6; border-color:#e9edfa; }
        QPushButton#candidate { padding:0; background:#ffffff; border:1px solid #dce4f0; border-radius:10px; }
        QPushButton#candidate[primaryCandidate="true"] { background:#f0f4ff; border:2px solid #aabcf2; }
        QPushButton#candidate:hover { background:#f4f7ff; border-color:#aabcf2; }
        QPushButton#candidate[directionActive="true"] { background:#edf2ff; border:2px solid #345ee5; }
        QPushButton#directionAction { font-size:20px; background:#edf2fb; border:1px solid #dce4f0; border-radius:10px; padding:4px; }
        QPushButton#directionAction[directionActive="true"] { background:#234cdb; color:white; border:2px solid #1739ac; }
        QPushButton#deleteAction { font-size:20px; background:#fbeeee; border:1px solid #f0d6d6; border-radius:10px; padding:4px; }
        QPushButton#deleteAction[directionActive="true"] { background:#dc2626; color:white; border:2px solid #a61b1b; }
        QFrame#selectionSquare { background:#f3f6fc; border:1px solid #dce4f0; border-radius:14px; }
        QScrollArea#statisticsScroll { background:transparent; border:none; }
        QScrollBar:vertical { background:#edf1f7; width:6px; margin:0; }
        QScrollBar::handle:vertical { background:#c3cddd; min-height:28px; border-radius:3px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        QProgressBar { background:#e9eef7; border:none; border-radius:2px; }
        QProgressBar::chunk { background:#345ee5; border-radius:2px; }
        QCheckBox { font-size:11px; spacing:4px; }
    ''')
    window.trial_metrics.setText('开始实验后显示用时、速度、\n识别准确率和出字成功率。')
    window.category_metrics.setText('横 —\n竖 —\n撇 —\n点／捺 —\n折 —')
    window.setMinimumSize(1000, 560)
    window.resize(1440, 900)
    window.adaptive_layout = AdaptiveTrialLayout(window, root, text_layout, text_fields, subtitle, footer)
