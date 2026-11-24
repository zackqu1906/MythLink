"""Authenticated local transport to the macOS input method, with no UI access.

The input method owns text transactions. This module only transports commands
and supplies fresh, verified context to the ASR worker.
"""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import threading
import time
import uuid

PROTOCOL = 1
MAX_MESSAGE = 8 * 1024 * 1024
STROKE_REPLIES = frozenset({"stroke_ready", "stroke_result", "stroke_delete_result", "stroke_cleared", "stroke_ink_cleared",
                          "stroke_correction_state", "stroke_correction_requested", "stroke_correction_key"})
SUSPENSION_REPLIES = frozenset({"input_suspended", "input_resumed"})


def default_socket_path() -> Path:
    return Path(os.environ.get("PROXIMIC_IME_SOCKET", "").strip() or
                Path.home() / "Library/Application Support/ProxiMic/ime/bridge.sock")


class IMEBridgeInUseError(BlockingIOError):
    """Another host owns the bridge; retry only after it releases the lock."""


class IMEBridge:
    """One listener per user path. Callbacks run on transport threads."""

    def __init__(self, on_message, *, path=None):
        self.path = Path(path) if path is not None else default_socket_path()
        self._callback = on_message
        self._condition = threading.Condition()
        self._outgoing = deque()
        self._connection = None
        self._listener = None
        self._lock_fd = None
        self._socket_inode = None
        self._closed = False
        self._epoch = ""
        self._state = {}
        self._context_replies = {}
        self._control_replies = {}
        from .context_preparation import InputContextPreparation
        self._context_preparation = InputContextPreparation(self)

    @property
    def connected(self):
        with self._condition:
            return self._connection is not None

    @property
    def epoch(self):
        with self._condition:
            return self._epoch

    def _notify(self, message):
        try:
            self._callback(message)
        except Exception:
            # A UI callback may already have been deleted during shutdown.
            pass

    def start(self):
        import fcntl  # Only a running POSIX bridge needs this; Windows imports the UI.

        directory = self.path.parent
        if directory.is_symlink():
            raise RuntimeError("输入法通信目录不能是符号链接")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.stat()
        if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("输入法通信目录不属于当前用户")
        if info.st_mode & 0o077:
            raise RuntimeError("输入法通信目录必须仅当前用户可访问（权限 700）")
        lock_path = directory / "bridge.lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
        self._lock_fd = fd
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError("输入法通信锁无效")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise IMEBridgeInUseError(
                    exc.errno, "输入法通道正由另一个 Mythlink 实例使用") from exc
            # Only the lock owner may remove a stale endpoint or rotate token.
            if self.path.is_symlink():
                raise RuntimeError("输入法套接字不能是符号链接")
            if self.path.exists():
                info = self.path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise RuntimeError("输入法套接字路径已被其他文件占用")
                self.path.unlink()
            token_path = directory / "bridge.token"
            token_fd = os.open(token_path, flags, 0o600)
            try:
                info = os.fstat(token_fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise RuntimeError("输入法连接凭据文件无效")
                os.fchmod(token_fd, 0o600)
                self._token = secrets.token_hex(32)
                os.ftruncate(token_fd, 0)
                os.write(token_fd, self._token.encode("ascii"))
            finally:
                os.close(token_fd)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener = listener
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            self._socket_inode = self.path.stat().st_ino
            listener.listen(4)
            listener.settimeout(0.3)
            self._spawn(self._accept, "ime-listener")
        except BaseException:
            self.close()
            raise

    def _spawn(self, target, name, *args):
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()

    @staticmethod
    def _encode(message):
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_MESSAGE:
            raise ValueError("输入法消息过长")
        return payload + b"\n"

    def send(self, message):
        with self._condition:
            if not self._connection or self._closed:
                return False
            data = {**message, "epoch": self._epoch}
            # Only consecutive non-final updates in the same transaction may
            # merge. Final, cancel and conversion preserve their exact order.
            if (data.get("type") == "update" and not data.get("final") and not data.get("error")
                    and self._outgoing and self._outgoing[-1].get("type") == "update"
                    and not self._outgoing[-1].get("final") and not self._outgoing[-1].get("error")
                    and all(self._outgoing[-1].get(key) == data.get(key) for key in ("epoch", "client_id", "utterance_id"))):
                self._outgoing[-1] = data
            elif (data.get("type") == "stroke_trace" and not data.get("finished")
                    and self._outgoing and self._outgoing[-1].get("type") == "stroke_trace"
                    and not self._outgoing[-1].get("finished")
                    and all(self._outgoing[-1].get(key) == data.get(key) for key in ("epoch", "client_id", "stroke_session"))):
                self._outgoing[-1] = data
            else:
                self._outgoing.append(data)
            self._condition.notify_all()
            return True

    def _accept(self):
        while not self._closed:
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except (OSError, AttributeError):
                break
            self._spawn(self._session, "ime-client", connection)

    def _session(self, connection):
        epoch = ""
        stream = None
        try:
            connection.settimeout(2)
            stream = connection.makefile("rb")
            raw = stream.readline(MAX_MESSAGE + 2)
            if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                return
            hello = json.loads(raw)
            if (not isinstance(hello, dict) or hello.get("type") != "hello"
                    or hello.get("protocol") != PROTOCOL or not isinstance(hello.get("token"), str)
                    or not secrets.compare_digest(hello["token"], self._token)):
                return
            with self._condition:
                if self._closed or self._connection is not None:
                    return
                epoch = uuid.uuid4().hex
                connection.sendall(self._encode({"type": "welcome", "protocol": PROTOCOL, "epoch": epoch}))
                self._connection = connection
                self._epoch = epoch
                self._state = {}
                self._outgoing.clear()
                self._condition.notify_all()
            connection.settimeout(None)
            self._spawn(self._writer, "ime-writer", connection, epoch)
            self._notify({"type": "connected", "epoch": epoch})
            while not self._closed:
                raw = stream.readline(MAX_MESSAGE + 2)
                if not raw or len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                    break
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("epoch") != epoch:
                    continue
                kind = message.get("type")
                if kind not in {"state", "finish_audio", "edit_requested", "polish_requested", "dictation_preview_requested", "dictation_preview_state", "preview_undo_caret", "interrupted", "settled", "pong", "wechat_key", "activation_recovery", "activation_trace", "clipboard_offer_state", "input_context_prepared"} | STROKE_REPLIES | SUSPENSION_REPLIES:
                    continue
                with self._condition:
                    if self._connection is not connection:
                        break
                    if kind == "input_context_prepared" and message.get("request_id") in self._control_replies:
                        self._control_replies[message["request_id"]] = dict(message)
                        self._condition.notify_all()
                    if message.get("type") == "state":
                        previous = self._state
                        same_client = previous.get("client_id") == message.get("client_id")
                        self._state = {**(previous if same_client else {}), **message}
                        request_id = message.get("request_id")
                        if request_id in self._context_replies:
                            self._context_replies[request_id] = dict(message)
                        self._condition.notify_all()
                request_id = message.get("request_id")
                # Correlated ASR context is private to asr_context(). Stroke
                # readiness, pinned voice-start, and commit replies belong to the UI state machine,
                # even though they also carry request IDs. Keep epoch checks
                # above and let that controller check the client/session token.
                ui_probe = (kind == "state" and isinstance(request_id, str)
                            and request_id.startswith(("stroke:", "start:", "resume:")))
                if (not request_id or ui_probe
                        or kind in {"wechat_key", "activation_recovery"} | STROKE_REPLIES | SUSPENSION_REPLIES):
                    self._notify(message)
        except (EOFError, OSError, ValueError, TypeError):
            pass
        finally:
            with self._condition:
                owned = self._connection is connection
                if owned:
                    self._connection = None
                    self._epoch = ""
                    self._state = {}
                    self._outgoing.clear()
                    self._condition.notify_all()
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if stream:
                stream.close()
            connection.close()
            if owned:
                self._notify({"type": "disconnected", "epoch": epoch})

    def _writer(self, connection, epoch):
        try:
            while not self._closed:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._connection is not connection or self._outgoing)
                    if self._closed or self._connection is not connection:
                        return
                    message = self._outgoing.popleft()
                connection.sendall(self._encode(message))
        except (OSError, ValueError):
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _request_state(self, *, timeout, capture_context, require_ready=True):
        """Correlate a live IMK response; cached readiness is not focus proof."""
        deadline = time.monotonic() + timeout
        with self._condition:
            epoch = self._epoch
            if not self._connection:
                return None
            previous_client = self._state.get("client_id")

            def activated():
                return (self._state.get("lifecycle_event") == "activate" and self._state.get("ready")
                        and self._state.get("client_id") and self._state["client_id"] != previous_client)

            def query(*, wake_on_activation):
                request_id = uuid.uuid4().hex
                self._context_replies[request_id] = None
                self.send({"type": "ping", "request_id": request_id, "capture_context": capture_context})
                self._condition.wait_for(lambda: self._context_replies[request_id] is not None
                                         or self._epoch != epoch or (wake_on_activation and activated()),
                                         max(0, deadline - time.monotonic()))
                return self._context_replies.pop(request_id)

            state = query(wake_on_activation=not capture_context)
            if (state is None and not capture_context and self._epoch == epoch
                    and activated() and time.monotonic() < deadline):
                # IMK may drop a ping during activation. Query the newly ready client
                # once, with a new request ID and the original deadline.
                state = query(wake_on_activation=False)
            if not state or self._epoch != epoch or (require_ready and not state.get("ready")):
                return None
            if any(self._state.get(key) != state.get(key) for key in ("client_id", "utterance_id")):
                return None
            return state

    def focus_state(self, *, timeout=0.2):
        """Fresh selection metadata, including an explicitly inactive client."""
        state = self._request_state(timeout=timeout, capture_context=False, require_ready=False)
        if state is None:
            return None
        return {key: state.get(key) for key in
                ("epoch", "client_id", "utterance_id", "application", "ready", "selection", "context_key_supported")}

    def refresh_input_context(self, origin, post_key, *, timeout=.12):
        """Arm consumption before posting a key; await receipt or new activation.

        Runs off the UI thread. Failure is not proof that the editor
        cannot dictate; the caller must re-probe and retain unknown-range support.
        """
        deadline = time.monotonic() + min(timeout, .12)
        request_id = uuid.uuid4().hex
        tag = 0x504D00000000 | secrets.randbits(32)
        with self._condition:
            epoch = self._epoch
            previous_client = self._state.get('client_id')
            if not self._connection or self._closed or timeout <= 0:
                return False
            self._context_replies[request_id] = None
            self._control_replies[request_id] = None
            self.send(dict(type="prepare_input_context", request_id=request_id, event_tag=tag,
                           application=origin[0], foreground_pid=origin[1]))
        try:
            with self._condition:
                self._condition.wait_for(lambda: self._control_replies.get(request_id) is not None
                                         or self._epoch != epoch or self._closed,
                                         max(0, deadline - time.monotonic()))
                armed = self._control_replies.get(request_id)
                if (not armed or not armed.get("armed") or self._epoch != epoch or self._closed
                        or time.monotonic() >= deadline):
                    return False
            # Never hold the transport lock across TIS main-queue reads or
            # event delivery. The native event may immediately reply/reenter.
            post_key(tag, timeout=max(0, deadline - time.monotonic()))
            with self._condition:
                def receipt():
                    correlated = self._context_replies.get(request_id)
                    if correlated is not None:
                        return correlated
                    # Some clients activate their text context on a function
                    # key without forwarding that key to IMK handle(). A newly
                    # prepared client is also a receipt; repeated cached state
                    # from the old client is not. Focus is re-probed by caller.
                    state = self._state
                    if (state.get('client_id') and state['client_id'] != previous_client
                            and state.get('ready') and state.get('application') == origin[0]):
                        return state
                    return None
                self._condition.wait_for(lambda: receipt() is not None
                                         or self._epoch != epoch or self._closed,
                                         max(0, deadline - time.monotonic()))
                state = receipt()
                return bool(state and self._epoch == epoch and state.get("ready")
                            and state.get("application") == origin[0] and state.get("client_id")
                            and all(self._state.get(k) == state.get(k) for k in ("client_id", "utterance_id")))
        finally:
            with self._condition:
                self._context_replies.pop(request_id, None)
                self._control_replies.pop(request_id, None)
                if self._epoch == epoch:
                    self.send(dict(type="cancel_input_context", request_id=request_id))

    def prepare_focus(self, origin, *, foreground, timeout=.35, known_state=None):
        """Wake the input client while acquiring focus for a voice gesture."""
        return self._context_preparation.prepare(origin, foreground=foreground, timeout=timeout,
                                                 known_state=known_state)

    def gesture_target(self, *, timeout=0.2):
        """Return only live client identity, never editor contents, for routing."""
        from .focus_probe import valid_selection
        state = self.focus_state(timeout=timeout)
        if (not state or not state.get("ready") or not state.get("client_id")
                or not valid_selection(state.get("selection"))):
            return None
        return tuple(str(state.get(key, "")) for key in
                     ("epoch", "client_id", "utterance_id", "application"))

    def asr_context(self, *, timeout=0.35, max_chars=1600, foreground=None):
        """Ask the active client for fresh context; never reuse another field."""
        unavailable = {"status": "unavailable", "source": "input_method", "reason": "input_method_not_ready"}
        origin = foreground() if foreground is not None else None
        with self._condition:
            epoch = self._epoch
            target = tuple(self._state.get(k) for k in ("client_id", "utterance_id", "application"))
        if foreground is not None and (not origin or not target[0] or origin[0] != target[2]):
            return {**unavailable, "reason": "input_context_target_changed"}
        state = self._request_state(timeout=timeout, capture_context=True)
        if foreground is not None and (foreground() != origin or not state or state.get("epoch") != epoch
                or tuple(state.get(k) for k in ("client_id", "utterance_id", "application")) != target):
            return {**unavailable, "reason": "input_context_target_changed"}
        if (not state or not state.get("context_complete")
                or not isinstance(state.get("original"), str)):
            return unavailable
        epoch = state["epoch"]
        original = state["original"]
        result = {"status": "captured" if original else "empty", "source": "input_method",
                  "source_char_count": len(original), "application": str(state.get("application", "")),
                  "target_key": f"{epoch}:{state.get('client_id', '')}",
                  "read_method": state.get("read_method", "IMKTextInput"),
                  "context_complete": True}
        if original:
            result["text"] = original[-max_chars:]
        else:
            result["reason"] = "empty_focused_text"
        return result

    def close(self):
        with self._condition:
            self._closed = True
            connection = self._connection
            self._condition.notify_all()
        if connection:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._listener:
            self._listener.close()
            self._listener = None
        if self._socket_inode is not None:
            try:
                if self.path.lstat().st_ino == self._socket_inode:
                    self.path.unlink()
            except FileNotFoundError:
                pass
            self._socket_inode = None
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
