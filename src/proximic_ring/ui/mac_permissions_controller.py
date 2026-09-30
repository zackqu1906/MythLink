"""Live permission status, including while the user is in System Settings."""
from __future__ import annotations

from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
import json
import os
import platform
import sys
import threading

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot, Qt, QBluetoothPermission, QMicrophonePermission
from PySide6.QtGui import QDesktopServices, QGuiApplication

from ..mac_permissions import (MacPermissionError, PermissionState,
                               request_post_event_access, running_identity,
                               read_screen_capture_access, request_screen_capture_access,
                               request_accessibility_access)
from ..native_access import read_control_permission_state


class MacPermissionsController(QObject):
    WAITING_INTERVAL_MS = 1000
    READY_INTERVAL_MS = 10000

    changed = Signal()
    diagnostic = Signal(object)
    _result = Signal(int, object)
    _screenRequestFinished = Signal()
    _screenPreviewFinished = Signal(object, str)
    systemPermissionRequestFinished = Signal(str, bool)
    _systemRequestFinished = Signal(str, bool)

    def __init__(self, parent=None, *, enabled=True, reader=None):
        super().__init__(parent)
        self._enabled = enabled and sys.platform == "darwin"
        self._reader = reader or read_control_permission_state
        self._state = PermissionState()
        self._checked = False
        self._checking = False
        self._closed = False
        self._generation = 0
        self._identity = running_identity()
        self._action_message = ""
        self._device_access = {"bluetooth": None, "microphone": None}
        self._screen_access = None
        self._screen_checked = False
        self._screen_requesting = False
        self._system_request_kind = ""
        self._screen_message = ""
        self._screen_setup_pending = False
        self._screen_preview_cancel = None
        self._screen_preview_verified = False
        self._screen_preview_message = "尚未验证实时预览"
        self._result.connect(self._apply)
        self._screenRequestFinished.connect(self._finish_screen_request, Qt.QueuedConnection)
        self._screenPreviewFinished.connect(self._finish_screen_preview, Qt.QueuedConnection)
        self._systemRequestFinished.connect(self._finish_system_request, Qt.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(self.WAITING_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        app = QGuiApplication.instance()
        if self._enabled and app is not None:
            if isinstance(app, QGuiApplication):
                app.applicationStateChanged.connect(self._on_application_state)
            # Voice input normally runs with another app in front. Waiting for
            # our own window to activate leaves permission feedback stale.
            self._timer.start()
        if self._enabled:
            QTimer.singleShot(0, self.refresh)

    @Property(bool, notify=changed)
    def warning(self):
        return self._enabled and self._checked and not self._state.ready

    @Property(str, notify=changed)
    def accessibilityWarningText(self):
        if not self._enabled or not self._checked:
            return ""
        if self._state.accessibility is False:
            return "未开启辅助功能权限，语音编辑与手势控制可能无法使用。"
        if self._state.post_events is False:
            return "按键控制权限尚未生效，发送与应用快捷键可能无法使用。"
        return ""

    @Property(bool, notify=changed)
    def accessibilityGranted(self):
        return self._enabled and self._checked and self._state.ready

    @Property(str, constant=True)
    def contactEmail(self):
        return "zackqu1906@gmail.com"

    @Slot(result=bool)
    def copyContactEmail(self):
        app = QGuiApplication.instance()
        if not isinstance(app, QGuiApplication):
            return False
        app.clipboard().setText(self.contactEmail)
        return True

    @Property("QVariantList", notify=changed)
    def essentialWarnings(self):
        notices = []
        if self.accessibilityWarningText:
            notices.append(dict(kind="accessibility", text=self.accessibilityWarningText))
        if self._enabled and self._device_access["bluetooth"] is False:
            notices.append(dict(kind="bluetooth", text="未开启蓝牙权限，无法连接 Ring。"))
        if self._enabled and self._device_access["microphone"] is False:
            notices.append(dict(kind="microphone", text="未开启麦克风权限，无法使用电脑音频。"))
        return notices

    def _refresh_device_permissions(self):
        # Passive checks only: never instantiate a Bluetooth manager or request
        # microphone capture just to render a notice. Unknown is not denied.
        app = QGuiApplication.instance()
        if not self._enabled or app is None:
            return
        for kind, permission in (("bluetooth", QBluetoothPermission), ("microphone", QMicrophonePermission)):
            try:
                status = app.checkPermission(permission())
                self._device_access[kind] = ({Qt.PermissionStatus.Granted: True,
                                             Qt.PermissionStatus.Denied: False}).get(status)
            except (RuntimeError, TypeError):
                self._device_access[kind] = None

    def permissionGranted(self, kind):
        if kind == "accessibility":
            return self.accessibilityGranted
        if kind == "screen":
            return self.screenRecordingGranted
        return self._device_access.get(kind) is True

    def refreshDevicePermissions(self):
        self._refresh_device_permissions()
        self.changed.emit()

    @Slot(str)
    def openPermissionSettings(self, kind):
        if kind == "accessibility":
            self.openSettings()
            return
        pane = {"bluetooth": "Privacy_Bluetooth", "microphone": "Privacy_Microphone"}.get(kind)
        if pane and self._enabled and not self._closed:
            QDesktopServices.openUrl(QUrl("x-apple.systempreferences:com.apple.preference.security?" + pane))

    @Slot(result=bool)
    def copyAppInfo(self):
        app = QGuiApplication.instance()
        if not isinstance(app, QGuiApplication):
            return False
        try:
            app_version = version("proximic-ring")
        except PackageNotFoundError:
            app_version = "开发版"
        app.clipboard().setText("MythLink\n版本：" + app_version + "\n系统：" + platform.system() + " "
                               + (platform.mac_ver()[0] if sys.platform == "darwin" else platform.release()))
        return True

    @Property(bool, notify=changed)
    def checking(self):
        return self._checking

    @Property(str, notify=changed)
    def title(self):
        return self._state.title if self._checked else "正在检查辅助功能权限…"

    @Property(str, notify=changed)
    def detail(self):
        return self._state.guidance if self._checked else "检查当前进程的系统权限。"

    @Property(str, constant=True)
    def location(self):
        return self._identity["app_path"] or self._identity["executable"]

    @Property(bool, constant=True)
    def canReveal(self):
        return bool(self._identity["app_path"])

    @Property(str, constant=True)
    def instructions(self):
        if not self._identity["frozen"]:
            return "源码运行：请为启动源码的终端授权。程序会在后台自动检测，权限生效后即可继续使用。"
        prefix = "请先将 App 拖入“应用程序”，退出当前副本，再从“应用程序”打开。" if self._identity["temporary_location"] else ""
        return prefix + ("请为下方路径的 Proximic Voice 授权，程序会自动检测。"
                         "更新后若已勾选但持续未生效，请核对是否授权了当前副本；必要时重新添加新版 App，"
                         "按键通道会自动重新连接，无需退出主程序。输入法更新与此权限独立。")

    @Property(str, notify=changed)
    def actionMessage(self):
        return self._action_message

    @Property(str, notify=changed)
    def screenRecordingStatus(self):
        if not self._screen_checked:
            return "尚未检测屏幕录制权限"
        if self._screen_access is None:
            return "暂时无法确认屏幕录制权限"
        return "屏幕录制权限已生效" if self._screen_access else "屏幕录制权限尚未生效"

    @Property(bool, notify=changed)
    def screenRecordingGranted(self):
        return self._screen_access is True

    @Property(bool, notify=changed)
    def screenRecordingWarning(self):
        return self._enabled and self._screen_checked and self._screen_access is False

    @Property(bool, notify=changed)
    def screenRecordingRequesting(self):
        return self._screen_requesting

    @Property(bool, notify=changed)
    def systemPermissionRequesting(self):
        return bool(self._system_request_kind) or self._screen_requesting or self.screenPreviewBusy

    def requestSystemPermission(self, kind):
        if (not self._enabled or self._closed or self.systemPermissionRequesting
                or kind not in {"accessibility", "screen"}):
            return False
        self._system_request_kind = kind
        self.changed.emit()

        def request():
            success = True
            try:
                (request_accessibility_access if kind == "accessibility" else request_screen_capture_access)()
            except Exception:
                success = False
            try:
                self._systemRequestFinished.emit(kind, success)
            except RuntimeError:
                pass
        threading.Thread(target=request, name="ProxiMicNativePermission", daemon=True).start()
        return True

    @Slot(str, bool)
    def _finish_system_request(self, kind, success):
        if self._closed or self._system_request_kind != kind:
            return
        self._system_request_kind = ""
        self.refresh()
        if kind == "screen":
            self.refreshScreenRecording()
        self.changed.emit()
        self.systemPermissionRequestFinished.emit(kind, success)

    @Property(str, constant=True)
    def screenRecordingInstructions(self):
        if not self._identity["frozen"]:
            return ("源码运行时，系统可能显示 VS Code、终端或 Python，请以授权弹窗中的名称为准。"
                    "开启后若仍未生效，请重新启动对应程序。")
        return ("请在系统设置中允许当前使用的 Proximic Voice，无需分别给被预览的应用授权。"
                "如系统提示，请退出并重新打开应用。")

    @Property(str, notify=changed)
    def screenRecordingMessage(self):
        return self._screen_message

    @Property(bool, notify=changed)
    def screenPreviewBusy(self):
        return self._screen_preview_cancel is not None

    @Property(str, notify=changed)
    def screenPreviewMessage(self):
        return self._screen_preview_message

    def _on_application_state(self, state):
        if state == Qt.ApplicationActive:
            self.refresh()
            self.refreshScreenRecording()

    @Slot()
    def refreshScreenRecording(self):
        if not self._enabled or self._closed:
            return
        try:
            self._screen_access = read_screen_capture_access()
        except Exception:
            self._screen_access = None
        self._screen_checked = True
        if self._screen_access:
            self._screen_message = "屏幕录制权限已生效，可以返回窗口总览查看预览。"
        elif self._screen_message:
            self._screen_message = "权限尚未生效。请确认已开启正确的程序；如系统提示，请重新启动该程序。"
        if self._screen_access is not True:
            self._screen_preview_verified = False
        self.changed.emit()

    @Slot()
    def openScreenRecordingSettings(self):
        if not self._enabled or self._closed or self.systemPermissionRequesting:
            return
        self._screen_setup_pending = True
        self._screen_requesting = True
        self._screen_message = "请在系统提示或设置中确认授权。"
        self.changed.emit()

        def request():
            try:
                request_screen_capture_access()
            except Exception:
                pass  # The settings pane remains a usable fallback.
            try:
                self._screenRequestFinished.emit()
            except RuntimeError:
                pass
        threading.Thread(target=request, name="ProxiMicScreenPermission", daemon=True).start()

    @Slot()
    def verifyScreenPreview(self):
        if not self._enabled or self._closed or self.systemPermissionRequesting:
            return
        self._screen_setup_pending = False
        self.refreshScreenRecording()
        if not self._screen_access:
            self._screen_preview_message = "请先完成屏幕录制授权，再验证预览。"
            self.changed.emit()
            return
        self._screen_preview_verified = False
        cancel = threading.Event()
        self._screen_preview_cancel = cancel
        self._screen_preview_message = "正在验证预览；如弹出含 bypass 的系统提示，请亲自点击 Allow／允许。"
        self.changed.emit()
        def work():
            try:
                from ..screen_preview_check import verify_screen_preview
                result = verify_screen_preview(cancel)
            except Exception:
                result = "unavailable"
            try:
                self._screenPreviewFinished.emit(cancel, result)
            except RuntimeError:
                pass
        threading.Thread(target=work, name="ProxiMicPreviewCheck", daemon=True).start()

    @Slot(object, str)
    def _finish_screen_preview(self, cancel, result):
        if self._closed or cancel is not self._screen_preview_cancel:
            return
        self._screen_preview_cancel = None
        self._screen_preview_verified = result == "verified"
        self._screen_preview_message = {
            "verified": "本次实时预览验证通过，测试已停止。系统以后仍可能再次要求确认。",
            "permission": "屏幕录制权限尚未生效，请核对授权并按系统提示重新启动。",
            "no_window": "请在当前屏幕打开另一个应用窗口，再点击验证。",
            "timeout": "尚未完成预览验证。请处理系统授权提示后重试。",
            "cancelled": "已取消预览验证。",
        }.get(result, "未能取得预览画面，请确认系统提示已允许，或换一个应用窗口重试。")
        self.refreshScreenRecording()

    @Slot()
    def cancelScreenPreview(self):
        self._screen_setup_pending = False
        if self._screen_preview_cancel is not None:
            self._screen_preview_cancel.set()
            self._screen_preview_cancel = None
            self._screen_preview_message = "已取消预览验证。"
            self.changed.emit()

    @Slot()
    def _finish_screen_request(self):
        if self._closed:
            return
        self._screen_requesting = False
        self.refreshScreenRecording()
        if not QDesktopServices.openUrl(QUrl("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")):
            self._screen_message = "请手动打开系统设置 → 隐私与安全性 → 屏幕与系统音频录制。"
        self.changed.emit()

    @Slot()
    def refresh(self):
        if not self._enabled or self._closed or self._checking:
            return
        self._checking = True
        self._refresh_device_permissions()
        self._generation += 1
        generation = self._generation
        self.changed.emit()

        def work():
            try:
                value = self._reader()
            except Exception as exc:
                value = PermissionState(error=type(exc).__name__)
            try:
                self._result.emit(generation, value)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="ProxiMicPermissions", daemon=True).start()

    @Slot(int, object)
    def _apply(self, generation, state):
        if self._closed or generation != self._generation:
            return
        self._checking = False
        changed = not self._checked or state != self._state
        was_waiting = self._checked and not self._state.ready
        was_ready = self._checked and self._state.ready
        self._checked, self._state = True, state
        interval = self.READY_INTERVAL_MS if state.ready else self.WAITING_INTERVAL_MS
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)
        if state.ready and was_waiting:
            # Recovery only updates readiness. A failed send/edit may now have
            # a stale target, so never replay it when permission arrives.
            self._action_message = "权限已生效，可以继续使用；刚才未完成的操作请重新触发。"
        elif was_ready and not state.ready:
            self._action_message = "权限已失效，重新授权后会自动检测。"
        if changed:
            self.diagnostic.emit({"kind": "permissions", **asdict(state), **self._identity, "pid": os.getpid()})
        self.changed.emit()

    def report_error(self, error):
        if not isinstance(error, MacPermissionError) or self._closed:
            return
        # A key failure is newer than any pending passive check.
        self._generation += 1
        self._apply(self._generation, error.state)

    @Slot()
    def openSettings(self):
        if not self._enabled or self._closed or self.systemPermissionRequesting:
            return
        try:
            request_post_event_access()
            self._action_message = "授权后会在后台自动检测，权限生效后即可继续使用。"
        except Exception as exc:
            self._action_message = "系统权限请求未完成：" + type(exc).__name__
        if not QDesktopServices.openUrl(QUrl("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")):
            self._action_message = "请手动打开系统设置 → 隐私与安全性 → 辅助功能。"
        self.refresh()
        self.changed.emit()

    @Slot()
    def revealApplication(self):
        if self.canReveal:
            import AppKit
            AppKit.NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(
                [AppKit.NSURL.fileURLWithPath_(self._identity["app_path"])])

    @Slot()
    def copyDiagnostics(self):
        app = QGuiApplication.instance()
        if isinstance(app, QGuiApplication):
            data = {"checked": self._checked, **asdict(self._state), **self._identity,
                    "pid": os.getpid(), "macos": platform.mac_ver()[0]}
            app.clipboard().setText(json.dumps(data, ensure_ascii=False, indent=2))
            self._action_message = "已复制权限状态和运行路径，不包含听写或聊天内容。"
            self.changed.emit()

    def close(self):
        self._closed = True
        self.cancelScreenPreview()
        self._timer.stop()
        app = QGuiApplication.instance()
        if self._enabled and isinstance(app, QGuiApplication):
            app.applicationStateChanged.disconnect(self._on_application_state)
