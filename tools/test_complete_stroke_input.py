"""Continuous local trial: DTW/fuzzy composition, deletion and two-finger (or hold) selection."""
import argparse
from pathlib import Path
import sys
import time
import csv
import html
import json
import math

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                              QCheckBox, QPlainTextEdit, QProgressBar, QLineEdit)
from proximic_ring.touchpad.stroke.stroke_experiment import SentenceTrial, FreeWritingSession, REPORT_COLUMNS, write_report
from proximic_ring.touchpad.stroke.stroke_tap_guard import TapConfirmationGuard
from proximic_ring.touchpad.stroke.stroke_display import TRIAL_TRACE_SCALE
from collect_stroke_samples import Window as StrokeWindow, RingWorker as StrokeWorker
from proximic_ring.touchpad.stroke.stroke_test_gestures import recognize, DELETE, CLEAR, DELETION_SOURCE
import stroke_trial_menu as trial_menu
from stroke_trial_trace import TrialTraceView, SELECTION_DISPLAY
from stroke_trial_status_log import StatusLogWindow
from proximic_ring.touchpad.stroke.stroke_clear_review import snapshot as clear_review_snapshot
from proximic_ring.touchpad.stroke.stroke_common import COMMON_CHARACTER_COUNT, common_characters, candidate_scope_snapshot
from proximic_ring.touchpad.stroke.stroke_left_dots import annotation_snapshot as left_dot_snapshot
from proximic_ring.touchpad.stroke.stroke_fuzzy import ADAPTIVE_SETTINGS
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext, ranking_snapshot
from stroke_trial_candidates import GlyphCandidateButton, DirectionActionButton, trial_candidate_visuals
from stroke_trial_correction import TrialCorrection
from proximic_ring.touchpad.stroke.stroke_word_correction import DEFAULT_CORRECTION_POLICY, merge_candidates
from proximic_ring.touchpad.stroke.stroke_llm_context import apply_predictions, CONTEXT_CHARS, SHORT_STROKES
from stroke_trial_llm import TrialLanguageContext
from stroke_trial_batch import CandidateBatch, BUDGET_MS
from proximic_ring.touchpad.stroke.stroke_personal import PersonalStrokeModel, TextLearningHistory, profile_key, qt_text_index
from stroke_contact_diagnostics import ContactDiagnostics, marker_value
from proximic_ring.touchpad.stroke.stroke_joined import JoinedStrokeModel, merge_joined_candidates
from proximic_ring.touchpad.stroke.stroke_vectors import StrokeVectorModel
from proximic_ring.touchpad.stroke.stroke_tolerant import DEFAULT_TOLERANT_POLICY, fill_page, index_for

FREE_AUTOSAVE_MS = 30000
# Events caused by a contact or button press; anything else (model results,
# candidate batches) is recorded only once the user has started writing.
FREE_INPUT_KINDS = {'gesture', 'tap_confirmation', 'manual_stroke', 'delete_character', 'undo_stroke'}


