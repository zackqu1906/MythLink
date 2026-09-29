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


def helper_path() -> Path:
    root = (Path(sys.executable).resolve().parent.parent / "Helpers" if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[3] / ".build/proximity")
    return root / "ProxiMicPresence.app/Contents/MacOS/ProxiMicPresence"


class ProximityController(QObject):
    changed = Signal()
    diagnostic = Signal(object)
    lockPreparing = Signal()
    locked = Signal()
    unlocked = Signal()
    reconnectRequested = Signal(str)
    _unlockGesture = Signal(object, object, float)

    def __init__(self, parent=None, *, settings, process_factory=QProcess):
        super().__init__(parent)
        self._gesture_state = (False, "")  # Immutable snapshot read by model worker.
        self._gesture_guard = threading.Lock()
        self._claimed_token = ""
        self._unlockGesture.connect(self._apply_unlock_gesture, Qt.QueuedConnection)
        self._settings = settings
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
        self._last_monitor_state = None
        self._monitor_logged_at = 0.0
        defaults = dict(awaySamples=2, lostDelay=15)
        self._values = {}
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

    @Property(str, constant=True)
    def lockScreenMessage(self): return "Ring 自动锁屏后，可弹指（snap）解锁"

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

    def filter_gesture(self, name, connection):
        """Consume every gesture while locking/locked; enqueue only a fresh snap."""
        with self._gesture_guard:
            blocked, token = self._gesture_state
            if not blocked:
                return False
            if name == "snap" and token and token != self._claimed_token:
                self._claimed_token = token
                self._unlockGesture.emit(token, connection, time.monotonic())
        return True

    @Slot(object, object, float)
    def _apply_unlock_gesture(self, token, connection, created):
        owner = self.parent()
        if (self._closed or not self._enabled or self._command != "monitor"
                or self._process is None or self._gesture_state != (True, token)
                or time.monotonic() - created > 1.0 or connection.is_set()
                or (owner is not None and connection is not owner._disconnect_event)):
            with self._gesture_guard:
                if self._claimed_token == token:
                    self._claimed_token = ""
            return
        self._process.write(("gesture-unlock " + token + "\n").encode("ascii"))
        self.diagnostic.emit({"action": "gesture_unlock", "gesture": "snap"})

    def _ready(self):
        if not self._target:
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
        if changed:
            self._stop()
        self._target = identifier; self._name = name or "当前戒指"
        if changed:
            self._load_calibration()
            self._calibration_status = ""
        self._error = ""
        if self._enabled and changed and not self.busy:
            if self._password and self._password_access and self._permission:
                self._start_monitor()
            else:
                self.refresh()
        self.changed.emit()

    @staticmethod
    def _valid_calibration(record):
        if not isinstance(record, dict) or record.get("version") != 1 or record.get("duration_seconds") != 10:
            return False
        samples, baseline = record.get("samples"), record.get("baseline")
        if not isinstance(samples, list) or not 10 <= len(samples) <= 100:
            return False
        if any(type(v) not in (int, float) or not math.isfinite(v) or not -100 <= v <= -20 for v in samples):
            return False
        return (type(baseline) in (int, float) and math.isfinite(baseline)
                and baseline == statistics.median(samples))

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
        if not self._target:
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
            self._finish_calibration("校准已保存，重连和重启后保持不变" if self._calibration_complete
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
        if not self._enabled or self._closed: return
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
                self._stop_failed_operation(); return
            self._calibration = record
            self._calibration_complete = True
            self._calibration_elapsed = 10.0
            self._calibration_samples = len(record["samples"])
            self._status = "校准已保存，重连和重启后保持不变"
            self.changed.emit(); return
        if kind in {"prepare_lock", "locked", "unlocked", "unlock_attempt", "error"}:
            self.diagnostic.emit({"action": kind, "error": str(message.get("error", ""))[:250],
                                  "reason": str(message.get("reason", ""))[:80],
                                  "lock": message.get("lock"), "unlock": message.get("unlock")})
        if kind == "monitor":
            self._monitor_received_at = time.monotonic()
            self._reconnect_target = (self._target if message.get("owned") and message.get("locked")
                                      and message.get("connected") and message.get("reconnect_allowed")
                                      and message.get("return_confirmed")
                                      and not message.get("error") else "")
            token = str(message.get("unlock_token", ""))
            try:
                token = str(UUID(token)) if token else ""
            except ValueError:
                token = ""
            self._gesture_state = (bool(message.get("locked") or message.get("lock_pending")), token)
            if not token:
                self._claimed_token = ""
            self._thresholds = {k: message.get(k) for k in
                                ("thresholds_ready", "baseline", "lock", "unlock", "calibration_samples", "calibration_window",
                                 "calibration_active", "rssi_samples", "raw_rssi", "disconnect_evidence")}
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
                                     "awaiting_return", "return_confirmed", "reconnect_allowed", "gesture_ready", "unlock_ready"))
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
                                             ("owned", "locked", "awaiting_return", "return_confirmed", "reconnect_allowed",
                                              "departure_samples", "required_departure_samples", "rssi_age_seconds",
                                              "gesture_ready", "unlock_ready", "return_wake_pending")},
                                          "error": self._error})
                self._status = str(message.get("state", "正在监测距离"))[:100]
                self._rssi = str(message.get("rssi", "—"))
                self.syncGestureReadiness()
                if self.reconnect_target:
                    self.reconnectRequested.emit(self.reconnect_target)
                self._watchdog.start(10000)
            else:
                self._status = ("可以开启" if self._target and self._password and self._password_access
                                and self._permission and self._calibration else "请连接戒指并完成校准及下方设置")
        elif kind == "prepare_lock" and self._enabled and self._command == "monitor":
            self._gesture_state = (True, "")
            self.lockPreparing.emit()  # Synchronous UI-thread pause before acknowledging the helper.
            if self._process is proc and self._enabled: proc.write(b"lock-ready\n")
        elif kind == "locked" and self._enabled:
            self._gesture_state = (True, "")
            self.locked.emit()
        elif kind == "unlocked" and self._enabled:
            self._gesture_state = (False, "")
            self.unlocked.emit()
        elif kind == "error":
            self._error = str(message.get("error", "距离监测异常"))[:250]
        self.changed.emit()

    def _finished(self, proc, code):
        if proc is not self._process: return
        self._read(proc)
        if proc is not self._process: return  # Reading can finish calibration and launch a new monitor.
        command = self._command
        if command == "calibrate":
            if not self._calibration_complete:
                self._error = self._error or "校准未完成，原校准数据已保留"
            self._finish_calibration(self._error or "校准已保存，重连和重启后保持不变")
            proc.deleteLater()
            return
        self._process = None; self._command = ""; self._watchdog.stop(); proc.deleteLater()
        if command == "monitor":
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
        self._gesture_state = (False, "")
        self._claimed_token = ""
        self._reconnect_target = ""
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
