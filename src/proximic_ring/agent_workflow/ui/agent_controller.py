"""Main-app Agent transport and gesture ownership; never opens a second BLE session."""
from __future__ import annotations

import os
import shutil
import threading
import time
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Property, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QImageReader

from proximic_ring.agent_workflow.agent.workspace import Workspace
from proximic_ring.agent_workflow.agent.connection import ConnectionIssue
from proximic_ring.agent_workflow.agent.attachments import local_attachment
from proximic_ring.platforms.macos.native_access import NativeAccessChannel
from proximic_ring.runtime_paths import cache_root
from proximic_ring.agent_workflow.ui.editor_gestures import EditorGestureActions
from proximic_ring.agent_workflow import pipeline_policy


def find_codex():
    # New desktop releases embed the CLI in its own signed application bundle.
    candidates = [str(root / app / 'Contents/Resources' / relative)
                  for root in (Path('/Applications'), Path.home() / 'Applications')
                  for app in ('Codex.app', 'ChatGPT.app')
                  for relative in ('codex-cli/CodexCLI.app/Contents/MacOS/codex',
                                   'codex-cli/bin/codex', 'codex')]
    candidates.append(shutil.which('codex'))
    return next((path for path in candidates if path and os.access(path, os.X_OK)), None)


def find_codex_app():
    cli = find_codex()
    if cli:
        for parent in Path(cli).parents:
            if parent.name in ('Codex.app', 'ChatGPT.app') and parent.is_dir():
                return parent
    return next((root / app for root in (Path('/Applications'), Path.home() / 'Applications')
                 for app in ('Codex.app', 'ChatGPT.app') if (root / app).is_dir()), None)


