"""Mythlink input, permissions and questions over a shared background owner."""
from .native_flow import NativeFlow
from .background_codex import BackgroundCodex, cancel_native_submissions
from .background_service import shutdown_services
from .flow import Flow
from . import requests_ui as req


class BackgroundFlow(NativeFlow):
    rpc_class = BackgroundCodex
    execution_owner = 'mythlink_background'
    disconnect_message = '输入连接已关闭；任务仍在后台执行，可通过链接按钮接回'

    def setup(self):
        super().setup()
        self.publish(detail='后台执行；授权和追问可以在浮窗中处理',
                     codexSyncMessage='新建和连接对话不会打开 Codex 界面')

    def check_computer_use(self):
        return '使用已安装的 Codex 执行任务；应用和浏览器操作使用 Computer Use。'

    def control(self, action, source):
        return Flow.control(self, action, source)

    def answer_text(self, *args, **kwargs):
        return Flow.answer_text(self, *args, **kwargs)

    def choose(self, token, index, source):
        r=self.current_request(token)
        if not r or self.cancelled or r['epoch']!=self.epoch:return
        if not isinstance(index,int) or not 0<=index<len(r['options']):return
        option=r['options'][index]
        if not option['enabled']:
            self.publish(detail=option['reason']);return
        if option['action']=='answer':self.answer(r,option['result']);return
        # Preserve the actual server-offered scope. The execution owner persists
        # app grants, so a card closing/reconnecting cannot revoke or widen them.
        self.reply(r['id'],option['result'],option['label'])
        self.publish(detail='选择已发送，正在等待执行结果')

    def reply(self, rid, result, disposition, *, quiet=False):
        request=self.pending.get(rid)
        try:super().reply(rid,result,disposition,quiet=quiet)
        except (ValueError,OSError) as exc:
            if request and rid in self.rpc.emitted_requests:
                self.pending[rid]=request
            self.show_pending()
            self.publish(detail=str(exc))

    def release(self):
        # NativeApprovals is the sole automatic decision path. Legacy Flow's
        # cached application grants must not race with desktop/manual responses.
        if self.cancelled or self.stop.is_set():return
        for rid,r in list(self.pending.items()):
            if r['epoch']!=self.epoch and r['kind']=='permission':
                self.reply(rid,req.decline_result(r),'用户修改了要求，此请求已失效')
        self.show_pending()

    def set_approval_mode(self, mode):
        super().set_approval_mode(mode)
        if mode=='codex':self.publish(detail='授权和追问会显示在浮窗，也可在 Codex 处理')
        elif mode=='auto':self.publish(detail='自动处理应用使用权限；其他授权和追问在浮窗确认')
        elif mode=='all':self.publish(detail='自动处理可识别的授权；追问及登录验证仍由你处理')


def shutdown_background_tasks():
    cancel_native_submissions()
    shutdown_services()
