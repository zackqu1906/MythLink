"""Asynchronous, draft-bound automatic context capture for the Qt composer."""
from copy import deepcopy
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, Slot
from proximic_ring.runtime_paths import cache_root
from proximic_ring.agent_workflow.agent.attachments import local_attachment
from .auto_context import needs_context, current_target, capture_window, MAX_CAPTURE_DELAY


class AutoContextController(QObject):
    completed = Signal(object, object, str)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='AgentWindowContext')
        self.cancelled = threading.Event()
        self.pending = None
        self.completed.connect(self._complete)

    def prepare(self, key):
        """Freeze at the send gesture while the same dictation is finishing."""
        self.start(key, {'text':'', 'attachments':self.owner.workspace.attachments.get(key,[])}, prepared=True)

    @staticmethod
    def _discard(item):
        if item:
            from pathlib import Path
            Path(item['path']).unlink(missing_ok=True)

    def cancel_prepared(self):
        binding=self.pending
        if binding and binding['command'] is None:
            self.pending=None
            if 'result' in binding:self._discard(binding['result'][0])

    def start(self, key, command, *, prepared=False):
        owner = self.owner
        if key != 'task' or not needs_context(command.get('text',''), command.get('attachments',[]),
                                            owner._auto_context_mode):
            self.cancel_prepared()
            return False
        if self.pending:
            binding=self.pending
            if prepared or binding['command'] is not None:return True
            # The voice result belongs to this send gesture. Keep its original
            # image/time even when ASR takes seconds; never recapture here.
            binding['command']=deepcopy(command)
            binding['draft']=owner.workspace.drafts.get(key,'')
            if (key!=binding['key'] or command.get('attachments',[])!=binding['attachments']):
                self.pending=None
                if 'result' in binding:self._discard(binding['result'][0])
                owner._input_notice('截图期间任务或附件已变化，未发送；请重新提交。')
                owner._emit()
                return True
            owner._context_message='正在保存发送时的窗口画面…'
            owner.changed.emit()
            if 'result' in binding:self._complete(binding,*binding.pop('result'))
            return True
        context = owner._recommendation_context()
        binding = dict(key=key, command=None if prepared else deepcopy(command), context=context,
                       attachments=deepcopy(command.get('attachments',[])),
                       capture_mode=owner._auto_context_mode,
                       requested_at=datetime.now(timezone.utc).isoformat(), started=time.monotonic(),
                       thread=owner.workspace.state.get('codexThreadId'),
                       turn=owner.workspace.state.get('turnId',''),
                       draft=owner.workspace.drafts.get(key,''))
        self.pending = binding
        if not prepared:
            owner._context_message = '正在保存发送时的窗口画面…'
            owner.changed.emit()
        # Pin the window at the submit event, before scheduling a worker or LLM.
        # This is a cheap WindowServer inventory; pixel capture stays off Qt.
        try:
            excluded_ids = owner.capture._excluded_windows()
            target = current_target(excluded_ids)
        except Exception as exc:
            self.completed.emit(binding, None, str(exc) or '无法确认截图窗口')
            return True
        def capture():
            item, error, path = None, '', None
            try:
                image = capture_window(target, self.cancelled,
                                       deadline=binding['started']+MAX_CAPTURE_DELAY)
                if self.cancelled.is_set(): return
                directory = cache_root()/'desktop-agent/attachments'
                directory.mkdir(parents=True, exist_ok=True)
                path = directory/('window-'+uuid4().hex+'.png')
                # Owner-readable before the image is written.
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                if not image.save(str(path),'PNG'): raise OSError('保存截图失败')
                item = local_attachment(path)
                item.update(automatic=True, context={k:target[k] for k in (
                    'pid','window_id','window_title','bundle','app','document_url') if k in target},
                    capturedAt=target.get('captured_at',datetime.now(timezone.utc).isoformat()))
                item['requestedAt'] = binding['requested_at']
                item['captureMs'] = round((target.get('captured_monotonic',time.monotonic())-binding['started'])*1000)
            except Exception as exc:
                error = str(exc) or '自动截图失败，请手动添加截图'
                if path: path.unlink(missing_ok=True)
            if self.cancelled.is_set():
                if path: path.unlink(missing_ok=True)
                return
            self.completed.emit(binding, item, error)
        self.worker.submit(capture)
        return True

    @Slot(object, object, str)
    def _complete(self, binding, item, error):
        owner = self.owner
        if self.pending is not binding or owner._closed:
            self._discard(item)
            return
        if binding['command'] is None:
            binding['result']=(item,error)
            return
        self.pending = None
        state = owner.workspace.state
        valid = (owner.inputKey == binding['key']
                 and not owner._input_busy() and not owner._composing
                 and owner.workspace.drafts.get(binding['key'],'') == binding['draft']
                 and owner.workspace.attachments.get(binding['key'],[]) == binding['command'].get('attachments',[])
                 and state.get('turnId','') == binding['turn']
                 and state.get('codexThreadId') == binding['thread'])
        if not valid:
            self._discard(item)
            owner._context_message = ''
            owner._input_notice('截图期间任务或输入已变化，未发送；请确认草稿后重新提交。')
        else:
            command = binding['command']
            command['context'] = binding['context']
            if binding['capture_mode'] == 'smart':
                command['context'] = dict(command['context'] or {})
                command['context']['visual_context'] = ({
                    'availability':'captured', 'path':item['path'],
                    'requested_at':item['requestedAt'], 'captured_at':item['capturedAt'],
                    'capture_ms':item['captureMs'], 'window':item['context'],
                } if not error else {'availability':'unavailable', 'reason':error,
                                     'requested_at':binding['requested_at']})
            if error:
                # Optional visual context must not prevent an otherwise valid
                # task from reaching Codex, or overwrite a completed task card.
                owner._context_message = ''
                owner.owner._event_log('AGENT_AUTO_CONTEXT', status='unavailable', reason=error)
                owner._input_notice('自动截图未附带，继续发送任务。'+error)
            else:
                if binding['capture_mode'] == 'always':
                    command.setdefault('attachments', []).append(item)
                    owner._context_message = '已附带窗口截图：'+(item['context'].get('window_title') or '当前窗口')
                else:
                    owner._context_message = '已保存发送时窗口画面，由 Agent 按需查看'
                owner.owner._event_log('AGENT_AUTO_CONTEXT', status='captured',
                    mode=binding['capture_mode'], capture_ms=item['captureMs'])
            owner._submit_command(binding['key'], command)
        owner._emit()

    def close(self):
        self.cancel_prepared()
        self.cancelled.set()
        self.worker.shutdown(wait=False, cancel_futures=True)
