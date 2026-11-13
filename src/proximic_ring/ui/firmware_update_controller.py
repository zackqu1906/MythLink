"""Qt OTA lifecycle: release input transport, update exact ring, restore input."""
from __future__ import annotations

import asyncio
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, Qt, Signal, Slot

from ..ble_runtime import run_ble
from ..firmware.bundle import load_bundle

PHASE_LABELS = {
    'preflight': '正在校验固件', 'connecting': '正在连接升级服务',
    'uploading': '正在传输固件', 'marking': '正在准备试运行',
    'rebooting': '正在重启戒指', 'verifying': '正在验证新固件',
    'reconnecting': '正在等待戒指重连', 'complete': '固件验证完成',
}


class FirmwareUpdateController(QObject):
    changed = Signal()
    _progress = Signal(object)
    _finished = Signal(object, str)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._busy = self._error = False
        self._message = self._current_version = ''
        self._percent = 0.0
        self._phase = 'idle'
        self._source = self._connection = self._bundle = None
        self._worker = self._runtime_worker = None
        self._result = None
        self._operation_error = ''
        self._deadline = 0.0
        self._target = self._target_name = ''
        try:
            self._bundle = load_bundle()
        except Exception as exc:
            self._error, self._message = True, f'随包固件不可用：{exc}'
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)
        self._progress.connect(self._show_progress, Qt.QueuedConnection)
        self._finished.connect(self._updated, Qt.QueuedConnection)
        for signal in (owner.connectedChanged, owner.busyChanged, owner.batteryChanged,
                       owner.interactionChanged, owner.statusChanged):
            signal.connect(self.changed)
        self.changed.connect(owner.connectionChanged)

    @Property(bool, notify=changed)
    def busy(self): return self._busy

    @Property(bool, notify=changed)
    def error(self): return self._error

    @Property(str, notify=changed)
    def message(self): return self._message

    @Property(str, notify=changed)
    def phase(self): return self._phase

    @Property(float, notify=changed)
    def progress(self): return self._percent

    @Property(str, notify=changed)
    def bundledVersion(self): return self._bundle.version if self._bundle else '未包含固件'

    @Property(str, notify=changed)
    def currentVersion(self):
        if self._busy: return self._current_version
        if not self.owner.connected: return '未连接'
        if self._connection is not self.owner._disconnect_event: return '正在读取…'
        return self._current_version

    @Property(str, notify=changed)
    def targetName(self): return self._target_name if self._busy else self.owner.deviceName

    @Property(str, notify=changed)
    def unavailableReason(self):
        if self._busy: return '固件更新进行中'
        if not self._bundle: return '当前应用没有可用的随包固件'
        if not self.owner.connected: return '请先连接需要更新的戒指'
        if self.owner.busy: return '请等待设备准备完成'
        if (self._source is None or self._connection is not self.owner._disconnect_event
                or self._connection.is_set()):
            return '正在读取设备信息'
        if self.owner._utterance_active or self.owner.interactionCanCancel:
            return '请先完成当前语音输入或编辑'
        if not self.owner.batteryAvailable: return '等待读取戒指电量'
        if self.owner.batteryPercentage < 30: return '请先将戒指电量充至 30% 以上'
        return ''

    @Property(bool, notify=changed)
    def canStart(self): return not self.unavailableReason

    @Slot(object, object)
    def attach(self, source, connection):
        if connection is not self.owner._disconnect_event or connection.is_set():
            return
        self._source, self._connection = source, connection
        self._current_version = getattr(source, 'firmware_identity', {}).get('version', '') or '未知'
        self.changed.emit()

    @Slot()
    def start(self):
        if not self.canStart:
            return
        try:
            bundle = load_bundle()  # Recheck the exact bytes before releasing the ring.
            from ring_python_sdk.ota import OtaClient
            identity = self._source.firmware_identity
            address = identity.get('address', '')
            if address.casefold() != self.owner.selector.casefold():
                raise ValueError('连接设备已变化，请重新连接后更新')
            OtaClient(address)  # Validate the address and dependencies before stopping input.
        except Exception as exc:
            self._error, self._message = True, f'无法开始更新：{exc}'
            self.changed.emit()
            return
        self._bundle = bundle
        self._target, self._target_name = address, self.owner.deviceName
        self._runtime_worker = self.owner._worker
        self._result, self._operation_error = None, ''
        self._busy, self._error, self._percent = True, False, 0.0
        self._phase, self._message = 'preparing', '正在停止语音与手势并释放戒指连接…'
        # Disconnect uses the ordinary cleanup path and retains saved input preferences.
        self.owner.disconnectDevice()
        self.owner._firmware_update_busy = True
        self.owner._append_log(f'开始固件更新：{self._target_name} → {bundle.version}；SHA256={bundle.sha256}')
        self._deadline = time.monotonic() + 45
        self._timer.start()
        self.changed.emit()

    def _poll(self):
        if self._phase == 'preparing':
            alive = self._runtime_worker is not None and self._runtime_worker.is_alive()
            if (not alive and self.owner._worker is None and not self.owner.busy
                    and self._source.transport_released):
                self._timer.stop()
                self._phase = 'connecting'
                target, path, image_hash = self._target, self._bundle.path, self._bundle.image_hash

                def work():
                    result, error = None, ''
                    try:
                        from ring_python_sdk.ota import OtaClient
                        async def update():
                            return await asyncio.wait_for(OtaClient(target).update(
                                path, on_progress=self._progress.emit, verify_timeout_s=90,
                                expected_image_hash=image_hash), 600)
                        result = run_ble(update())
                    except Exception as exc:
                        error = str(exc) or type(exc).__name__
                    self._finished.emit(result, error)
                self._worker = threading.Thread(target=work, name='RingFirmwareUpdate', daemon=True)
                self._worker.start()
                self.changed.emit()
            elif time.monotonic() > self._deadline:
                self._finish('设备连接尚未释放，未开始写入固件。请等待设备断开后重试。')
        elif self._phase == 'restoring':
            if self.owner.connected and not self.owner.busy:
                self._finish(self._operation_error)
            elif (self.owner._worker is None and not self.owner.busy) or time.monotonic() > self._deadline:
                detail = '固件已验证完成，但应用连接未恢复；请手动重新连接。' if self._result else '请手动重新连接戒指后重试。'
                self._finish((self._operation_error + '；' if self._operation_error else '') + detail)

    @Slot(object)
    def _show_progress(self, progress):
        self._phase = progress.phase.value
        if progress.bytes_total:
            self._percent = progress.percent
        self._message = PHASE_LABELS.get(self._phase, progress.message)
        if self._phase == 'uploading':
            self._message += f' · {self._percent:.0f}%'
        self.changed.emit()

    @Slot(object, str)
    def _updated(self, result, error):
        self._result, self._operation_error = result, error
        self._phase, self._message = 'restoring', '正在恢复戒指连接…'
        self._deadline = time.monotonic() + 90
        self.owner._proximity_reconnect_cancelled = False
        self.owner._selected_device = None  # Resolve the same platform UUID/MAC after reboot.
        self.owner._start_selected_device(firmware_restore=True)
        self._timer.start()
        self.changed.emit()

    def _finish(self, error):
        self._timer.stop()
        self._busy = self.owner._firmware_update_busy = False
        self._error = bool(error)
        self._phase = 'failed' if error else 'complete'
        self._message = f'更新未完成：{error}' if error else f'固件已更新并验证：{self._bundle.version}，戒指已重新连接。'
        if not error: self._percent = 100.0
        self.owner._append_log(self._message)
        self.changed.emit()
        self.owner._restore_input_preferences()
