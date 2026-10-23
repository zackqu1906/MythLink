"""Recent-conversation picker; switching never transfers a draft or a live turn."""
from PySide6.QtCore import QObject, Property, Signal, Slot, Qt


class AgentConversations(QObject):
    changed = Signal()
    _loaded = Signal(int, object, str)

    def __init__(self, agent):
        super().__init__(agent)
        self.agent = agent
        self._opened = self._loading = False
        self._rows, self._error = [], ''
        self._generation = 0
        self._target = None
        self._loaded.connect(self._accept, Qt.QueuedConnection)

    @Property(bool, notify=changed)
    def opened(self): return self._opened
    @Property(bool, notify=changed)
    def loading(self): return self._loading
    @Property(bool, notify=changed)
    def connecting(self): return self._target is not None
    @Property('QVariantList', notify=changed)
    def rows(self): return self._rows
    @Property(str, notify=changed)
    def error(self): return self._error

    @Slot()
    def open(self):
        if not self.agent.canAddTask: return
        self._opened = True
        self.agent.focusCard('task')
        self.refresh()

    @Slot()
    def close(self):
        if self.connecting or not self._opened: return
        self._opened = False
        self.changed.emit()

    @Slot()
    def refresh(self):
        if self._loading or self.connecting or self.agent._closed: return
        self._loading, self._error = True, ''
        self._generation += 1
        generation = self._generation
        self.changed.emit()
        def read():
            from .agent_controller import find_codex
            from ..agent.recent_threads import list_recent_threads
            try:
                codex = find_codex()
                if not codex: raise RuntimeError('请先安装并登录 Codex')
                rows = list_recent_threads(self.agent.runtime_directory()/'catalog', codex)
                self._loaded.emit(generation, rows, '')
            except Exception:
                self._loaded.emit(generation, [], '无法读取最近对话，请确认 Codex 已打开后重试。')
        self.agent._records_worker.submit(read)

    @Slot(int, object, str)
    def _accept(self, generation, rows, error):
        if self.agent._closed or generation != self._generation: return
        self._loading, self._rows, self._error = False, rows, error
        self.changed.emit()

    @Slot(str)
    def select(self, thread_id):
        agent, sessions = self.agent, self.agent.sessions
        row = next((row for row in self._rows if row['id'] == thread_id), None)
        if not row or not agent.canAddTask: return
        if (agent.workspace.state.get('codexThreadId') == thread_id and agent.connectionReady
                and agent.workspace.state.get('codexSyncState') == 'ready'):
            self.close()
            return
        target = next((s for s in sessions.members if not s._closed
                       and s.workspace.state.get('codexThreadId') == thread_id
                       and s.connectionReady and s.workspace.state.get('codexSyncState') == 'ready'), None)
        if target:
            self._opened = False
            sessions.replace(agent, target)
            self.changed.emit()
            return
        target = sessions.create(agent, activate=False)
        if target is None: return
        self._target, self._error = target, ''
        target.changed.connect(self._target_changed)
        self.changed.emit()
        sessions.notify_focus()
        target._send({'type': 'attach_thread', 'threadId': thread_id})

    @Slot()
    def _target_changed(self):
        target = self._target
        if target is None: return
        state = target.workspace.state
        ready = state.get('threadAttachState') == 'ready'
        failed = state.get('threadAttachState') == 'error' or target._connection_state in ('error', 'missing', 'disconnected')
        if not ready and not failed: return
        target.changed.disconnect(self._target_changed)
        self._target = None
        agent = self.agent
        if not agent._closed:
            if ready and agent.visible and self._opened and agent.sessions.replace(agent, target):
                self._opened = False
            else:
                self._error = ('请结束当前输入后重新选择对话。' if ready else
                               state.get('threadAttachError') or target.connectionMessage or '连接失败，请重试。')
            self.changed.emit()
            agent.sessions.notify_focus()
