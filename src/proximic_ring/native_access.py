"""Restartable macOS control channel. The UI/audio host never needs to restart.

Only the same executable is launched, over private inherited pipes. A denied
worker is discarded because CoreGraphics can cache its negative preflight for
the lifetime of that process. No command with a possible side effect is retried.
"""
from __future__ import annotations

import atexit
from dataclasses import replace
import json
import os
import select
import subprocess
import sys
import threading
import time

from .mac_permissions import MacPermissionError, PermissionState
from .scene_diagnostics import SceneActionError, safe_data


class NativeAccessChannel:
    def __init__(self, *, pass_fds=()):
        self._pass_fds = tuple(pass_fds)
        self._lock = threading.Lock()
        self._process = None
        self._buffer = b""
        self._sequence = 0
        self._diagnostics = threading.local()

    @property
    def last_diagnostic(self):
        return dict(getattr(self._diagnostics, "value", {}))

    def _start(self):
        if self._process is not None and self._process.poll() is None:
            return
        self._stop()
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--native-access-worker"]
        else:
            args = [sys.executable, "-B", "-m", "proximic_ring.native_access_worker"]
        self._process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, bufsize=0,
                                         pass_fds=self._pass_fds,
                                         env={**os.environ, 'PYTHONFAULTHANDLER': '1'})
        from .diagnostic_log import RotatingDiagnosticLog, diagnostic_log_path
        writer = RotatingDiagnosticLog(diagnostic_log_path())
        process = self._process
        def collect_stderr():
            try:
                # Bound reads even if a failing native library omits newlines.
                for data in iter(lambda: process.stderr.readline(65536), b''):
                    writer.record(data.decode('utf-8', errors='replace').rstrip(),
                                  source=f'native.stderr.{process.pid}', level='ERROR')
            except (OSError, ValueError):
                pass
        self._stderr_reader = threading.Thread(target=collect_stderr, name='NativeDiagnostics', daemon=True)
        self._stderr_reader.start()

    def _stop(self):
        process, self._process = self._process, None
        self._buffer = b""
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.3)
        finally:
            process.stdin.close()
            process.stdout.close()
            if getattr(process, 'stderr', None) is not None:
                reader = getattr(self, '_stderr_reader', None)
                if reader is not None:
                    reader.join(timeout=.3)
                process.stderr.close()

    def close(self):
        with self._lock:
            self._stop()

    def call(self, operation, **params):
        with self._lock:
            self._diagnostics.value = {"operation": operation, "reason": "channel_start_failed"}
            self._start()
            self._sequence += 1
            sequence = self._sequence
            message = json.dumps({"id": sequence, "operation": operation, **params}).encode() + b"\n"
            try:
                self._process.stdin.write(message)
                # Metadata reads have their own channel and may traverse large
                # menus. Keep the short deadline for foreground key delivery.
                menu_read = operation == "application_menu"
                deadline = time.monotonic() + (8.0 if menu_read else 2.0)
                while b"\n" not in self._buffer:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([self._process.stdout], [], [], remaining)[0]:
                        raise TimeoutError("按键通道响应超时，请重新操作")
                    chunk = os.read(self._process.stdout.fileno(), 65536)
                    if not chunk:
                        raise RuntimeError("按键通道已断开，请重新操作")
                    self._buffer += chunk
                    if len(self._buffer) > (1048576 if menu_read else 262144):
                        raise ValueError("按键通道响应异常")
                line, self._buffer = self._buffer.split(b"\n", 1)
                reply = json.loads(line)
                if not isinstance(reply, dict):
                    raise ValueError("按键通道响应格式异常")
                self._diagnostics.value = safe_data(reply.get("diagnostic", {}))
                if reply.get("id") != sequence:
                    raise ValueError("按键通道响应已过期，请重新操作")
            except Exception as exc:
                self._diagnostics.value = {"operation": operation, "reason": "channel_timeout" if isinstance(exc, TimeoutError)
                    else "channel_protocol_error" if isinstance(exc, (ValueError, json.JSONDecodeError)) else "channel_disconnected",
                    "error_type": type(exc).__name__, "delivery": "unknown" if operation in {
                        "shortcut", "touchpad_apply"} else "not_sent"}
                # Delivery may already have happened. Never retry a write.
                self._stop()
                raise
            if "permissions" in reply:
                state = PermissionState(**reply["permissions"])
                # The next probe/action starts a fresh process and thus can
                # observe a grant while the main application keeps running.
                if not state.ready:
                    self._stop()
                if reply.get("error"):
                    raise MacPermissionError(state)
                return state
            if reply.get("error"):
                if reply.get("diagnostic", {}).get("reason"):
                    raise SceneActionError(reply["diagnostic"]["reason"], reply["diagnostic"])
                raise RuntimeError(reply["error"])
            return reply.get("result")

    def permissions(self):
        state = self.call("status")
        return replace(state, control_channel="worker")


_channel = NativeAccessChannel()
atexit.register(_channel.close)


def native_access():
    return _channel


def read_control_permission_state():
    return _channel.permissions()
