"""Manual Workflow runner and a paired timing view using the existing Agent."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView)

from proximic_ring.agent_workflow.execution.workflow_trials import available_workflows, make_case, TrialStore, comparison, MODES, HISTORY_MODES
from proximic_ring.agent_workflow.extraction.workflow_artifacts import workflow_steps
from proximic_ring.agent_workflow.parameters.parameter_contract import required


class WorkflowTrialDialog(QDialog):
    def __init__(self, agent, root):
        super().__init__()
        self.agent = agent
        self.case = None
        self.entries = []
        self.rows = []
        self.parameters = {}
        self.pending_run = False
        self.last_trial_id = None
        self.setObjectName('workflowTrialDialog')
        self.setWindowTitle('Workflow · 手动执行与耗时对比')
        self.resize(850, 760)
        self.setMinimumSize(680, 580)
        layout = QVBoxLayout(self)
        self.root = QLineEdit(str(root))
        self.root.setReadOnly(True)
        pick = QPushButton('选择记录目录')
        pick.clicked.connect(self.choose_root)
        refresh = QPushButton('刷新流程')
        refresh.clicked.connect(self.load)
        top = QHBoxLayout(); top.addWidget(self.root, 1); top.addWidget(pick); top.addWidget(refresh)
        layout.addLayout(top)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)
        setup = QWidget(); form = QVBoxLayout(setup)
        self.flows = QComboBox(); self.flows.setObjectName('trialWorkflowSelector')
        self.flows.currentIndexChanged.connect(self.select_flow)
        form.addWidget(QLabel('选择一份流程草稿（不会自动执行或重新提取）')); form.addWidget(self.flows)
        self.goal = QPlainTextEdit(); self.goal.setObjectName('trialGoal'); self.goal.setMaximumHeight(80)
        form.addWidget(QLabel('本次完整任务：各组共用，描述要达到的结果，不填写操作步骤'))
        form.addWidget(self.goal)
        parameter_area = QScrollArea(); parameter_area.setWidgetResizable(True); parameter_area.setMaximumHeight(150)
        parameter_widget = QWidget(); self.parameter_form = QFormLayout(parameter_widget)
        self.parameter_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        parameter_area.setWidget(parameter_widget); form.addWidget(parameter_area)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True); self.preview.setObjectName('trialWorkflowPreview')
        form.addWidget(QLabel('自然语言步骤（将直接替代原任务发送给 Agent）')); form.addWidget(self.preview, 1)
        self.initial = QPlainTextEdit(); self.initial.setMaximumHeight(70)
        self.initial.setPlaceholderText('例如：Safari 已打开，当前标签页为 about:blank；网络和登录状态相同。每次运行前手动恢复。')
        form.addWidget(QLabel('共同起始状态与复位方法（只用于实验记录，不交给 Agent）')); form.addWidget(self.initial)
        prepare = QPushButton('固定任务和参数，进入测试'); prepare.setObjectName('trialPrepare')
        prepare.clicked.connect(self.prepare); form.addWidget(prepare)
        self.tabs.addTab(setup, '任务与流程')

        testing = QWidget(); test_layout = QVBoxLayout(testing)
        self.history = QComboBox(); self.saved_cases = []
        self.history.activated.connect(self.select_case)
        test_layout.addWidget(QLabel('已保存的测试任务')); test_layout.addWidget(self.history)
        self.case_label = QPlainTextEdit('先在“任务与流程”中准备一次测试。')
        self.case_label.setReadOnly(True); self.case_label.setMaximumHeight(120)
        test_layout.addWidget(self.case_label)
        info = QLabel('两组使用相同目标和参数，各开一个新会话。计时从执行请求受理到 Agent 本轮结束，'
                      '包含会话准备、授权和回答等待，不含手动复位。不会自动连续运行。')
        info.setWordWrap(True); test_layout.addWidget(info)
        self.reset = QCheckBox('我已恢复上面记录的起始状态；本轮可以开始真实操作电脑')
        self.reset.setObjectName('trialInitialStateConfirmed'); test_layout.addWidget(self.reset)
        buttons = QHBoxLayout()
        self.connect_button = QPushButton('连接 Agent')
        self.connect_button.clicked.connect(agent.connectCodex); buttons.addWidget(self.connect_button)
        cancel = QPushButton('取消'); cancel.clicked.connect(agent.cancel); buttons.addWidget(cancel)
        test_layout.addLayout(buttons)
        modes = QHBoxLayout()
        self.run_buttons = {}
        for mode in MODES:
            button = QPushButton('执行 · ' + MODES[mode]); button.setObjectName('trialRun_' + mode)
            button.clicked.connect(lambda checked=False, selected=mode: self.run(selected))
            self.run_buttons[mode] = button; modes.addWidget(button)
        test_layout.addLayout(modes)
        self.summary = QPlainTextEdit(); self.summary.setReadOnly(True); test_layout.addWidget(self.summary)
        self.summary.setFixedHeight(110)
        self.table = QTableWidget(0, 6); self.table.setObjectName('trialResults')
        self.table.setHorizontalHeaderLabels(['组别', '运行状态', '总耗时', '工具调用', '等待次数', '目标完成'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        test_layout.addWidget(self.table, 1)
        verify = QHBoxLayout()
        for success, label in ((True, '选中运行：目标已完成'), (False, '选中运行：未完成目标')):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=success: self.verify(value)); verify.addWidget(button)
        folder = QPushButton('打开测试报告'); folder.clicked.connect(self.open_folder); verify.addWidget(folder)
        test_layout.addLayout(verify)
        note = QLabel('只有人工确认完成、未中途修改任务且设置一致的两组才进入对比。'
                      '请交替运行顺序；少量样本只能观察差值，不能证明稳定加速。')
        note.setWordWrap(True); test_layout.addWidget(note)
        self.tabs.addTab(testing, '运行与对比')
        self.message = QLabel(); self.message.setWordWrap(True); layout.addWidget(self.message)
        self.message.setTextFormat(Qt.PlainText)
        self.timer = QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self.poll)
        self.reset.toggled.connect(self.update_buttons)
        self.agent.changed.connect(self.update_buttons)
        self.load()

    def showEvent(self, event):
        super().showEvent(event); self.timer.start(); self.poll()

    def hideEvent(self, event):
        self.timer.stop(); super().hideEvent(event); self.agent._configure_workflows()

    def choose_root(self):
        if self.agent.busy or self.pending_run:
            self.message.setText('请等当前任务结束再切换记录目录。'); return
        root = QFileDialog.getExistingDirectory(self, '选择 agent-experience 目录', self.root.text())
        if root: self.root.setText(root); self.case = None; self.load()

    def load(self):
        try:
            self.entries = available_workflows(self.root.text())
            self.flows.blockSignals(True); self.flows.clear()
            self.flows.addItems([e['name'] for e in self.entries]); self.flows.blockSignals(False)
            self.select_flow(self.flows.currentIndex())
            self.load_cases()
            self.message.setText('没有流程草稿。请先完成一个任务并提取，或选择已有 agent-experience 目录。' if not self.entries else '')
        except Exception as exc:
            self.message.setText('读取流程失败：' + type(exc).__name__)

    def select_flow(self, index):
        while self.parameter_form.rowCount(): self.parameter_form.removeRow(0)
        self.parameters = {}
        if not 0 <= index < len(self.entries):
            self.goal.clear(); self.preview.clear(); return
        flow = self.entries[index]['flow']
        self.goal.setPlainText(flow['goal'])
        for parameter in flow['parameters']:
            field = QLineEdit(); field.setObjectName('trialParam_' + parameter['name'])
            suffix='' if required(parameter) else '（可选，可留空）'
            field.setPlaceholderText(parameter['description']+suffix)
            self.parameters[parameter['name']] = field
            self.parameter_form.addRow(parameter['name'] + suffix + ' · ' + parameter['description'], field)
        self.update_preview()

    def update_preview(self):
        index = self.flows.currentIndex()
        if not 0 <= index < len(self.entries): self.preview.clear(); return
        flow = self.entries[index]['flow']
        text = workflow_steps(flow)
        self.preview.setPlainText(text)

    def prepare(self):
        if self.agent.busy or self.pending_run:
            self.message.setText('请等当前任务结束再更换测试任务。'); return
        try:
            index = self.flows.currentIndex()
            if index < 0: raise ValueError('请先选择流程。')
            self.case = make_case(self.entries[index], self.goal.toPlainText(),
                {k:v.text() for k,v in self.parameters.items()}, self.initial.toPlainText())
            TrialStore(self.root.text()).case(self.case)
            self.load_cases(); self.show_case()
            self.reset.setChecked(False); self.tabs.setCurrentIndex(1); self.message.clear(); self.poll()
        except (ValueError, OSError) as exc: self.message.setText(str(exc))

    def update_buttons(self):
        extracting = any(j['status']=='running' for j in self.agent._workflow_status.get('jobs', []))
        enabled = bool(self.case and self.case.get('schema_version')==5 and self.reset.isChecked() and self.agent.connectionReady
                       and not self.agent.busy and not self.agent.workspace.state.get('taskQueue') and not self.pending_run
                       and not extracting and not getattr(self.agent.owner, '_pending_text_requests', {}))
        for mode, button in self.run_buttons.items():
            button.setEnabled(enabled and (mode!='natural_language' or bool(self.case.get('natural_language_workflow'))))
        self.connect_button.setEnabled(not self.agent.connectionReady and not self.agent.busy)

    def load_cases(self):
        self.saved_cases = TrialStore(self.root.text()).cases()
        self.history.clear()
        self.history.addItems([v['goal'][:90] for v in self.saved_cases])
        if self.case:
            for i, value in enumerate(self.saved_cases):
                if value['id'] == self.case['id']: self.history.setCurrentIndex(i)

    def show_case(self):
        self.case_label.setPlainText('固定任务：' + self.case['goal'] + '\n参数：' +
            '；'.join(f'{k}={v}' for k,v in self.case['parameters'].items()) + '\n起始状态：' + self.case['initial_state'])
        if self.case.get('schema_version') != 5:
            self.message.setText('旧测试仅供回查；请在“任务与流程”重新准备两组测试。')
        self.agent._configure_workflows()

    def select_case(self, index):
        if self.agent.busy or self.pending_run or not 0 <= index < len(self.saved_cases): return
        self.case = self.saved_cases[index]
        self.show_case(); self.reset.setChecked(False); self.poll()

    def run(self, mode):
        if mode not in self.run_buttons: return
        self.update_buttons()
        if not self.run_buttons[mode].isEnabled(): return
        self.pending_run = True
        self.last_trial_id = (self.agent.workspace.state.get('workflowTrial') or {}).get('id')
        sent = self.agent._send(dict(type='workflow_trial', case=self.case, mode=mode,
            initialStateConfirmed=True, recordsRoot=self.root.text(), source='workflow_trial'))
        if not sent: self.pending_run = False
        self.reset.setChecked(False)
        self.message.setText('执行已提交；进度和权限请求继续在 Agent 窗口显示。')
        self.agent._configure_workflows(); self.update_buttons()

    def poll(self):
        state = self.agent.workspace.state
        trial = state.get('workflowTrial') or {}
        if trial.get('id') != self.last_trial_id or state.get('workflowTrialError') or not self.agent.connectionReady:
            self.pending_run = False
        if state.get('workflowTrialError'): self.message.setText(state['workflowTrialError'])
        elif self.agent.busy: self.message.setText(self.agent.status + ' · ' + self.agent.feedback[:180])
        if self.case:
            try:
                self.rows = TrialStore(self.root.text()).runs(self.case['id'])
                selected = self.table.currentRow()
                self.table.setRowCount(len(self.rows))
                for i, row in enumerate(self.rows):
                    status = {'running':'执行中 / 未收到结束','completed':'正常结束','failed':'失败',
                              'interrupted':'已取消','unfinished':'中断','start_failed':'启动失败'}.get(row['status'], row['status'])
                    values = [HISTORY_MODES.get(row['mode'], row['mode']), status,
                        f"{row['elapsed_seconds']:.1f} 秒" if row.get('elapsed_seconds') is not None else '—',
                        str(row['tool_calls']), str(row['human_requests']),
                        {'unknown':'待确认','success':'已完成','failure':'未完成'}[row['goal_verification']]]
                    for col, value in enumerate(values): self.table.setItem(i, col, QTableWidgetItem(value))
                if 0 <= selected < len(self.rows): self.table.selectRow(selected)
                self.summary.setPlainText(comparison(self.rows) if self.case.get('schema_version')==5 else '历史测试，仅供回查，不计入当前两组对比。')
            except (OSError, ValueError): self.message.setText('测试记录暂时不可读。')
        self.update_buttons()

    def verify(self, success):
        if not self.case or self.case.get('schema_version') != 5:
            self.message.setText('历史记录只供回查。'); return
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows): self.message.setText('先选择一条运行记录。'); return
        try:
            TrialStore(self.root.text()).verify(self.rows[index]['id'], success)
            self.poll(); self.message.clear()
        except (ValueError, OSError) as exc: self.message.setText(str(exc))

    def open_folder(self):
        from proximic_ring.agent_workflow.execution.workflow_trials import export_trials
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(export_trials(self.root.text(), output_name='comparison.html'))))
