"""Agent destination for the shared screenshot interaction."""
from uuid import uuid4

from PySide6.QtCore import QTimer

from proximic_ring.runtime_paths import cache_root
from proximic_ring.screenshots.controller import ScreenCaptureController


class CaptureController(ScreenCaptureController):
    confirm_label = '加入输入框 →'
    confirm_hint = '右滑加入输入框'

    def __init__(self, agent):
        self.agent = agent
        super().__init__(agent.owner, agent)

    def can_start(self, key):
        agent = self.agent
        if agent._closed or key != 'task' or key != agent.inputKey: return False
        if agent._hold_editor_input():
            self.report_error('请先完成当前语音输入，再截图。')
            return False
        return True

    def began(self, key):
        agent = self.agent
        self.target_turn = agent.workspace.draft_turns.get('task', agent.workspace.state.get('turnId', ''))
        agent._picking_attachments = True
        agent._error = ''

    def presented(self):
        self.agent.changed.emit()

    def save_crop(self, crop):
        directory = cache_root() / 'desktop-agent' / 'attachments'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'截图-{uuid4().hex}.png'
        try:
            if not crop.save(str(path), 'PNG'): raise OSError('无法保存截图')
            if not self.agent._add_attachments('task', [path], target_turn=self.target_turn):
                raise RuntimeError('任务输入位置已变化，请重试截图')
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def report_error(self, error):
        self.agent._error = str(error)
        self.agent.changed.emit()

    def restore(self):
        agent = self.agent
        agent._picking_attachments = False
        if not agent._closed:
            agent._emit()
            agent.focusRequested.emit('task')
            QTimer.singleShot(0, agent._reconcile_input)

    def close(self):
        super().close()
        self.agent._picking_attachments = False
