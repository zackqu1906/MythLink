"""Opt-in proximity lock. All Bluetooth, Keychain and OS actions run out of process."""
from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
import threading
from uuid import UUID

from PySide6.QtCore import QObject, Property, Qt, QProcess, QTimer, QCoreApplication, QSettings, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from ...gestures.gesture_settings import GESTURE_LABELS


def helper_path() -> Path:
    root = (Path(sys.executable).resolve().parent.parent / "Helpers" if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[4] / ".build/proximity")
    return root / "ProxiMicPresence.app/Contents/MacOS/ProxiMicPresence"


class ProximityController(QObject):
    changed = Signal()
    lockNoticeChanged = Signal()
    diagnostic = Signal(object)
    lockPreparing = Signal()
    locked = Signal()
    unlocked = Signal()
    reconnectRequested = Signal(str)
    _unlockGesture = Signal(object, object, float, int)
    _activityEvidence = Signal(str, object, float, object, int)

    def __init__(self, parent=None, *, settings, process_factory=QProcess):
        super().__init__(parent)
        self._gesture_state = (False, "")  # Immutable snapshot read by model worker.
        self._input_resume_allowed = False
        self._lock_preparing = False
        self._lock_notice_state = ""
        self._lock_countdown_seconds = 0
        self._lock_notice_timer = QTimer(self)
        self._lock_notice_timer.setSingleShot(True)
        self._lock_notice_timer.timeout.connect(self._clear_lock_notice)
        self._gesture_guard = threading.Lock()
        self._claimed_token = ""
        self._unlockGesture.connect(self._apply_unlock_gesture, Qt.QueuedConnection)
        self._activityEvidence.connect(self._apply_activity_evidence, Qt.QueuedConnection)
        self._settings = settings
        saved_gesture = settings.value("proximity/unlockGesture", "snap")
        self._unlock_gesture = (saved_gesture if isinstance(saved_gesture, str)
                                and saved_gesture in GESTURE_LABELS else "snap")
        self._unlock_gesture_revision = 0
        self._factory = process_factory
        self._process = None
        self._command = ""
        self._buffer = bytearray()
        self._enabled = (sys.platform == "darwin" and os.environ.get("PROXIMIC_STARTUP_PROBE") != "1"
                         and settings.value("proximity/enabled", False, type=bool))
        self._target = ""
        self._name = ""
        self._password = False
        self._password_access = False
        self._password_status_checked = False
        self._permission = False
        self._status = "尚未开启"
        self._error = ""
        self._lock_screen_message_status = ""
        self._rssi = "—"
        self._closed = False
        self._manually_disconnected = False
        self._last_monitor_state = None
        self._monitor_logged_at = 0.0
        defaults = dict(awaySamples=2, lostDelay=15)
        self._values = {}
        self._baseline_options = {key: settings.value("proximity/baseline/" + key, default, type=bool)
                                  for key, default in dict(enabled=False, keyboardMouse=True,
                                                           gestures=False, voice=False).items()}
        self._baseline_revision = 0
        self._sent_baseline_options = None
        self._last_voice_evidence = None
        self._thresholds = {}
        self._calibration = {}
        self._calibration_elapsed = 0.0
        self._calibration_samples = 0
        self._calibration_complete = False
        self._calibration_status = ""
        self._reconnect_target = ""
        self._monitor_received_at = 0.0
        for retired in ("lockRSSI", "unlockRSSI", "automaticThresholds", "nearDelay", "oneSecondUnlockMigrated", "awayDelay"):
            settings.remove("proximity/" + retired)
        for key, fallback in defaults.items():
            try:
                self._values[key] = int(settings.value("proximity/" + key, fallback))
            except (ValueError, TypeError, OverflowError):
                self._values[key] = fallback
        if not self.valid_options(self._values):
            self._values = defaults.copy()
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._timeout)
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.close)
        if sys.platform == "darwin" and os.environ.get("PROXIMIC_STARTUP_PROBE") != "1":
            QTimer.singleShot(0, self.refresh)

    @staticmethod
    def valid_options(v):
        return (1 <= v["awaySamples"] <= 60 and
                5 <= v["lostDelay"] <= 120)

    @Property(bool, notify=changed)
    def enabled(self): return self._enabled

    @enabled.setter
    def enabled(self, value):
        if bool(value) == self._enabled:
            return
        if value and not self._ready():
            self.changed.emit(); return
        self._enabled = bool(value)
        self._settings.setValue("proximity/enabled", self._enabled)
        self._stop()
        if self._enabled:
            self._start_monitor()
        else:
            self._status = "已关闭"; self._error = ""; self._rssi = "—"
        self.changed.emit()

    @Property(bool, notify=changed)
    def busy(self): return self._process is not None and self._command != "monitor"
    @Property(str, notify=lockNoticeChanged)
    def lockNoticeState(self): return self._lock_notice_state
    @Property(int, notify=lockNoticeChanged)
    def lockCountdownSeconds(self): return self._lock_countdown_seconds
    @Property(str, notify=changed)
    def deviceName(self): return self._name or "请先在主界面连接戒指"
    @Property(bool, notify=changed)
    def passwordConfigured(self): return self._password
    @Property(bool, notify=changed)
    def permissionReady(self): return self._permission
    @Property(bool, notify=changed)
    def passwordAccessReady(self): return self._password_access
    @Property(bool, notify=changed)
    def passwordStatusChecked(self): return self._password_status_checked
    @Property(str, notify=changed)
    def status(self): return self._status
    @Property(str, notify=changed)
    def error(self): return self._error
    @Property(str, notify=changed)
    def rssi(self): return self._rssi
    @Property("QVariantMap", notify=changed)
    def options(self): return dict(self._values)
    @Property("QVariantMap", notify=changed)
    def baselineOptions(self): return dict(self._baseline_options)
    @Property("QVariantMap", notify=changed)
    def learnedThresholds(self): return dict(self._thresholds)
    @Property("QVariantMap", notify=changed)
    def savedCalibration(self): return dict(self._calibration)
    @Property(bool, notify=changed)
    def calibrating(self): return self._command == "calibrate"
    @Property(float, notify=changed)
    def calibrationElapsed(self): return self._calibration_elapsed
    @Property(int, notify=changed)
    def calibrationSamples(self): return self._calibration_samples
    @Property(str, notify=changed)
    def calibrationStatus(self): return self._calibration_status
    @Property(str, constant=True)
    def componentPath(self): return str(helper_path().parents[2])

    @Property("QVariantList", constant=True)
    def unlockGestureOptions(self):
        return [dict(value=key, label=label) for key, label in GESTURE_LABELS.items()]

    @Property(str, notify=changed)
    def unlockGesture(self): return self._unlock_gesture

    @unlockGesture.setter
    def unlockGesture(self, value):
        if value not in GESTURE_LABELS or value == self._unlock_gesture:
            return
        with self._gesture_guard:
            self._unlock_gesture = value
            self._unlock_gesture_revision += 1
            self._claimed_token = ""
        self._settings.setValue("proximity/unlockGesture", value)
        self._lock_screen_message_status = ""
        self.changed.emit()

    @Property(str, notify=changed)
    def unlockGestureLabel(self): return GESTURE_LABELS[self._unlock_gesture]

    @Property(str, notify=changed)
    def lockScreenMessage(self): return f"Ring 已连接且手势解锁功能开启时，可{self.unlockGestureLabel}解锁"

    @Property(str, notify=changed)
    def lockScreenMessageStatus(self): return self._lock_screen_message_status

    @Slot()
    def copyLockScreenMessage(self):
        QGuiApplication.clipboard().setText(self.lockScreenMessage)
        self._lock_screen_message_status = "提示已复制，请在系统锁屏设置中粘贴并保存。"
        self.changed.emit()

    @Slot()
    def openLockScreenSettings(self):
        opened = QDesktopServices.openUrl(QUrl("x-apple.systempreferences:com.apple.Lock-Screen-Settings.extension"))
        self._lock_screen_message_status = (
            "请开启“锁定时显示信息”，点击“设定”，粘贴提示并保存；如已有留言，请保留原文并追加。"
            if opened else "请手动打开系统设置 → 锁定屏幕 → 锁定时显示信息。")
        self.changed.emit()

    @property
    def gestures_blocked(self):
        return self._gesture_state[0]

    @property
    def input_resume_allowed(self):
        return (self._enabled and self._command == "monitor" and self._process is not None
                and self._input_resume_allowed and not self.gestures_blocked
                and time.monotonic() - self._monitor_received_at < 3)

    def syncGestureReadiness(self):
        """Push live health, then acknowledge that the offered unlock route is installed."""
        if self._closed or not self._enabled or self._command != "monitor" or self._process is None:
            return
        owner = self.parent()
        ready = bool(owner is not None and getattr(owner, "proximity_gesture_ready", False))
        self._process.write(b"gesture-ready 1\n" if ready else b"gesture-ready 0\n")
        blocked, token = self._gesture_state
        if ready and blocked and token:
            # Set _gesture_state before acknowledging: the native helper may
            # wake the display as soon as this command reaches it.
            self._process.write(("gesture-installed " + token + "\n").encode("ascii"))

    @property
    def lock_connection_ready(self):
        owner = self.parent()
        return (not self._manually_disconnected and owner is not None
                and bool(getattr(owner, "proximity_lock_connected", False)))

    def syncConnectionState(self):
        """Keep the native client's connection separate from permission to lock."""
        if not self._closed and self._enabled and self._command == "monitor" and self._process is not None:
            self._process.write(b"host-connected 1\n" if self.lock_connection_ready else b"host-connected 0\n")

    def pauseForDisconnect(self):
        """An explicit disconnect stops both monitoring and its independent BLE retries."""
        if self.calibrating:
            self._calibration_status = "设备已手动断开，校准已取消，原校准数据已保留"
        self._manually_disconnected = True
        self._stop()
        self._status = "设备已手动断开，距离功能已暂停；重新连接后恢复"
        self._error = ""; self._rssi = "—"
        self.changed.emit()

    def filter_gesture(self, name, connection):
        """Consume every gesture while locking/locked; enqueue the selected gesture."""
        with self._gesture_guard:
            blocked, token = self._gesture_state
            if not blocked or self._lock_preparing:
                if name and name != "empty":
                    self.record_activity("gestures", connection)
            if not blocked:
                return False
            if name == self._unlock_gesture and token and token != self._claimed_token:
                self._claimed_token = token
                self._unlockGesture.emit(token, connection, time.monotonic(), self._unlock_gesture_revision)
        return True

    def record_activity(self, source, connection):
        """Worker-safe: bind evidence to the current helper, options and connection."""
        if self._baseline_options["enabled"] and self._baseline_options.get(source, False):
            self._activityEvidence.emit(source, connection, time.monotonic(),
                                        self._process, self._baseline_revision)

    def record_voice_activity(self, text, session_id, connection):
        text = str(text or "").strip()
        key = (connection, session_id, text)
        if text and key != self._last_voice_evidence:
            self._last_voice_evidence = key
            self.record_activity("voice", connection)

    @Slot(str, object, float, object, int)
    def _apply_activity_evidence(self, source, connection, created, process, revision):
        owner = self.parent()
        if (source not in {"gestures", "voice"} or self._closed or not self._enabled
                or self._command != "monitor" or process is None or process is not self._process
                or revision != self._baseline_revision or not self._baseline_options["enabled"]
                or not self._baseline_options[source] or (self.gestures_blocked and not self._lock_preparing)
                or not 0 <= time.monotonic() - created < 5 or connection.is_set()
                or (owner is not None and (connection is not owner._disconnect_event
                    or not owner._connected or str(owner._selector).lower() != self._target))):
            return
        self._sync_baseline_options()
        # A brief UI stall may outlast the helper's connection heartbeat. Renew
        # the verified live connection before forwarding the queued operation.
        self.syncConnectionState()
        process.write(f"baseline-activity {revision} {source}\n".encode("ascii"))

    @Slot(str, bool)
    def setBaselineOption(self, key, value):
        if self._closed or self.busy or key not in self._baseline_options:
            return
        value = bool(value)
        if self._baseline_options[key] == value:
            return
        self._baseline_options = {**self._baseline_options, key: value}
        self._baseline_revision += 1
        self._settings.setValue("proximity/baseline/" + key, value)
        self._last_voice_evidence = None
        self._sync_baseline_options()
        self.changed.emit()

    def _sync_baseline_options(self):
        if self._command != "monitor" or self._process is None or not self._calibration:
            return
        mask = (sum(bit for key, bit in (("keyboardMouse", 1), ("gestures", 2), ("voice", 4))
                    if self._baseline_options[key]) if self._baseline_options["enabled"] else 0)
        state = (self._baseline_revision, mask)
        if state == self._sent_baseline_options or (mask == 0 and self._sent_baseline_options is None):
            return
        baseline = self._calibration["baseline"]
        result = self._process.write(f"baseline-options {state[0]} {mask} {baseline}\n".encode("ascii"))
        if result is None or result >= 0:
            self._sent_baseline_options = state

    @Slot(object, object, float, int)
    def _apply_unlock_gesture(self, token, connection, created, revision):
        owner = self.parent()
        if (self._closed or not self._enabled or self._command != "monitor"
                or self._process is None or self._gesture_state != (True, token)
                or revision != self._unlock_gesture_revision
                or time.monotonic() - created > 1.0 or connection.is_set()
                or (owner is not None and connection is not owner._disconnect_event)):
            with self._gesture_guard:
                if self._claimed_token == token and revision == self._unlock_gesture_revision:
                    self._claimed_token = ""
            return
        self._process.write(("gesture-unlock " + token + "\n").encode("ascii"))
        self.diagnostic.emit({"action": "gesture_unlock", "gesture": self._unlock_gesture})

    def _ready(self):
        if self._manually_disconnected:
            self._error = "请先在主界面重新连接戒指"
        elif not self._target:
            self._error = "请先在主界面连接戒指"
        elif not self._password_status_checked:
            self.refresh()
            self._error = "正在检查已保存的密码授权，请稍候"
        elif not self._password:
            self._error = "请先设置本机解锁密码"
        elif not self._password_access:
            self._error = "请先点击允许访问已存密码，并在钥匙串弹窗中选择始终允许"
        elif not self._permission:
            self._error = "请先授权距离锁屏组件，再点击检查状态"
        elif not self._calibration:
            self._error = "请先在设置中为当前戒指完成 10 秒校准"
        else:
            return True
        return False

    def useDevice(self, identifier, name):
        """Follow a confirmed main-window connection; retain it during link loss."""
        try:
            identifier = str(UUID(identifier))
        except ValueError:
            self._error = "当前设备没有有效的蓝牙标识"; self.changed.emit(); return
        changed = identifier != self._target
        resuming = self._manually_disconnected
        self._manually_disconnected = False
        if changed:
            self._stop()
        self._target = identifier; self._name = name or "当前戒指"
        if changed:
            self._load_calibration()
            self._calibration_status = ""
        self._error = ""
        if self._enabled and (changed or resuming) and not self.busy:
            if self._password and self._password_access and self._permission:
                self._start_monitor()
            else:
                self.refresh()
        self.changed.emit()

    @staticmethod
    def _valid_calibration(record):
        if not isinstance(record, dict):
            return False
        if record.get("version") == 2:
            duration, sources = record.get("duration_seconds"), record.get("sources")
            if (record.get("method") != "activity" or type(duration) not in (int, float)
                    or not math.isfinite(duration) or not 2 <= duration <= 120
                    or not isinstance(sources, list) or not sources
                    or any(s not in ("keyboardMouse", "gestures", "voice") for s in sources)):
                return False
        elif record.get("version") != 1 or record.get("duration_seconds") != 10:
            return False
        samples, baseline = record.get("samples"), record.get("baseline")
        minimum = 5 if record.get("version") == 2 else 10
        if not isinstance(samples, list) or not minimum <= len(samples) <= 100:
            return False
        if any(type(v) not in (int, float) or not math.isfinite(v) or not -100 <= v <= -20 for v in samples):
            return False
        return (type(baseline) in (int, float) and math.isfinite(baseline)
                and baseline == statistics.median(samples))

    def _save_calibration(self, record):
        key = "proximity/calibrations/" + self._target
        previous = self._settings.value(key)
        self._settings.setValue(key, json.dumps(record))
        self._settings.sync()
        if self._settings.status() != QSettings.NoError:
            if previous is None:
                self._settings.remove(key)
            else:
                self._settings.setValue(key, previous)
            self._error = "无法保存校准，请检查设置目录的写入权限后重试"
            self._stop_failed_operation()
            return False
        self._calibration = record
        return True

    def _load_calibration(self):
        try:
            record = json.loads(self._settings.value("proximity/calibrations/" + self._target, ""))
        except (ValueError, TypeError):
            record = {}
        self._calibration = record if self._valid_calibration(record) else {}

    @Slot()
    def startCalibration(self):
        if self._closed or self.busy:
            return
        if not self._target or self._manually_disconnected:
            self._error = "请先在主界面连接戒指后再校准"
            self.changed.emit(); return
        if self.gestures_blocked:
            self._error = "请先解锁屏幕后再校准"
            self.changed.emit(); return
        self._calibration_elapsed = 0.0
        self._calibration_samples = 0
        self._calibration_complete = False
        self._calibration_status = ""
        self._launch("calibrate", [self._target])

    @Slot()
    def cancelCalibration(self):
        if self.calibrating:
            self._finish_calibration("校准已保存，重连和重启后沿用" if self._calibration_complete
                                     else "校准已取消，原校准数据已保留")

    def _finish_calibration(self, message):
        """End the exclusive capture and restore the user's current monitoring setting."""
        self._stop()
        self._calibration_status = message
        self._status = message
        if self._enabled and not self._closed:
            self._start_monitor()
        self.changed.emit()

    def _stop_failed_operation(self):
        if self.calibrating:
            self._finish_calibration(self._error)
        else:
            self._stop(); self.changed.emit()

    @property
    def reconnect_target(self):
        if (self._enabled and self._command == "monitor" and not self._closed
                and time.monotonic() - self._monitor_received_at < 3):
            return self._reconnect_target
        return ""

    @Slot(str, int)
    def setOption(self, key, value):
        if key not in self._values or self._enabled: return
        candidate = {**self._values, key: value}
        if not self.valid_options(candidate):
            self._error = "等待时间超出允许范围"; self.changed.emit(); return
        self._values = candidate
        self._settings.setValue("proximity/" + key, value)
        self._error = ""; self.changed.emit()

    @Slot()
    def refresh(self):
        if self._closed or self.busy: return
        if self._command == "monitor" and self._process is not None:
            return  # The running monitor already publishes fresh status once a second.
        self._launch("status")

    @Slot()
    def configurePassword(self):
        if self.busy: return
        self.enabled = False
        self._launch("password")

    @Slot()
    def authorizePassword(self):
        if self.busy: return
        self.enabled = False
        self._launch("authorize-password")

    @Slot()
    def forgetPassword(self):
        if self.busy: return
        self.enabled = False
        self._launch("forget")

    @Slot()
    def requestPermissions(self):
        if self.busy: return
        self.enabled = False
        self._launch("authorize")

    @Slot()
    def revealComponent(self):
        # Show the exact helper for the Accessibility pane's '+' picker.
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(helper_path().parents[3])))

    def _start_monitor(self):
        if not self._enabled or self._closed or self._manually_disconnected: return
        if not self._ready():
            self._status = "需要完成设置"; self.changed.emit(); return
        v = self._values
        self._launch("monitor", [self._target, *(str(v[k]) for k in
                      ("awaySamples", "lostDelay")), str(self._calibration["baseline"])])

    def _launch(self, command, args=()):
        if self._closed: return
        self._stop()
        path = helper_path()
        if not path.is_file():
            self._error = "缺少距离锁屏组件；源码运行请先执行 scripts/build-proximity.sh"
            self._status = "组件未安装"; self.changed.emit(); return
        self._last_monitor_state = None
        self._monitor_logged_at = 0.0
        self._error = ""; self._command = command
        self._status = ("正在启动距离监测…" if command == "monitor" else
                        "正在连接戒指，连接后开始采集 10 秒…" if command == "calibrate" else "正在检查或设置…")
        proc = self._factory(self); self._process = proc
        proc.setProcessChannelMode(QProcess.SeparateChannels)
        proc.readyReadStandardOutput.connect(lambda: self._read(proc))
        # Do not forward native stderr; only the allowlisted JSON status enters the UI.
        proc.readyReadStandardError.connect(lambda: proc.readAllStandardError())
        proc.finished.connect(lambda code, *_: self._finished(proc, code))
        proc.errorOccurred.connect(lambda *_: self._process_error(proc))
        proc.start(str(path), [command, *args])
        self._watchdog.start(10000 if command == "monitor" else 300000 if command in {"password", "authorize-password"} else 15000)
        self.changed.emit()

    def _read(self, proc):
        if proc is not self._process: return
        self._buffer.extend(bytes(proc.readAllStandardOutput()))
        if len(self._buffer) > 32768:
            self._error = "距离监测通信异常"; self._stop_failed_operation(); return
        while b"\n" in self._buffer:
            raw, _, self._buffer = self._buffer.partition(b"\n")
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if isinstance(message, dict): self._accept(message, proc)

    def _accept(self, message, proc):
        if proc is not self._process: return
        kind = message.get("event")
        if kind == "baseline_updated" and self._command == "monitor" and self._enabled:
            if (message.get("revision") != self._baseline_revision
                    or not self._baseline_options["enabled"]):
                return
            record = {key: message.get(key) for key in ("baseline", "samples", "duration_seconds", "sources")}
            record.update(version=2, method="activity", calibrated_at=datetime.now(timezone.utc).isoformat())
            if (message.get("device") != self._target or not self._valid_calibration(record)
                    or any(not self._baseline_options[source] for source in record["sources"])):
                self._error = "自动基线数据无效，距离监测已停止"
                self._stop_failed_operation(); return
            if not self._save_calibration(record):
                return
            baseline = record["baseline"]
            self._thresholds.update(baseline=baseline, lock=baseline - 8, unlock=baseline - 4)
            self._calibration_status = "已根据所选操作更新基线和靠近、远离阈值"
            self.diagnostic.emit({"action": kind, "baseline": baseline, "lock": baseline - 8,
                                  "unlock": baseline - 4, "sources": record["sources"],
                                  "samples": len(record["samples"]), "duration_seconds": record["duration_seconds"]})
            self.changed.emit(); return
        if kind == "return_wake" and self._command == "monitor":
            self.diagnostic.emit({"action": "return_wake", "success": bool(message.get("success")),
                                  "result": message.get("result"),
                                  **{k: message[k] for k in ("owned", "unlock_ready", "return_to_wake_ms") if k in message}})
            return  # Display wake does not change lock ownership or gesture state.
        if kind == "return_confirmed" and self._command == "monitor":
            self.diagnostic.emit({"action": kind, "decision_rssi": message.get("decision_rssi"),
                                  "threshold": message.get("threshold"),
                                  **{k: message[k] for k in ("signal_source", "smoothed_rssi") if k in message}})
            return
        if kind == "calibration_progress" and self.calibrating:
            self._calibration_elapsed = max(0, min(10, float(message.get("elapsed", 0))))
            self._calibration_samples = int(message.get("samples", 0))
            self._status = (f"正在校准：{self._calibration_elapsed:.1f} / 10 秒，请保持正常坐姿"
                            if self._calibration_elapsed else "正在连接戒指，连接后开始采集 10 秒…")
            self._watchdog.start(10000)
            self.changed.emit(); return
        if kind == "calibration_complete" and self.calibrating and not self._calibration_complete:
            record = {key: message.get(key) for key in ("baseline", "samples", "duration_seconds")}
            record.update(version=1, calibrated_at=datetime.now(timezone.utc).isoformat())
            if message.get("device") != self._target or not self._valid_calibration(record):
                self._error = "校准结果无效，原校准数据已保留"
                self._stop_failed_operation(); return
            if not self._save_calibration(record):
                return
            self._calibration_complete = True
            self._calibration_elapsed = 10.0
            self._calibration_samples = len(record["samples"])
            self._status = "校准已保存，重连和重启后沿用"
            self.changed.emit(); return
        if kind in {"prepare_lock", "locked", "unlocked", "unlock_attempt", "error"}:
            self.diagnostic.emit({"action": kind, "error": str(message.get("error", ""))[:250],
                                  "reason": str(message.get("reason", ""))[:80],
                                  "lock": message.get("lock"), "unlock": message.get("unlock")})
        if kind == "monitor":
            self._update_lock_notice(message)
            self._monitor_received_at = time.monotonic()
            self._input_resume_allowed = (message.get("locked") is False and not message.get("lock_pending")
                                          and bool(message.get("permission")) and not message.get("error"))
            self._reconnect_target = (self._target if message.get("locked") and message.get("permission")
                                      and message.get("connected") and message.get("reconnect_allowed")
                                      and not message.get("error") else "")
            token = str(message.get("unlock_token", ""))
            try:
                token = str(UUID(token)) if token else ""
            except ValueError:
                token = ""
            self._gesture_state = (bool(message.get("locked") or message.get("lock_pending")), token)
            self._lock_preparing = message.get("locked") is False and bool(message.get("lock_pending"))
            if not token:
                self._claimed_token = ""
            self._thresholds = {k: message.get(k) for k in
                                ("thresholds_ready", "baseline", "lock", "unlock", "calibration_samples", "calibration_window",
                                 "calibration_active", "rssi_samples", "raw_rssi", "disconnect_evidence")}
            for key in ("activity_calibration_samples", "activity_calibration_active", "activity_lock_protected"):
                self._thresholds[key] = message.get(key)
            self._sync_baseline_options()
        if kind == "calibration_reset":
            self.diagnostic.emit({"action": kind})
            return
        if kind == "calibration_sample":
            self.diagnostic.emit({"action": kind, **{k: message.get(k) for k in
                                  ("raw_rssi", "decision_rssi", "collecting", "lock", "unlock", "sample_interval_seconds")}})
            return
        if kind in ("status", "monitor"):
            if kind == "status": self._password_status_checked = True
            self._permission = bool(message.get("permission", False))
            if "password" in message: self._password = bool(message["password"])
            if "password_access" in message: self._password_access = bool(message["password_access"])
            self._error = str(message.get("error", ""))[:250]
            if kind == "monitor":
                fingerprint = tuple(message.get(k) for k in
                                    ("connected", "armed", "permission", "state", "error", "owned", "locked",
                                     "locking_enabled", "activity_lock_protected", "awaiting_return", "return_confirmed", "reconnect_allowed", "gesture_eligible", "gesture_ready", "unlock_ready"))
                now = time.monotonic()
                if fingerprint != self._last_monitor_state or now - self._monitor_logged_at >= 10:
                    self._last_monitor_state = fingerprint; self._monitor_logged_at = now
                    self.diagnostic.emit({"action": "monitor_state", "connected": bool(message.get("connected")),
                                          "armed": bool(message.get("armed")), "permission": self._permission,
                                          "rssi": message.get("rssi"), "state": str(message.get("state", ""))[:100],
                                          "lock": message.get("lock"), "unlock": message.get("unlock"),
                                          "thresholds_ready": bool(message.get("thresholds_ready")),
                                          "disconnect_evidence": str(message.get("disconnect_evidence", "unknown"))[:20],
                                          **{k: message.get(k) for k in
                                             ("owned", "locked", "locking_enabled", "activity_lock_protected", "awaiting_return", "return_confirmed", "reconnect_allowed",
                                              "departure_samples", "required_departure_samples", "rssi_age_seconds",
                                              "gesture_eligible", "gesture_ready", "unlock_ready", "return_wake_pending")},
                                          "error": self._error})
                self._status = str(message.get("state", "正在监测距离"))[:100]
                if message.get("locked") and message.get("unlock_ready") and not self._error:
                    self._status = f"手势已连接，请{self.unlockGestureLabel}解锁"
                self._rssi = str(message.get("rssi", "—"))
                self.syncConnectionState()
                self.syncGestureReadiness()
                if self.reconnect_target:
                    self.reconnectRequested.emit(self.reconnect_target)
                self._watchdog.start(10000)
            else:
                self._status = ("可以开启" if self._target and self._password and self._password_access
                                and self._permission and self._calibration else "请连接戒指并完成校准及下方设置")
        elif kind == "prepare_lock" and self._enabled and self._command == "monitor":
            self._clear_lock_notice()
            if not self.lock_connection_ready:
                self.syncConnectionState()
                proc.write(b"lock-cancel\n")
                return
            self._input_resume_allowed = False
            self._lock_preparing = True
            self._gesture_state = (True, "")
            self.lockPreparing.emit()  # Synchronous UI-thread pause before acknowledging the helper.
            if self._process is proc and self._enabled:
                proc.write(b"lock-ready\n" if self.lock_connection_ready else b"lock-cancel\n")
        elif kind == "locked" and self._enabled:
            self._clear_lock_notice()
            self._input_resume_allowed = False
            self._lock_preparing = False
            self._gesture_state = (True, "")
            self.locked.emit()
        elif kind == "unlocked" and self._enabled:
            self._clear_lock_notice()
            self._input_resume_allowed = True
            self._lock_preparing = False
            self._monitor_received_at = time.monotonic()
            self._gesture_state = (False, "")
            self.unlocked.emit()
        elif kind == "error":
            self._clear_lock_notice()
            self._error = str(message.get("error", "距离监测异常"))[:250]
        self.changed.emit()

    def _update_lock_notice(self, message):
        # Display the helper's authoritative clock; the HUD never initiates a lock.
        if (not self._enabled or self._command != "monitor" or message.get("locked") is not False
                or not message.get("permission") or not message.get("connected")
                or not message.get("locking_enabled") or message.get("error")):
            self._clear_lock_notice()
            return
        seconds = message.get("lock_countdown_seconds", 0)
        if type(seconds) is not int or not 0 <= seconds <= 5:
            self._clear_lock_notice()
            return
        if seconds > 0 and not message.get("lock_pending"):
            self._set_lock_notice("countdown", seconds)
            # Hide a stale HUD if status updates stop before the process watchdog.
            self._lock_notice_timer.start(2500)
        elif message.get("lock_pending"):
            self._clear_lock_notice()
        elif self._lock_notice_state == "countdown":
            self._set_lock_notice("cancelled")
            self._lock_notice_timer.start(1800)

    def _set_lock_notice(self, state, seconds=0):
        if (state, seconds) == (self._lock_notice_state, self._lock_countdown_seconds):
            return
        self._lock_notice_state, self._lock_countdown_seconds = state, seconds
        self.lockNoticeChanged.emit()

    def _clear_lock_notice(self):
        self._lock_notice_timer.stop()
        self._set_lock_notice("")

    def _finished(self, proc, code):
        if proc is not self._process: return
        self._read(proc)
        if proc is not self._process: return  # Reading can finish calibration and launch a new monitor.
        command = self._command
        if command == "calibrate":
            if not self._calibration_complete:
                self._error = self._error or "校准未完成，原校准数据已保留"
            self._finish_calibration(self._error or "校准已保存，重连和重启后沿用")
            proc.deleteLater()
            return
        self._process = None; self._command = ""; self._watchdog.stop(); proc.deleteLater()
        if command == "monitor":
            self._clear_lock_notice()
            self._status = "距离监测已停止"
            self._error = self._error or "监测组件已退出，请关闭后重新开启此功能"
        elif code != 0:
            self._error = self._error or "组件未完成操作，请重新检查"
        elif self._enabled and command == "status" and not self._error:
            self._start_monitor()
        self.changed.emit()

    def _process_error(self, proc):
        if proc is not self._process: return
        self._error = "距离锁屏组件无法运行，请重新构建或安装"
        self._stop_failed_operation()

    def _timeout(self):
        self._error = ("校准组件未响应，原校准数据已保留" if self.calibrating
                       else "距离锁屏组件未响应，已停止自动操作")
        self.diagnostic.emit({"action": "timeout", "error": self._error})
        self._stop_failed_operation()

    def _stop(self):
        self._clear_lock_notice()
        self._input_resume_allowed = False
        self._lock_preparing = False
        self._gesture_state = (False, "")
        self._claimed_token = ""
        self._reconnect_target = ""
        self._sent_baseline_options = None
        self._last_voice_evidence = None
        self._watchdog.stop()
        proc, self._process = self._process, None
        self._command = ""; self._buffer.clear(); self._thresholds = {}
        if proc:
            proc.closeWriteChannel()
            proc.kill()  # No synchronous wait on the UI/gesture thread.
            proc.finished.connect(proc.deleteLater)

    @Slot()
    def close(self):
        self._closed = True; self._stop()