class RingWorker(StrokeWorker):
    tap_contact = Signal(object)
    selection = Signal(object)
    selection_state = Signal(object)
    contact_intent = Signal(object)
    focused_input = Signal(object)

    def __init__(self, *args, diagnostics=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.focus_since = None
        self.diagnostics = diagnostics

    def run(self):
        try:
            super().run()
        finally:
            if self.diagnostics is not None:
                self.diagnostics.close()

    def configure_processor(self, processor):
        if self.diagnostics is not None:
            self.diagnostics.instrument(processor)

    async def configure_touchpad(self, stream):
        await super().configure_touchpad(stream)
        if self.diagnostics is not None:
            self.diagnostics.observe_packets(stream)

    @property
    def protocol(self):
        # Only Raw2 tags frames as two-finger; the hold menu keeps the legacy link.
        return 'raw2' if trial_menu.TWO_FINGER else 'legacy'

    def configure_output(self, output):
        output.input_since = lambda: self.focus_since
        def send(kind, value):
            self.focused_input.emit((output.input_epoch, kind, value))
        output.on_trace = lambda points, finished: send('trace', points)
        output.on_stroke_details = lambda value: send('stroke', value)
        output.on_tap = lambda: send('tap', {})
        output.on_tap_contact = lambda value: send('tap', value)
        output.on_selection = lambda value: send('selection', value)
        output.on_selection_state = lambda value: send('selection_state', value)
        output.contact_classifier = trial_menu.trial_contact
        output.two_finger_classifier = trial_menu.two_finger_selection
        output.on_contact_intent = lambda value: send('contact_intent', value)
        if self.diagnostics is not None:
            self.diagnostics.observe_collector(output.collector)
            output.on_diagnostic = lambda reason: self.diagnostics.record('output_diagnostic', reason=reason)

    def recognize(self, points, kinematics=None):
        # The confirmed-contact pipeline still owns release and click separation.
        # Do not classify as handwriting before the complete control gesture.
        self.stroke.emit(dict(points=points, kinematics=kinematics))


class Window(StrokeWindow):
    trace_view_type = TrialTraceView

    def __init__(self, device='Ringo6B72', *, common_only=True, llm_context=False,
                 writing_profile='默认', personal_learning=True, single_display=False,
                 joined_model_path=None, joined_enabled=True, vectors_enabled=True, components_enabled=True,
                 vector_scope='prefix', fill_enabled=True, context_model='wanxiang'):
        self._focus_since = None
        self._last_focus_since = 0.
        super().__init__(device, gestures=False, recognizer='dtw',
                         allowed_characters=common_characters() if common_only else None)
        self.dictionary.components_enabled = components_enabled and self.dictionary.component_decoder is not None
        self.record('component_encoding_settings', enabled=self.dictionary.components_enabled,
                    model=self.dictionary.component_decoder.snapshot() if self.dictionary.component_decoder else {})
        self.joined_model = None
        self._trial_joined_ambiguous = False
        self.joined_error = ''
        self.joined_model_path = Path(joined_model_path) if joined_model_path is not None else ROOT/'data'/'models'/'joined-strokes'/'model.json.gz'
        try:
            self.joined_model = JoinedStrokeModel.load(self.joined_model_path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.joined_error = '尚未建立连笔模型' if isinstance(error, FileNotFoundError) else '连笔模型不可用，已保留原识别'
        self.joined_toggle = QCheckBox('连笔适配（本地样本）')
        self.joined_toggle.setChecked(joined_enabled and self.joined_model is not None)
        self.joined_toggle.setEnabled(self.joined_model is not None)
        self.joined_toggle.setToolTip('按厂形／斤、白、了／子、衤、走之底末两笔展开；走之第一点单独写。保留原笔画，一次落笔整体撤回；走之暂用部件参考轨迹，可补采个人样本。')
        self.joined_status = QLabel()
        self.joined_status.setWordWrap(True)
        self.joined_status.setObjectName('muted')
        self.joined_toggle.toggled.connect(self.set_joined_enabled)
        self.record('joined_stroke_settings', enabled=self.joined_toggle.isChecked(),
            model=self.joined_model.snapshot() if self.joined_model else None, error=self.joined_error)
        if context_model != getattr(self.predictor, 'model', 'legacy'):
            self.predictor = StrokeContext(model=context_model)
        self.record('context_model_settings', model=self.predictor.model)
        self.vector_model = StrokeVectorModel(scope=vector_scope)
        self.vector_toggle = QCheckBox('按笔画长短／方向排序' if vector_scope == 'prefix' else '同码字按笔画长短／方向排序')
        self.vector_toggle.setChecked(vectors_enabled)
        self.vector_toggle.setToolTip(
            '从第一笔起，所有候选（含未写完和纠错的字）都按已写各笔的起止向量与字形前几笔比较后计分；'
            '只在同一优先级内调整，不使用笔间位置。' if vector_scope == 'prefix' else
            '笔画编码完全相同的完整字（如土／士／工、人／八／入）按各笔起止向量与字形比较后重排；'
            '不使用笔间位置，不改变其他候选。')
        self.vector_toggle.toggled.connect(self.set_vectors_enabled)
        page_word = '五六'[trial_menu.PAGE_SIZE - 5] if trial_menu.PAGE_SIZE in (5, 6) else str(trial_menu.PAGE_SIZE)
        self.fill_toggle = QCheckBox(f'候选不足时补足{page_word}个')
        self.fill_toggle.setChecked(fill_enabled)
        self.fill_toggle.setToolTip(f'第一页不满{page_word}个时，按“错认、多写、漏写、换序”放宽匹配补位，仍不够再用联想和常用字补满；'
                                    '补位字标“猜”，不改变原有候选顺序，选中后不参与个人学习。')
        self.fill_toggle.toggled.connect(self.set_fill_enabled)
        self.record('candidate_fill_settings', enabled=fill_enabled, policy=DEFAULT_TOLERANT_POLICY.snapshot())
        self.dictionary.stroke_vectors = self.vector_model if vectors_enabled else None
        self.record('stroke_vector_settings', enabled=vectors_enabled, model=self.vector_model.snapshot())
        self.writing_profile = writing_profile.strip()[:24] or '默认'
        self._initial_personal_learning = personal_learning
        if sys.platform == 'win32':
            font_path = Path('C:/Windows/Fonts/msyh.ttc')
            if font_path.is_file():
                font_id = QFontDatabase.addApplicationFont(str(font_path))
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families: self.setFont(QFont(families[0], 10))
            kaishu = Path('C:/Windows/Fonts/simkai.ttf')
            if kaishu.is_file(): QFontDatabase.addApplicationFont(str(kaishu))
        self.setWindowTitle('Ring 完整输入测试 · ' + ('双指八向选字' if trial_menu.TWO_FINGER else '触桌停留／八向选字'))
        self.resize(850, 760)
        candidate_row = self.layout().takeAt(4)
        for button in self.buttons: button.hide()
        self._old_candidate_row = candidate_row
        menu = QGridLayout()
        self.buttons = [None]*trial_menu.PAGE_SIZE
        self.direction_buttons = {}
        self.selection_state = dict(phase='idle', progress=0, direction=None)
        self.arrows = [cell[3] for cell in trial_menu.CANDIDATE_CELLS]
        for index, (direction, row, col, arrow) in enumerate(trial_menu.CANDIDATE_CELLS):
            button = GlyphCandidateButton(self.arrows[index], index + 1)
            button.setMinimumHeight(48)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda checked=False, i=index: self.commit(self.selected//trial_menu.PAGE_SIZE*trial_menu.PAGE_SIZE+i))
            self.buttons[index] = button
            self.direction_buttons[direction] = button
            menu.addWidget(button, row, col)
        for direction, col, arrow, symbol, title in trial_menu.PUNCTUATION_CELLS:
            button = DirectionActionButton(arrow, symbol, title)
            button.setMinimumHeight(48)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda checked=False, s=symbol: self.punctuate(s))
            self.direction_buttons[direction] = button
            menu.addWidget(button, 0, col)
        for direction, row, col, arrow, symbol, title, action in trial_menu.ACTION_CELLS:
            button = DirectionActionButton(arrow, symbol, title)
            button.setMinimumHeight(48)
            button.setFocusPolicy(Qt.NoFocus)
            button.setObjectName('deleteAction')
            button.clicked.connect(lambda checked=False: self.delete_one())
            self.direction_buttons[direction] = button
            menu.addWidget(button, row, col)
        self.selection_hint = QLabel(self.idle_hint())
        self.selection_hint.setAlignment(Qt.AlignCenter)
        self.layout().insertLayout(4, menu)
        pages = QHBoxLayout()
        self.page_label = QLabel()
        self.common_toggle = QCheckBox(f'常用／通用字（{COMMON_CHARACTER_COUNT}）')
        self.common_toggle.setChecked(common_only)
        self.common_toggle.toggled.connect(self.set_common_only)
        for title, shift in [('上一页', -trial_menu.PAGE_SIZE), ('下一页', trial_menu.PAGE_SIZE)]:
            button = QPushButton(title)
            button.setFocusPolicy(Qt.NoFocus)
            if shift > 0:
                self.next_page_button = button
                button.setObjectName('trialNextPage')
                button.clicked.connect(self.next_candidate_page)
            else:
                self.previous_page_button = button
                button.setObjectName('trialPreviousPage')
                button.clicked.connect(lambda checked=False: self.page(-trial_menu.PAGE_SIZE))
            pages.addWidget(button)
        pages.addWidget(self.common_toggle)
        pages.addWidget(self.page_label)
        self.layout().insertLayout(5, pages)
        manual = QHBoxLayout()
        for category, label in [('h','横'),('s','竖'),('p','撇'),('n','点／捺'),('z','折')]:
            button = QPushButton(label)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda checked=False, c=category: self.add_manual(c))
            manual.addWidget(button)
        self.layout().addLayout(manual)
        examples = QLabel('笔画对照：斜钩＝戈的第二笔；弯钩＝狗左边的第二笔；卧钩＝心的第二笔。')
        examples.setWordWrap(True)
        self.layout().addWidget(examples)
        self.contact_test.hide()
        self.contact_prompt.hide()
        for i in range(self.layout().count()):
            item = self.layout().itemAt(i)
            if item.layout():
                for j in range(item.layout().count()):
                    widget = item.layout().itemAt(j).widget()
                    if isinstance(widget, QPushButton) and widget.text().startswith('标记：'): widget.hide()
            widget = item.widget()
            if isinstance(widget, QLabel) and widget.text().startswith('轻触确认候选；'):
                widget.setText('双指滑向格子选字 · 双指左滑退一笔（笔序为空时删正文一字）· 原地抬手取消\n'
                               '单指左→右也可退一笔 · 往返≥3段清空笔画 · 轻触选第1候选' if trial_menu.TWO_FINGER else
                               '左→右退一笔（不抬手，尽量沿原方向返回）；单独左滑按笔画识别\n笔序为空时删正文一字 · 往返≥3段清空笔画 · 轻触选第1候选')
        self.editor.textChanged.connect(self.refresh)
        self.record('complete_trial', selection=trial_menu.TRIAL_SELECTION, deletion=DELETE, clear=CLEAR,
                    context_ranking=ranking_snapshot(),
                    fuzzy_policy=self.fuzzy_policy.snapshot(),
                    clearReview=clear_review_snapshot(),
                    candidateScope=candidate_scope_snapshot(common_only),
                    deletionSource=DELETION_SOURCE, contextAssist=False,
                    punctuation='discard-pending-strokes-then-punctuation')
        self.refresh()
        self.setup_experiment()
        self.tap_guard = TapConfirmationGuard()
        self.tap_guard_toggle = QCheckBox('连续点击保护（250ms）')
        self.tap_guard_toggle.setChecked(True)
        self.tap_guard_toggle.toggled.connect(self.set_tap_guard)
        self.tap_guard_status = QLabel('稳定轻触选字 · 出字后保护250ms')
        self.tap_guard_status.setWordWrap(True)
        self.tap_guard_status.setObjectName('muted')
        self.record('tap_guard_settings', enabled=True, policy=self.tap_guard.snapshot())
        self.correction_toggle = QCheckBox('整字纠错（本地）')
        self.correction_toggle.setChecked(True)
        self.correction_toggle.setToolTip('按整字笔序尝试替换、补漏笔、忽略多笔和相邻换序；不改动原始输入。')
        self.correction_status = QLabel('至少输入3笔后分析')
        self.correction_status.setWordWrap(True)
        self.correction_status.setObjectName('muted')
        self.correction = TrialCorrection(self.can_apply_correction, self)
        self.correction.changed.connect(self.correction_ready)
        self.correction_toggle.toggled.connect(self.set_correction_enabled)
        self.record('whole_correction_settings', enabled=True, policy=DEFAULT_CORRECTION_POLICY.snapshot())
        self.llm_toggle = QCheckBox('大模型联想（本地Qwen）')
        self.llm_toggle.setChecked(llm_context)
        self.llm_toggle.setToolTip('根据已输入文字预测词语；未写笔画或仅写1–3笔时辅助选字，每次仍只输入一个字。')
        self.llm_status = QLabel('已关闭')
        self.llm_status.setObjectName('muted')
        self.llm_status.setWordWrap(True)
        self.language = TrialLanguageContext(self.can_apply_correction, self)
        self.language.changed.connect(self.language_ready)
        self.llm_toggle.toggled.connect(self.set_llm_enabled)
        self.llm_hints = {}
        self.once_toggle = QCheckBox('候选一次显示（Qwen）')
        self.once_toggle.setToolTip('先显示加载中，再一次显示六个候选位置；Qwen最多等待400ms，超时使用本地结果，显示后不重排。')
        self.once_status = QLabel('关闭 · 使用原有渐进候选')
        self.once_status.setObjectName('muted')
        self.once_status.setWordWrap(True)
        self.batch = CandidateBatch(self.can_publish_batch, self)
        self.batch.changed.connect(self.batch_ready)
        self.once_toggle.toggled.connect(self.set_once_enabled)
        self.setup_contact_diagnostics()
        from stroke_trial_layout import compact_layout
        compact_layout(self)
        self.once_toggle.setChecked(single_display)
        self.refresh()

    def record(self, kind, **values):
        values.setdefault('monotonic', time.monotonic())
        super().record(kind, **values)
        status_log = getattr(self, 'status_log', None)
        if status_log is not None:
            status_log.add(kind, values)
        diagnostics = getattr(self, 'contact_diagnostics', None)
        if diagnostics is not None and kind in {
                'input_focus', 'contact_intent', 'contact_intent_state', 'tap_confirmation',
                'contact_decision', 'stroke', 'hold_selection', 'commit', 'delete_one', 'clear_all'}:
            diagnostics.record('application_event', event_kind=kind, event=values)

    def setup_contact_diagnostics(self):
        from PySide6.QtWidgets import QComboBox
        self.contact_diagnostics = None
        self.diagnostic_group = None
        self.diagnostic_task = QComboBox()
        self.diagnostic_task.addItems(['自由书写', '单笔后抬手', '正常三横', '快速三横',
                                       '折笔／中途停顿', '轻触确认', '长按选字', '落手调整'])
        self.diagnostic_note = QLineEdit()
        self.diagnostic_note.setPlaceholderText('可选备注，如：漏了第2笔')
        self.diagnostic_status = QLabel('连接后自动记录；问题出现后停手2秒再标记。')
        self.diagnostic_status.setObjectName('muted')
        self.diagnostic_status.setWordWrap(True)
        self.diagnostic_buttons = []
        for title, outcome in [('开始一组', 'start'), ('正常完成', 'normal'),
                               ('抬手未结束', 'release-not-ended'), ('漏笔', 'missing-stroke')]:
            button = QPushButton(title)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda checked=False, value=outcome: self.mark_diagnostic(value))
            self.diagnostic_buttons.append(button)

    def mark_diagnostic(self, outcome):
        if self.worker is None or self.contact_diagnostics is None or self.contact_diagnostics.closed:
            self.diagnostic_status.setText('请先连接戒指，再开始记录。')
            return
        if self.contact_diagnostics.error or self.contact_diagnostics.dropped:
            self.update_diagnostic_status()
            return
        if outcome == 'start':
            self.diagnostic_group = time.time_ns()
        value = marker_value(self.diagnostic_task.currentText(), outcome,
                             self.diagnostic_note.text().strip(), group_id=self.diagnostic_group,
                             code=self.code, focus_since=self._focus_since)
        self.record('contact_diagnostic_marker', **value)
        self.contact_diagnostics.record('trial_start' if outcome == 'start' else 'annotation', **value)
        title = dict(start='已开始一组', normal='已标记正常',
                     **{'release-not-ended': '已标记抬手未结束', 'missing-stroke': '已标记漏笔'})[outcome]
        self.diagnostic_status.setText(title + ' · 识别状态未重置')
        if outcome != 'start':
            self.diagnostic_group = None

    def update_diagnostic_status(self):
        diagnostics = self.contact_diagnostics
        if diagnostics is None:
            return
        if diagnostics.error or diagnostics.dropped:
            self.diagnostic_status.setText(
                f'诊断日志异常：{diagnostics.error or "队列溢出"}；丢失{diagnostics.dropped}条，请停止本轮。')

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.ActivationChange, QEvent.WindowStateChange):
            self.update_input_focus()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.update_input_focus()

    def update_input_focus(self):
        if not hasattr(self, 'tap_guard') or self.log.closed:
            return
        active = self.isVisible() and self.isActiveWindow() and not self.isMinimized() and not self.closing
        if active == (self._focus_since is not None):
            return
        # Windows' monotonic clock can repeat across a fast focus round trip.
        # Keep the cutoff unique so queued callbacks cannot regain permission.
        self._focus_since = (math.nextafter(max(time.monotonic(), self._last_focus_since), math.inf)
                             if active else None)
        if active:
            self._last_focus_since = self._focus_since
        if self.worker is not None:
            self.worker.focus_since = self._focus_since
        self.tap_guard.reset()
        if not active:
            self.show_selection_state(dict(phase='idle', progress=0, direction=None))
            self.selection_hint.setText('窗口未聚焦 · 戒指输入已暂停')
            self.trace_view.show_trace([])
            self.reset_contact_display()
        else:
            self.show_selection_state(self.selection_state)
        self.record('input_focus', active=active)

    def receive_focused_input(self, packet):
        epoch, kind, value = packet
        # Qt may deliver an already queued callback after a focus round trip.
        if (epoch is None or epoch != self._focus_since or self.closing
                or not self.isVisible() or not self.isActiveWindow() or self.isMinimized()):
            return
        if kind == 'trace':
            self.trace_view.show_trace(value)
        elif kind == 'stroke':
            self.accept_stroke(value)
        elif kind == 'tap':
            self.accept_sdk_tap(value)
        elif kind == 'selection':
            self.accept_selection(value)
        elif kind == 'selection_state':
            self.show_selection_state(value)
        elif kind == 'contact_intent':
            self.record('contact_intent', **value, code=self.code)

    def setup_experiment(self):
        self.trial = None
        self.report_path = None
        self.free_session = None
        self.free_path = None
        self.free_armed = False
        self.free_autosave = QTimer(self)
        self.free_autosave.timeout.connect(lambda: self.save_free_session('autosave'))
        self.free_autosave.start(FREE_AUTOSAVE_MS)
        self.target_input = QPlainTextEdit('今天我用戒指输入一句话。')
        self.target_input.setPlaceholderText('粘贴或输入待测试文本（换行会转为空格）')
        self.target_input.setMaximumHeight(75)
        self.target_preview = QLabel('自由书写中；开始实验后显示目标进度。')
        self.target_preview.setWordWrap(True)
        self.target_preview.setTextFormat(Qt.RichText)
        self.target_preview.setMinimumHeight(80)
        self.target_preview.setStyleSheet('background:#f4f6fa; padding:16px; font-size:24px;')
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.trial_metrics = QLabel('时间 —    字/分 —    文本准确率 —    笔画准确率 —')
        self.trial_metrics.setWordWrap(True)
        self.trial_metrics.setStyleSheet('font-size:15px; color:#234cdb; padding:8px;')
        self.category_metrics = QLabel('横 —    竖 —    撇 —    点／捺 —    折 —')
        self.category_metrics.setWordWrap(True)
        self.editor.setPlaceholderText('可直接自由书写、选字；开始实验后清空并计时')
        self.trial_hint = QLabel('连接并预热后即可自由书写。点击“开始实验”清空并计时；文本完全一致时自动结束。')
        self.trial_hint.setWordWrap(True)
        self.start_trial_button = QPushButton('开始实验')
        self.finish_trial_button = QPushButton('结束并保存')
        self.finish_trial_button.setEnabled(False)
        self.start_trial_button.clicked.connect(self.start_trial)
        self.finish_trial_button.clicked.connect(self.finish_trial)
        actions = QHBoxLayout()
        actions.addWidget(self.start_trial_button)
        actions.addWidget(self.finish_trial_button)
        # Keep the existing candidate menu indices intact; place the experiment
        # above the local editor, after the connection row and status.
        for index, widget in enumerate((self.target_input, self.target_preview,
                                        self.progress, self.trial_metrics, self.trial_hint), 2):
            self.layout().insertWidget(index, widget)
        self.layout().insertLayout(7, actions)
        self.layout().insertWidget(7, self.category_metrics)
        self.editor.textChanged.connect(self.update_trial)
        self.trial_timer = QTimer(self)
        self.trial_timer.timeout.connect(self.update_trial)
        self.trial_timer.start(100)
        self.trace_view.setMinimumHeight(100)
        self.resize(960, 1000)
        self.personal_model = None
        self.personal_history = TextLearningHistory()
        self.personal_controls = QHBoxLayout()
        self.personal_toggle = QCheckBox('学习个人写法')
        self.personal_toggle.setChecked(self._initial_personal_learning)
        self.personal_profile = QLineEdit(self.writing_profile)
        self.personal_profile.setMaxLength(24)
        self.personal_profile.setMaximumWidth(110)
        self.personal_profile.setAccessibleName('书写档案')
        self.personal_status = QLabel()
        self.personal_status.setWordWrap(True)
        self.personal_controls.addWidget(self.personal_toggle)
        self.personal_controls.addWidget(self.personal_profile)
        self.personal_controls.addWidget(self.personal_status, 1)
        for title, action in [('撤回学习', self.undo_personal_learning), ('清空档案', self.clear_personal_learning)]:
            button = QPushButton(title)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(action)
            self.personal_controls.addWidget(button)
        self.layout().addLayout(self.personal_controls)
        self.load_personal_profile()
        self.personal_toggle.toggled.connect(self.toggle_personal_learning)
        self.personal_profile.editingFinished.connect(self.change_personal_profile)
        self.editor.textChanged.connect(self.reconcile_personal_text)

    def personal_path(self):
        return self.log_path.parent.parent / 'stroke-profiles' / (profile_key(self.writing_profile) + '.json')

    def load_personal_profile(self):
        try:
            path = self.personal_path()
            payload = path.read_text(encoding='utf-8') if path.is_file() else None
            self.personal_model = PersonalStrokeModel(payload)
        except (OSError, UnicodeError):
            self.personal_model = PersonalStrokeModel()
            self.personal_model.error = '个人档案读取失败，已使用通用识别'
        self.personal_history = TextLearningHistory()
        self.toggle_personal_learning()

    def toggle_personal_learning(self):
        self.dictionary.personal_model = self.personal_model if self.personal_toggle.isChecked() else None
        self.update_personal_status()
        self.record('personal_learning_settings', enabled=self.personal_toggle.isChecked(),
                    profile=self.writing_profile, model=self.personal_model.snapshot())

    def update_personal_status(self):
        snapshot = self.personal_model.snapshot()
        self.personal_status.setText(snapshot['error'] or
            (f"已学习 {snapshot['confirmations']} 次选字 · {snapshot['samples']} 笔" if self.personal_toggle.isChecked()
             else '个人学习已关闭 · 使用通用识别'))

    def save_personal_profile(self):
        try:
            path = self.personal_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(self.personal_model.dumps(), encoding='utf-8')
            temporary.replace(path)
        except OSError as exc:
            self.personal_model.error = '个人档案保存失败，本次学习仅在当前运行中有效'
            self.record('personal_learning_save_failed', error=str(exc))
        self.update_personal_status()

    def change_personal_profile(self):
        if self.code or (self.trial is not None and self.trial.active):
            self.personal_profile.setText(self.writing_profile)
            return
        name = self.personal_profile.text().strip()[:24] or '默认'
        if name == self.writing_profile:
            return
        self.writing_profile = name
        self.personal_profile.setText(self.writing_profile)
        self.load_personal_profile()

    def undo_personal_learning(self):
        if not self.code and not (self.trial and self.trial.active) and self.personal_model.retract():
            self.save_personal_profile()
            self.record('personal_learning_retracted', model=self.personal_model.snapshot())

    def clear_personal_learning(self):
        if not self.code and not (self.trial and self.trial.active):
            self.personal_model.clear()
            self.personal_history = TextLearningHistory()
            self.save_personal_profile()
            self.record('personal_learning_cleared')

    def reconcile_personal_text(self):
        if self.personal_history.reconcile(self.personal_model, self.editor.text()):
            self.save_personal_profile()
            self.record('personal_learning_text_retracted', model=self.personal_model.snapshot())

    def start_trial(self):
        target = ' '.join(self.target_input.toPlainText().splitlines()).strip()
        if not target:
            self.trial_hint.setText('请先输入待测试文本。')
            return
        self.save_free_session('trial_started', finish=True)
        self.trial = None
        self._trial_joined_ambiguous = False
        self.language.reset()
        self.personal_history = TextLearningHistory()  # Starting a new task is not correcting previous text.
        self.editor.clear()
        super().clear()
        self.trial = SentenceTrial(target, self.dictionary.entries)
        self.trial_initial_fuzzy_policy = self.fuzzy_policy.snapshot()
        self.trial_initial_personal_model = self.personal_model.snapshot()
        self.personal_toggle.setEnabled(False)
        self.personal_profile.setEnabled(False)
        self.tap_guard.reset()
        self.tap_guard_toggle.setEnabled(False)
        self.tap_guard_status.setText('稳定轻触选字 · 出字后保护250ms' if self.tap_guard_toggle.isChecked() else '连续点击保护已关闭 · 手势判定仍开启')
        self.report_path = None
        self.editor.setReadOnly(True)
        self.editor.setPlaceholderText('使用戒指书写并确认候选，已输入文字显示在这里')
        self.target_input.setEnabled(False)
        self.common_toggle.setEnabled(False)
        self.fuzzy_toggle.setEnabled(False)
        self.correction_toggle.setEnabled(False)
        self.llm_toggle.setEnabled(False)
        self.once_toggle.setEnabled(False)
        self.joined_toggle.setEnabled(False)
        self.vector_toggle.setEnabled(False)
        self.fill_toggle.setEnabled(False)
        self.start_trial_button.setEnabled(False)
        self.finish_trial_button.setEnabled(True)
        self.record('sentence_trial_start', target=target,
                    context_ranking=ranking_snapshot(),
                    personal_learning=self.personal_toggle.isChecked(), writing_profile=self.writing_profile,
                    personal_model=self.trial_initial_personal_model,
                    common_only=self.common_toggle.isChecked(), fuzzy=self.fuzzy_toggle.isChecked(),
                    fuzzy_policy=self.fuzzy_policy.snapshot(),
                    adaptive_fuzzy_policy=ADAPTIVE_SETTINGS,
                    whole_correction_enabled=self.correction_toggle.isChecked(),
                    whole_correction_policy=DEFAULT_CORRECTION_POLICY.snapshot(),
                    llm_context_enabled=self.llm_toggle.isChecked(), llm_model=self.language.settings.model,
                    single_display_enabled=self.once_toggle.isChecked(), single_display_budget_ms=BUDGET_MS,
                    tap_guard_enabled=self.tap_guard_toggle.isChecked(), tap_guard_policy=self.tap_guard.snapshot())
        self.update_trial()

    def update_trial(self):
        self.result.setToolTip(self.result.text())
        self.trial_hint.setToolTip(self.trial_hint.text())
        trial = getattr(self, 'trial', None)
        if trial is None:
            return
        actual = self.editor.text() if trial.active else trial.actual
        # Experiment is sequential transcription; mouse selection must not
        # change the expected character or replace previously committed text.
        if trial.active:
            self.editor.deselect()
            self.editor.setCursorPosition(len(actual))
        parts = []
        correct = 0
        for index, char in enumerate(trial.target):
            color = '#1a936f' if index < len(actual) and actual[index] == char else '#d64550' if index < len(actual) else '#8b94a5'
            correct += index < len(actual) and actual[index] == char
            background = 'background:#dbe5ff; border-bottom:2px solid #234cdb;' if index == len(actual) else ''
            parts.append(f'<span style="color:{color};{background}">{html.escape(char)}</span>')
        if len(actual) > len(trial.target):
            parts.append('<span style="color:#d64550">'+html.escape(actual[len(trial.target):])+'</span>')
        self.target_preview.setText(''.join(parts))
        self.progress.setValue(round(correct / len(trial.target) * 100))
        metrics = trial.summary(actual)
        percent = lambda value: '—' if value is None else f'{value:.1%}'
        rows = [('总用时', f"{metrics['elapsed_s']:.1f} 秒"),
                ('输入速度', f"{metrics['characters_per_minute']:.1f} 字/分"),
                ('文本准确率', percent(metrics['text_accuracy'])),
                ('笔画准确率', percent(metrics['stroke_accuracy'])),
                ('出字成功率', percent(metrics['delivery_success_rate'])),
                ('提交正确率', percent(metrics['commit_accuracy'])),
                ('评分 / 未评分', f"{metrics['scored_strokes']} / {metrics['unscored_strokes']} 笔"),
                ('平均识别耗时', f"{metrics['mean_recognition_ms'] or 0:.1f} ms")]
        for title, value in rows:
            if hasattr(self, 'metric_values'):
                self.metric_values[title].setText(value)
        self.trial_metrics.setText('<table width="100%" cellspacing="0" cellpadding="2">' + ''.join(
            f'<tr><td style="color:#738099">{title}</td><td align="right"><b>{value}</b></td></tr>'
            for title, value in rows) + '</table>')
        from proximic_ring.touchpad.stroke.stroke_input import NAMES
        if hasattr(self, 'category_values'):
            for category, row in metrics['per_category'].items():
                self.category_values[category].setText(
                    f'{percent(row["accuracy"])}  {row["correct"]}/{row["samples"]}')
        self.category_metrics.setText('<table width="100%" cellspacing="0" cellpadding="1">' + ''.join(
            f'<tr><td>{NAMES[category]}</td><td align="right">{percent(row["accuracy"])}　'
            f'<span style="color:#738099">{row["correct"]}/{row["samples"]}</span></td></tr>'
            for category, row in metrics['per_category'].items()) + '</table>')
        if trial.active:
            char = trial.target[len(actual)] if len(actual) < len(trial.target) else None
            code = trial.codes.get(char, '')
            expected = NAMES.get(code[len(self.code)], '—') if len(self.code) < len(code) else '请选字 / 退笔'
            unsupported = ''.join(dict.fromkeys(c for c in trial.target if c not in trial.codes and c not in '，。？'))
            self.trial_hint.setText(f'当前第 {len(actual)+1} 字：{char or "已超出原文"} · 下一笔：{expected} · '
                                   '仅戒指识别计入笔画准确率；按钮输入单独记录。' +
                                   (f'\n原文含未支持字符：{unsupported}。可结束保存未完成结果，或重新编辑原文。' if unsupported else ''))

    def trial_finished(self):
        trial = getattr(self, 'trial', None)
        return trial is not None and not trial.active

    def finish_trial(self, reason='manual'):
        trial = getattr(self, 'trial', None)
        if trial is None or not trial.active:
            return
        report = trial.finish(self.editor.text())
        report['finish_reason'] = reason if isinstance(reason, str) else 'manual'
        report['fuzzy_policy_initial'] = self.trial_initial_fuzzy_policy
        report['personal_model_initial'] = self.trial_initial_personal_model
        self.add_report_settings(report)
        self.record('sentence_trial_end', summary=report)
        self.log.flush()
        self.report_path = self.log_path.with_name(self.log_path.stem + f'-trial-{time.time_ns()}.json')
        write_report(self.report_path, report)
        self.target_input.setEnabled(True)
        self.personal_toggle.setEnabled(True)
        self.personal_profile.setEnabled(True)
        self.common_toggle.setEnabled(True)
        self.fuzzy_toggle.setEnabled(True)
        self.correction_toggle.setEnabled(True)
        self.llm_toggle.setEnabled(not self.once_toggle.isChecked())
        self.once_toggle.setEnabled(True)
        self.joined_toggle.setEnabled(self.joined_model is not None)
        self.vector_toggle.setEnabled(True)
        self.fill_toggle.setEnabled(True)
        self.start_trial_button.setEnabled(True)
        self.start_trial_button.setText('重新开始实验')
        self.finish_trial_button.setEnabled(False)
        self.tap_guard_toggle.setEnabled(True)
        self.editor.setReadOnly(False)
        self.editor.setPlaceholderText('可继续自由书写、选字；重新开始实验后清空并计时')
        self.update_trial()
        self.trial_hint.setText(('完成' if report['completed'] else '已结束（未完成）') +
                               f' · 报告：{self.report_path}\n可继续自由书写；本轮统计和报告已冻结。' +
                               (' 本轮含手动笔画，请勿作为纯戒指实验数据。' if report['contains_manual_strokes'] else ''))

    def add_report_settings(self, report):
        report['common_only'] = self.common_toggle.isChecked()
        report['fuzzy_matching'] = self.fuzzy_toggle.isChecked()
        report['fuzzy_policy'] = self.fuzzy_policy.snapshot()
        report['adaptive_fuzzy_policy'] = dict(ADAPTIVE_SETTINGS)
        report['left_dot_policy'] = dict(left_dot_snapshot(), cost=self.fuzzy_policy.left_dot_cost)
        report['recognizer'] = self.recognizer
        report['context_ranking'] = ranking_snapshot()
        report['context_model'] = getattr(self.predictor, 'model', 'legacy')
        report['personal_learning'] = self.personal_toggle.isChecked()
        report['writing_profile'] = self.writing_profile
        report['personal_model_final'] = self.personal_model.snapshot()
        report['whole_correction_enabled'] = self.correction_toggle.isChecked()
        report['llm_context_enabled'] = self.llm_toggle.isChecked()
        report['single_display_enabled'] = self.once_toggle.isChecked()
        report['joined_stroke_enabled'] = self.joined_toggle.isChecked()
        report['stroke_vector_enabled'] = self.vector_toggle.isChecked()
        report['stroke_vector_model'] = self.vector_model.snapshot()
        report['candidate_fill_enabled'] = self.fill_toggle.isChecked()
        report['component_encoding'] = dict(enabled=self.dictionary.components_enabled,
            model=self.dictionary.component_decoder.snapshot() if self.dictionary.component_decoder else {})
        report['joined_stroke_model'] = self.joined_model.snapshot() if self.joined_model else None
        report['joined_contacts_unscored'] = sum(e['kind'] == 'joined_contact_unscored' for e in report['events'])
        report['single_display_budget_ms'] = BUDGET_MS
        report['llm_model'] = self.language.settings.model
        report['llm_context_policy'] = dict(contextChars=CONTEXT_CHARS, maxStrokes=SHORT_STROKES,
                                          commit='single-character', localOnly=True)
        report['llm_assisted_commits'] = sum(e['kind'] == 'llm_context_commit' and e['delivered']
                                              for e in report['events'])
        report['whole_correction_policy'] = DEFAULT_CORRECTION_POLICY.snapshot()
        correction_events = [e for e in report['events'] if e['kind'] == 'whole_correction_candidates']
        report['whole_correction_analyses'] = len(correction_events)
        report['whole_correction_mean_ms'] = (sum(e['elapsed_ms'] for e in correction_events) /
                                               len(correction_events) if correction_events else None)
        report['whole_correction_mean_response_ms'] = (sum(e['response_ms'] for e in correction_events) /
                                                        len(correction_events) if correction_events else None)
        report['whole_correction_commits'] = sum(e['kind'] == 'whole_correction_commit' and e['delivered']
                                                 for e in report['events'])
        report['template_profile'] = self.template_profile
        report['trace_display'] = dict(mode='fixed-scale', pixels_per_sdk_unit=TRIAL_TRACE_SCALE)
        report['trace_display']['selection'] = dict(SELECTION_DISPLAY)
        report['tap_guard_enabled'] = self.tap_guard_toggle.isChecked()
        report['tap_guard_policy'] = self.tap_guard.snapshot()
        report['selection_policy'] = dict(trial_menu.TRIAL_SELECTION)
        report['raw_log'] = str(self.log_path)

    def recording(self, *, start=False):
        """The active sentence trial, else the free-writing session.

        Free writing is armed while the ring is connected and opens on the
        first input, so idle connected time is not logged as writing.
        """
        trial = getattr(self, 'trial', None)
        if trial is not None and trial.active:
            return trial
        if getattr(self, 'free_session', None) is None and start and getattr(self, 'free_armed', False):
            self.free_session = FreeWritingSession(self.dictionary.entries)
            self.free_path = self.log_path.with_name(self.log_path.stem + f'-free-{time.time_ns()}.json')
            self.free_initial_fuzzy_policy = self.fuzzy_policy.snapshot()
            self.free_initial_personal_model = self.personal_model.snapshot()
            self.free_saved_events = 0
            self.record('free_writing_start', path=str(self.free_path), text_before=self.editor.text())
        return getattr(self, 'free_session', None)

    def save_free_session(self, reason, *, finish=False):
        """Write the free-writing report; finishing closes it for the next input."""
        session = getattr(self, 'free_session', None)
        if session is None or (not finish and len(session.events) == self.free_saved_events):
            return
        report = session.finish(self.editor.text()) if finish else session.summary(self.editor.text())
        report['finish_reason'] = reason
        report['in_progress'] = not finish
        report['fuzzy_policy_initial'] = self.free_initial_fuzzy_policy
        report['personal_model_initial'] = self.free_initial_personal_model
        self.add_report_settings(report)
        try:
            write_report(self.free_path, report)
        except OSError as exc:
            self.record('free_writing_save_failed', path=str(self.free_path), error=str(exc))
            return
        self.free_saved_events = len(session.events)
        if finish:
            self.record('free_writing_end', path=str(self.free_path), reason=reason,
                        summary={k: v for k, v in report.items() if k != 'events'})
            self.free_session = None
            self.show_free_status(saved=self.free_path)

    def show_free_status(self, saved=None):
        trial = getattr(self, 'trial', None)
        if trial is not None and trial.active:
            return
        session = getattr(self, 'free_session', None)
        if session is not None:
            summary = session.summary(self.editor.text())
            self.trial_hint.setText(f"自由书写记录中 · {summary['stroke_attempts']} 笔 / "
                                    f"{summary['committed_characters']} 字 · 断开戒指或关闭程序时自动保存")
        elif saved is not None:
            self.trial_hint.setText(f'自由书写记录已保存：{saved.name}')
        else:
            return
        self.trial_hint.setToolTip(str(saved or self.free_path))

    def trial_event(self, kind, **values):
        session = self.recording(start=kind in FREE_INPUT_KINDS)
        if session is not None:
            session.event(kind, **values)

    def commit(self, index):
        if self.batch_loading():
            return
        session = self.recording(start=True)
        position = len(self.editor.text())
        before = self.editor.text()
        character = self.candidates[index] if 0 <= index < len(self.candidates) else None
        phrase = getattr(self, 'llm_hints', {}).get(character)
        repair = next((row for row in self.matches if row['char'] == character and
                       row.get('match_type') in ('whole_character', 'tolerant')), None)
        code = self.code
        evidence = list(self.evidence)
        joined_match = next((row for row in self.matches if row['char'] == character and row.get('match_type') == 'joined_stroke'), None)
        start = qt_text_index(before, self.editor.selectionStart() if self.editor.hasSelectedText() else self.editor.cursorPosition())
        end = start + len(self.editor.selectedText())
        self._committing = True
        try:
            super().commit(index)
        finally:
            self._committing = False
        if (character is not None and self.personal_toggle.isChecked() and not repair and not joined_match and
                self.editor.text() == before[:start] + character + before[end:]):
            token = self.personal_model.learn(self.dictionary, character, evidence)
            if token:
                self.personal_history.track(token, self.editor.text(), qt_text_index(self.editor.text(), self.editor.cursorPosition()))
                self.save_personal_profile()
                detail = dict(profile=self.writing_profile, model=self.personal_model.snapshot())
                self.record('personal_learning', **detail)
                self.trial_event('personal_learning', **detail)
        if isinstance(session, FreeWritingSession):
            session.commit(position, character, character is not None and self.editor.text() == before + character,
                           code=code, label=not repair and not joined_match)
        elif session is not None:
            session.commit(position, character, character is not None and self.editor.text() == before + character, code=code)
        if phrase:
            detail = dict(character=character, phrase=phrase, code=code,
                          delivered=self.editor.text() == before + character)
            self.record('llm_context_commit', **detail)
            self.trial_event('llm_context_commit', **detail)
        if repair and repair['match_type'] == 'tolerant':
            # Page-filling guesses are logged apart from whole-character repairs.
            detail = dict(character=character, code=code, guessed_code=repair.get('code', ''),
                level=repair['tolerant_level'], prefix_length=repair.get('prefix_length'),
                edits=repair.get('edits', []), cost=repair.get('cost'),
                delivered=self.editor.text() == before + character)
            self.record('candidate_fill_commit', **detail)
            self.trial_event('candidate_fill_commit', **detail)
        elif repair:
            detail = dict(character=character, code=code, corrected_code=repair['code'],
                match_type=repair['match_type'], edits=repair['edits'],
                correction_summary=repair['correction_summary'],
                delivered=self.editor.text() == before + character)
            self.record('whole_correction_commit', **detail)
            self.trial_event('whole_correction_commit', **detail)
        if joined_match:
            detail = dict(character=character, physical_code=code, logical_prefix=joined_match['logical_prefix'],
                family=joined_match['joined_family'], contacts=joined_match['joined_contacts'],
                delivered=self.editor.text() == before[:start]+character+before[end:])
            self.record('joined_stroke_commit', **detail)
            self.trial_event('joined_stroke_commit', **detail)
        if character is not None and self.editor.text() == before + character:
            self.tap_guard.committed(getattr(self, '_commit_contact', None))
        self.check_trial_complete()
        self.show_free_status()

    def check_trial_complete(self):
        self.update_trial() if hasattr(self, 'trial') else None
        trial = getattr(self, 'trial', None)
        if trial is not None and trial.active and self.editor.text() == trial.target and not getattr(self, '_punctuating', False):
            self.finish_trial('completed')

    def undo(self):
        self.trial_event('undo_stroke', code=self.code)
        super().undo()
        self._trial_joined_ambiguous = self.joined_toggle.isChecked() and any(s.get('joined_options') for s in self.evidence)

    def clear(self):
        self._trial_joined_ambiguous = False
        if not getattr(self, '_committing', False):
            self.trial_event('clear_strokes', code=self.code)
        super().clear()

    def closeEvent(self, event):
        self.closing = True
        self.update_input_focus()
        self.finish_trial('window_closed')
        self.save_free_session('window_closed', finish=True)
        if hasattr(self, 'correction'):
            self.correction.close()
        if hasattr(self, 'language'):
            self.language.close()
        if hasattr(self, 'batch'):
            self.batch.close()
        super().closeEvent(event)

    def refresh(self):
        if hasattr(self, 'batch') and self.once_toggle.isChecked():
            self.refresh_batch()
            return
        selected = self.selected
        super().refresh()
        if hasattr(self, 'joined_toggle'):
            context = '' if self.editor.hasSelectedText() else self.editor.text()[:self.editor.cursorPosition()]
            self.candidates, self.matches = merge_joined_candidates(self.dictionary, self.evidence,
                self.candidates, self.matches, self.joined_model, enabled=self.joined_toggle.isChecked(),
                fuzzy=self.fuzzy_toggle.isChecked(), predictor=self.predictor, context=context,
                allowed=self.allowed_characters, policy=self.fuzzy_policy)
            self.selected = max(0, min(self.selected, len(self.candidates)-1))
        if hasattr(self, 'correction'):
            generation = self.correction.generation
            context = '' if self.editor.hasSelectedText() else self.editor.text()[:self.editor.cursorPosition()]
            repairs = self.correction.update(
                enabled=self.correction_toggle.isChecked(),
                code=self.code, evidence=self.evidence, context=context,
                dictionary=self.dictionary, predictor=self.predictor,
                allowed=self.allowed_characters, normal=self.candidates)
            if self.code and repairs:
                # The promoted completed hypothesis already supplies a recovery
                # choice. Keep the original four normal choices plus one repair.
                reserved = 1 if any(row.get('context_extra_slot') for row in self.matches) else 2
                self.candidates = merge_candidates(self.candidates, repairs, reserved=reserved)
                self.matches += repairs
                self.selected = min(selected, len(self.candidates) - 1) if generation == self.correction.generation else 0
            self.correction_status.setText(self.correction.status)
            self.correction_status.setToolTip(self.correction.status)
            match = next((row for row in self.matches if self.candidates and
                          row['char'] == self.candidates[self.selected]), {})
            if match.get('match_type') == 'whole_character':
                self.composition.setToolTip('整字纠错建议：' + match['correction_summary'])
            if self.code and repairs:
                # Inserting repairs changes list indices. Resolve highlights
                # through the selected character rather than a normal-list slot.
                from proximic_ring.touchpad.stroke.stroke_input import SYMBOLS
                replacements = set(match.get('corrections', ()) if match.get('match_type') == 'whole_character'
                                   else match.get('replacement_corrections', ()))
                self.composition.setText(' '.join(
                    ('<span style="color:#DC2626">' + SYMBOLS[c].replace('𠃍', '乛') + '</span>'
                     if i in replacements else SYMBOLS[c].replace('𠃍', '乛')) +
                    ('<sub style="font-size:9px">?</sub>' if self.fuzzy_toggle.isChecked()
                     and self.evidence[i]['ambiguous'] else '') for i, c in enumerate(self.code)))
                if match.get('match_type') != 'whole_character':
                    self.composition.setToolTip('；'.join(
                        f'候选「{match["char"]}」：第{i+1}笔 {SYMBOLS[self.code[i]]} → {SYMBOLS[match["code"][i]]}'
                        for i in sorted(replacements)))
        if not self.code:
            context = '' if self.editor.hasSelectedText() else self.editor.text()[:self.editor.cursorPosition()]
            self.candidates = self.predictor.suggestions(context, allowed_characters=self.allowed_characters) if context else []
            self.matches = []
            self.selected = max(0, min(self.selected, len(self.candidates)-1))
            self.composition.setText('联想候选' if self.candidates else '等待落笔')
        if hasattr(self, 'language'):
            context = '' if self.editor.hasSelectedText() else self.editor.text()[:self.editor.cursorPosition()]
            generation = self.language.generation
            phrases = self.language.update(self.llm_toggle.isChecked(), context)
            self.llm_hints = {}
            if phrases:
                joined_rows = {r['char']: r for r in self.matches if r.get('match_type') == 'joined_stroke'}
                self.candidates, self.matches, self.llm_hints = apply_predictions(
                    self.candidates, self.matches, phrases, dictionary=self.dictionary,
                    code=self.code, evidence=self.evidence, fuzzy=self.fuzzy_toggle.isChecked(),
                    policy=self.fuzzy_policy, allowed=self.allowed_characters)
                self.matches = [dict(joined_rows[r['char']], **({'llm_phrase': r['llm_phrase']} if r.get('llm_phrase') else {}))
                                if r['char'] in joined_rows else r for r in self.matches]
                self.selected = (max(0, min(selected, len(self.candidates) - 1))
                                 if generation == self.language.generation else 0)
            self.llm_status.setText(self.language.status)
            self.llm_status.setToolTip(self.language.status + '\n' + '、'.join(phrases))
        self.fill_candidates()
        if hasattr(self, 'arrows'):
            self.render_candidates()

    def fill_candidates(self):
        # Strict mode stays a pure code lookup for comparison.
        if (not hasattr(self, 'fill_toggle') or not self.fill_toggle.isChecked() or not self.code
                or not self.fuzzy_toggle.isChecked()):
            return
        context = '' if self.editor.hasSelectedText() else self.editor.text()[:self.editor.cursorPosition()]
        self.candidates, self.matches = fill_page(self.candidates, self.matches,
            index=index_for(self.dictionary, self.allowed_characters), evidence=self.evidence,
            predictor=self.predictor, context=context, allowed=self.allowed_characters,
            priors=self.dictionary._fuzzy_priors, page_size=trial_menu.PAGE_SIZE)

    def set_fill_enabled(self, enabled):
        self.selected = 0
        self.refresh()
        self.record('candidate_fill_settings', enabled=enabled, policy=DEFAULT_TOLERANT_POLICY.snapshot())

    def render_candidates(self):
        if hasattr(self, 'arrows'):
            self.update_joined_status()
            offset = self.selected//trial_menu.PAGE_SIZE*trial_menu.PAGE_SIZE
            visuals = trial_candidate_visuals(self)
            for index, button in enumerate(self.buttons):
                absolute = offset+index
                button.set_visual(visuals[index] if index < len(visuals) else None)
                button.set_loading(self.batch_loading())
                button.setEnabled(absolute < len(self.candidates))
            self.page_label.setText('无常用字匹配' if self.code and not self.candidates and self.allowed_characters is not None
                                   else f'第 {offset//trial_menu.PAGE_SIZE+1} / {max(1,(len(self.candidates)+trial_menu.PAGE_SIZE-1)//trial_menu.PAGE_SIZE)} 页')
            self.previous_page_button.setEnabled(offset > 0)
            self.next_page_button.setEnabled(len(self.candidates) > trial_menu.PAGE_SIZE)
            self.show_selection_state(self.selection_state)

    def batch_loading(self):
        return hasattr(self, 'batch') and self.once_toggle.isChecked() and self.batch.loading

    def can_publish_batch(self):
        return (not self.closing and self.selection_state['phase'] == 'idle'
                and not any(button.isDown() for button in self.direction_buttons.values())
                and not getattr(self, '_committing', False) and not getattr(self, '_punctuating', False))

    def set_once_enabled(self, enabled):
        self.batch.reset()
        self.language.reset()
        self.correction.debounce.stop()
        self.correction.worker.cancel()
        self.correction.key = None
        self.correction.generation += 1
        self.correction.rows, self.correction.deferred = [], None
        self.llm_toggle.setEnabled(not enabled)
        self.selected = 0
        self.once_status.setText('开启 · 计算后一次显示' if enabled else '关闭 · 使用原有渐进候选')
        self.record('single_display_settings', enabled=enabled, budget_ms=BUDGET_MS)
        self.refresh()

    def refresh_batch(self):
        from proximic_ring.touchpad.stroke.stroke_input import SYMBOLS
        self.update_adaptive_status()
        context = '' if self.editor.hasSelectedText() else self.editor.text()[:self.editor.cursorPosition()]
        allowed = frozenset(self.allowed_characters) if self.allowed_characters is not None else None
        # Ink and confidence can change even when the recognized code is equal.
        key = (self.code, json.dumps(self.evidence, sort_keys=True), context,
               id(self.dictionary), id(self.predictor), self.fuzzy_toggle.isChecked(),
               self.fuzzy_policy.level, allowed, self.correction_toggle.isChecked(), self.joined_toggle.isChecked(),
               self.vector_toggle.isChecked(), self.fill_toggle.isChecked())
        self.batch.update(key, dict(code=self.code, evidence=self.evidence, context=context,
            dictionary=self.dictionary, predictor=self.predictor, allowed=allowed,
            fuzzy=self.fuzzy_toggle.isChecked(), policy=self.fuzzy_policy,
            correction=self.correction_toggle.isChecked(), joined_enabled=self.joined_toggle.isChecked(), joined_model=self.joined_model,
            fill=self.fill_toggle.isChecked()))
        snapshot = self.batch.result
        self.candidates = list(snapshot['chars']) if snapshot else []
        self.matches = list(snapshot['rows']) if snapshot else []
        self.llm_hints = {}
        self.selected = max(0, min(self.selected, len(self.candidates) - 1))
        match = next((row for row in self.matches if self.candidates and
                      row['char'] == self.candidates[self.selected]), {})
        replacements = set(match.get('corrections', ()) if match.get('match_type') == 'whole_character'
                           else match.get('replacement_corrections', ()))
        self.composition.setText(' '.join(
            ('<span style="color:#DC2626">' + SYMBOLS[c].replace('𠃍', '乛') + '</span>'
             if i in replacements else SYMBOLS[c].replace('𠃍', '乛')) +
            ('<sub style="font-size:9px">?</sub>' if self.fuzzy_toggle.isChecked()
             and self.evidence[i]['ambiguous'] else '') for i, c in enumerate(self.code))
            or ('联想候选' if self.candidates else '等待落笔'))
        self.composition.setToolTip(match.get('correction_summary', ''))
        self.once_status.setText('加载中…' if self.batch.loading else
            f"{snapshot['reason']} · {snapshot['elapsed_ms']:.0f} ms · 本批已固定")
        self.correction_status.setText('随本批一次计算' if self.correction_toggle.isChecked() else '已关闭')
        self.llm_status.setText('使用一次显示模式中的Qwen选字')
        self.render_candidates()
        if self.batch.loading:
            self.page_label.setText('候选加载中…')

    def batch_ready(self, result):
        if self.closing or not self.once_toggle.isChecked():
            return
        detail = dict(code=self.code, candidates=result['chars'], elapsed_ms=result['elapsed_ms'],
                      reason=result['reason'], choice=result['choice'], error=result['error'])
        self.record('single_display_candidates', **detail)
        self.trial_event('single_display_candidates', **detail)
        self.selected = 0
        self.refresh()

    def can_apply_correction(self):
        # Aiming at a candidate freezes its position until the contact finishes.
        # Paging also keeps the current page stable until input changes.
        return (not self.closing and self.selection_state['phase'] == 'idle' and self.selected == 0
                and not any(button.isDown() for button in self.direction_buttons.values())
                and not getattr(self, '_committing', False) and not getattr(self, '_punctuating', False))

    def correction_ready(self, result):
        if self.closing or (hasattr(self, 'once_toggle') and self.once_toggle.isChecked()):
            return
        detail = dict(code=result['code'], elapsed_ms=result['elapsed_ms'],
                      response_ms=result['response_ms'],
                      candidate_count=len(result['rows']), candidates=result['rows'], error=result['error'])
        self.record('whole_correction_candidates', **detail)
        self.trial_event('whole_correction_candidates', **detail)
        self.refresh()

    def set_correction_enabled(self, enabled):
        self.selected = 0
        self.refresh()
        self.record('whole_correction_settings', enabled=enabled, policy=DEFAULT_CORRECTION_POLICY.snapshot())

    def language_ready(self, result):
        if self.closing or self.once_toggle.isChecked():
            return
        self.record('llm_context_prediction', **result)
        self.trial_event('llm_context_prediction', **result)
        self.refresh()

    def set_llm_enabled(self, enabled):
        self.selected = 0
        self.language.reset()
        self.refresh()
        self.record('llm_context_settings', enabled=enabled, model=self.language.settings.model,
                    contextChars=CONTEXT_CHARS, maxStrokes=SHORT_STROKES)

    def show_selection_state(self, state):
        if state['phase'] != 'idle' and self.selection_state['phase'] == 'idle':
            self._batch_contact = self.batch_loading()
        if state['phase'] != self.selection_state['phase']:
            self.record('contact_intent_state', **state)
        self.selection_state = state
        self.trace_view.set_selection_state(state)
        phase, direction = state['phase'], state.get('direction')
        two_finger = state.get('source') == 'two-finger'
        self.selection_hint.setText(
            ('双指左滑 · 抬手删除' if direction == 'left' else
             '双指选字 · 抬手确认' if direction else '双指滑向目标格子 · 原地抬手取消') if two_finger else
            '选字模式 · 抬手确认' if phase == 'selecting' else
            f"保持不动 · {int(state['progress'])}%" if phase == 'holding' else
            '书写中 · 抬手结束' if phase == 'writing' else
            self.idle_hint())
        if self.worker is not None and self._focus_since is None:
            self.selection_hint.setText('窗口未聚焦 · 戒指输入已暂停')
        elif self.batch_loading():
            self.selection_hint.setText('候选加载中 · 请稍候选字')
        self.selection_hint.setStyleSheet('color:#234cdb; font-weight:bold;' if phase == 'selecting' else '')
        if hasattr(self, 'selection_progress'):
            self.selection_progress.setValue(int(state.get('progress', 0)))
        for name, button in self.direction_buttons.items():
            if button.styleSheet(): button.setStyleSheet('')
            active = phase == 'selecting' and name == (direction or 'center')
            if button.property('directionActive') != active:
                button.setProperty('directionActive', active)
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()

    @staticmethod
    def idle_hint():
        return '双指滑向格子选字 · 双指左滑删除' if trial_menu.TWO_FINGER else '停留 0.333 秒进入选字'

    def next_candidate_page(self):
        if len(self.candidates) <= trial_menu.PAGE_SIZE:
            self.result.setText('只有一页候选，当前笔画已保留')
            return
        self.selected = ((self.selected//trial_menu.PAGE_SIZE+1)*trial_menu.PAGE_SIZE) % (((len(self.candidates)+trial_menu.PAGE_SIZE-1)//trial_menu.PAGE_SIZE)*trial_menu.PAGE_SIZE)
        self.refresh()
        self.result.setText('已翻页；双指滑动继续选字' if trial_menu.TWO_FINGER else '已翻页；触桌停留后继续选字')

    def accept_selection(self, value):
        decision = trial_menu.trial_selection(value['decision'])
        blocked = self.batch_loading() or getattr(self, '_batch_contact', False)
        self._batch_contact = False
        self.record('hold_selection', **dict(value, decision=decision), source_decision=value['decision'], code=self.code)
        self.trial_event('gesture', action=decision['action'], target=decision.get('target'))
        self.show_selection_state(dict(phase='idle', progress=0, direction=None))
        self.trace_view.show_trace([])
        if blocked and decision['action'] in ('select', 'page'):
            self.result.setText('本次接触开始时候选仍在加载，未选字')
            return
        self._commit_contact = value
        try:
            self.apply_contact_decision(decision, [])
        finally:
            self._commit_contact = None

    def disconnected(self):
        self.free_armed = False
        self.save_free_session('disconnected', finish=True)
        diagnostics = self.contact_diagnostics
        if diagnostics is not None:
            self.record('contact_diagnostics_end', path=str(diagnostics.path),
                        dropped_records=diagnostics.dropped, error=diagnostics.error,
                        flushed=diagnostics.closed and not diagnostics.thread.is_alive())
            if not diagnostics.error and not diagnostics.dropped:
                self.diagnostic_status.setText('诊断日志已保存 · 与会话日志放在同一目录。')
            self.update_diagnostic_status()
        self.show_selection_state(dict(phase='idle', progress=0, direction=None))
        super().disconnected()

    def set_common_only(self, enabled):
        self.allowed_characters = common_characters() if enabled else None
        self.selected = 0
        self.refresh()
        self.record('candidate_scope', candidateScope=candidate_scope_snapshot(enabled),
                    code=self.code)

    def toggle(self):
        if self.worker is not None:
            self.connect_button.setEnabled(False)
            self.status.setText('正在停止并断开…')
            self.worker.stop()
            return
        diagnostic_path = self.log_path.with_name(self.log_path.stem + f'-contact-{time.time_ns()}.jsonl')
        try:
            self.contact_diagnostics = ContactDiagnostics(diagnostic_path, self.log_path, synthetic=self.synthetic)
        except OSError as exc:
            self.diagnostic_status.setText(f'无法创建诊断日志：{exc}')
            return
        self.diagnostic_group = None
        self.diagnostic_status.setText('逐帧诊断记录中 · 可先选任务，再点“开始一组”。')
        self.diagnostic_status.setToolTip(str(diagnostic_path))
        self.record('contact_diagnostics', path=str(diagnostic_path),
                    schema='stroke-contact-diagnostics-v1', stream_id=self.contact_diagnostics.stream_id)
        self.worker = RingWorker(self.device.text().strip(), self.dictionary,
                                 synthetic=self.synthetic, gestures=False, diagnostics=self.contact_diagnostics)
        self.update_input_focus()
        self.worker.focus_since = self._focus_since
        self.warmup = 0
        self.last_contact_at = time.monotonic()
        self.worker.status.connect(self.status.setText)
        self.worker.focused_input.connect(self.receive_focused_input)
        self.worker.sdk_click.connect(lambda value: self.record('sdk_click', **value))
        self.worker.stats.connect(self.show_stats)
        self.worker.contact_samples.connect(self.show_contact)
        self.worker.contact_reset.connect(self.reset_contact_display)
        self.worker.finished.connect(self.disconnected)
        self.device.setEnabled(False)
        self.connect_button.setText('停止并断开')
        self.free_armed = True
        self.worker.start()

    def show_stats(self, value):
        super().show_stats(value)
        self.update_diagnostic_status()
        self.metrics.setText(f'预热 {value.warmup_frames}/200 · 收包 {value.packets}\n'
                             f'缺包 {value.missing_packets} · 重置 {value.resets} · 概率 {value.contact_probability:.2f}\n'
                             f'计算 {value.inference_batch_ms:.1f} ms · 队列 {value.packet_queue_age_ms:.1f} ms')

    def set_tap_guard(self, enabled):
        self.tap_guard.reset()
        self.tap_guard_status.setText('稳定轻触选字 · 出字后保护250ms' if enabled else '连续点击保护已关闭 · 手势判定仍开启')
        self.record('tap_guard_settings', enabled=enabled, policy=self.tap_guard.snapshot())

    def accept_sdk_tap(self, contact=None):
        blocked = self.batch_loading() or getattr(self, '_batch_contact', False)
        self._batch_contact = False
        if blocked:
            self.show_selection_state(dict(phase='idle', progress=0, direction=None))
            return
        contact = contact or {}
        decision = self.tap_guard.decide(contact, enabled=self.tap_guard_toggle.isChecked())
        self.record('tap_confirmation', **decision, points=contact.get('points', []), code=self.code,
                    candidate=self.candidates[self.selected//trial_menu.PAGE_SIZE*trial_menu.PAGE_SIZE] if self.candidates else None)
        self.trial_event('tap_confirmation', **decision)
        if not decision['allowed']:
            self.trace_view.show_trace(contact.get('points') or [])
            message = ('这次接触有明显移动，未选字；当前笔序保留，请重写这一笔或用方向选字。'
                       if decision['reason'] == 'moving-contact' else
                       '刚刚已出字，本次连续点击未确认；请稍后轻触或用方向选字。'
                       if decision['reason'] == 'commit-cooldown' else
                       ('本次动作未能确定为点击，未选字；请重新轻触或双指选字。' if trial_menu.TWO_FINGER else
                        '本次动作未能确定为点击，未选字；请重新轻触或停留选字。')
                       if decision['reason'] == 'uncertain-motion' else '接触轨迹不完整或无效，未选字；当前笔序保留。')
            self.tap_guard_status.setText(message)
            self.tap_guard_status.setToolTip(message)
            self.result.setText(message)
            return
        self.tap_guard_status.setText('轻触已放行 · 点击确认首候选')
        self.trace_view.show_trace([])
        self._commit_contact = contact
        try:
            self.commit(self.selected//trial_menu.PAGE_SIZE*trial_menu.PAGE_SIZE)
        finally:
            self._commit_contact = None

    def accept_stroke(self, value):
        self._batch_contact = False
        points = value['points']
        decision = recognize(points, stroke_dictionary=self.dictionary, legacy_selection=False)
        self.record('contact_decision', points=points, kinematics=value.get('kinematics'), decision=decision,
                    code=self.code, context_enabled=False, deletionSource=DELETION_SOURCE)
        action = decision['action']
        if action != 'stroke':
            self.trial_event('gesture', action=action, target=decision.get('target'))
        self.trace_view.show_trace(points)
        self.apply_contact_decision(decision, points, value.get('kinematics'))

    def apply_contact_decision(self, decision, points, kinematics=None):
        if self.batch_loading() and decision['action'] in ('select', 'page'):
            self.result.setText('候选加载中，本次未选字')
            return
        action = decision['action']
        if action == 'select':
            index = self.selected//trial_menu.PAGE_SIZE*trial_menu.PAGE_SIZE+decision['target']
            if index >= len(self.candidates):
                session = self.recording(start=True)
                if session is not None:
                    session.commit(len(self.editor.text()), None, False, source='gesture', code=self.code)
                self.result.setText('这个方向没有候选，当前笔画已保留')
            else:
                self.commit(index)
                self.result.setText(f"已选第 {decision['target']+1} 候选")
        elif action == 'punctuation': self.punctuate(decision['target'])
        elif action == 'page': self.next_candidate_page()
        elif action == 'cancel-selection': self.result.setText('已取消选字，当前笔画已保留')
        elif action == 'delete-one': self.delete_one()
        elif action == 'clear-all':
            self.clear()
            self.result.setText('已清空当前笔画')
        elif action == 'ignore': self.result.setText('上方向暂未分配操作' if decision.get('reason')=='up-reserved' else '无效轨迹，未输入')
        else:
            started = time.monotonic()
            review = decision.get('clearReview')
            reused = review is not None and review['modelAttempted']
            result = review['recognition'] if reused else self.dictionary.recognize(points)
            if result and self.joined_model is not None:
                result = dict(result, joined_options=self.joined_model.recognize(points))
            recognition_ms = (review['recognitionMs'] if reused else 0.) + (time.monotonic()-started)*1000
            session = self.recording(start=True)
            if session is not None:
                self._trial_joined_ambiguous = self._trial_joined_ambiguous or bool(
                    self.joined_toggle.isChecked() and result and result.get('joined_options'))
                if self._trial_joined_ambiguous:
                    session.event('joined_contact_unscored', recognition_ms=recognition_ms,
                        reason='physical contact count differs from logical stroke count',
                        options=result.get('joined_options', []) if result else [],
                        stroke_index=len(self.code))
                else:
                    session.stroke(len(self.editor.text()), len(self.code), result, recognition_ms)
            if result:
                super().accept_stroke(dict(points=points, result=result,
                    recognition_ms=recognition_ms, kinematics=kinematics))
                if self.joined_toggle.isChecked() and result.get('joined_options'):
                    self.record('joined_stroke_options', options=result['joined_options'],
                        physical_code=self.code, candidates=[r for r in self.matches[:5] if r.get('match_type') == 'joined_stroke'])
                if review and review['protect']:
                    self.result.setText(self.result.text()+'\n已按笔画保留'+
                                        ('，识别仍需确认' if review['status']=='uncertain' else ''))
            elif review and review['protect']:
                self.result.setText('这一笔识别不确定，当前笔画未清空；请重写这一笔')
        self.check_trial_complete()
        self.show_free_status()

    def delete_one(self):
        had_code = bool(self.code)
        if had_code: self.undo()
        else:
            self.trial_event('delete_character')
            self.editor.backspace()
        self.result.setText('已退一笔' if had_code else '已删除')
        self.record('delete_one', code=self.code)

    def punctuate(self, symbol):
        self._punctuating = True
        discarded_code = self.code
        if self.code:
            self.clear()
        before = self.editor.text()
        self.editor.insert(symbol)
        if self.editor.text() == before + symbol:
            self.tap_guard.committed(getattr(self, '_commit_contact', None))
        session = self.recording(start=True)
        if session is not None:
            session.commit(len(before), symbol, self.editor.text() == before + symbol, source='punctuation')
        self._punctuating = False
        self.record('punctuation', symbol=symbol, discarded_code=discarded_code)
        self.result.setText('已输入'+symbol)
        self.refresh()
        self.check_trial_complete()

    def page(self, shift):
        index = self.selected//trial_menu.PAGE_SIZE*trial_menu.PAGE_SIZE+shift
        if 0 <= index < len(self.candidates): self.selected = index
        self.refresh()

    def add_manual(self, category):
        self.trial_event('manual_stroke', category=category, stroke_index=len(self.code))
        from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
        self.code += category
        self.evidence.append(stroke_evidence(category))
        self.selected = 0
        self.refresh()
        self.record('manual_stroke', category=category)

    def set_joined_enabled(self, enabled):
        self.selected = 0
        if hasattr(self, 'batch'):
            self.batch.reset()
        self.refresh()
        self.record('joined_stroke_settings', enabled=enabled,
            model=self.joined_model.snapshot() if self.joined_model else None)

    def set_vectors_enabled(self, enabled):
        self.dictionary.stroke_vectors = self.vector_model if enabled else None
        self.selected = 0
        if hasattr(self, 'batch'):
            self.batch.reset()
        self.refresh()
        self.record('stroke_vector_settings', enabled=enabled, model=self.vector_model.snapshot())

    def update_joined_status(self):
        if not hasattr(self, 'joined_status'):
            return
        selected = self.candidates[self.selected] if self.candidates else None
        match = next((r for r in self.matches if r['char'] == selected), {})
        text = self.joined_error if self.joined_model is None else '已关闭' if not self.joined_toggle.isChecked() else '连笔开启 · 保留原笔画，按部件展开'
        if match.get('match_type') == 'joined_stroke':
            text = f"当前候选按连笔解释 · {len(self.code)}次落笔 / {match['logical_entered']}笔"
        self.joined_status.setText(text)




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='Ringo6B72')
    parser.add_argument('--all-characters', action='store_true', help='Start with the unrestricted character dictionary')
    parser.add_argument('--llm-context', action='store_true', help='Enable local Qwen phrase predictions')
    parser.add_argument('--single-display', action='store_true', help='Show loading, then publish candidates once with a 400ms Qwen budget')
    parser.add_argument('--joined-model', type=Path, help='Path to the local experimental joined-stroke model')
    parser.add_argument('--no-joined', action='store_true', help='Disable joined-stroke interpretations for comparison')
    parser.add_argument('--no-stroke-vectors', action='store_true', help='Rank by stroke codes and frequency only')
    parser.add_argument('--stroke-vector-scope', choices=('prefix', 'complete'), default='prefix',
                        help='prefix: score every candidate from the first stroke; complete: only same-code complete characters (v1)')
    parser.add_argument('--no-stroke-components', action='store_true', help='Disable component encoding paths')
    parser.add_argument('--no-candidate-fill', action='store_true', help='Do not top up a short first page with guesses')
    parser.add_argument('--context-model', choices=('wanxiang', 'legacy'), default='wanxiang',
                        help='wanxiang: rime-wanxiang-data grammar and dictionary; legacy: Tatoeba/Wikipedia top-8 followers')
    parser.add_argument('--writing-profile', default='默认', help='Local writer ID; use a different ID for each participant')
    parser.add_argument('--no-personal-learning', action='store_true', help='Use the unchanged generic recognizer')
    parser.add_argument('--hold-select', action='store_true',
                        help='Legacy touchpad link with the old six-candidate hold-then-slide menu (no two-finger)')
    args = parser.parse_args()
    from ring_python_sdk.touchpad.raw2_model import raw2_platform_supported
    two_finger = not args.hold_select and raw2_platform_supported()
    if not args.hold_select and not two_finger:
        print('当前环境不支持 Raw2 双指（需要 Windows x64 的 Raw2 依赖或 Apple Silicon），已改用停留选字。')
    trial_menu.use('two-finger' if two_finger else 'hold')
    app = QApplication([])
    # Native Windows controls otherwise differ from the offscreen preview and
    # use more height on 200% displays. Keep the standalone trial consistent.
    app.setStyle('Fusion')
    window = Window(args.device, common_only=not args.all_characters, llm_context=args.llm_context,
                    writing_profile=args.writing_profile, personal_learning=not args.no_personal_learning,
                    joined_model_path=args.joined_model, joined_enabled=not args.no_joined,
                    vectors_enabled=not args.no_stroke_vectors, vector_scope=args.stroke_vector_scope,
                    fill_enabled=not args.no_candidate_fill, context_model=args.context_model,
                    components_enabled=not args.no_stroke_components,
                    single_display=args.single_display)
    window.status_log = StatusLogWindow(window)
    show_for_screen(window)
    window.status_log.place_beside(window)
    window.status_log.show()
    app.exec()


def show_for_screen(window):
    """Use Qt logical work-area units; never impose a taller minimum than it."""
    available = window.screen().availableGeometry()
    if available.height() < 820 or available.width() < 1250:
        # Also keep the initial (pre-maximize) geometry inside the work area.
        # Windows may paint a frame before sending the maximized resize event.
        window.resize(available.width(), max(window.minimumHeight(), available.height() - 32))
        window.showMaximized()
    else:
        window.resize(min(1440, available.width() - 40), min(900, available.height() - 64))
        window.show()


if __name__ == '__main__': main()
