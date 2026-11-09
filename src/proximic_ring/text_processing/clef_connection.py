"""Direct HTTP(S) transport for the user's public Clef service."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import os
import re
import threading
from urllib import error, request
from urllib.parse import urlsplit

from .intent_router import ClefIntentRouter, _NoRedirect


@dataclass(frozen=True)
class ClefServerSettings:
    host: str = ""
    port: int = 20031

    def base_url(self):
        host = self.host.strip()
        if not host:
            raise ValueError("请在设置 → 自动路由服务器中填写服务器地址和公网端口")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("公网端口需为 1–65535")
        if any(char.isspace() for char in host):
            raise ValueError("服务器地址只填写域名或 IP，可带 http:// 或 https://")
        # Accept bare IPv6 as well as bracketed IPv6 in an HTTP(S) address.
        if "://" not in host:
            try:
                if ipaddress.ip_address(host).version == 6:
                    host = f"[{host}]"
            except ValueError:
                pass
            host = "http://" + host
        try:
            url = urlsplit(host)
            if (url.scheme not in {"http", "https"} or not url.hostname
                    or url.username is not None or url.password is not None
                    or url.path not in {"", "/"} or url.query or url.fragment
                    or url.port is not None or url.netloc.endswith(":")):
                raise ValueError
            name = url.hostname
            if ":" in name:
                ipaddress.IPv6Address(name)
                name = f"[{name}]"
            elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", name):
                raise ValueError
        except ValueError:
            raise ValueError("请填写域名或 IP（可带 http:// 或 https://）；公网端口请单独填写，不含账号或接口路径") from None
        return f"{url.scheme}://{name}:{self.port}"

    def validate(self):
        self.base_url()


class ClefConnection:
    def __init__(self, settings=ClefServerSettings(), *, on_status=None):
        self.settings = settings
        self.on_status = on_status or (lambda text: None)
        self._lock = threading.RLock()
        self._closed = False
        # Connect to the configured server directly, without inherited proxies.
        self.opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    def configure(self, settings):
        settings.validate()
        with self._lock:
            if self._closed:
                raise RuntimeError("自动路由连接已关闭")
            if settings != self.settings:
                self.settings = settings
                self.on_status("服务器地址已保存，下次请求直接连接")

    def endpoint(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("自动路由连接已关闭")
            return self.settings.base_url() + "/v1/systemone"

    def check(self):
        endpoint = self.endpoint()
        self.on_status("正在检查公网 Clef 服务…")
        try:
            with self.opener.open(endpoint.removesuffix("/v1/systemone") + "/health", timeout=5) as response:
                raw = response.read(16385)
            if len(raw) > 16384:
                raise ValueError
            health = json.loads(raw)
            if not isinstance(health, dict) or health.get("status") != "ready" or health.get("model") != "clef-flash":
                raise ValueError
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise RuntimeError(f"Clef 健康检查失败（HTTP {status}），请检查公网地址、端口映射和服务状态") from None
        except (ValueError, UnicodeError):
            raise RuntimeError("公网端口未返回已就绪的 Clef 服务，请检查端口映射和服务状态") from None
        except (error.URLError, OSError):
            raise RuntimeError("无法连接公网 Clef 服务，请检查服务器地址、公网端口、网络和服务监听状态") from None
        with self._lock:
            if self._closed:
                raise RuntimeError("自动路由连接已关闭")
        self.on_status("已连接 · Clef 服务就绪")
        return endpoint

    def close(self):
        self.shutdown()

    def shutdown(self):
        with self._lock:
            self._closed = True


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
