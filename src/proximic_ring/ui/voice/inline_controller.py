"""Qt-facing transport for native input-method transactions.

All text, composition, geometry and undo decisions belong to the Swift helper.
Native keys handle sentence undo/caret positioning; WeChat has a separate full-field override.
"""
from __future__ import annotations

from pathlib import Path
import os
import sys
import threading
import time
import uuid

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ...desktop.desktop_target import DesktopTargetRef
from ...voice.ime.ime_bridge import IMEBridge, IMEBridgeInUseError
from ..permissions.mac_permissions_controller import MacPermissionsController
from .input_source_activation import INPUT_ACTIVATION_TIMEOUT_MS, InputSourceActivation, warm_input_method


class InlineInputController(QObject):
    changed = Signal()
    began = Signal()
    diagnostic = Signal(object)
    _received = Signal(object)
    _installationFinished = Signal(str)
    editIntentConfirmed = Signal(int)
    editRequested = Signal(object, str, str, object)
    polishRequested = Signal(object, str, object)
    audioEndRequested = Signal()
    interrupted = Signal()
    settled = Signal(str, str)
    transactionSettled = Signal(object, str, str, str, bool)
    actionRequested = Signal(str)
    strokeCommitted = Signal(str, bool, str)
    strokeInvalidated = Signal()
    strokeEvent = Signal(object)

    def __init__(self, parent=None, *, enabled=None, bridge_factory=None, source_switch_factory=None):
        super().__init__(parent)
        self._enabled = sys.platform == "darwin" if enabled is None else bool(enabled)
        self._permissions = MacPermissionsController(self, enabled=self._enabled)
        self._permissions.diagnostic.connect(self.diagnostic.emit)
        self._closed = False
        self.stroke_mode = False
        self.stroke_target = None  # (transport epoch, native client, stroke session)
        self.stroke_context = None  # Verified text before the caret after the last stroke commit.
        self._view = {"phase": "idle", "ready": False}
        self._epoch = ""
        self._client_id = ""
        self._utterance_id = ""
        self._session_id = 0
        self._seq = 0
        self._final_sent = False
        self._first_text_update_sent = False
        self._last_state_diagnostic = None
        self._requested = set()
        self._settled = set()
        self._compatibility_requests = set()
        self._shortcut = "F8"
        from ...gestures.gesture_settings import GestureBindings
        self._gesture_hints = GestureBindings().as_dict()
        self._hud_scale_percent = 80
        self._multi_undo_enabled = False
        self._utterance_sessions = {}
        self._edit_intents = {}
        self.settled_session_id = None
        self._bridge = None
        self._bridge_factory = bridge_factory or IMEBridge
        self._transport_busy = False
        self._transport_retry_delay = 1000
        self._transport_retry_timer = QTimer(self)
        self._transport_retry_timer.setSingleShot(True)
        self._transport_retry_timer.timeout.connect(self._start_bridge)
        self._stroke_request = None
        self._stroke_timer = QTimer(self)
        self._stroke_timer.setSingleShot(True)
        self._stroke_timer.timeout.connect(self._stroke_timed_out)
        self._transport_error = ""
        self._installing = False
        self._input_method_version = dict(state="unknown", text="版本待确认", current=False, detail="")
        self._installation_message = "使用 Mythlink 时也可直接更新；安装后点入文本框并双击 tap 即可使用。"
        self._installationFinished.connect(self._installation_finished)
        self._reply_stage = ""
        self._source_switch_factory = source_switch_factory or InputSourceActivation
        self._warm_enabled = bridge_factory is None
        self._source_activation = None
        self._source_pending = False
        self._focus_recovery_attempted = False
        self._startup_probe = None
        self._startup_deadline = 0.0
        self._startup_clients = set()
        self._pending_gesture_focus = None
        self._startup_focus_target = None
        self._startup_focus_request = ""
        self._activation_timer = QTimer(self)
        self._activation_timer.setInterval(150)
        self._activation_timer.timeout.connect(self._refresh_activation)
        self._begin_origin = None
        self._sentence_origin = None
        self._input_preparation = None
        self._startup_buffering = False
        self._pending_update = None
        self._pending_finish = False
        self._finish_as_dictation = False
        self._action_pending = None
        self._action_timer = QTimer(self)
        self._action_timer.setSingleShot(True)
        self._action_timer.timeout.connect(self._clear_action_pending)
        self._reply_timer = QTimer(self)
        self._reply_timer.setSingleShot(True)
        self._reply_timer.timeout.connect(self._reply_timed_out)
        self._received.connect(self._accept)
        self._start_bridge()

    def _start_bridge(self):
        if not self._enabled or self._closed or self._bridge is not None:
            return
        self._transport_retry_timer.stop()
        previous_error = self._transport_error
        bridge = None
        try:
            # A failed start closes the transport permanently. Each retry
            # needs a fresh instance and must acquire the same exclusive lock.
            bridge = self._bridge_factory(self._received.emit)
            bridge.start()
        except Exception as exc:
            if bridge is not None:
                bridge.close()
            self._transport_busy = isinstance(exc, IMEBridgeInUseError)
            self._transport_error = str(exc)
            if self._transport_busy:
                self._transport_retry_timer.start(self._transport_retry_delay)
                self._transport_retry_delay = min(self._transport_retry_delay * 2, 5000)
            if self._transport_error != previous_error:
                self._diagnose("transport_unavailable", error=self._transport_error,
                               reason="bridge_in_use" if self._transport_busy else "bridge_start_failed")
                self.changed.emit()
            return
        self._bridge = bridge
        self._transport_error = ""
        self._transport_busy = False
        self._transport_retry_delay = 1000
        if previous_error:
            self._diagnose("transport_recovered")
            self.changed.emit()
        if self._warm_enabled:
            QTimer.singleShot(0, self._warm_input_method)

    def _warm_input_method(self):
        if not self._closed and not self._installing and self._enabled and self._warm_enabled:
            warm_input_method()

    @Property(bool, constant=True)
    def enabled(self):
        return self._enabled

    @Property(QObject, constant=True)
    def permissions(self):
        return self._permissions

    @Property(bool, notify=changed)
    def installing(self):
        return self._installing

    @Property(str, notify=changed)
    def installationMessage(self):
        return self._installation_message

    @Property("QVariantMap", notify=changed)
    def inputMethodVersion(self):
        return self._input_method_version

    @Slot()
    def refreshInputMethodVersion(self):
        if not self._enabled or self._closed or self._installing:
            return
        from ...voice.ime.input_method_install import InputMethodInstaller
        state = InputMethodInstaller().version_status()
        if state != self._input_method_version:
            self._input_method_version = state
            self.changed.emit()

    @Slot()
    def installInputMethod(self):
        if not self._enabled or self._installing or self._closed:
            self._diagnose("installation_blocked", reason=(
                "disabled" if not self._enabled else "already_installing" if self._installing else "closed"))
            return
        if (self.active or self._begin_origin is not None or self._input_preparation
                or self.stroke_mode or self._stroke_request or self._view.get("awaiting_readback")):
            self._installation_message = "请先结束当前语音或笔画输入，再更新输入法；无需手动切换输入法。"
            self._diagnose("installation_blocked", reason="input_session_active")
            self.changed.emit()
            return
        if getattr(getattr(self.parent(), "_app_gestures", None), "_source_busy", False):
            self._installation_message = "正在切换输入法，请稍后再点击更新。"
            self._diagnose("installation_blocked", reason="source_switch_busy")
            self.changed.emit()
            return
        self._installing = True
        self._installation_message = "正在更新输入法，使用中的 Mythlink 会临时切换到系统键盘…"
        self._diagnose("installation_started", reason="user_request")
        self.changed.emit()

        def work():
            try:
                from ...voice.ime.input_method_install import InputMethodInstaller
                result = InputMethodInstaller().install()
                if result.get("input_source_restore_error"):
                    message = "已安装并启用，但未能自动恢复输入法。请点入文本框后双击 tap，或从菜单栏选择 Mythlink。"
                elif result.get("input_source_restored"):
                    message = "已安装并启用，已恢复 Mythlink 输入法。"
                elif result.get("input_source_switched"):
                    message = "已安装并启用；应用或输入法已变化，保留当前选择。点入文本框后可双击 tap 切入。"
                else:
                    message = "已安装并启用。请点入目标文本框并双击 tap，程序会自动切入语音输入法。"
            except Exception as exc:
                message = "安装未完成：" + str(exc)
            try:
                self._installationFinished.emit(message)
            except RuntimeError:
                pass  # The window may have closed; the atomic installation is complete.

        # Finish the short atomic installation even if the app is closed.
        threading.Thread(target=work, name="ProxiMicIMEInstaller", daemon=False).start()

    @Slot(str)
    def _installation_finished(self, message):
        self._installing = False
        self._installation_message = message
        self._diagnose("installation_finished", reason=message)
        self.refreshInputMethodVersion()
        self.changed.emit()

    @Slot()
    def openInputSettings(self):
        if sys.platform == "darwin":
            QDesktopServices.openUrl(QUrl("x-apple.systempreferences:com.apple.Keyboard-Settings.extension"))

    @Slot()
    def openAccessibilitySettings(self):
        self._permissions.openSettings()

    @Property(bool, notify=changed)
    def ready(self):
        return bool(self._epoch and self._view.get("ready"))

    @Property(str, notify=changed)
    def connectionStatus(self):
        if self._transport_busy:
            return "输入法通道被另一个 Mythlink 实例占用 · 关闭另一个实例后将自动连接"
        if self._transport_error:
            return "输入法连接失败：" + self._transport_error
        if self._epoch:
            return "输入法已连接 · 已就绪" if self.ready else "输入法已连接 · 点入文本框后双击 tap 自动开始"
        installed = [Path.home() / "Library/Input Methods/ProxiMicInput.app", Path("/Library/Input Methods/ProxiMicInput.app")]
        if not any(path.exists() for path in installed):
            return "语音输入法未安装"
        return "语音输入法待连接 · 点入文本框后双击 tap 自动切入"

    @Property(bool, notify=changed)
    def active(self):
        return (self.ready or self._begin_origin is not None) and self._view.get("phase") in {"starting", "listening", "finishing", "dictated", "editing", "edited", "polishing"}

    def _awaiting_settlement(self):
        # Committed text stays active for undo/conversion. Losing its client
        # later does not turn a successful input into a failed transaction.
        return bool(self._reply_stage or self._action_pending or self._view.get("awaiting_readback")
                    or self._view.get("phase") in {"starting", "listening", "finishing", "editing", "polishing"})

    @Property(bool, notify=changed)
    def visible(self):
        return self.active and bool(self._view.get("visible", True))

    @Property(bool, notify=changed)
    def canUndo(self):
        return bool(self.active or (self.ready and self._multi_undo_enabled
                    and self._view.get("phase") == "undone"
                    and self._view.get("undo_history_depth", 0) > 0))

    @Property(str, notify=changed)
    def status(self):
        return {"starting": "正在准备", "listening": "听写中", "finishing": "编辑 · 正在收尾" if self._view.get("edit_requested") else "正在定稿", "dictated": "已润色" if self._view.get("polished") else "已听写", "polishing": "正在润色", "editing": "正在编辑", "edited": "已编辑", "undone": "已撤销", "interrupted": "已停止", "error": "输入停止"}.get(self._view.get("phase"), self.connectionStatus)

    @Property(str, notify=changed)
    def error(self):
        return str(self._view.get("error", "") or self._transport_error)

    @Property(bool, notify=changed)
    def canConvert(self):
        return bool(self.active and self._view.get("can_convert", False) and
                    self._view.get("phase") in {"listening", "finishing"})

    @Property(bool, notify=changed)
    def canRequestConversion(self):
        # A manual request must reach the IME so an unavailable capability can
        # produce a specific explanation instead of silently ignoring a swipe.
        return bool(self.active and self._view.get("has_composition") and
                    not self._view.get("edit_requested") and
                    self._view.get("phase") in {"listening", "finishing"})

    @Property(str, notify=changed)
    def cancelLabel(self):
        if self._view.get("phase") == "edited" or self._view.get("polish_restore_available"):
            return "还原听写"
        if self._view.get("phase") in {"starting", "listening", "finishing", "editing", "polishing"}:
            return "取消"
        return "撤销"

    def target_reference(self):
        if not self.ready or not self._client_id:
            return None
        return DesktopTargetRef(0, 0, str(self._view.get("application", "")),
                                process_name=str(self._view.get("application", "")),
                                accessibility_id=f"ime:{self._epoch}:{self._client_id}")

    def history_application(self):
        """Resolve a display owner without changing the IME's target identity."""
        if not self.ready or not self._client_id:
            return ""
        application = str(self._view.get("application", "") or "")
        origin = self._sentence_origin
        if origin and origin[0] != application:
            # A cached ready state can still belong to the preceding app while
            # BEGIN is activating the current target. Wait for its acknowledgment.
            return ""
        if origin and origin[0] == application:
            # Source-launched Qt has no bundle ID. Every editor in our process,
            # including Agent drafts, belongs to Mythlink rather than Python.
            if origin[1] == os.getpid():
                return "com.proximic.voice"
            if not application and sys.platform == "darwin":
                try:
                    from AppKit import NSRunningApplication
                    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(origin[1])
                    return str(app.bundleIdentifier() or app.localizedName() or "") if app else ""
                except Exception:
                    return ""
        return application

    def context_provider(self):
        if self._bridge is None:
            return {"status": "unavailable", "source": "input_method", "reason": "bridge_unavailable"}
        return self._bridge.asr_context()

    def capture_gesture_target(self):
        """Use the active IMK client when an app does not expose AX focus."""
        if (not self.enabled or not self.ready or not self._bridge
                or not self._client_id or self.stroke_mode):
            return None
        expected = (self._epoch, self._client_id, self._utterance_id,
                    str(self._view.get("application", "")))
        front = self._foreground_identity()
        if front is None or front[0] != expected[3]:
            return None
        probe = getattr(self._bridge, "gesture_target", None)
        if not callable(probe):
            return None
        try:
            if probe() != expected:
                return None
        except Exception:
            return None
        target = (*expected, front[1])
        return target if self.same_gesture_target(target) else None

    def capture_start_focus(self):
        """Shared gesture focus: activate if needed, then use live IMK selection."""
        from ...voice.ime.focus_probe import probe_input_focus, InputFocus
        from ...voice.ime.system_input_source import activate_voice_input_source
        if (not self.enabled or self._closed or self._installing or self.stroke_mode
                or not callable(getattr(self._bridge, "focus_state", None))):
            return InputFocus("unavailable", reason="input_method_not_ready")
        return probe_input_focus(self._bridge, self._foreground_identity, activate_voice_input_source)

    def same_input_focus(self, proof):
        from ...voice.ime.focus_probe import same_input_focus
        return bool(self.enabled and not self._closed and not self.stroke_mode
                    and same_input_focus(self._bridge, self._foreground_identity, proof))

    def pin_start_focus(self, target, created):
        self._pending_gesture_focus = (target, created) if target is not None else None

    def same_gesture_target(self, target):
        if target and len(target) == 6:
            from ...voice.ime.focus_probe import InputFocus
            if (target[2] or self._utterance_id) and tuple(target[:4]) != (
                    self._epoch, self._client_id, self._utterance_id, str(self._view.get("application", ""))):
                return False
            return self.same_input_focus(InputFocus("text", target))
        matches = bool(target and self.enabled and self.ready and not self.stroke_mode
                    and tuple(target[:4]) == (self._epoch, self._client_id, self._utterance_id,
                                             str(self._view.get("application", "")))
                    and self._foreground_identity() == (target[3], target[4]))
        if not matches or target[2]:
            return matches
        # WeChat can reuse the same client after focus leaves its editor.
        # Before a sentence exists, recheck selection instead of cached ready.
        return bool(self._bridge and self._bridge.gesture_target() == tuple(target[:4])
                    and self._foreground_identity() == (target[3], target[4]))

    def set_gesture_hints(self, bindings):
        """Publish display metadata without touching shortcuts or voice state."""
        snapshot = {action: list(names) for action, names in bindings.items()}
        if snapshot == self._gesture_hints:
            return
        self._gesture_hints = snapshot
        self._publish_gesture_hints()

    def _publish_gesture_hints(self):
        if self._bridge is not None and not self._closed:
            self._bridge.send({"type": "configure_hud", "gesture_hints": self._gesture_hints,
                               "scale_percent": self._hud_scale_percent})

    def set_hud_scale(self, percent: int):
        percent = max(80, min(140, int(percent)))
        if percent != self._hud_scale_percent:
            self._hud_scale_percent = percent
            self._publish_gesture_hints()

    def configure(self, shortcut, *, multi_undo_enabled=None):
        self._shortcut = str(shortcut or "F8")
        if multi_undo_enabled is not None:
            self._multi_undo_enabled = bool(multi_undo_enabled)
        if self._bridge is not None:
            self._bridge.send({"type": "configure", "mode_switch_shortcut": self._shortcut,
                               "cancel_shortcut": "Esc", "multi_undo_enabled": self._multi_undo_enabled})
        self.changed.emit()

    def _send(self, operation, **fields):
        if not self._bridge or not self._epoch or not self._client_id:
            return False
        self._seq += 1
        return self._bridge.send({"type": operation, "client_id": self._client_id,
                                  "utterance_id": self._utterance_id, "seq": self._seq, **fields})

    def show_notice(self, text):
        """Presentation-only IME feedback, without altering any input transaction."""
        if self._closed or not self._bridge or not self._epoch or not str(text).strip():
            return False
        import os
        origin = self._foreground_identity()
        return self._bridge.send({'type': 'input_notice', 'text': str(text)[:512],
            'foreground_pid': origin[1] if origin else os.getpid(),
            'client_id': self._client_id, 'utterance_id': self._utterance_id})

    @Slot(object, object)
    def notify_hotwords_learned(self, key, words):
        current = (self._epoch, self._client_id, self._utterance_id, self._view.get("revision", 0))
        if (not self._closed and tuple(key) == current and self._view.get("phase") == "edited"
                and not self._view.get("awaiting_readback") and not self._view.get("error") and words):
            self._send("hotwords_learned", revision=current[-1], words=list(words)[:8])

    def stroke_update(self, code, candidates, selected, prediction=False):
        if self._installing:
            return False
        if self.stroke_target is not None:
            return self.send_stroke("stroke_update", code=str(code)[:64], candidates=list(candidates)[:5],
                                    selected=int(selected), prediction=bool(prediction))
        if (not self.ready or not self._bridge or self._begin_origin is not None
                or self._view.get("phase") in {"starting", "listening", "finishing", "editing", "polishing"}):
            return False
        return self._bridge.send({"type": "stroke_update", "client_id": self._client_id,
                                  "code": str(code)[:64], "candidates": list(candidates)[:5],
                                  "selected": int(selected)})

    def stroke_clear(self):
        if self.stroke_target is not None:
            self.send_stroke("stroke_clear")
        elif self._bridge and self._client_id:
            self._bridge.send({"type": "stroke_clear", "client_id": self._client_id})
        self._stroke_request = None
        self._stroke_timer.stop()

    def stroke_commit(self, char):
        if (self._installing or (self.stroke_target is None and not self.ready) or not self._bridge or self._stroke_request or self._begin_origin is not None
                or self._view.get("phase") in {"starting", "listening", "finishing", "editing", "polishing"}):
            return False
        request = uuid.uuid4().hex
        client = self.stroke_target[1] if self.stroke_target else self._client_id
        sent = (self.send_stroke("stroke_commit", request_id=request, text=str(char)) if self.stroke_target else
                self._bridge.send({"type": "stroke_commit", "client_id": client,
                                   "request_id": request, "text": str(char)}))
        if sent:
            self._stroke_request = (request, str(char), client)
            self._stroke_timer.start(5000)
        return sent

    def send_stroke(self, kind, **fields):
        target = self.stroke_target
        if self._installing or not target or target[0] != self._epoch or not self._bridge:
            return False
        return self._bridge.send({"type": kind, "client_id": target[1], "stroke_session": target[2], **fields})

    def finish_for_stroke(self):
        """Used only when a voice sentence is already in progress."""
        if self._startup_buffering:
            if not (self._pending_update and self._pending_update[0]):
                self.reset()
                self.interrupted.emit()
            else:
                self._finish_as_dictation = True
                self._pending_finish = True
            return
        self._send("finish_dictation")

    def _stroke_timed_out(self):
        if self._stroke_request is not None:
            _, char, _ = self._stroke_request
            self._stroke_request = None
            self.strokeCommitted.emit(char, False, "输入法未确认本次输入，请检查目标文本框")

    def _diagnose(self, kind, *, characters=None, error=None, reason=None):
        """Publish an explicit metadata allowlist, never editor or ASR text."""
        readback = self._view.get("readback_diagnostics", {})
        safe_readback = {}
        if isinstance(readback, dict):
            for key in ("expected_characters", "observed_characters"):
                if type(readback.get(key)) is int:
                    safe_readback[key] = readback[key]
            if type(readback.get("text_matches")) is bool:
                safe_readback["text_matches"] = readback["text_matches"]
            if type(readback.get("key_acknowledged")) is bool:
                safe_readback["key_acknowledged"] = readback["key_acknowledged"]
            if readback.get("operation_stage") in {"context_whole", "context_caret", "capture", "written",
                    "sentence_selected", "sentence_written", "native_caret", "native_restore_caret", "verify"}:
                safe_readback["operation_stage"] = readback["operation_stage"]
            for key in ("operation_ms", "post_write_ms", "context_prepare_ms"):
                if type(readback.get(key)) in (int, float):
                    safe_readback[key] = readback[key]
            if readback.get("ack_source") in {"text", "selection_collapse", "native_selection", "committed_range", "composition_end"}:
                safe_readback["ack_source"] = readback["ack_source"]
            for key in ("expected_selection", "observed_selection", "expected_marked", "observed_marked"):
                value = readback.get(key)
                if isinstance(value, list) and len(value) == 2 and all(type(item) is int for item in value):
                    safe_readback[key] = value
        client_read = self._view.get("client_read_diagnostics", {})
        safe_client_read = {}
        if isinstance(client_read, dict):
            if client_read.get("query") in {"snapshot", "probe", "range"}:
                safe_client_read["query"] = client_read["query"]
            for key in ("document_length", "returned_characters", "context_returned_characters", "context_queries", "context_characters"):
                if type(client_read.get(key)) is int:
                    safe_client_read[key] = client_read[key]
            for key in ("document_access", "text_queried", "marked_text_queried", "composition_geometry_only"):
                if type(client_read.get(key)) is bool:
                    safe_client_read[key] = client_read[key]
            for key in ("selection", "reported_selection", "marked_range", "actual_range", "requested_range", "context_requested_range", "context_actual_range", "context_readable_range"):
                value = client_read.get(key)
                if isinstance(value, list) and len(value) == 2 and all(type(item) is int for item in value):
                    safe_client_read[key] = value
        handoff = self._view.get("handoff_diagnostics", {})
        safe_handoff = {}
        if isinstance(handoff, dict):
            if handoff.get("action") in {"waiting", "commit_previous", "reset_stale", "ready", "failed", "cancelled"}:
                safe_handoff["action"] = handoff["action"]
            if handoff.get("write_action") in {"", "commit_previous", "reset_stale"}:
                safe_handoff["write_action"] = handoff["write_action"]
            for key in ("writes", "marked_characters"):
                if type(handoff.get(key)) is int:
                    safe_handoff[key] = handoff[key]
            for key in ("document_access", "text_queried"):
                if type(handoff.get(key)) is bool:
                    safe_handoff[key] = handoff[key]
            for key in ("selection", "marked_range"):
                value = handoff.get(key)
                if isinstance(value, list) and len(value) == 2 and all(type(item) is int for item in value):
                    safe_handoff[key] = value
        event = {
            "type": kind,
            "phase": str(self._view.get("phase", "idle")),
            "ready": self.ready,
            "application": str(self._view.get("application", "")),
            "client_id": self._client_id,
            "utterance_id": self._utterance_id,
            "revision": self._view.get("revision", 0),
            "characters": len(str(self._view.get("raw", ""))) if characters is None else characters,
            "error": self.error if error is None else str(error),
            "reason": str(self._view.get("reason", "")) if reason is None else str(reason),
            "lifecycle_event": str(self._view.get("lifecycle_event", "")),
            "has_composition": bool(self._view.get("has_composition", False)),
            "awaiting_readback": bool(self._view.get("awaiting_readback", False)),
            "readback_stage": str(self._view.get("readback_stage", "")),
            "readback_diagnostics": safe_readback,
            "client_read_diagnostics": safe_client_read,
            "handoff_state": str(self._view.get("handoff_state", "")) if self._view.get("handoff_state", "") in {"pending", "ready", "failed"} else "",
            "handoff_diagnostics": safe_handoff,
        }
        if kind == "state":
            if event == self._last_state_diagnostic:
                return
            self._last_state_diagnostic = event.copy()
        self.diagnostic.emit(event)

    def _foreground_identity(self):
        if sys.platform != "darwin":
            return None
        try:
            from ...platforms.macos.mac_workspace import frontmost_application
            app = frontmost_application()
            if app and int(app.processIdentifier()) > 0:
                # A terminal-launched Qt host has no bundle ID. Its PID is
                # still a real foreground identity, shared by voice/strokes.
                return (str(app.bundleIdentifier() or ""), int(app.processIdentifier()))
        except Exception:
            pass
        return None

    def _startup_context(self):
        return (self._epoch, self._client_id, self._utterance_id, self._begin_origin,
                self._source_activation, self._source_pending, self._reply_stage, self._closed)

    def _startup_focus_matches(self, *, allow_source=True):
        """Return None if callbacks advanced/replaced startup during the read."""
        context = self._startup_context()
        origin = self._begin_origin
        foreground = self._foreground_identity()
        if context != self._startup_context():
            return None
        if foreground == origin:
            return True
        source = self._source_activation
        matches = bool(allow_source and self._source_pending and source is not None
                       and getattr(source, "owns_foreground", lambda: False)())
        if context != self._startup_context():
            return None
        if not matches:
            self.diagnostic.emit({"type": "startup_focus_changed",
                                  "expected_application": origin[0] if origin else "",
                                  "expected_pid": origin[1] if origin else 0,
                                  "actual_application": foreground[0] if foreground else "",
                                  "actual_pid": foreground[1] if foreground else 0,
                                  "utterance_id": self._utterance_id})
        return matches if context == self._startup_context() else None

    def _recover_selected_source(self):
        if self._focus_recovery_attempted or self._source_activation is None:
            return
        self._focus_recovery_attempted = True
        self._source_activation.cancel()
        source = self._source_switch_factory(self)
        self._source_activation = source
        self._source_pending = True
        source.event.connect(lambda message: self._source_event(source, message))
        self._diagnose("auto_input_source", reason="refreshing_input_context")
        source.recover(self._begin_origin)

    def _refresh_activation(self):
        if self._closed or self._begin_origin is None:
            self._activation_timer.stop()
            return
        focus_matches = self._startup_focus_matches()
        if focus_matches is None:
            return
        if not focus_matches:
            self._transport_failed("已切换应用，本句已取消")
            return
        if time.monotonic() >= self._startup_deadline:
            self._reply_timed_out()
            return
        if self._source_pending:
            return
        if not self._bridge or not self._epoch:
            return
        if self._reply_stage == "begin":
            # Recover a delayed/lost BEGIN acknowledgment without replaying the
            # command or resetting the native input source.
            self._bridge.send({"type": "ping"})
            return
        if self._reply_stage != "ready":
            return
        # Observe readiness only. Never switch to ABC and back: that creates
        # another lifecycle transition which can outlive this Tap request.
        if self._startup_focus_target is not None:
            self._request_start_focus()
        else:
            self._bridge.send({"type": "ping"})

    def _request_start_focus(self):
        # A plain ping intentionally returns the previous sentence's original
        # snapshot. A request ID asks native IMK for the current caret after a
        # settled sentence, and fences delayed replies from earlier starts.
        self._startup_focus_request = "start:" + self._utterance_id
        self._bridge.send({"type": "ping", "request_id": self._startup_focus_request,
                           "capture_context": False})

    def _source_event(self, source, message):
        if self._closed or source is not self._source_activation or self._begin_origin is None:
            return
        focus_matches = self._startup_focus_matches(allow_source=False)
        if focus_matches is None:
            return
        if not focus_matches:
            self._transport_failed("已切换应用，本句已取消")
            return
        kind = message.get("event")
        self._diagnose("auto_input_source", reason=str(kind), error=str(message.get("error", "")))
        if kind == "error":
            self._transport_failed(str(message.get("error") or "自动切换输入法失败"))
        elif kind == "selected":
            self._source_pending = False
            self._focus_recovery_attempted |= message.get("focus_restored") is True
            # Selection, focus recovery and native readiness share the original
            # deadline. Finishing a stage must not restart the waiting budget.
            switched = message.get("already_selected") is not True
            self._diagnose("source_selection", reason="switched" if switched else "already_selected")
            self.diagnostic.emit({"type": "input_source_result",
                                  "focus_restored": message.get("focus_restored", False),
                                  "elapsed_ms": message.get("elapsed_ms")})
            # Request a fresh native state; never begin against the cached
            # ready flag from before the system-source check.
            self._wait_for_reply("ready")
            self._bridge.send({"type": "ping"})
            self.changed.emit()

    def _clear_startup(self):
        source, self._source_activation = self._source_activation, None
        self._source_pending = False
        self._focus_recovery_attempted = False
        self._startup_probe = None
        if source is not None:
            source.cancel()
        self._activation_timer.stop()
        self._startup_deadline = 0.0
        self._startup_clients.clear()
        self._startup_focus_target = None
        self._startup_focus_request = ""
        self._begin_origin = None
        self._input_preparation = None
        self._startup_buffering = False
        self._pending_update = None
        self._pending_finish = False
        self._finish_as_dictation = False

    def _start_input_activation(self, origin, *, auto_select, buffering):
        """Shared source selection/readiness transaction for voice and strokes."""
        self._begin_origin = origin
        self._startup_buffering = buffering
        self._startup_deadline = time.monotonic() + INPUT_ACTIVATION_TIMEOUT_MS / 1000
        self._activation_timer.start()
        if auto_select:
            if not self._epoch:
                self._warm_input_method()
            self._source_pending = True
            source = self._source_switch_factory(self)
            self._source_activation = source
            source.event.connect(lambda message: self._source_event(source, message))
            self._wait_for_reply("ready")
            self._diagnose("begin", reason="checking_input_source")
            self.changed.emit()
            source.start(origin)

    def prepare_input_method(self, token, origin, ready, failed):
        """Use Tap's activation path without opening an audio/voice sentence."""
        if self._installing:
            failed("输入法正在更新，请稍后重试")
            return
        self._stop_reply_wait()
        self._clear_startup()
        if not self._enabled or self._closed or not origin or self._transport_error or not self._bridge:
            failed(self.error or "输入法尚未连接")
            return
        self._input_preparation = (token, ready, failed)
        try:
            self._start_input_activation(origin, auto_select=True, buffering=False)
        except Exception as exc:
            self._transport_failed(str(exc))

    def cancel_input_preparation(self, token):
        if self._input_preparation and self._input_preparation[0] == token:
            self._stop_reply_wait()
            self._clear_startup()

    def _input_client_ready(self, message):
        target = self._startup_focus_target
        if target is not None:
            from ...voice.ime.focus_probe import valid_selection
            selection = message.get("selection")
            if (not message.get("ready") or
                    (message.get("epoch"), message.get("client_id"), message.get("application"))
                    != (target[0], target[1], target[3])
                    or not valid_selection(selection) or tuple(selection) != target[5]):
                self._transport_failed("输入目标或光标已变化，本句已取消")
                return False
        if not message.get("ready"):
            if self._reply_stage == "ready":
                self._recover_selected_source()
            return False
        return bool(self._begin_origin and message.get("client_id")
                    and message.get("application") == self._begin_origin[0])

    @staticmethod
    def _previous_input_settled(message):
        """A failed edit may leave a healthy client ready for a new sentence."""
        return (message.get("phase") in {"dictated", "edited", "undone", "interrupted"}
                or (message.get("phase") == "error"
                    and message.get("has_composition") is False
                    and message.get("awaiting_readback") is False))

    def begin(self, target=None, *, auto_select=False):
        if self._installing or self.stroke_mode or self._input_preparation:
            return False
        if not self._enabled or self._closed:
            return False
        self.actionRequested.emit("begin")
        self._clear_action_pending()
        self._stop_reply_wait()
        self._clear_startup()
        # BEGIN is a new attempt even when activation fails before audio starts.
        # Clear the previous settlement identity before emitting any callbacks.
        self.settled_session_id = 0
        self._session_id = 0
        origin = self._foreground_identity()
        pending_focus, self._pending_gesture_focus = self._pending_gesture_focus, None
        if pending_focus is not None:
            focus, created = pending_focus
            if time.monotonic() - created > 1.0 or origin != (focus[3], focus[4]):
                self._transport_failed("输入目标已变化或手势已超时，请重新开始")
                return False
            self._startup_focus_target = focus
        self._sentence_origin = origin
        needs_activation = auto_select or not self.ready or bool(origin and self._view.get("application") != origin[0])
        if needs_activation and (origin is None or self._transport_error or self._bridge is None):
            reason = "暂时无法确认输入目标，请重新点入文本框" if origin is None else self._transport_error or self.connectionStatus
            self._utterance_id = ""
            self._view = {**self._view, "phase": "error", "error": reason}
            self._diagnose("begin", reason="input_method_not_ready")
            self.changed.emit()
            self.interrupted.emit()
            self.settled.emit("error", "")
            return False
        self._utterance_id = uuid.uuid4().hex
        self.settled_session_id = 0
        self._session_id = 0
        self._seq = 0
        self._final_sent = False
        self._first_text_update_sent = False
        self._requested.clear()
        self._settled.clear()
        self._view = {**self._view, "phase": "starting", "error": "", "reason": "", "raw": "", "edit_requested": False}
        if origin is not None:
            # Fence startup through BEGIN acknowledgment, including a cached
            # ready state. No text goes to a client before this handshake.
            self._start_input_activation(origin, auto_select=auto_select and self._startup_focus_target is None,
                                         buffering=True)
        if self._startup_focus_target is not None:
            # The gesture already selected the source and proved its client.
            # Re-running the legacy focus-window helper could dismiss a popup
            # or select a new source after the user changed it. Only ping/BEGIN.
            self._wait_for_reply("ready")
            self._request_start_focus()
            self.changed.emit()
            return True
        if auto_select:
            return True
        if needs_activation:
            # Source activation/IPC can trail the first tap. Keep this same
            # audio sentence within the startup budget; never switch the input source or app.
            self._begin_origin = origin
            self._startup_buffering = True
            self._wait_for_reply("ready")
            self._bridge.send({"type": "ping"})
            self._diagnose("begin", reason="waiting_for_input_method")
            self.changed.emit()
            return True
        self.changed.emit()
        self._startup_clients.add(self._client_id)
        sent = self._send("begin")
        if not sent:
            self._transport_failed("输入法连接已断开")
            self._diagnose("begin", reason="send_failed")
        else:
            self._diagnose("begin", reason="requested")
            self._wait_for_reply("begin")
        return sent

    def _wait_for_reply(self, stage):
        self._reply_stage = stage
        remaining = (max(1, int((self._startup_deadline - time.monotonic()) * 1000))
                     if self._begin_origin is not None else
                     INPUT_ACTIVATION_TIMEOUT_MS if stage in {"ready", "begin"} else 3000)
        self._reply_timer.start(remaining)

    def _stop_reply_wait(self):
        self._reply_timer.stop()
        self._reply_stage = ""

    def _transport_failed(self, reason):
        if self._input_preparation:
            failed = self._input_preparation[2]
            self._stop_reply_wait()
            self._clear_startup()
            failed(reason)
            return
        if self._startup_buffering:
            self._send("reset")
        self._stop_reply_wait()
        self._clear_startup()
        self._view = {**self._view, "phase": "error", "error": reason}
        self._final_sent = True
        self._diagnose("interrupted", reason="input_method_reply_failed")
        self.settled_session_id = self._session_id
        self._utterance_id = ""
        self._session_id = 0
        self._requested.clear()
        self.changed.emit()
        self.interrupted.emit()
        self.settled.emit("error", "")

    def _reply_timed_out(self):
        stage = self._reply_stage
        if not stage or self._closed:
            return
        if self._input_preparation:
            self._transport_failed("输入法未连接到当前文本框，请重新点入文本框")
            return
        # Retire this exact transaction; never retry its text write.
        self._send("reset")
        self._utterance_id = ""
        self._transport_failed("输入法未连接到当前文本框，请重新点入文本框" if stage == "ready" else
                               "输入法未确认开始听写，请重新点入文本框" if stage == "begin"
                               else "输入法未确认定稿，已停止本句后续处理")

    def bind_session(self, session_id):
        """Bind at audio START, before an older worker can deliver a late result."""
        if self.active and self._utterance_id:
            self._session_id = int(session_id)
            self._remember_session()

    def _confirm_edit_intent(self):
        utterance = self._utterance_id
        if not utterance:
            return
        recorded_session = self._edit_intents.setdefault(utterance, 0)
        if self._session_id and recorded_session != self._session_id:
            self._edit_intents[utterance] = self._session_id
            self.editIntentConfirmed.emit(self._session_id)
        while len(self._edit_intents) > 256:
            self._edit_intents.pop(next(iter(self._edit_intents)))

    def _remember_session(self):
        if self._utterance_id and self._session_id:
            self._utterance_sessions[self._utterance_id] = self._session_id
            if self._utterance_id in self._edit_intents:
                self._confirm_edit_intent()
            while len(self._utterance_sessions) > 256:
                self._utterance_sessions.pop(next(iter(self._utterance_sessions)))

    def update(self, text, final, session_id, error=""):
        if (self._closed or not self.active or self._final_sent or
                self._view.get("phase") not in {"starting", "listening", "finishing"}):
            return False
        if self._session_id and session_id and self._session_id != session_id:
            return False
        self._session_id = session_id or self._session_id
        self._remember_session()
        if self._startup_buffering:
            # Coalesce partials; a buffered final/error always wins.
            if self._pending_update and (self._pending_update[1] or self._pending_update[3]):
                return False
            self._pending_update = (str(text), bool(final), self._session_id, str(error))
            return True
        accepted = self._send("update", text=str(text), final=bool(final), error=str(error))
        if not accepted:
            self._transport_failed("输入法连接已断开，已停止本句")
            return False
        if accepted and (final or error or (text and not self._first_text_update_sent)):
            self._diagnose("update", characters=len(str(text)), error=error,
                           reason="error" if error else "final" if final else "first_partial")
        if accepted and text:
            self._first_text_update_sent = True
        if accepted and (final or error):
            self._final_sent = True
            self._wait_for_reply("final")
        return accepted

    def finish(self):
        if self._startup_buffering:
            self._pending_finish = True
            return
        if self.active and self._view.get("phase") in {"starting", "listening"}:
            self._send("finish_dictation" if self._finish_as_dictation else "finish")
            self._finish_as_dictation = False

    @Slot()
    def convert(self):
        if self.stroke_mode:
            return
        if self.canRequestConversion:
            self._mark_action_pending("convert")
            self._send("convert")

    @Slot()
    def cancel(self):
        self.actionRequested.emit("cancel")
        if self._startup_buffering:
            session_id = self._session_id
            self.reset()
            self.settled_session_id = session_id
            self._view = {**self._view, "phase": "undone", "error": ""}
            self.changed.emit()
            self.interrupted.emit()
            self.settled.emit("undone", "")
            return
        if self.canUndo:
            self._mark_action_pending("cancel")
            self._send("cancel")

    def _mark_action_pending(self, action):
        self.actionRequested.emit(action)
        self._action_pending = (self._utterance_id, self._view.get("revision", 0), self._view.get("phase"))
        self._action_timer.start(4000)
        self.changed.emit()

    def _clear_action_pending(self):
        self._action_timer.stop()
        if self._action_pending is not None:
            self._action_pending = None
            self.changed.emit()

    def polish(self, *, restore_raw_on_undo=True):
        if (self.ready and self._utterance_id and self._view.get("phase") == "dictated"
                and self._view.get("raw") and not self._view.get("polished")
                and not self._view.get("awaiting_readback") and not self._action_pending):
            self._mark_action_pending("polish")
            self._send("polish", restore_raw_on_undo=bool(restore_raw_on_undo))
            return True
        return False

    def apply_polish(self, key, text, error):
        current = (self._epoch, self._client_id, self._utterance_id, self._view.get("revision", 0))
        if tuple(key) == current and self._view.get("phase") == "polishing":
            self._send("polish_result", revision=current[-1], text=str(text), error=str(error))

    def apply_edit(self, key, text, error):
        current = (self._epoch, self._client_id, self._utterance_id, self._view.get("revision", 0))
        if tuple(key) == current and self._view.get("phase") == "editing":
            self._send("edit_result", revision=current[-1], text=str(text), error=str(error))

    @Slot(object)
    def _accept(self, message):
        if self._closed or not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind in {"state", "stroke_ready", "stroke_cleared", "stroke_ink_cleared", "disconnected", "connected"}:
            if kind == "connected" or message.get("epoch") == self._epoch:
                self.strokeEvent.emit(dict(message))
        if kind == "stroke_cleared":
            # Token-bound sessions are handled by StrokeSessionController.
            # A delayed clear for the previous token must not end a new one
            # in the same native client. Keep legacy unbound composition clears.
            if (not message.get("stroke_session") and message.get("epoch") == self._epoch
                    and message.get("client_id") == self._client_id):
                self.strokeInvalidated.emit()
            return
        if kind == "stroke_result":
            pending = self._stroke_request
            if (pending and message.get("epoch") == self._epoch
                    and message.get("client_id") == pending[2]
                    and message.get("request_id") == pending[0]):
                self._stroke_request = None
                self._stroke_timer.stop()
                context = message.get("context")
                self.stroke_context = context[-4:] if isinstance(context, str) and message.get("success") else None
                self.strokeCommitted.emit(pending[1], bool(message.get("success")),
                                          str(message.get("error") or ""))
            return
        if kind == "connected":
            session_id = self._session_id
            if self._stroke_request is not None:
                self._stroke_timed_out()
            self._utterance_sessions.clear()
            self.settled_session_id = None
            self._compatibility_requests.clear()
            if self._begin_origin is not None:
                self._epoch = str(message.get("epoch", ""))
                self._client_id = ""
                self._startup_clients.clear()
                self._wait_for_reply("ready")
                self._transport_error = ""
                self._publish_gesture_hints()
                self.configure(self._shortcut)
                self._diagnose("connected")
                self.changed.emit()
                return
            had_transaction = self._awaiting_settlement()
            self._clear_startup()
            self._stop_reply_wait()
            self._epoch = str(message.get("epoch", ""))
            self._client_id = self._utterance_id = ""
            self._view = {"phase": "idle", "ready": False}
            if had_transaction:
                self._view["error"] = "输入法连接已重新建立，请重新开始本句"
            self._transport_error = ""
            self._last_state_diagnostic = None
            self._publish_gesture_hints()
            self.configure(self._shortcut)
            self._diagnose("connected")
            self.changed.emit()
            if had_transaction:
                self.settled_session_id = session_id
                self.interrupted.emit()
                self.settled.emit("error", "")
            return
        if message.get("epoch") != self._epoch:
            return
        request = message.get("request_id")
        if isinstance(request, str) and request.startswith("start:"):
            if (not self._startup_focus_target or self._reply_stage != "ready"
                    or request != self._startup_focus_request):
                return
        if kind == "activation_trace":
            allowed = {"type", "event", "controller", "lease", "client_id", "activated", "preparing",
                       "native_read", "state_read", "phase", "application", "front_application", "sender", "bound_sender"}
            self.diagnostic.emit({"lifecycle_event" if key == "event" else key: value
                                  for key, value in message.items()
                                  if key in allowed and isinstance(value, (str, int, bool))})
            return
        if kind == "disconnected":
            session_id = self._session_id
            self.strokeInvalidated.emit()
            if self._stroke_request is not None:
                self._stroke_timer.stop()
                _, char, _ = self._stroke_request
                self._stroke_request = None
                self.strokeCommitted.emit(char, False, "输入法连接已断开")
            if self._begin_origin is not None and (self._startup_buffering or self._input_preparation):
                # No ASR text has been released yet. Keep the same sentence
                # through a cold IME reconnect, bounded by its existing deadline.
                self._epoch = self._client_id = ""
                self._startup_clients.clear()
                self._view = {**self._view, "phase": "idle" if self._input_preparation else "starting", "ready": False}
                self._wait_for_reply("ready")
                self._warm_input_method()
                self._diagnose("disconnected", reason="startup_reconnecting")
                self.changed.emit()
                return
            self._utterance_sessions.clear()
            self.settled_session_id = None
            had_transaction = self._awaiting_settlement()
            self._stop_reply_wait()
            self._clear_startup()
            self._epoch = self._client_id = self._utterance_id = ""
            self._view = {"phase": "idle", "ready": False, "error": "输入法连接已断开"}
            self._last_state_diagnostic = None
            self._diagnose("disconnected", reason="transport_disconnected")
            self.changed.emit()
            if had_transaction:
                self.settled_session_id = session_id
                self.interrupted.emit()
                self.settled.emit("error", "")
            return
        client_id = str(message.get("client_id", ""))
        utterance_id = str(message.get("utterance_id", ""))
        if kind == "activation_recovery":
            return  # Ignore obsolete native replies from the removed cycling path.
        if kind == "state":
            if self._input_preparation:
                preparation = self._input_preparation
                focus_matches = self._startup_focus_matches()
                if focus_matches is None or preparation is not self._input_preparation:
                    return
                if not focus_matches:
                    self._transport_failed("已切换应用，输入法唤起已取消")
                    return
                if self._source_pending:
                    return
                if utterance_id and not self._previous_input_settled(message):
                    return
                if not self._input_client_ready(message):
                    return
                ready = preparation[1]
                self._stop_reply_wait()
                self._clear_startup()
                self._client_id = client_id
                self._view = {**message, "phase": "idle", "raw": "", "error": ""}
                ready(dict(message))
                return
            if self._begin_origin is not None:
                focus_matches = self._startup_focus_matches()
                if focus_matches is None:
                    return
                if not focus_matches:
                    self._transport_failed("已切换应用，本句已取消")
                    return
                if self._source_pending:
                    return
                if (self._startup_focus_target is not None and self._reply_stage == "ready"
                        and message.get("request_id") != self._startup_focus_request):
                    # Ordinary state notifications still describe the previous
                    # sentence snapshot; only our live response can authorize BEGIN.
                    return
                # Record transitions that startup intentionally buffers. The
                # ordinary state log only records accepted states, hiding the
                # activation/deactivation sequence behind a startup timeout.
                probe = {key: message.get(key) for key in (
                    "client_id", "utterance_id", "phase", "ready", "application",
                    "lifecycle_event", "handoff_state", "error")}
                if probe != self._startup_probe:
                    self._startup_probe = probe
                    self.diagnostic.emit({"type": "startup_state", **probe,
                                          "expected_utterance_id": self._utterance_id})
                acknowledged = (client_id == self._client_id and utterance_id == self._utterance_id
                                and message.get("ready") and message.get("phase") == "listening")
                if acknowledged:
                    if self._startup_focus_target is not None and not self._input_client_ready(message):
                        return
                    # Only this exact BEGIN ack can release buffered ASR.
                    self._begin_origin = None
                    self._activation_timer.stop()
                elif utterance_id and not (
                        (self._source_activation is not None
                         or (self._startup_focus_target is not None
                             and utterance_id == self._startup_focus_target[2])) and message.get("ready")
                        and self._previous_input_settled(message)
                        and utterance_id != self._utterance_id and client_id not in self._startup_clients):
                    # An obsolete controller may report its empty BEGIN as
                    # interrupted during activation churn. It received no ASR.
                    if (client_id == self._client_id and utterance_id == self._utterance_id
                            and message.get("phase") == "error"):
                        self._transport_failed(str(message.get("error") or "输入法未能开始本句"))
                    return
                else:
                    if (self._reply_stage == "begin" and client_id in self._startup_clients
                            and message.get("ready") and message.get("phase") == "idle"
                            and "selection" not in message):
                        # Native BEGIN invalidates the old empty session first.
                        # Its intermediate idle notification has no snapshot;
                        # only the subsequent BEGIN ack can confirm the target.
                        return
                    if not self._input_client_ready(message):
                        if not message.get("ready") and (not client_id or client_id == self._client_id):
                            self._wait_for_reply("ready")
                        return
                    if client_id in self._startup_clients:
                        return  # queued idle ping or retired activation
                    self._startup_clients.add(client_id)
                    self._client_id = client_id
                    self._view = {**message, "phase": "starting", "raw": "", "error": ""}
                    if self._send("begin"):
                        self._wait_for_reply("begin")
                        self._diagnose("begin", reason="ready_after_activation")
                        self.changed.emit()
                    else:
                        self._transport_failed("输入法连接已断开")
                    return
            # Idle activation/state messages may switch the active session.
            # Old non-empty transaction IDs can never replace the current one.
            if utterance_id and (client_id != self._client_id or utterance_id != self._utterance_id):
                return
            if (not utterance_id and self._utterance_id and (self.active or self._reply_stage)
                    and client_id == self._client_id
                    and message.get("ready") and message.get("phase") == "idle"):
                # An idle ping captured before BEGIN may arrive afterwards.
                # It cannot retire the new transaction or its acknowledgment timer.
                return
            begin_acknowledged = (self._reply_stage == "begin" and client_id == self._client_id
                                  and utterance_id == self._utterance_id and message.get("ready")
                                  and message.get("phase") == "listening")
            previous_active = self.active
            changed_client = client_id != self._client_id
            if changed_client:
                if self._stroke_request is not None:
                    self._stroke_timer.stop()
                    _, char, _ = self._stroke_request
                    self._stroke_request = None
                    self.strokeCommitted.emit(char, False, "已切换输入框")
                self._utterance_sessions.clear()
                self._client_id = client_id
                self._utterance_id = ""
                self._view = {}
            if not utterance_id and self._utterance_id and message.get("phase") == "idle":
                self._utterance_id = ""
            self._view = {**self._view, **message}
            if utterance_id == self._utterance_id and (message.get("edit_requested") or message.get("phase") in {"editing", "edited"}):
                self._confirm_edit_intent()
            if self._action_pending is not None:
                pending_id, pending_revision, pending_phase = self._action_pending
                if (utterance_id != pending_id or self._view.get("revision", 0) != pending_revision
                        or self._view.get("phase") != pending_phase
                        or self._view.get("error") or not self.ready):
                    self._clear_action_pending()
            if (changed_client or not self.ready
                    or (self._reply_stage == "begin" and self._view.get("phase") != "starting")
                    or self._view.get("phase") in {"dictated", "editing", "edited", "undone", "interrupted", "error"}):
                self._stop_reply_wait()
            if not message.get("ready"):
                self._view.pop("original", None)
                self._view["context_complete"] = False
                self._view["error"] = str(message.get("error") or message.get("reason") or self.connectionStatus)
            elif previous_active and changed_client:
                self._view["error"] = str(message.get("error") or message.get("reason") or "已切换输入框")
            self._diagnose("state")
            if changed_client or not self.ready:
                self.strokeInvalidated.emit()
            self.changed.emit()
            if previous_active and (changed_client or not self.ready or self._view.get("phase") == "error"):
                self._clear_startup()
                self.interrupted.emit()
            elif self._startup_buffering and utterance_id and self._view.get("phase") == "listening":
                pending, finish, as_dictation = self._pending_update, self._pending_finish, self._finish_as_dictation
                self._clear_startup()
                if pending:
                    self.update(*pending)
                if finish and not self._final_sent:
                    self._finish_as_dictation = as_dictation
                    self.finish()
            if begin_acknowledged and self.ready and self._view.get("phase") == "listening":
                self.began.emit()
            return
        if self._begin_origin is not None:
            if (kind == "interrupted" and client_id == self._client_id
                    and utterance_id == self._utterance_id):
                reason = str(message.get("reason") or message.get("error") or "")
                if reason and reason not in {"已切换输入框", "已切换输入法或输入框",
                                             "输入法已重新激活", "输入会话已关闭"}:
                    self._transport_failed(reason)
            return
        if client_id != self._client_id or utterance_id != self._utterance_id:
            return
        if kind == "wechat_key":
            request_id = message.get("request_id")
            key = (self._epoch, client_id, utterance_id, request_id)
            application = message.get("application")
            if (not isinstance(request_id, str) or not request_id or key in self._compatibility_requests
                    or not self.active or not self._utterance_id
                    or not isinstance(application, str)
                    or self._view.get("application") != application
                    or (application != "com.tencent.xinWeChat"
                        and message.get("command") in {"select_all", "select_to_start", "caret_from_end", "caret_to_end", "caret_backward", "caret_forward"}
                        and self._view.get("phase") not in {"editing", "edited", "polishing"})):
                return
            self._compatibility_requests.add(key)
            error = ""
            try:
                from ...platforms.macos.wechat_native_keys import send_wechat_key, send_input_method_key
                args = (message.get("command"), message.get("count"), message.get("event_tag"))
                if application == "com.tencent.xinWeChat":
                    send_wechat_key(*args)
                elif not application:
                    # Unbundled Qt clients share an empty bundle ID. Pin the
                    # PID captured at begin, never resolve a new target here.
                    origin = self._sentence_origin
                    if not origin or origin[0] or self._foreground_identity() != origin:
                        raise RuntimeError("输入目标已切换，未发送快捷键")
                    send_input_method_key(application, *args, expected_pid=origin[1])
                else:
                    send_input_method_key(application, *args)
            except Exception as exc:
                error = str(exc)
                self._permissions.report_error(exc)
            self._send("wechat_key_result", request_id=request_id, error=error)
            self._diagnose("wechat_key", error=error, reason=str(message.get("command", "")))
        elif kind == "finish_audio":
            self.audioEndRequested.emit()
        elif kind == "interrupted":
            self._stop_reply_wait()
            reason = str(message.get("reason") or message.get("error") or self.error or "输入会话已结束")
            self._view = {**self._view, "error": reason, "reason": reason}
            if "lifecycle_event" in message:
                self._view["lifecycle_event"] = str(message["lifecycle_event"])
            if "has_composition" in message:
                self._view["has_composition"] = bool(message["has_composition"])
            self._diagnose("interrupted", reason=reason)
            self.changed.emit()
            self.interrupted.emit()
        elif kind == "polish_requested":
            key = (self._epoch, client_id, utterance_id, message.get("revision", 0))
            if (key not in self._requested and self._view.get("phase") == "polishing"
                    and key[-1] == self._view.get("revision")):
                self._requested.add(key)
                self.polishRequested.emit(key, str(message.get("text", "")), self.target_reference())
        elif kind == "edit_requested":
            revision = message.get("revision", 0)
            key = (self._epoch, client_id, utterance_id, revision)
            if (key not in self._requested and self._view.get("phase") == "editing"
                    and message.get("edit_context_available", self._view.get("context_complete"))):
                self._requested.add(key)
                self._confirm_edit_intent()
                original = message.get("original", self._view.get("original", ""))
                self.editRequested.emit(key, str(message.get("instruction", "")), str(original), self.target_reference())
        elif kind == "settled":
            self._stop_reply_wait()
            self._clear_action_pending()
            phase = str(message.get("phase", ""))
            key = (utterance_id, phase, message.get("revision", self._view.get("revision", 0)), str(message.get("text", "")))
            if key not in self._settled:
                self._settled.add(key)
                if "error" in message:
                    self._view["error"] = str(message.get("error") or "")
                source = message.get("source_utterance_id", utterance_id)
                self.settled_session_id = (self._session_id if source == utterance_id
                                           else self._utterance_sessions.get(source, 0))
                self.transactionSettled.emit(
                    (self._epoch, client_id, source, message.get("revision", self._view.get("revision", 0))),
                    phase, str(message.get("text", "")), str(message.get("error") or ""), source != utterance_id)
                self.settled.emit(phase, str(message.get("text", "")))

    def reset(self):
        self.actionRequested.emit("reset")
        self._clear_action_pending()
        self._stop_reply_wait()
        self._clear_startup()
        self._finish_as_dictation = False
        if self._utterance_id:
            self._send("reset")
        self._utterance_id = ""
        self._sentence_origin = None
        self._session_id = 0
        self._utterance_sessions.clear()
        self.settled_session_id = None
        self._view = {**self._view, "phase": "idle", "raw": ""}
        self._requested.clear()
        self.changed.emit()

    def release_completed_shortcut(self, *, reset_native=False, sent=False):
        """Retire host bookkeeping after native send/navigation takes over.

        Ordinary keys already invalidate the native composition. A local editor
        replaced programmatically must explicitly retire it before clearing text.
        """
        if self._view.get("phase") in {"starting", "listening", "finishing", "editing", "polishing"} or self._view.get("awaiting_readback"):
            return
        if reset_native or sent:
            if self._view.get("has_composition") or self.stroke_mode or self._input_preparation:
                return
            if self._utterance_id:
                self._send("end_input", outcome="sent" if sent else "replaced")
                self._diagnose("session_retired", reason="sent" if sent else "editor_context_changed")
            self._clear_startup()
        self._stop_reply_wait()
        self._clear_action_pending()
        self._utterance_id = ""
        self._session_id = 0
        self._utterance_sessions.clear()
        self.settled_session_id = None
        self._requested.clear()
        self._settled.clear()
        self._view = {**self._view, "phase": "idle", "raw": "", "error": "",
                      "has_composition": False, "edit_requested": False,
                      "utterance_id": "", "revision": 0, "polished": False,
                      "can_polish": False, "polish_restore_available": False,
                      "undo_history_depth": 0, "can_cancel": False, "can_convert": False}
        self.changed.emit()

    def close(self):
        if self._closed:
            return
        self.reset()
        self._clear_startup()
        self._closed = True
        self._transport_retry_timer.stop()
        self._permissions.close()
        from ...platforms.macos.native_access import native_access
        native_access().close()
        if self._bridge:
            self._bridge.close()
