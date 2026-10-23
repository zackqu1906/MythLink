"""Local Codex desktop coordination transport (not an App Server replacement).

The desktop coordination protocol is private. Keep its version gate here; never
write Codex's database or claim conversations created by another client.
"""
from __future__ import annotations

import json
import os
import plistlib
import queue
import socket
import stat
import struct
import threading
import uuid
from pathlib import Path


SUPPORTED_BUILDS = {'20156', '20770'}
MAX_FRAME = 32 * 1024 * 1024
VERSIONS = {
    'thread-owner-discovery': 1,
    'thread-follower-start-turn': 2,
    'thread-follower-steer-turn': 1,
    'thread-follower-interrupt-turn': 4,
    'thread-follower-read-model-settings': 1,
    'thread-follower-load-complete-history': 1,
    'thread-follower-command-approval-decision': 1,
    'thread-follower-file-approval-decision': 1,
    'thread-follower-permissions-request-approval-response': 1,
    'thread-follower-submit-user-input': 1,
    'thread-follower-submit-mcp-server-elicitation-response': 1,
}


def desktop_supported(codex_path):
    for parent in Path(codex_path or '').parents:
        if parent.name in ('ChatGPT.app', 'Codex.app'):
            try:
                with (parent/'Contents/Info.plist').open('rb') as stream:
                    return str(plistlib.load(stream).get('CFBundleVersion')) in SUPPORTED_BUILDS
            except (OSError, ValueError):
                return False
    return False


def ipc_path():
    root = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    preferred = root/'ipc/ipc.sock'
    return preferred if preferred.exists() else Path(f'/tmp/codex-ipc/ipc-{os.getuid()}.sock')


def encode(message):
    payload = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode()
    if not 0 < len(payload) <= MAX_FRAME:
        raise ValueError('Codex 同步数据过大')
    return struct.pack('<I', len(payload)) + payload


def read_frame(sock):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(size-len(data))
            if not chunk:
                raise EOFError('Codex 同步连接已关闭')
            data.extend(chunk)
        return data
    size, = struct.unpack('<I', exact(4))
    if not 0 < size <= MAX_FRAME:
        raise ValueError('Codex 同步数据长度异常')
    result = json.loads(exact(size))
    if not isinstance(result, dict):
        raise ValueError('Codex 同步数据格式异常')
    return result


class DesktopIPC:
    """One connection; a lost owner never silently reconnects and steals a turn.

    A bounded writer keeps socket backpressure off the agent event loop.
    All received messages are consumed on that event loop, including discovery.
    """
    def __init__(self, path=None):
        self.path = Path(path) if path else ipc_path()
        self.inbox = queue.Queue(maxsize=256)
        self.outbox = queue.Queue(maxsize=64)
        self.closed = threading.Event()
        self.sock = None
        self.client_id = None
        self.reader = self.writer = None

    def connect(self):
        info = self.path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('Codex 本地同步端点权限不正确')
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.settimeout(3)
            self.sock.connect(str(self.path))
            request_id = str(uuid.uuid4())
            self.sock.sendall(encode(dict(type='request', requestId=request_id,
                sourceClientId='initializing-client', version=0, method='initialize',
                params={'clientType':'mythlink'})))
            while True:
                response = read_frame(self.sock)
                if response.get('requestId') == request_id:
                    if response.get('resultType') != 'success':
                        raise RuntimeError('Codex 拒绝同步连接')
                    self.client_id = response['result']['clientId']
                    break
            self.sock.settimeout(None)
        except BaseException:
            self.sock.close()
            raise
        self.reader = threading.Thread(target=self._read, name='CodexDesktopRead', daemon=True)
        self.writer = threading.Thread(target=self._write, name='CodexDesktopWrite', daemon=True)
        self.reader.start()
        self.writer.start()

    def _lost(self):
        if not self.closed.is_set():
            self.closed.set()
            try: self.sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass

    def _read(self):
        try:
            while not self.closed.is_set():
                self.inbox.put(read_frame(self.sock), timeout=1)
        except (OSError, EOFError, ValueError, queue.Full):
            self._lost()

    def _write(self):
        try:
            while not self.closed.is_set():
                try: message = self.outbox.get(timeout=.2)
                except queue.Empty: continue
                self.sock.sendall(message)
        except OSError:
            self._lost()

    def send(self, message):
        if self.closed.is_set():
            raise ConnectionError('Codex 实时同步已断开')
        message = {'sourceClientId':self.client_id, **message}
        try: self.outbox.put_nowait(encode(message))
        except (queue.Full, ValueError):
            self._lost()
            raise ConnectionError('Codex 实时同步拥塞，已停止继续提交') from None

    def broadcast(self, method, version, params, targets=None):
        message = dict(type='broadcast', method=method, version=version, params=params)
        if targets is not None:
            if not targets: return
            message['targetClientIds'] = list(targets)
        self.send(message)

    def respond(self, request, result=None, error=None):
        response = dict(type='response', requestId=request['requestId'],
                        method=request['method'], handledByClientId=self.client_id)
        if error is None: response.update(resultType='success', result=result or {})
        else: response.update(resultType='error', error=str(error))
        self.send(response)

    def close(self):
        self.closed.set()
        if self.sock:
            try: self.sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.sock.close()
        for worker in (self.reader, self.writer):
            if worker: worker.join(timeout=1)
