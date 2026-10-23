"""Loopback broker: native-only HTTP, live requests, expiring commands and ACKs.

No HTTP endpoint is available to websites (Origin/Fetch headers and all CORS
preflights are rejected). Native clients, already running as the same OS user,
establish a per-process capability; every subsequent request needs that token.
There is no generic execution, navigation, filesystem or keyboard endpoint.
"""
from __future__ import annotations

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time

from .protocol import ACTIONS, BUNDLES, MAX_MESSAGE, PORT, VERSION, clean_snapshot, identity


class Broker:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.condition = threading.Condition()
        self.clients = {}
        self.waiting = {}
        self.closed = False

    def connect(self, browser, session):
        if browser not in BUNDLES or not isinstance(session, str) or not 8 <= len(session) <= 160:
            raise ValueError("invalid_client")
        with self.condition:
            now = self.clock()
            for key, client in list(self.clients.items()):
                if now - client["seen"] > 10 or (client["browser"], client["session"]) == (browser, session):
                    self.clients.pop(key)
            if len(self.clients) >= 16:
                raise ValueError("too_many_clients")
            token = secrets.token_urlsafe(32)
            self.clients[token] = dict(browser=browser, session=session, seen=now, snapshot={}, commands=deque())
            return {"version": VERSION, "token": token, "command_wait": True}

    def exchange(self, token, data):
        wait_ms = data.get("wait_ms", 0)
        if type(wait_ms) is not int or not 0 <= wait_ms <= 1000:
            raise ValueError("invalid_wait")
        with self.condition:
            client = self.clients.get(token)
            if client is None:
                raise PermissionError("browser_disconnected")
            client["seen"] = self.clock()
            if "snapshot" in data:
                snapshot = clean_snapshot(data["snapshot"])
                if snapshot["session"] != client["session"]:
                    raise ValueError("invalid_session")
                client["snapshot"] = snapshot
            replies = data.get("replies", [])
            if not isinstance(replies, list):
                raise ValueError("invalid_replies")
            for reply in replies[:16]:
                if not isinstance(reply, dict):
                    continue
                pending = self.waiting.get(reply.get("id"))
                if pending and pending["token"] == token and pending["deadline"] > self.clock():
                    if "snapshot" in reply:
                        snapshot = clean_snapshot(reply["snapshot"])
                        if snapshot["session"] != client["session"]:
                            continue
                        client["snapshot"] = snapshot
                        pending["result"] = snapshot
                    else:
                        ok = reply.get("ok") is True
                        pending["result"] = {"ok": ok,
                            "reason": "browser_action_applied" if ok else reply.get("reason") if reply.get("reason") in {
                                "page_changed", "text_focus", "browser_action_failed", "browser_window_unfocused",
                                "browser_page_unavailable", "browser_command_expired",
                                "browser_playback_permission_required"} else "browser_action_failed"}
            # Deliver ACKs before waiting for the next command. Waiting here
            # releases the lock and request() wakes us immediately; a browser
            # no longer has to sleep through a fixed heartbeat after each ACK.
            self.condition.notify_all()
            deadline = time.monotonic() + wait_ms / 1000
            while not client["commands"] and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
                if self.clients.get(token) is not client:
                    raise PermissionError("browser_disconnected")
            commands = []
            while client["commands"]:
                command = client["commands"].popleft()
                pending = self.waiting.get(command["id"])
                if pending and pending["deadline"] > self.clock():
                    commands.append({**command, "expires": int(time.time() * 1000 +
                        (pending["deadline"] - self.clock()) * 1000)})
            self.condition.notify_all()
            return {"version": VERSION, "commands": commands}

    def request(self, bundle, operation, target=None, action=None, timeout=1.4):
        with self.condition:
            now = self.clock()
            clients = [(key, value) for key, value in self.clients.items()
                       if BUNDLES[value["browser"]].casefold() == bundle.casefold()
                       and now - value["seen"] < 2.5 and value["snapshot"].get("focused")]
            if len(clients) != 1:
                return {"error": "browser_disconnected" if not clients else "browser_ambiguous_window"}
            token, client = clients[0]
            if operation not in {"snapshot", "action"} or (operation == "action" and (action not in ACTIONS or not isinstance(target, dict))):
                return {"error": "browser_action_failed"}
            if len(self.waiting) >= 32:
                return {"error": "browser_timeout"}
            if target and identity(target) != identity(client["snapshot"]):
                return {"error": "page_changed"}
            command_id = secrets.token_hex(16)
            pending = dict(token=token, deadline=now + timeout, result=None)
            self.waiting[command_id] = pending
            client["commands"].append(dict(id=command_id, operation=operation, target=target, action=action))
            self.condition.notify_all()
            try:
                while pending["result"] is None and not self.closed:
                    remaining = pending["deadline"] - self.clock()
                    if remaining <= 0:
                        break
                    self.condition.wait(min(remaining, .2))
                return pending["result"] or {"error": "browser_timeout"}
            finally:
                self.waiting.pop(command_id, None)

    def status(self):
        with self.condition:
            result = {}
            for browser in BUNDLES:
                clients = [c for c in self.clients.values() if c["browser"] == browser and self.clock() - c["seen"] < 3]
                snapshots = [c["snapshot"] for c in clients]
                result[browser] = dict(connected=bool(clients),
                    authorized=any(s.get("authorized") for s in snapshots),
                    reason=next((s.get("reason", "") for s in snapshots if s.get("focused")), ""),
                    scene=next((s.get("scene", "") for s in snapshots if s.get("focused")), ""))
            return result


class BridgeServer:
    def __init__(self, port=PORT):
        self.broker = Broker()
        broker = self.broker

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, *args):
                pass

            def do_POST(self):
                # Native hosts use URLSession/urllib, never browser fetch.
                if (self.headers.get("Origin") is not None or any(k.lower().startswith("sec-fetch-") for k in self.headers)
                        or self.headers.get("X-Mythlink-Native") != "1"
                        or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                        or self.headers.get_content_type() != "application/json"):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_MESSAGE:
                        raise ValueError("invalid_length")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict) or data.get("version") != VERSION:
                        raise ValueError("invalid_version")
                    if self.path == "/connect":
                        result = broker.connect(data.get("browser"), data.get("session"))
                    elif self.path == "/exchange":
                        result = broker.exchange(self.headers.get("Authorization", ""), data)
                    elif self.path == "/request":
                        # Desktop worker and native bridge are same-user clients.
                        result = broker.request(data.get("bundle", ""), data.get("operation"), data.get("target"), data.get("action"))
                    else:
                        self.send_error(404)
                        return
                    payload = json.dumps(result, separators=(",", ":")).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except PermissionError:
                    self.send_error(403)
                except (ValueError, TypeError, KeyError, AttributeError):
                    self.send_error(400)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=self.http.serve_forever, name="BrowserExtensionBridge", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        with self.broker.condition:
            self.broker.closed = True
            self.broker.condition.notify_all()
        self.http.shutdown()
        self.http.server_close()
