"""One headless task service per Mythlink application process, never per card."""
from __future__ import annotations
import atexit
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import threading
import time

from .network_env import app_server_environment

_ASSETS = Path(__file__).with_name('desktop_owner')
_LOCK = threading.Lock()
_SERVICES = {}


def node_path(codex_path):
    for parent in Path(codex_path).parents:
        if parent.name in ('ChatGPT.app', 'Codex.app'):
            candidate=parent/'Contents/Resources/cua_node/bin/node'
            if candidate.is_file():return candidate
    raise RuntimeError('安装的 Codex 缺少后台任务所需的 Node 运行时，请更新 Codex')


def version():
    digest=hashlib.sha256()
    for p in sorted([*_ASSETS.glob('*.js'), *_ASSETS.glob('*.cjs')]):
        digest.update(p.name.encode());digest.update(p.read_bytes())
    return digest.hexdigest()[:16]


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise PermissionError('后台任务目录权限不正确')


class BackgroundService:
    def __init__(self, codex_path, root=None):
        self.codex_path=str(codex_path)
        self.root=Path(root) if root else Path(tempfile.gettempdir())/f'mythlink-agent-{os.getuid()}-{os.getpid()}'
        # macOS's temporary directory can itself be very long; the user-owned
        # short directory in /tmp keeps the Unix socket under sun_path's limit.
        if root is None and len(os.fsencode(self.root/'control.sock'))>=100:
            self.root=Path('/tmp')/f'mythlink-agent-{os.getuid()}-{os.getpid()}'
        self.socket=self.root/'control.sock'
        self.process=None

    def call(self, method, params=None, timeout=35):
        info=self.socket.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise PermissionError('后台任务端点权限不正确')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as stream:
            stream.settimeout(timeout);stream.connect(str(self.socket))
            stream.sendall((json.dumps({'id':1,'method':method,'params':params or {}},ensure_ascii=False)+'\n').encode())
            data=bytearray()
            while b'\n' not in data:
                part=stream.recv(65536)
                if not part:raise RuntimeError('后台任务连接已断开；未自动重发操作')
                data.extend(part)
                if len(data)>32*1024*1024:raise ValueError('后台返回数据过大')
            response=json.loads(data.split(b'\n',1)[0])
            if response.get('error'):raise RuntimeError(response['error'])
            return response['result']

    def ensure(self):
        with _LOCK:
            try:
                state=self.call('ping',timeout=1)
                if state['version']!=version():raise RuntimeError('后台适配已更新，请退出 Mythlink 后重新启动')
                return self
            except (FileNotFoundError,ConnectionRefusedError):pass
            private_directory(self.root)
            lock=self.root/'service.lock'
            if lock.exists():
                pid=int(lock.read_text())
                try:os.kill(pid,0)
                except ProcessLookupError:lock.unlink();self.socket.unlink(missing_ok=True)
                else:raise RuntimeError('后台任务进程正在启动或失去响应，请稍后重试')
            env=app_server_environment()
            # An external application is not a Codex desktop task. Keep only an
            # explicitly configured home; do not inherit a privileged task pipe.
            for key in list(env):
                if key.startswith('CODEX_') and key!='CODEX_HOME':env.pop(key)
            node=node_path(self.codex_path)
            env['CODEX_MCP_NODE_PATH']=str(node)  # executable location, not an identity
            log=(self.root/'service.log').open('ab')
            try:self.process=subprocess.Popen([str(node),str(_ASSETS/'service.cjs'),str(self.root),self.codex_path,str(os.getpid()),version()],
                    stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=env,start_new_session=True)
            finally:log.close()
            end=time.monotonic()+15
            while time.monotonic()<end:
                if self.process.poll() is not None:raise RuntimeError('后台任务进程启动失败，请查看 service.log')
                try:self.call('ping',timeout=.5);return self
                except (FileNotFoundError,ConnectionRefusedError,socket.timeout):time.sleep(.05)
            raise TimeoutError('后台任务进程启动超时；未提交任务')

    def shutdown(self):
        try:self.call('shutdown',timeout=2)
        except (OSError,RuntimeError):return
        if self.process:
            try:self.process.wait(timeout=9)
            except subprocess.TimeoutExpired:pass


def service_for(codex_path):
    with _LOCK:
        service=_SERVICES.setdefault(str(codex_path),BackgroundService(codex_path))
    return service.ensure()


def shutdown_services():
    for service in list(_SERVICES.values()):service.shutdown()


atexit.register(shutdown_services)
