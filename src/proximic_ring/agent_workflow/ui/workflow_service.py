"""Nonblocking UI bridge to a low-priority extraction subprocess."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from proximic_ring.agent_workflow import pipeline_policy


class WorkflowService:
    def __init__(self, root, since, on_status):
        self.root=Path(root); self.since=since; self.on_status=on_status
        self.commands=queue.Queue(); self.stopped=threading.Event(); self.thread=None

    def send(self, command):
        if not pipeline_policy.legacy_workflow_enabled():
            self.close()
            return
        self.commands.put(command)
        if self.thread is None:
            self.thread=threading.Thread(target=self._run,daemon=True,name='WorkflowBridge')
            self.thread.start()

    def close(self):
        # No network wait, flush, process join or model cancellation in the UI.
        self.stopped.set()

    def _run(self):
        if not pipeline_policy.legacy_workflow_enabled():
            return
        process=None
        try:
            args=([sys.executable,'--workflow-worker'] if getattr(sys,'frozen',False) else
                  [sys.executable,'-m','proximic_ring.agent_workflow.extraction.workflows'])
            args+=['--root',str(self.root),'--serve','--since',self.since]
            env=dict(os.environ)
            if not getattr(sys,'frozen',False):
                source=str(Path(__file__).resolve().parents[3])
                env['PYTHONPATH']=source+os.pathsep+env.get('PYTHONPATH','')
            process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,text=True,encoding='utf-8',env=env)
            last_status=''; last_read=0
            while not self.stopped.is_set():
                if process.poll() is not None:
                    raise RuntimeError('后台提取进程退出；重新打开应用可恢复队列')
                try: command=self.commands.get(timeout=.25)
                except queue.Empty: command=None
                if command:
                    process.stdin.write(json.dumps(command,ensure_ascii=False)+'\n'); process.stdin.flush()
                if time.monotonic()-last_read>1:
                    last_read=time.monotonic()
                    try:
                        value=(self.root/'workflows/status.json').read_text(encoding='utf-8')
                        if value!=last_status:
                            self.on_status(json.loads(value)); last_status=value
                    except (OSError,ValueError): pass
        except Exception as exc:
            if not self.stopped.is_set():
                self.on_status({'error':str(exc) if type(exc) is RuntimeError else '后台提取不可用：'+type(exc).__name__})
        finally:
            if process:
                if process.stdin: process.stdin.close()
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=2)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