class AgentController(QObject, EditorGestureActions):
    changed = Signal()
    dismissAvailabilityChanged = Signal()
    cardsChanged = Signal()
    focusRequested = Signal(str)
    toggleRequested = Signal()
    localFocusRequested = Signal(object)
    _stateReceived = Signal(object)
    _failed = Signal(str)
    _connectionFailed = Signal(str, str)
    _connectionEnded = Signal()
    _gestureReceived = Signal(str, str, object, float)
    _inputGestureReceived = Signal(str, str, object, float, object)
    _focusResult = Signal(int, str, object)
    _recordsReady = Signal(str, str)
    _workflowStatusReceived = Signal(object)
    _workflowAutoRequested = Signal(bool)

    def __init__(self, owner, *, flow_factory=None, focus_channel=None, sessions=None, session_id=''):
        super().__init__(owner)
        from .agent_sessions import AgentSessions
        self.session_id = session_id
        self.sessions = sessions or AgentSessions(self)
        self.owner = owner
        self.workspace = Workspace()
        self._visible = self._focused = self._closed = self._starting = False
        self._auto = True
        self._approval_mode = 'auto'
        settings = getattr(owner, '_settings', None)
        self._auto_context_mode = str(settings.value('agent/autoContext','smart')) if settings else 'smart'
        if self._auto_context_mode not in ('smart','always','off'): self._auto_context_mode='smart'
        self._context_message = ''
        self._opened_codex_thread = None
        self._error = ''
        self._connection_state = 'idle'
        self._connection_message = '连接本机已登录的 Codex，按任务使用问答和可用工具。'
        self._codex_app = None
        self._flow = self._thread = None
        self._flow_factory = flow_factory
        self._lock = threading.Lock()
        self._commands = []
        self._record_context = {'availability':'not_captured'}
        self._invocation_context = None
        self._capture_pending = False
        self._invocation_record_error = ''
        self._submission_gesture = None
        self._exporting_records = False
        self._records_server = None
        self._records_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='AgentRecordsView')
        from .agent_conversations import AgentConversations
        self.conversations = AgentConversations(self)
        self.conversations.changed.connect(self.dismissAvailabilityChanged)
        self._workflow_service = None
        self._workflow_since = datetime.now(timezone.utc).isoformat()
        self._workflow_root = None
        self._workflow_status = {}
        self._workflow_trial_dialog = None
        from proximic_ring.agent_workflow.ui.recommendation_controller import RecommendationController
        self.recommendations = RecommendationController(self)
        self._workflow_auto = pipeline_policy.legacy_workflow_enabled() and (
            settings.value('agent/workflowAuto', True, type=bool) if settings else True)
        self._focus_epoch = 0
        self._local_return = None
        self._editor_key = ''
        self._composing = False
        self._picking_attachments = False
        self._stroke_drawing = False
        self._pending_input_action = None
        self._remembering = False
        self._focus_after_input = False
        self._focus_deadline = 0
        self._focus_channel = (sessions.root._focus_channel if sessions else focus_channel or NativeAccessChannel())
        self._focus_worker = (sessions.root._focus_worker if sessions else
                              ThreadPoolExecutor(max_workers=1, thread_name_prefix='AgentFocus'))
        self._stateReceived.connect(self._apply_state, Qt.QueuedConnection)
        self._failed.connect(self._apply_error, Qt.QueuedConnection)
        self._connectionFailed.connect(self._connection_failed, Qt.QueuedConnection)
        self._connectionEnded.connect(self._connection_ended, Qt.QueuedConnection)
        self._gestureReceived.connect(self._gesture, Qt.QueuedConnection)
        self._inputGestureReceived.connect(self._input_gesture, Qt.QueuedConnection)
        self._focusResult.connect(self._focus_result, Qt.QueuedConnection)
        self._recordsReady.connect(self._records_ready, Qt.QueuedConnection)
        self._workflowStatusReceived.connect(self._workflow_status_received, Qt.QueuedConnection)
        self._workflowAutoRequested.connect(self._set_workflow_auto, Qt.QueuedConnection)
        self._workflow_timer = QTimer(self)
        self._workflow_timer.setInterval(3000)
        self._workflow_timer.timeout.connect(self._configure_workflows)
        if pipeline_policy.legacy_workflow_enabled():
            self._workflow_timer.start()
        self.toggleRequested.connect(self.toggleFocus)
        self._memory_timer = QTimer(self)
        self._memory_timer.setInterval(750)
        self._memory_timer.timeout.connect(self._remember_application)
        if sessions is None: self._memory_timer.start()
        self._input_timer = QTimer(self)
        self._input_timer.setInterval(100)
        self._input_timer.timeout.connect(self._finish_focus_input)
        self._send_timer = QTimer(self)
        self._send_timer.setSingleShot(True)
        self._send_timer.setInterval(12000)
        self._send_timer.timeout.connect(self._input_action_timeout)
        owner._inline_input.settled.connect(self._input_settled)
        owner._inline_input.actionRequested.connect(self._cancel_input_action)
        owner._inline_input.changed.connect(self._input_state_changed)
        owner._touchpad.changed.connect(self._schedule_input_reconcile)
        owner._touchpad.strokeTraceChanged.connect(self._stroke_trace_changed)
        self.changed.connect(self.dismissAvailabilityChanged)
        owner._inline_input.changed.connect(self.dismissAvailabilityChanged)
        owner._touchpad.changed.connect(self.dismissAvailabilityChanged)
        from proximic_ring.agent_workflow.capture.capture_controller import CaptureController
        self.capture = CaptureController(self)
        from proximic_ring.agent_workflow.capture.auto_context_controller import AutoContextController
        self.auto_context = AutoContextController(self)
        from proximic_ring.agent_workflow.ui.agent_setup import AgentSetup
        self._setup = AgentSetup(self)
        from .history_recommendation_controller import HistoryRecommendationController
        self.history_recommendations = HistoryRecommendationController(self)

    @Property(QObject, constant=True)
    def historyRecommendations(self): return self.history_recommendations

    @Property(QObject, constant=True)
    def conversationPicker(self): return self.conversations

    @Property(str, notify=changed)
    def conversationTitle(self): return self.workspace.state.get('codexThreadTitle', '')

    @Property(QObject,constant=True)
    def setup(self):return self._setup

    @Property(str,notify=changed)
    def computerUseMessage(self):
        return self.workspace.state.get('computerUseMessage') or '连接 Codex 后检查电脑操作能力；问答、写作等任务不依赖此能力。'

    @Property(bool, notify=changed)
    def visible(self): return self._visible
    @Property(bool, notify=changed)
    def focused(self): return self._focused
    @Property(bool, notify=changed)
    def workspaceActive(self): return self.sessions.focused
    @Property(int, notify=changed)
    def workspaceCount(self): return sum(s.visible for s in self.sessions.members)
    @Property(int, notify=changed)
    def workspacePosition(self):
        visible = [s for s in self.sessions.members if s.visible]
        return visible.index(self) + 1 if self in visible else 0

    @Slot()
    def nextTask(self): self.sessions.next_task(self)
    @Property(str, notify=changed)
    def focusKey(self): return self.workspace.focus
    @Property(str, notify=changed)
    def inputKey(self): return self.workspace.input_key
    @Property(bool, notify=changed)
    def autoApprove(self): return self._auto

    @Property(str, notify=changed)
    def approvalMode(self): return self._approval_mode

    @Property(bool, notify=changed)
    def nativeExecution(self): return self.workspace.state.get('nativeExecution', True)
    @Property(bool, notify=changed)
    def connectionReady(self): return self._connection_state == 'ready'
    @Property(bool, notify=changed)
    def connectionChecking(self): return self._connection_state == 'checking'
    @Property(bool, notify=changed)
    def connectionMissing(self): return self._connection_state == 'missing'
    @Property(str, notify=changed)
    def connectionStatus(self):
        return {'idle':'未连接', 'checking':'检查中', 'ready':'已连接', 'missing':'未安装',
                'login':'待登录', 'tools':'待配置', 'error':'连接失败',
                'disconnected':'已断开'}.get(self._connection_state, '未连接')
    @Property(str, notify=changed)
    def connectionMessage(self): return self._connection_message
    @Property(bool, notify=changed)
    def connectionNeedsHelp(self):
        return self._connection_state in {'missing', 'login', 'tools', 'error', 'disconnected'}
    @Property(str, notify=changed)
    def connectionHelpLabel(self):
        return '打开 ' + self._codex_app.stem if self._codex_app else '安装 Codex'

    @Slot()
    def refreshInstallation(self):
        if hasattr(self,'_setup'):self._setup.refresh()
        self._codex_app = find_codex_app()
        if self._connection_state in {'idle', 'missing'}:
            installed = bool(find_codex())
            self._connection_state = 'idle' if installed else 'missing'
            self._connection_message = ('连接本机已登录的 Codex，按任务使用问答和可用工具。' if installed
                else '请先安装并登录 Codex 桌面应用，再返回连接。')
        self.changed.emit()

    @Slot()
    def connectCodex(self):
        if self._closed or self.connectionChecking or self.busy: return
        self.refreshInstallation()
        if self._connection_state == 'missing': return
        self._connection_state = 'checking'
        self._connection_message = '正在检查本地连接…'
        self._error = ''
        with self._lock:
            flow = self._flow
            if flow: flow.inputs.put(('command', {'type':'check_connection'}))
        if flow: self.changed.emit()
        else: self._start()

    @Slot()
    def openCodex(self):
        self._codex_app = find_codex_app()
        url = (QUrl.fromLocalFile(str(self._codex_app)) if self._codex_app
               else QUrl('https://developers.openai.com/codex/app'))
        if not QDesktopServices.openUrl(url):
            self._connection_message = '无法自动打开，请手动打开或安装 Codex，再返回连接。'
        self.changed.emit()

    @Property(str, notify=changed)
    def codexSyncMessage(self):
        return self.workspace.state.get('codexSyncMessage','连接后可在 Codex 中查看')

    @Property(bool, notify=changed)
    def codexSyncUnavailable(self):
        return self.workspace.state.get('codexSyncState') in ('unsupported','error')

    @Property(bool, notify=changed)
    def hasCodexThread(self): return bool(self.workspace.state.get('codexThreadId'))

    @Slot()
    def openCodexTask(self):
        from proximic_ring.agent_workflow.recording.codex_link import codex_thread_url
        url = codex_thread_url(self.workspace.state.get('codexThreadId'))
        if url and not QDesktopServices.openUrl(QUrl(url)):
            self._apply_error('无法打开 Codex 对话，请确认 Codex 正在运行。')

    @Property(str, notify=changed)
    def autoContextMode(self): return self._auto_context_mode

    @Property(str, notify=changed)
    def contextMessage(self): return self._context_message

    @Property(bool, notify=changed)
    def contextCapturing(self):
        pending=self.auto_context.pending
        return pending is not None and pending['command'] is not None

    @Slot()
    def cycleAutoContext(self):
        if self.contextCapturing: return
        modes = ('smart','always','off')
        self._auto_context_mode = modes[(modes.index(self._auto_context_mode)+1)%3]
        settings = getattr(self.owner,'_settings',None)
        if settings: settings.setValue('agent/autoContext',self._auto_context_mode)
        self.changed.emit()

    @Slot(str, str)
    def _connection_failed(self, state, message):
        if self._closed: return
        self.recommendations.connection_failed()
        self._connection_state, self._connection_message = state, message
        self._apply_error(message)

    @Slot()
    def _connection_ended(self):
        if self._closed: return
        self.recommendations.connection_failed()
        self._starting = False
        if self._connection_state in {'ready', 'checking'}:
            self._connection_state = 'disconnected'
            self._connection_message = '连接已断开，请重新连接。'
        self._emit()
    @Property(bool, notify=changed)
    def busy(self):
        return self._starting or bool(self.workspace.state.get('turnId')) or self.workspace.state.get('status') == '启动任务'
    @Property(bool, notify=changed)
    def taskCompleted(self):
        return (not self.busy and not self.workspace.requests
                and self.workspace.state.get('taskOutcome') == 'completed')
    @Property(bool, notify=dismissAvailabilityChanged)
    def canAddTask(self):
        return (self.sessions.can_switch(self)
                and not any(s.conversations.connecting for s in self.sessions.members))

    @Slot()
    def addTask(self):
        if self.canAddTask: self.sessions.create(self)
    @Property(bool, notify=dismissAvailabilityChanged)
    def canDismiss(self):
        return (not self.contextCapturing and not self.captureActive and not self._hold_editor_input()
                and not self.conversations.connecting)
    @Property(str, notify=changed)
    def status(self):
        if self._starting: return '正在连接 Agent…'
        return self.workspace.state.get('status') or self._error or 'Agent'
    @Property(str, notify=changed)
    def errorMessage(self): return self._error
    @Property(str, notify=changed)
    def recordingWarning(self):
        state = self.workspace.state
        if state.get('recordingError'): return '工作记录异常：' + state['recordingError']
        if state.get('recordingDroppedEvents'): return '工作记录不完整，部分事件未能保存。'
        return ''

    @Property(bool, notify=changed)
    def workflowPipelineEnabled(self): return pipeline_policy.legacy_workflow_enabled()

    @Property(bool, notify=changed)
    def workflowAuto(self): return self.workflowPipelineEnabled and self._workflow_auto

    @workflowAuto.setter
    def workflowAuto(self, enabled):
        if not self.workflowPipelineEnabled:
            self._workflow_auto = False
            self.changed.emit()
            return
        self._workflow_auto = bool(enabled)
        if getattr(self.owner, '_settings', None):
            self.owner._settings.setValue('agent/workflowAuto', self._workflow_auto)
        self._configure_workflows(); self.changed.emit()

    @Slot(bool)
    def _set_workflow_auto(self, enabled):
        if not self._closed: self.workflowAuto = enabled

    @Property(str, notify=changed)
    def workflowStatus(self):
        if not self.workflowPipelineEnabled: return pipeline_policy.DISABLED_MESSAGE
        if self._workflow_status.get('error'): return self._workflow_status['error']
        jobs = self._workflow_status.get('jobs', [])
        if any(j['status']=='running' for j in jobs): return '正在后台提取'
        if any(j['status']=='queued' for j in jobs): return '等待空闲后提取'
        if jobs and jobs[0]['status']=='failed': return '提取失败，查看流程记录'
        return '流程草稿已保存' if jobs else '尚无流程草稿'

    @Slot(object)
    def _workflow_status_received(self, status):
        if self._closed: return
        self._workflow_status = status
        if self._records_server:
            self._records_server.update(error=status.get('error', ''))
        self.changed.emit()

    @Slot()
    def _configure_workflows(self):
        if not self.workflowPipelineEnabled:
            self._workflow_timer.stop()
            if self._workflow_service:
                self._workflow_service.close()
                self._workflow_service = None
            return
        if self._closed or not hasattr(self.owner, '_llm_settings'): return
        if hasattr(self,'_setup'):self._setup.refresh()
        # This timer only copies config/flags to a queue. Disk scanning, process
        # startup and model requests all happen outside the Qt/Agent threads.
        root = self.workspace.state.get('recordsRoot') or self._workflow_root
        if not root:
            from proximic_ring.agent_workflow.recording.recording import records_root
            root = str(records_root())
        from dataclasses import asdict
        from proximic_ring.agent_workflow.ui.workflow_service import WorkflowService
        if self._workflow_service is not None and root != self._workflow_root:
            self._workflow_service.close()
            self._workflow_service = None
        if self._workflow_service is None:
            self._workflow_root = root
            self._workflow_service = WorkflowService(root,self._workflow_since,self._workflowStatusReceived.emit)
        text_busy = bool(getattr(self.owner,'_pending_text_requests',{}))
        experiment_open = bool(self._workflow_trial_dialog and self._workflow_trial_dialog.isVisible()
                               and self._workflow_trial_dialog.case)
        self._workflow_service.send(dict(type='configure',settings=asdict(self.owner._llm_settings()),
            automatic=self._workflow_auto,busy=self.busy or text_busy or experiment_open))
        self._last_workflow_busy = self.busy
        if self._records_server:
            self._records_server.update(automatic=self._workflow_auto,busy=self.busy or text_busy or experiment_open)

    @Slot()
    @Slot(str)
    def openRecommendations(self, trigger='manual'):
        if self.workflowPipelineEnabled:
            self.recommendations.open(trigger)
        else:
            self.history_recommendations.open(trigger)

    @Slot()
    def openWorkflowTrials(self):
        if self._closed: return
        if not self.workflowPipelineEnabled:
            self.owner._app_gestures.notify(pipeline_policy.DISABLED_MESSAGE)
            return
        from proximic_ring.agent_workflow.ui.workflow_trial_dialog import WorkflowTrialDialog
        from proximic_ring.agent_workflow.recording.recording import records_root
        if self._workflow_trial_dialog is None:
            root = self.workspace.state.get('recordsRoot') or self._workflow_root or records_root()
            self._workflow_trial_dialog = WorkflowTrialDialog(self, root)
        self._workflow_trial_dialog.show()
        self._workflow_trial_dialog.raise_()
        self._workflow_trial_dialog.activateWindow()

    @Slot()
    def openRecords(self):
        if self._closed or self._exporting_records: return
        self._exporting_records = True
        self._configure_workflows()
        flow = self._flow
        root = self.workspace.state.get('recordsRoot') or self._workflow_root
        automatic = self._workflow_auto
        busy = self.busy or bool(getattr(self.owner,'_pending_text_requests',{}))
        def export():
            store = None
            try:
                from proximic_ring.agent_workflow.recording.records import export_html
                from proximic_ring.agent_workflow.recording.recording import records_root
                from proximic_ring.agent_workflow.recording.records_server import RecordsServer
                from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
                from proximic_ring.agent_workflow.extraction.workflow_view import export_workflows
                if flow and flow.recorder: flow.recorder.flush(timeout=3)
                store = WorkflowStore(root or records_root())
                export_workflows(store)
                export_html(store.root)
                if self.workflowPipelineEnabled:
                    from proximic_ring.agent_workflow.operations.operation_service import update_catalog
                    update_catalog(store.root)
                if self._closed: return
                server = self._records_server
                if server is None or server.root != store.root.resolve():
                    # Socket startup stays outside the Agent's command lock.
                    created = RecordsServer(store.root,
                        on_automatic=self._workflowAutoRequested.emit,automatic=automatic,busy=busy,
                        on_context=lambda root=store.root:self.recommendations.capture_context('web',root))
                    with self._lock:
                        if self._closed:
                            created.close()
                            return
                        self._records_server = created
                    if server: server.close()
                    server = created
                if not self._closed: self._recordsReady.emit(server.overview_url,'')
            except Exception as exc:
                if not self._closed: self._recordsReady.emit('', '无法打开工作记录：' + type(exc).__name__)
            finally:
                if store: store.close()
        self._records_worker.submit(export)

    def _capture_invocation(self, focus=None, *, trigger, pending=False, identifier=None):
        from proximic_ring.agent_workflow.recommendation.recommendation_context import invocation_context, persist_invocation
        from proximic_ring.agent_workflow.recording.recording import records_root
        from copy import deepcopy
        context = invocation_context(focus, trigger=trigger, identifier=identifier,
                                     status='pending' if pending else None)
        self._invocation_context = context
        self._capture_pending = pending
        if pending: return
        # Only freeze metadata on Qt's thread. Persistence shares the records
        # worker, never holds up focus transfer, Agent dispatch or text editing.
        root = self.workspace.state.get('recordsRoot') or self._workflow_root or records_root()
        snapshot = deepcopy(context)
        def save():
            try:
                persist_invocation(root, snapshot)
                self._invocation_record_error = ''
            except Exception as exc:
                self._invocation_record_error = '唤起场景保存失败：' + type(exc).__name__
        if not self._closed: self._records_worker.submit(save)

    def _recommendation_context(self):
        from copy import deepcopy
        context = deepcopy(self._invocation_context or {'availability':'invocation_not_captured'})
        if self._invocation_record_error:
            context['recording_error'] = self._invocation_record_error
        return context

    @Slot(str, str)
    def _records_ready(self, path, error):
        self._exporting_records = False
        if self._closed: return
        if error: self._apply_error(error)
        elif not QDesktopServices.openUrl(QUrl(path)):
            self._apply_error('无法自动打开工作记录：' + path)
    @Property(str, notify=changed)
    def feedback(self):
        return self.workspace.state.get('agent') or self.workspace.state.get('detail') or ''
    @Property(str, notify=changed)
    def taskText(self): return self.workspace.state.get('transcript', '')
    @Property('QVariantList', notify=cardsChanged)
    def cards(self):
        return [dict(key='task',kind='task',title='Agent',message='',options=[],
                     request=self.workspace.input_request,
                     questionCount=sum(r['kind']=='question' for r in self.workspace.requests))] + [
            {**r,'key':r['token']} for r in self.workspace.requests if r['kind']!='question']

    @Slot(str, result=str)
    def draft(self, key): return self.workspace.drafts.get(key, '')

    @Property('QVariantList', notify=changed)
    def inputAttachments(self):
        return self.workspace.attachments.get(self.inputKey, [])

    @Property(bool, notify=changed)
    def pickingAttachments(self): return self._picking_attachments

    @Property(bool, notify=changed)
    def captureActive(self): return self.capture.active

    @Slot(str)
    def startCapture(self, key):
        if not self.contextCapturing:self.capture.start(key)

    @Slot(str)
    def chooseAttachments(self, key):
        if key != 'task' or key != self.inputKey or self._picking_attachments or self.contextCapturing: return
        from PySide6.QtWidgets import QFileDialog
        target = self.workspace.state.get('turnId', '')
        self._picking_attachments = True
        self.changed.emit()
        try:
            paths, _ = QFileDialog.getOpenFileNames(None, '添加图片或文件', '',
                '所有文件 (*);;图片 (*.png *.jpg *.jpeg *.webp *.gif)')
            self._add_attachments(key, paths, target_turn=target)
        finally:
            self._picking_attachments = False
            self._emit()
            self.focusRequested.emit('task')
            QTimer.singleShot(0, self._reconcile_input)

    def _add_attachments(self, key, paths, *, target_turn=None):
        if key != 'task' or key != self.inputKey: return False
        attachments, errors = [], []
        for path in paths:
            try:
                item = local_attachment(path)
                if item['kind'] == 'image' and not QImageReader(item['path']).canRead():
                    raise ValueError(f'无法读取图片：{item["name"]}。请检查图片格式。')
                attachments.append(item)
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
        self.workspace.attach(key, attachments, target_turn)
        self._error = '\n'.join(errors)
        self._emit()
        return bool(attachments)

    @Slot(str, 'QVariantList', result=bool)
    def addAttachmentUrls(self, key, urls):
        paths = []
        for value in urls:
            url = QUrl(value)
            if not url.isLocalFile():
                self._apply_error('请添加本地图片或文件。')
                return False
            paths.append(url.toLocalFile())
        return self._add_attachments(key, paths)

    @Slot(str, str)
    def removeAttachment(self, key, path):
        self.workspace.remove_attachment(key, path)
        self._error = ''
        self._emit()

    @Slot(str, result=bool)
    def pasteAttachments(self, key):
        if key != 'task' or key != self.inputKey: return False
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None: return False
        if mime.hasUrls():
            # Leave ordinary web links to the editor's normal text paste.
            if not all(url.isLocalFile() for url in mime.urls()): return False
            self.addAttachmentUrls(key, mime.urls())
            return True
        if not mime.hasImage(): return False
        image = QImage(QGuiApplication.clipboard().image())
        try:
            directory = cache_root()/'desktop-agent'/'attachments'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory/f'截图-{uuid4().hex}.png'
            if image.isNull() or not image.save(str(path), 'PNG'):
                raise OSError('无法保存剪贴板图片')
            self._add_attachments(key, [path])
        except OSError:
            self._apply_error('无法添加剪贴板图片，请重试或选择图片文件。')
        return True
    @Slot(str, result=int)
    def selected(self, key): return self.workspace.selections.get(key, -1)
    @Slot(str, str)
    def edit(self, key, text):
        self.workspace.edit(key, text)
        if not text and key == self.inputKey: QTimer.singleShot(0, self._reconcile_input)

    @Slot(str, bool)
    def editorComposing(self, key, composing):
        if key == self.inputKey:
            self._composing = composing
            if not composing: QTimer.singleShot(0, self._reconcile_input)

    def _schedule_input_reconcile(self):
        QTimer.singleShot(0, self._reconcile_input)

    def _stroke_trace_changed(self, points, finished):
        self._stroke_drawing = bool(points) and not finished
        if not self._stroke_drawing: self._schedule_input_reconcile()

    def _reconcile_input(self):
        if self._closed: return
        previous = self.inputKey
        self.workspace.reconcile_input(hold_input=self._hold_editor_input())
        if previous != self.inputKey:
            self._input_replaced(previous)
            self._emit()

    def _hold_editor_input(self):
        inline = self.owner._inline_input
        touchpad = getattr(self.owner, '_touchpad', None)
        stroke = getattr(touchpad, 'strokeSession', None)
        return bool(self._picking_attachments or self._input_busy() or self._composing or inline._view.get('has_composition')
                    or inline._action_pending or inline._reply_stage == 'final'
                    or self._pending_input_action is not None
                    or (stroke and stroke.engaged and (stroke.phase != 'running'
                        or self._stroke_drawing or touchpad.strokeCode or touchpad._pending_stroke_commit)))

    def _input_replaced(self, previous):
        self._retire_editor_input(previous)
        self._cancel_input_action()
        self._error = ''

    def _retire_editor_input(self, key, *, sent=False):
        # The task and each question share a TextArea, not an input session.
        # Never invalidate dictation belonging to a foreground external app.
        if (self._focused and self._editor_key == key
                and not self._input_busy() and not self._composing):
            inline = self.owner._inline_input
            front = inline._foreground_identity()
            if front != (str(inline._view.get('application', '')), os.getpid()):
                return
            self._cancel_input_action()
            touchpad = getattr(self.owner, '_touchpad', None)
            if touchpad and touchpad.strokeSession.engaged:
                touchpad.strokeSession.cancel('Agent 输入框已切换')
            inline.release_completed_shortcut(reset_native=True, sent=sent)

    def _emit(self):
        # Only queue a flag/config copy here; the bridge/worker handles disk and
        # cancellation. Do not wait for the 3-second heartbeat when a turn starts.
        if self.busy != getattr(self, '_last_workflow_busy', False):
            self._last_workflow_busy = self.busy
            self._configure_workflows()
        self.changed.emit()
        self.cardsChanged.emit()

    @Slot(object)
    def _apply_state(self, state):
        if self._closed: return
        if state.get('connectionState'):
            self._connection_state = state['connectionState']
            self._connection_message = state.get('connectionMessage', '')
        previous = self.inputKey
        self.workspace.update(state, hold_input=self._hold_editor_input())
        if previous != self.inputKey:
            self._input_replaced(previous)
        if state.get('status') not in ('准备中',): self._starting = False
        self._auto = bool(state.get('autoApprove', self._auto))
        if state.get('approvalMode') in ('codex', 'auto', 'all'):
            self._approval_mode = state['approvalMode']
        opened = state.get('codexOpenThreadId')
        if opened and opened != self._opened_codex_thread:
            self._opened_codex_thread = opened
            self.openCodexTask()
        self._emit()

    @Slot(str)
    def _apply_error(self, error):
        if self._closed: return
        self._error = error
        self._starting = False
        self._emit()

    def _start(self):
        if self._thread and self._thread.is_alive(): return
        self._starting = True
        self._error = ''
        self._connection_state = 'checking'
        self._connection_message = '正在连接本地 Codex…'
        initial_auto = self._auto
        initial_approval_mode = self._approval_mode
        def run():
            try:
                from proximic_ring.agent_workflow.agent.background_flow import BackgroundFlow
                codex = find_codex()
                if not codex: raise ConnectionIssue('missing', '请先安装并登录 Codex 桌面应用，再返回连接。')
                runtime = self.runtime_directory()
                runtime.mkdir(parents=True, exist_ok=True)
                # Carry forward explicit app grants from the validated demo once.
                old = cache_root()/('desktop-agent/app-grants.json' if self.session_id else 'ring-agent-demo/app-grants.json')
                if not (runtime/'app-grants.json').exists() and old.exists():
                    shutil.copyfile(old, runtime/'app-grants.json')
                flow = (self._flow_factory or BackgroundFlow)(with_ring=False, runtime_dir=runtime,
                    codex_path=codex, on_state=self._stateReceived.emit)
                with self._lock:
                    self._flow = flow
                    if self._closed: flow.stop.set()
                    flow.inputs.put(('command',
                        {'type':'set_approval_mode','mode':initial_approval_mode}
                        if getattr(flow, 'native_execution', False) else
                        {'type':'set_auto_approve','enabled':initial_auto}))
                    for command in self._commands: flow.inputs.put(('command', command))
                    self._commands.clear()
                if not self._closed: flow.run()
                elif not flow.events.closed:
                    flow.close_recording();flow.events.close()
            except ConnectionIssue as exc:
                if not self._closed: self._connectionFailed.emit(exc.state, str(exc))
            except Exception:
                if not self._closed:
                    self._connectionFailed.emit('error', '连接失败，请打开本地 Codex 确认可用后重试。')
            finally:
                with self._lock: self._flow = None
                if not self._closed: self._connectionEnded.emit()
        self._thread = threading.Thread(target=run, name='MythlinkDesktopAgent', daemon=True)
        self._thread.start()
        self._emit()

    def runtime_directory(self):
        base = cache_root()/'desktop-agent'
        return base/'sessions'/self.session_id if self.session_id else base

    def _send(self, command):
        if self._closed or command is None: return False
        command = dict(command)
        command.setdefault('source', 'agent_editor')
        if command.get('type') in ('text','steer','answer') and 'context' not in command:
            if self._invocation_context is None:
                self._capture_invocation(self._record_context, trigger='submission_without_focus_capture')
            command['context'] = dict(self._recommendation_context(),
                submitted_at=datetime.now(timezone.utc).isoformat(),
                trigger=self._submission_gesture or 'agent_editor',
                input_origin='mixed_editor_unknown', agent_focused=self._focused)
        with self._lock:
            if self._flow:
                self._flow.inputs.put(('command', command))
                return True
            self._commands.append(command)
        self._start()
        return True

    @Slot(str)
    def submit(self, key):
        if key != self.inputKey or self._picking_attachments or self.contextCapturing or self._input_busy() or self._composing: return
        touchpad = getattr(self.owner, '_touchpad', None)
        if touchpad and touchpad.strokeSession.engaged and touchpad.strokeCode:
            self._input_notice('请先 tap 确认当前候选字，再上滑发送。')
            return
        try: command = self.workspace.submission(key)
        except ValueError as exc:
            if key == 'task': self.workspace.draft_turns[key] = ''
            self._input_notice(str(exc))
            return
        if command is None: return
        try:
            for item in command.get('attachments', []): local_attachment(item['path'])
        except ValueError as exc:
            self._apply_error(str(exc))
            return
        if key == 'task' and self._starting: return
        if touchpad and touchpad.strokeSession.engaged:
            touchpad.strokeSession.cancel('已提交 Agent 输入')
        self._error = ''
        if self.auto_context.start(key, command): return
        self._context_message = ''
        self._submit_command(key, command)

    def _submit_command(self, key, command):
        if self._send(command) is False: return
        self._retire_editor_input(key, sent=True)
        self.workspace.sent(key)
        self._emit()

    @Slot(str, int)
    def choose(self, key, index):
        self._send(self.workspace.confirm(key, index))

    @Slot(bool)
    def setAutoApprove(self, enabled):
        self._auto = bool(enabled)
        self._approval_mode = 'auto' if self._auto else 'codex'
        if self._flow: self._send({'type':'set_auto_approve','enabled':self._auto})
        self.changed.emit()

    @Slot()
    def cycleApprovalMode(self):
        if not self.nativeExecution:
            self.setAutoApprove(not self._auto)
            return
        modes = ('codex', 'auto', 'all')
        self._approval_mode = modes[(modes.index(self._approval_mode) + 1) % len(modes)]
        self._auto = self._approval_mode != 'codex'
        if self._flow:
            self._send({'type':'set_approval_mode', 'mode':self._approval_mode})
        self.changed.emit()

    @Slot()
    def cancel(self):
        if self.busy: self._send({'type':'cancel'})

    def _input_busy(self):
        inline = getattr(self.owner, '_inline_input', None)
        return bool(getattr(self.owner, '_utterance_active', False)
                    or (inline and (inline._view.get('phase') in {
                        'starting','listening','finishing','editing','polishing'}
                        or inline._view.get('awaiting_readback'))))

    @Slot()
    def show(self):
        if self is self.sessions.root and self.sessions.active is not self:
            self.sessions.active.show()
            return
        self._visible = True
        if not self._focused: self._request_focus('capture', trigger='agent_show')
        else: self.focusRequested.emit(self.workspace.focus)
        self._emit()

    @Slot()
    def dismiss(self):
        """Hide the companion, keeping its conversation and draft for next time."""
        if not self.canDismiss: return
        self._retire_editor_input(self.inputKey)
        self._focus_epoch += 1
        self._capture_pending = False
        self._focus_after_input = False
        self._input_timer.stop()
        self._cancel_input_action()
        self._editor_key = ''
        self.sessions.closing.emit(self)
        self._visible = self._focused = False
        self.sessions.dismissed(self)
        self._emit()

    @Slot()
    def toggleFocus(self):
        if self is self.sessions.root and self.sessions.active is not self:
            self.sessions.active.toggleFocus()
            return
        if self._closed: return
        if self._input_busy():
            self.owner._finish_utterance_event.set()
            self._focus_after_input = True
            self._focus_deadline = time.monotonic()+12
            self._input_timer.start()
            return
        self._visible = True
        if self._focused and self._local_return is not None:
            self.localFocusRequested.emit(self._local_return)
            return
        self._request_focus('restore' if self._focused else 'capture')
        self._emit()

    def _finish_focus_input(self):
        if self._closed or not self._focus_after_input:
            self._input_timer.stop(); return
        if not self._input_busy():
            self._focus_after_input = False
            self._input_timer.stop()
            self.toggleFocus()
        elif time.monotonic() >= self._focus_deadline:
            self._focus_after_input = False
            self._input_timer.stop()
            self._input_notice('输入尚未结束，已保留当前焦点和文字。')

    def rememberLocalWindow(self, window):
        self._local_return = window

    def _remember_application(self):
        if self is self.sessions.root and self.sessions.active is not self:
            self.sessions.active._remember_application()
            return
        if self._closed or not self._visible or self._focused or self._remembering: return
        self._remembering = True
        def work():
            try:
                result = self._focus_channel.call('agent_focus_capture',host_pid=os.getpid())
                if not self._closed: self._focusResult.emit(-1,'remember',result)
            except Exception: pass
            finally: self._remembering = False
        self._focus_worker.submit(work)

    def _request_focus(self, action, *, trigger='focus_toggle'):
        if action == 'capture':
            self._capture_invocation(trigger=trigger, pending=True)
        elif self._capture_pending:
            # A newer restore invalidates the outstanding capture epoch. Do not
            # leave future manual focus changes permanently marked as pending.
            self._capture_pending = False
        self._focus_epoch += 1
        epoch = self._focus_epoch
        def work():
            try:
                result = self._focus_channel.call('agent_focus_' + action, host_pid=os.getpid())
            except Exception as exc:
                result = {'status':'error', 'message':str(exc)}
            if not self._closed: self._focusResult.emit(epoch, action, result)
        self._focus_worker.submit(work)

    @Slot(int, str, object)
    def _focus_result(self, epoch, action, result):
        if (action == 'remember' or epoch == self._focus_epoch) and result.get('context'):
            self._record_context = dict(result['context'])
        if action == 'remember':
            if result.get('status') == 'remembered': self._local_return = None
            return
        if self._closed or epoch != self._focus_epoch: return
        if action == 'capture':
            pending = self._invocation_context or {}
            self._capture_invocation(result.get('context') or {'availability':result.get('status', 'unavailable')},
                trigger=pending.get('invocation_trigger', 'focus_toggle'), identifier=pending.get('invocation_id'))
            if result.get('status') == 'remembered': self._local_return = None
            self.sessions.activate(self)
            if result.get('status') == 'error': self._error = '窗口焦点记录失败：' + result.get('message', '')
            self._emit()
            self.focusRequested.emit(self.workspace.focus)
        elif result.get('status') == 'restored':
            self._focused = False
            self.sessions.notify_focus()
            self._emit()
        else:
            self._apply_error('上次窗口已关闭或无法切换；当前输入已保留。')

    @Slot(str, bool)
    def windowFocused(self, key, focused):
        if focused and key in self.workspace.keys:
            if not self._focused and not self._capture_pending:
                self._capture_invocation(self._record_context, trigger='window_focus')
            self.workspace.focus = key
            self.sessions.activate(self)
            self._emit()

    @Slot(bool)
    def workspaceFocused(self, focused):
        if self._focused != focused:
            if focused and not self._capture_pending:
                self._capture_invocation(self._record_context, trigger='workspace_focus')
            if focused: self.sessions.activate(self)
            else:
                self._focused = False
                self.sessions.notify_focus()
            if not focused:
                self._cancel_input_action()
            self._emit()

    @Slot(str)
    def focusCard(self, key):
        if key not in self.workspace.keys or self._input_busy(): return
        self.sessions.activate(self)
        self.workspace.focus = key
        self.focusRequested.emit(key)
        self.changed.emit()

    @Slot(str, bool)
    def editorFocused(self, key, focused):
        if focused:
            if self._editor_key != key: self._cancel_input_action()
            self._editor_key = key
            # Agent editors are real IME clients, never the touchpad trial box.
            touchpad = getattr(self.owner, '_touchpad', None)
            if touchpad:
                touchpad.setLocalStrokeFocus(False)
                touchpad.setLocalStrokeInput(False)
        elif self._editor_key == key:
            self._editor_key = ''
            self._cancel_input_action()


    def _input_context_valid(self, key, connection, context):
        sentence, bindings = context
        return (not self._closed and not self._picking_attachments and self._focused and self.workspace.focus == 'task'
                and key == self.inputKey == self._editor_key
                and (key == 'task' or self.workspace.card(key) is not None)
                and connection is self.owner._disconnect_event and not connection.is_set()
                and bindings is self.owner._gesture_bindings
                and sentence == self.owner._app_gestures.sentence()
                and not self.owner._ring_gestures.session_blocked())


    def _submit_from_gesture(self, key, name):
        self._submission_gesture = {'gesture':name,'at':datetime.now(timezone.utc).isoformat()}
        try: self.submit(key)
        finally: self._submission_gesture = None

    def owns_input_focus(self):
        if self is self.sessions.root and self.sessions.active is not self:
            return self.sessions.active.owns_input_focus()
        request = self.workspace.input_request or {}
        return bool(not self._closed and not self._picking_attachments and self._focused and self.workspace.focus == 'task'
                    and self._editor_key == self.inputKey
                    and not request.get('submitting') and not request.get('blocked'))

    def filter_gesture(self, event, connection):
        """Return True only when this workspace consumes the firmware event."""
        if self is self.sessions.root and self.sessions.active is not self:
            return self.sessions.active.filter_gesture(event, connection)
        if self.capture.consume(event, connection): return True
        if not self.workflowPipelineEnabled and self.history_recommendations.filter_gesture(event, connection):
            return True
        if self.recommendations.filter_gesture(event, connection): return True
        if self._picking_attachments: return False
        name = getattr(event, 'name', '')
        # Invocation goes through the configurable app/scene/global router.
        # Only gestures inside an already-open editor/panel are consumed here.
        if not self._focused: return False
        bindings = self.owner._gesture_bindings
        voice_gestures = {g for pair in bindings.as_dict().values() for g in pair if g}
        if self.workspace.focus != 'task':
            # Permission windows accept mouse clicks only, never ring decisions.
            return name in voice_gestures | {'tap','double-tap','swipe-up','swipe-down',
                                             'swipe-left','swipe-right','middle-pinch'}
        if name in voice_gestures | {'swipe-up','swipe-down'}:
            # Native stroke candidates keep their original gestures and palette.
            stroke = getattr(getattr(self.owner, '_touchpad', None), 'strokeSession', None)
            if stroke and stroke.engaged and name != 'middle-pinch':
                return False
            # Reading/selecting rich reply text can own keyboard focus. An
            # explicit input gesture returns to this task's existing composer
            # before the shared voice endpoint or queued send action runs.
            if not self.owns_input_focus():
                self.focusRequested.emit('task')
            if name in bindings.confirm + bindings.end:
                return False  # Shared audio endpoint owns start/end, including tap toggles.
            if name in {'swipe-up','swipe-down'} | set(bindings.undo + bindings.switch_mode):
                # Qt tool windows are AXSystemDialog. Use the already-bound
                # local editor and native sentence, not a generic AX dialog route.
                self._inputGestureReceived.emit(name,self.inputKey,connection,time.monotonic(),
                    (self.owner._app_gestures.sentence(),bindings))
                return True
            return False

    @Slot(str, str, object, float)
    def _gesture(self, name, key, connection, created):
        if (self._closed or connection is not self.owner._disconnect_event
                or connection.is_set() or time.monotonic()-created > 1): return
        if name == 'workflow-recommendations': self.openRecommendations('gesture'); return

    def close(self):
        if self._closed: return
        if self is self.sessions.root:
            for session in self.sessions.members[1:]: session.close()
        self._closed = True
        self._setup.close()
        self.capture.close()
        self.auto_context.close()
        self.recommendations.close()
        self.history_recommendations.close()
        self._memory_timer.stop()
        self._workflow_timer.stop()
        if self._workflow_service: self._workflow_service.close()
        if self._workflow_trial_dialog: self._workflow_trial_dialog.close()
        self._input_timer.stop()
        self._cancel_input_action()
        with self._lock:
            if self._records_server: self._records_server.close()
            self._commands.clear()
            if self._flow: self._flow.stop.set()
        if self is self.sessions.root: self._focus_worker.shutdown(wait=False, cancel_futures=True)
        self._records_worker.shutdown(wait=False, cancel_futures=True)
        if self is self.sessions.root: self._focus_channel.close()
        if self._thread: self._thread.join(timeout=6)
