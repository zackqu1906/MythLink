"""Small stdio App Server client for the standalone ring experiment."""
import json
import queue
import subprocess
import threading
import time
from pathlib import Path

from .network_env import app_server_environment


class RPC:
    def __init__(self, *, runtime_dir, codex_path, observer=None):
        self.observer = observer
        self.stderr = (Path(runtime_dir) / 'app-server-stderr.log').open('a')
        try:
            self.proc = subprocess.Popen(
                [str(codex_path), 'app-server', '--listen', 'stdio://'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                text=True, bufsize=1, env=app_server_environment(),
            )
        except BaseException:
            self.stderr.close()
            raise
        self.inbox = queue.Queue()
        self.next_id = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    message = json.loads(line)
                    self._observe('incoming', message)
                    self.inbox.put(message)
                except ValueError:
                    self._observe('local', {'method':'transport/invalid_json',
                                           'params':{'length':len(line)}})
        finally:
            self._observe('local', {'method':'transport/closed',
                                   'params':{'exit_code':self.proc.poll()}})
            self.inbox.put({'transportClosed': self.proc.poll()})

    def _observe(self, direction, message):
        observer = getattr(self, 'observer', None)
        if observer:
            # Evidence collection must never approve, delay on disk, or break a tool.
            try: observer(direction, message)
            except Exception: pass

    def write(self, obj):
        self._observe('outgoing', obj)
        try:
            self.proc.stdin.write(json.dumps(obj) + '\n')
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._observe('local', {'method':'transport/write_failed',
                'params':{'requestId':obj.get('id'),'error_type':type(exc).__name__}})
            raise

    def send(self, method, params=None):
        self.next_id += 1
        obj = {'id': self.next_id, 'method': method}
        if params is not None:
            obj['params'] = params
        self.write(obj)
        return self.next_id

    def call(self, method, params=None, timeout=30):
        """Bounded setup/lifecycle calls; preserve notifications for the event loop."""
        request_id = self.send(method, params)
        deadline = time.monotonic() + timeout
        deferred = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(method)
                try:
                    event = self.inbox.get(timeout=remaining)
                except queue.Empty:
                    raise TimeoutError(method) from None
                if 'transportClosed' in event:
                    raise RuntimeError('Codex App Server 连接已关闭')
                if event.get('id') == request_id and 'method' not in event:
                    if 'error' in event:
                        raise RuntimeError(json.dumps(event['error'], ensure_ascii=False))
                    return event.get('result')
                deferred.append(event)
        finally:
            # Prepend under the Queue lock to retain original event order.
            with self.inbox.mutex:
                for event in reversed(deferred):
                    self.inbox.queue.appendleft(event)
                self.inbox.not_empty.notify_all()

    def initialize(self):
        result = self.call('initialize', {
            'clientInfo': {'name': 'mythlink_ring_agent_demo',
                           'title': 'Mythlink ring desktop experiment', 'version': '0.1.0'},
            'capabilities': {'experimentalApi': True},
        })
        self.write({'method': 'initialized'})
        return result

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.reader.join(timeout=1)
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.stderr.close()
