"""Desktop Agent input flow backed by Codex's native executor."""
import plistlib
import time
from pathlib import Path

from .connection import ConnectionIssue
from .flow import Flow
from .native_codex import NativeCodex, NATIVE_BUILDS
from .native_approvals import MODES
from proximic_ring.agent_workflow.recording.recording import ExperienceRecorder


class NativeFlow(Flow):
    native_execution = True
    rpc_class = NativeCodex
    execution_owner = 'codex_desktop'

    def setup(self):
        build = None
        for parent in Path(self.codex_path or '').parents:
            if parent.name in ('Codex.app', 'ChatGPT.app'):
                with (parent/'Contents/Info.plist').open('rb') as stream:
                    build = str(plistlib.load(stream).get('CFBundleVersion'))
                break
        if build not in NATIVE_BUILDS:
            raise ConnectionIssue('error', '此 Codex 版本尚未验证原生任务接口，请使用已适配版本；没有提交任务。')
        self.recorder = ExperienceRecorder(self.recording_dir, metadata={
            'execution_owner':self.execution_owner, 'codex_build':build,
            'runtime_directory':str(self.root), 'initial_context':{'availability':'not_captured'}})
        self.rpc = self.rpc_class(runtime_dir=self.root, codex_path=self.codex_path,
                               observer=self._record_rpc, on_state=self._native_state)
        self.rpc.initialize()
        self._computer_use_ready = None
        self.event('agent_ready', executionOwner=self.execution_owner)
        self.publish(status='等待文字指令', connectionState='ready', nativeExecution=True,
                     autoApprove=False, approvalMode='codex', codexSyncState='ready',
                     connectionMessage=self.check_computer_use(),
                     detail='语音、截图和文字发送到 Codex；授权与追问在 Codex 中处理')

    def _native_state(self, **fields):
        if fields.get('codexThreadId'): self.tid = fields['codexThreadId']
        if fields.get('threadAttachState') == 'ready': self.turn = fields.get('turnId') or None
        self.publish(**fields)

    def command(self, data):
        if data.get('type') == 'set_approval_mode':
            self.set_approval_mode(data.get('mode'))
            return
        if data.get('type') == 'attach_thread':
            try:
                self.rpc.attach(data.get('threadId'))
            except Exception as exc:
                self.publish(threadAttachState='error', threadAttachError=str(exc), status='连接失败')
                self.stop.set()
            return
        if self.state.get('threadAttachState') in ('connecting', 'error') and data.get('type') in ('text', 'steer', 'cancel'):
            self.publish(detail='请等待对话连接完成后再操作')
            return
        super().command(data)

    def poll_transport(self):
        self.rpc.poll()

    def poll_pending_actions(self):
        # Process queued stop/mode/steer commands before granting anything.
        if (not self.stop.is_set() and not self.cancelled
                and not self.steer_requests and self.inputs.empty()):
            self.rpc.approvals.process()

    def check_computer_use(self):
        return '已连接 Codex 原生执行；工具、授权和追问由 Codex 管理。'

    def release_computer_control(self, turn_id):
        # Never reset a native executor's CUA resources from the input client.
        self.publish(computerControlState='idle')

    def set_auto_approve(self, enabled):
        if isinstance(enabled, bool): self.set_approval_mode('auto' if enabled else 'codex')

    def set_approval_mode(self, mode):
        if mode not in MODES: return
        self.rpc.approvals.mode = mode
        self.auto_approve = mode != 'codex'
        self.event('auto_approval_mode', mode=mode, executionOwner=self.execution_owner)
        self.publish(autoApprove=self.auto_approve, approvalMode=mode, detail={
            'codex': '授权由你在 Codex 中确认',
            'auto': '自动允许应用使用权限；其他授权和追问在 Codex 处理',
            'all': '自动处理应用、命令、文件和网络授权；追问及登录验证仍由你处理',
        }[mode])

    def control(self, action, source):
        if action in ('approve', 'decline'):
            self.publish(detail='请在 Codex 中处理授权或追问')
            return
        super().control(action, source)

    def answer_text(self, *args, **kwargs):
        self.publish(detail='请在 Codex 中回答当前追问')

    def send_interrupt(self):
        if self.turn and self.interrupt_sent_at is None:
            self._native_interrupt_id = self.rpc.send('turn/interrupt',
                {'threadId':self.tid, 'turnId':self.turn})
            self.interrupt_sent_at = time.monotonic()
            self.event('interrupt_sent', turnId=self.turn)

    def handle(self, event):
        if (event.get('id') is not None and event.get('id') == getattr(self, '_native_interrupt_id', None)
                and event.get('error')):
            self.cancelled = False
            self.cancel_at = self.interrupt_sent_at = None
            self.publish(status='取消未确认', detail=event['error']['message']+'；请在 Codex 中查看')
        if (event.get('method') == 'turn/completed' and
                event.get('params', {}).get('turn', {}).get('status') == 'completed'):
            # A stop request losing the race with completion is not an interruption.
            self.cancelled = False
        super().handle(event)
