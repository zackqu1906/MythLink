"""Per-user SSH transport for Clef. No developer tunnel or shell environment required."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
import time
from urllib import request

from .intent_router import ClefIntentRouter, _NoRedirect


@dataclass(frozen=True)
class ClefSSHSettings:
    host: str = ""
    port: int = 22
    user: str = "root"
    key_file: str = ""
    service_port: int = 30000

    def validate(self):
        if not self.host:
            raise ValueError("请在设置 → 自动路由服务器中填写 SSH 连接信息")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", self.host):
            raise ValueError("服务器地址只填写域名或 IP，不含 ssh、账号和端口")
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", self.user):
            raise ValueError("请填写有效的 SSH 用户名")
        if not all(isinstance(p, int) and 1 <= p <= 65535 for p in (self.port, self.service_port)):
            raise ValueError("端口需为 1–65535")
        if self.key_file:
            key = Path(self.key_file).expanduser()
            if not key.is_file() or not os.access(key, os.R_OK):
                raise ValueError("无法读取私钥，请重新选择私钥文件")
            if key.suffix == ".pub":
                raise ValueError("请选择私钥文件，不是 .pub 公钥")


class ClefSSHConnection:
    def __init__(self, settings=ClefSSHSettings(), *, directory=None, on_status=None):
        self.settings = settings
        self.directory = Path(directory or Path.home() / "Library/Application Support/Mythlink/clef")
        self.on_status = on_status or (lambda text: None)
        self._lock = threading.RLock()
        self._closed = False
        self._process = None
        self._stderr = None
        self._port = 0
        # A loopback tunnel must never go through a desktop/system proxy.
        self.opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    def configure(self, settings):
        with self._lock:
            if settings != self.settings:
                self._stop()
                self.settings = settings
                self.on_status("连接信息已保存，下次自动连接")

    def _command(self, port):
        s = self.settings
        known = self.directory / "known_hosts"
        args = ["/usr/bin/ssh", "-F", "/dev/null", "-N", "-T",
                "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                "-o", f'UserKnownHostsFile="{known}" "{Path.home() / ".ssh/known_hosts"}"',
                "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
                "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=2", "-o", "UseKeychain=yes",
                "-o", "PermitLocalCommand=no", "-o", "ForwardAgent=no",
                "-p", str(s.port), "-l", s.user]
        if s.key_file:
            args += ["-o", "IdentitiesOnly=yes", "-i", str(Path(s.key_file).expanduser())]
        return args + ["-L", f"127.0.0.1:{port}:127.0.0.1:{s.service_port}", s.host]

    def _ssh_error(self):
        self._stderr.seek(0)
        message = self._stderr.read().decode("utf-8", "replace").lower()
        if "host key verification failed" in message or "host identification has changed" in message:
            return "服务器身份与已保存记录不一致，请联系管理员核实；未连接"
        if "permission denied" in message or "load key" in message or "bad permissions" in message:
            return "SSH 认证失败：请检查账号、私钥及文件权限；加密私钥需先加入 macOS 钥匙串 / ssh-agent"
        if "resolve hostname" in message:
            return "无法解析服务器地址，请检查域名和网络"
        return "SSH 连接失败，请检查地址、端口、网络以及服务器的端口转发权限"

    def endpoint(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("自动路由连接已关闭")
            self.settings.validate()
            if self._process is not None and self._process.poll() is None:
                return f"http://127.0.0.1:{self._port}/v1/systemone"
            self._stop()
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                self._port = sock.getsockname()[1]
            self.on_status("正在连接 SSH…")
            self._stderr = tempfile.TemporaryFile()
            try:
                self._process = subprocess.Popen(self._command(self._port), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=self._stderr, start_new_session=True)
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if self._closed:
                        raise RuntimeError("自动路由连接已关闭")
                    if self._process.poll() is not None:
                        raise RuntimeError(self._ssh_error())
                    try:
                        with socket.create_connection(("127.0.0.1", self._port), timeout=.1):
                            return f"http://127.0.0.1:{self._port}/v1/systemone"
                    except OSError:
                        time.sleep(.05)
                raise TimeoutError("SSH 连接超时，请检查地址、端口和网络")
            except Exception:
                self._stop()
                raise

    def check(self):
        endpoint = self.endpoint()
        try:
            with self.opener.open(endpoint.removesuffix("/v1/systemone") + "/health", timeout=5) as response:
                health = json.loads(response.read(16384))
            if health.get("status") != "ready" or health.get("model") != "clef-flash":
                raise ValueError("unexpected service")
        except Exception:
            raise RuntimeError("SSH 已连接，但 Clef 服务未就绪；请检查服务器内服务端口并启动已部署的 Clef 服务") from None
        self.on_status("已连接 · Clef 服务就绪")
        return endpoint

    def _stop(self):
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
            self._process = None
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    def close(self):
        self.shutdown()
        with self._lock:
            self._stop()

    def shutdown(self):
        """Nonblocking UI exit; also interrupts a connection still being established."""
        self._closed = True
        process = self._process
        if process is not None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass


class ConnectedClefRouter:
    """Snapshot each request; settings changes cannot redirect in-flight data."""
    def __init__(self, connection):
        self.connection = connection

    def warmup(self):
        if self.connection.settings.host:
            return self.connection.check()
        # Keep explicit CLI environment support for developer tools.
        ClefIntentRouter().warmup()

    def classify(self, *args, **kwargs):
        if not self.connection.settings.host:
            if not os.environ.get("PROXIMIC_CLEF_URL"):
                raise RuntimeError("请在设置 → 自动路由服务器中配置并测试连接")
            return ClefIntentRouter().classify(*args, **kwargs)
        try:
            router = ClefIntentRouter(endpoint=self.connection.endpoint(), model="clef-flash",
                api_key="", timeout_s=20, urlopen=self.connection.opener.open)
            result = router.classify(*args, **kwargs)
            self.connection.on_status("已连接 · 自动路由可用")
            return result
        except Exception as exc:
            self.connection.on_status(str(exc))
            raise

    def close(self):
        self.connection.close()
