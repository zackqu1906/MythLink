"""Local logged-in Codex transport. No model calls in the history status path."""
import json
from pathlib import Path
import queue
import re
import tempfile
import time

from ..agent.rpc import RPC
from ..agent.connection import verify_login
from .history_contract import PROMPT, SCHEMA
from .history_source import HistoryUnavailable
from .history_threads import select_threads


class HistoryBackend:
    def __init__(self, codex_path, *, rpc_factory=RPC):
        self.directory = tempfile.TemporaryDirectory(prefix="mythlink-recommend-")
        self.rpc = None
        self.control = None
        self.servers = []
        self.configured_servers = []
        self._threads = []
        self._threads_checked = float("-inf")
        self.codex_path, self.rpc_factory = codex_path, rpc_factory

    def _thread(self, **extra):
        result = self.rpc.call("thread/start", {
            "cwd": self.directory.name, "ephemeral": True,
            "approvalPolicy": "never", "sandbox": "read-only",
            "environments": [], **extra,
        })
        return result["thread"]["id"]

    def _servers(self, thread):
        rows, cursor, seen = [], None, set()
        while True:
            result = self.rpc.call("mcpServerStatus/list", {
                "threadId": thread, "detail": "toolsAndAuthOnly",
                "limit": 100, **({"cursor": cursor} if cursor else {}),
            })
            rows.extend(result.get("data", []))
            cursor = result.get("nextCursor")
            if not cursor or cursor in seen:
                return rows
            seen.add(cursor)

    def status(self):
        if self.rpc is None:
            if not self.codex_path:
                raise HistoryUnavailable("未找到 Codex，请先安装并登录")
            self.rpc = self.rpc_factory(runtime_dir=Path(self.directory.name), codex_path=self.codex_path)
            self.rpc.initialize()
            verify_login(self.rpc)
            config = self.rpc.call("config/read", {"includeLayers": False})
            self.configured_servers = list((config.get("config") or {}).get("mcp_servers", {}))
            self.control = self._thread()
            self.servers = self._servers(self.control)
        deadline = time.monotonic() + 10
        while any(row.get("runtimeStatus") in ("starting", "notStarted") and (
                row.get("name") == "computer-history"
                or "computer-history" in (row.get("pluginId") or "")) for row in self.servers):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
            self.servers = self._servers(self.control)
        server = next((row for row in self.servers if
                       "computer_history_status" in row.get("tools", {})), None)
        if not server or server.get("runtimeStatus") not in (None, "connected"):
            raise HistoryUnavailable("请在 Codex 中启用 Computer History 插件")
        result = self.rpc.call("mcpServer/tool/call", {
            "threadId": self.control, "server": server["name"],
            "tool": "computer_history_status", "arguments": {},
        }, timeout=15)
        if result.get("isError"):
            raise HistoryUnavailable("Computer History 暂不可用")
        value = result.get("structuredContent")
        if not isinstance(value, dict) or "state" not in value:
            for block in result.get("content", []):
                if block.get("type") == "text":
                    try:
                        value = json.loads(block["text"])
                        if isinstance(value, dict) and "state" in value:
                            break
                    except (ValueError, KeyError):
                        pass
        if not isinstance(value, dict) or "state" not in value:
            raise HistoryUnavailable("Computer History 状态格式不兼容，请更新 Codex")
        return value

    def threads(self):
        now = time.monotonic()
        if now - self._threads_checked < 60:
            return self._threads
        self._threads_checked = now
        try:
            result = self.rpc.call("thread/list", {
                "limit": 40, "sortKey": "updated_at", "sortDirection": "desc",
                "archived": False, "sourceKinds": ["cli", "vscode", "appServer"],
                "useStateDbOnly": True,
            }, timeout=5)
            self._threads = select_threads(result.get("data", []))
        except (OSError, RuntimeError, TimeoutError, TypeError, AttributeError, ValueError):
            # Listing is optional: a temporary catalog failure must not break
            # ordinary recommendations or retain stale target associations.
            self._threads = []
        return self._threads

    def generate(self, evidence, stop, timeout=120):
        if stop.is_set():
            raise TimeoutError("推荐生成已取消")
        # Status is called by the host; the inference thread gets only a bounded
        # text excerpt. Disable every discovered MCP server, plugins/connectors,
        # command execution and external tools in this thread only.
        config = {
            "features": {"shell_tool": False, "apps": False, "plugins": False,
                         "multi_agent": False, "memories": False, "hooks": False,
                         "goals": False},
            "web_search": "disabled",
            "history": {"persistence": "none"},
            "plugins": {row["pluginId"]: {"enabled": False} for row in self.servers
                        if row.get("pluginId")},
        }
        # Dotted leaf overrides preserve each user's transport configuration.
        # Plugin servers have their own namespace and are disabled above.
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in self.configured_servers):
            raise RuntimeError("当前 MCP 配置无法安全隔离")
        config.update({f"mcp_servers.{name}.enabled": False
                       for name in self.configured_servers})
        thread = self._thread(config=config, developerInstructions=PROMPT)
        turn = None
        finished = False
        try:
            # Fail closed if a new plugin escaped the configuration overrides.
            if any(row.get("runtimeStatus") != "disabled" for row in self._servers(thread)):
                raise RuntimeError("推荐专用连接仍暴露外部工具")
            if stop.is_set():
                raise TimeoutError("推荐生成已取消")
            response = self.rpc.call("turn/start", {
                "threadId": thread, "input": [{"type": "text",
                    "text": json.dumps(evidence, ensure_ascii=False)}],
                "outputSchema": SCHEMA, "effort": "low",
                "disabledPluginIds": sorted({row["pluginId"] for row in self.servers
                                            if row.get("pluginId")}),
            })
            turn = response["turn"]["id"]
            deadline, final = time.monotonic() + timeout, ""
            while not stop.is_set() and time.monotonic() < deadline:
                try:
                    event = self.rpc.inbox.get(timeout=0.25)
                except queue.Empty:
                    continue
                if "transportClosed" in event:
                    raise RuntimeError("Codex 连接中断")
                if "id" in event and "method" in event:
                    # No approvals, dynamic tools or user interaction on a
                    # background recommendation connection.
                    self.rpc.write({"id": event["id"], "error": {
                        "code": -32601, "message": "Background inference has no interactive tools"}})
                    raise RuntimeError("推荐请求尝试使用交互工具")
                params = event.get("params") or {}
                if params.get("threadId") != thread:
                    continue
                if event.get("method") == "item/completed":
                    item = params.get("item") or {}
                    if item.get("type") == "agentMessage":
                        final = item.get("text", "")
                if event.get("method") == "turn/completed":
                    if params.get("turn", {}).get("status") != "completed":
                        raise RuntimeError("推荐生成未完成")
                    finished = True
                    return json.loads(final)
            raise TimeoutError("推荐生成超时" if not stop.is_set() else "推荐生成已取消")
        finally:
            if turn and not finished:
                self.rpc.send("turn/interrupt", {"threadId": thread, "turnId": turn})
            self.rpc.send("thread/unsubscribe", {"threadId": thread})

    def close(self):
        if self.rpc:
            self.rpc.close()
            self.rpc = None
        self.directory.cleanup()
