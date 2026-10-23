"""原生推荐面板的异步桥：先捕获场景、后排序、最后展示。

数据库/窗口元数据读取都在独立单线程工作器；不在 Agent 请求链调用 LLM。
每次打开/手动刷新重排；打开期间只检查库 token，不改动位置和流程版本。
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer
from proximic_ring.agent_workflow.recommendation.recommendation_context import invocation_context, persist_invocation
from proximic_ring.platforms.macos.native_access import NativeAccessChannel
from proximic_ring.agent_workflow import pipeline_policy


class RecommendationController(QObject):
    _result = Signal(int, str, object, str)
    def __init__(self, agent):
        super().__init__(agent)
        self.agent=agent;self.dialog=None;self.epoch=0;self.closed=False;self.loading=False
        self.scope='user';self.snapshot=None;self.executing=False;self.polling=False
        self.worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WorkflowRecommendations')
        self._result.connect(self._receive,Qt.QueuedConnection)
        self.timer=QTimer(self);self.timer.setInterval(2000);self.timer.timeout.connect(self.poll)
        self.last_execution=None;self.request_root=None
        # Keep the hub's captured window independent of Agent focus history.
        self._focus_channel=NativeAccessChannel()
        self.input=None
        if pipeline_policy.legacy_workflow_enabled():
            from proximic_ring.agent_workflow.ui.recommendation_input import RecommendationInput
            self.input=RecommendationInput(self)

    def owns_input_focus(self):
        # Release builds omit the retired input UI. Shared gesture routing must
        # query this controller without requiring that optional editor to exist.
        return bool(not self.closed and pipeline_policy.legacy_workflow_enabled()
                    and self.input is not None and self.input.owns_input_focus())

    def filter_gesture(self, event, connection):
        if not pipeline_policy.legacy_workflow_enabled(): return False
        return self.input.filter_gesture(event,connection)

    @property
    def root(self):
        from proximic_ring.agent_workflow.recording.recording import records_root
        return Path(self.agent.workspace.state.get('recordsRoot') or self.agent._workflow_root or records_root())

    def _job(self, kind, call, epoch=None):
        epoch=self.epoch if epoch is None else epoch
        def work():
            try: value=call();error=''
            except Exception as exc: value=None;error=str(exc) if type(exc) is ValueError else '推荐处理失败：'+type(exc).__name__
            if not self.closed:self._result.emit(epoch,kind,value,error)
        self.worker.submit(work)

    def _store(self, root, function):
        from proximic_ring.agent_workflow.operations.operation_store import OperationStore
        store=OperationStore(root)
        try:return function(store)
        finally:store.close()

    def capture_context(self, trigger, root=None):
        """Call on a worker (native request or HTTP), never synchronously on Qt."""
        try:result=self._focus_channel.call('agent_focus_capture',host_pid=os.getpid())
        except Exception:result={'status':'unavailable'}
        context=invocation_context(result.get('context') or {'availability':result.get('status','unavailable')},
                                   trigger='recommendation_'+trigger)
        persist_invocation(root or self.root,context)
        return context

    @Slot(str)
    def open(self, trigger='manual'):
        if self.closed or self.loading:return
        if not pipeline_policy.legacy_workflow_enabled():
            if trigger != 'gesture':
                self.agent.owner._app_gestures.notify(pipeline_policy.DISABLED_MESSAGE)
            return
        if self.agent.busy or self.agent._input_busy() or self.agent._composing:
            self.agent.owner._app_gestures.notify('请先结束当前任务或输入，再打开推荐')
            return
        if self.dialog and self.dialog.closing:self.dialog.finish_dismissal()
        if self.dialog and self.dialog.isVisible():
            self.dialog.present();return
        self.epoch+=1;self.loading=True;root=self.root;scope=self.scope
        self.request_root=root
        # Capture BEFORE showing any recommendation window. Do not use the
        # last Agent invocation or polling cache when capture fails/returns self.
        def request():
            from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
            context=self.capture_context(trigger,root)
            return self._store(root,lambda s:request_recommendations(s,context,scope=scope,limit=4,trigger=trigger,surface='native'))
        self._job('request',request)

    def refresh(self):
        if self.executing:return
        if self.dialog:
            if self.input.stage!='hub':return
            self.input.release();self.dialog.hide()
        self.timer.stop();self.open('manual')

    def dismiss(self):
        if self.input:self.input.release()
        self.epoch+=1;self.loading=False;self.timer.stop();self.snapshot=None

    def poll(self):
        if self.closed or self.polling or not self.snapshot:return
        from proximic_ring.agent_workflow.recommendation.recommendation_catalog import catalog_state, pipeline_status
        self.polling=True;root=self.request_root
        self._job('catalog',lambda:dict(self._store(root,catalog_state),pipeline=pipeline_status(root)))

    def expose(self, position):
        if not self.snapshot:return
        item=self.snapshot['items'][position-1];request=self.snapshot['request_id'];root=self.request_root
        self._job('exposure',lambda:dict(position=position,exposure=self._store(root,
            lambda s:s.expose(request,item['operation_id'],method='native_visible_500ms'))))

    def reject(self, position):
        if self.executing or not self.snapshot:return
        card=self.dialog.cards[position-1];item=self.snapshot['items'][position-1];root=self.request_root
        if not card['exposure'] or card['rejected']:return
        card['rejected']=True
        self._job('rejected',lambda:self._store(root,lambda s:s.feedback(card['exposure'],item['operation_id'],'rejected')))
        card['choose'].setEnabled(False);card['title'].setEnabled(False);card['reject'].setEnabled(False)
        if self.input.stage=='parameters':self.dialog.back()

    def execute(self, position, parameters):
        if not pipeline_policy.legacy_workflow_enabled(): return
        if self.executing or not self.snapshot:return
        if self.agent.busy:
            self.dialog.message.setText('Agent 正在工作，请完成后重新获取推荐');return
        exposure=self.dialog.cards[position-1]['exposure'];root=self.request_root
        if not exposure:return
        self.executing=True;self.dialog.set_execution_busy(True)
        def prepare(store):
            from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution, dispatch_command
            identifier=prepare_execution(store,exposure,parameters)
            return dispatch_command(store,identifier)
        self._job('execute',lambda:self._store(root,prepare))

    @Slot(int,str,object,str)
    def _receive(self, epoch, kind, value, error):
        if kind=='catalog':self.polling=False
        if self.closed:return
        if epoch!=self.epoch:
            if kind=='execute' and value:
                self._not_started(value['execution_id'],'推荐面板已关闭，未送入 Agent')
            return
        if kind=='request':self.loading=False
        if error:
            if self.dialog:self.dialog.message.setText(error);self.dialog.set_execution_busy(False)
            else:self.agent.owner._app_gestures.notify(error)
            self.executing=False
            if kind=='exposure' and self.dialog:
                for card in self.dialog.cards:card['pending']=False
            return
        if kind=='request':
            if self.agent.busy or self.agent._input_busy() or self.agent._composing:
                return  # Input/task may have started during the asynchronous capture.
            from proximic_ring.agent_workflow.ui.recommendation_dialog import RecommendationDialog
            if self.dialog is None:self.dialog=RecommendationDialog(self)
            self.snapshot=value;self.executing=False;self.dialog.populate(value)
            self.dialog.present();self.timer.start()
        elif kind=='catalog':
            pipeline=value['pipeline']
            self.dialog.pipeline_notice.setText(f"后台提取：等待 {pipeline['queued']}，处理中 {pipeline['running']}，失败 {pipeline['failed']}；待入组 {value['pending']}，归并失败 {value['failed']}。" + pipeline['error'])
            if self.snapshot and value['token']!=self.snapshot['catalog']['token']:
                self.dialog.update_notice.setText('操作库已更新；本次四项保持不变，点击刷新或下次打开使用最新排序。')
        elif kind=='exposure':
            card=self.dialog.cards[value['position']-1];card['exposure']=value['exposure'];card['pending']=False
            self.dialog.set_execution_busy(self.executing)
        elif kind=='execute':
            self.executing=False
            if self.agent.busy:
                self._not_started(value['execution_id'],'Agent 已开始其他任务，请重新获取推荐')
                self.dialog.set_execution_busy(False)
                self.dialog.message.setText('Agent 已开始其他任务，本次推荐未发送');return
            self.last_execution=value['execution_id']
            if self.agent._send(value) is False:
                self._not_started(value['execution_id'],'Agent 连接已关闭')
                self.dialog.message.setText('Agent 连接已关闭，请重新连接后再打开推荐。')
                self.dialog.set_execution_busy(False);return
            self.dialog.accept()
            self.agent._visible=True;self.agent._focused=True;self.agent._emit()
            self.agent.focusRequested.emit('task')
        elif kind=='rejected':self.dialog.message.setText('已记录拒绝；反馈暂不影响统计分数')

    def _not_started(self, identifier, message):
        root=self.request_root or self.root
        def update(store):
            from proximic_ring.agent_workflow.extraction.workflow_store import now
            with store.db:
                store.db.execute("UPDATE recommendation_executions SET state='not_started',error=?,updated_at=? WHERE id=? AND state IN ('prepared','submitted')",(message,now(),identifier))
        self.worker.submit(lambda:self._store(root,update))

    def connection_failed(self):
        if self.last_execution:self._not_started(self.last_execution,'Agent 连接失败；不会自动重发，请重新获取推荐')
        with self.agent._lock:
            self.agent._commands=[c for c in self.agent._commands if c.get('type')!='workflow_recommendation']

    def close(self):
        if self.closed:return
        self.closed=True;self.timer.stop()
        if self.input:self.input.close()
        if self.dialog:
            self.dialog.close();self.dialog.deleteLater()
        self.worker.shutdown(wait=False,cancel_futures=True)
        self._focus_channel.close()
