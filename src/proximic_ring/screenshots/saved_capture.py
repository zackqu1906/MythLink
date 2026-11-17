"""Global screenshot destination: durable PNG first, then image clipboard."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import sys

from PySide6.QtCore import Property, Signal, Slot, QStandardPaths, QSaveFile, QIODevice, QUrl, QTimer
from PySide6.QtGui import QGuiApplication, QDesktopServices
from PySide6.QtWidgets import QFileDialog

from .controller import ScreenCaptureController
from .context import SavedScreenshotContext
from ..gestures.gesture_settings import GESTURE_LABELS

DIRECTORY_KEY = 'screenshots/directory'


def default_directory():
    pictures = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
    return Path(pictures or Path.home() / 'Pictures') / 'Mythlink 截图'


def save_png(image, directory):
    """Commit the complete image atomically; a failed save leaves no partial PNG."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')[:-3]
    path = directory / f'截图-{stamp}-{uuid4().hex[:8]}.png'
    output = QSaveFile(str(path))
    if not output.open(QIODevice.WriteOnly):
        raise OSError(f'无法保存截图：{output.errorString()}')
    if not image.save(output, 'PNG'):
        output.cancelWriting()
        raise OSError('无法写入截图，请检查保存位置和可用空间。')
    if not output.commit():
        raise OSError(f'无法完成截图保存：{output.errorString()}')
    return path


class SavedCaptureController(ScreenCaptureController):
    changed = Signal()
    savedNotice = Signal(str)

    def __init__(self, owner, parent=None):
        super().__init__(owner, parent)
        saved = str(owner._settings.value(DIRECTORY_KEY, '') or '')
        self._directory = str(Path(saved).expanduser()) if saved else str(default_directory())
        self._status = self._last_path = ''
        self._source_app = None
        self.saved_context = SavedScreenshotContext(
            owner._modification_dataset.user_root / 'saved_screenshot_context',
            on_changed=self.changed.emit)
        from ..ui.shared.saved_context_clipboard import SavedContextClipboardWatcher
        self._clipboard_watcher = SavedContextClipboardWatcher(self.saveCopiedText, self)

    @Property('QVariantMap', notify=changed)
    def contextRecord(self):
        record = self.saved_context.record()
        path = self.saved_context.root / record.get('image_file', '')
        return {key: record.get(key, '') for key in (
            'capture_id', 'captured_at', 'source', 'status', 'text', 'reason')} | {
                'imageUrl': QUrl.fromLocalFile(str(path)).toString() if path.is_file() else ''}

    def process_saved_capture(self, path):
        """Screenshot migration hook: pass the confirmed, durable PNG path."""
        return self.saved_context.submit(path, self.owner._llm_settings())

    @Slot(str)
    def saveCopiedText(self, text):
        self.saved_context.save_text(text)

    def saved_context_snapshot(self):
        self._clipboard_watcher.flush()
        return self.saved_context.snapshot()

    def close(self):
        self._clipboard_watcher.close()
        self.saved_context.close()
        super().close()

    @Property(str, notify=changed)
    def directory(self): return self._directory

    @Property(str, notify=changed)
    def status(self): return self._status

    @Property(str, notify=changed)
    def lastSavedPath(self): return self._last_path

    def can_start(self, key):
        if not super().can_start(key):
            self.report_error('请先完成当前语音输入，再截图。')
            return False
        agent = getattr(self.owner, '_agent', None)
        if agent is not None and (agent.pickingAttachments or agent.contextCapturing):
            self.report_error('请先完成当前附件操作，再截图。')
            return False
        return True

    def began(self, key):
        self._status = ''
        self._source_app = None
        gesture = self.owner._global_gesture_bindings.screen_capture
        # Built-in screenshot actions win over an overlapping global binding.
        self.exit_gesture = gesture if gesture not in ('tap', 'swipe-left', 'swipe-right') else ''
        if sys.platform == 'darwin' and QGuiApplication.platformName() == 'cocoa':
            from AppKit import NSWorkspace
            self._source_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        self.changed.emit()

    def save_crop(self, crop):
        path = save_png(crop, self.directory)
        self._last_path = str(path)
        try:
            QGuiApplication.clipboard().setImage(crop)
            gesture = self.owner._global_gesture_bindings.clipboard_paste
            label = GESTURE_LABELS.get(gesture, '')
            self._status = ('截图已保存并复制。\n切到目标应用后，用「' + label + '」粘贴图片。'
                            if label else '截图已保存并复制，可在目标应用粘贴图片。')
        except Exception:
            # The durable result remains useful even if clipboard delivery fails.
            self._status = f'截图已保存：{path}；复制到剪贴板失败。'
        self.changed.emit()

        self.process_saved_capture(path)

    def restore(self):
        source, self._source_app = self._source_app, None
        epoch = self.epoch
        def finish():
            if self.active or self.epoch != epoch: return
            if source is not None and not source.isTerminated():
                from AppKit import NSApplicationActivateIgnoringOtherApps
                source.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            if self._status: self.savedNotice.emit(self._status)
        QTimer.singleShot(0, finish)
        self.changed.emit()

    def report_error(self, error):
        self._status = str(error)
        self.changed.emit()
        if not self.active: self.savedNotice.emit(self._status)

    @Slot(str, result=bool)
    def setDirectory(self, directory):
        if self.active or not directory: return False
        path = Path(directory).expanduser()
        if not path.is_absolute() or (path.exists() and not path.is_dir()):
            self.report_error('请选择有效的截图保存文件夹。')
            return False
        self._directory = str(path)
        self.owner._settings.setValue(DIRECTORY_KEY, self._directory)
        self._status = ''
        self.changed.emit()
        return True

    @Slot()
    def chooseDirectory(self):
        if self.active: return
        directory = QFileDialog.getExistingDirectory(None, '选择截图保存文件夹', self.directory)
        if directory: self.setDirectory(directory)

    @Slot()
    def openDirectory(self):
        try:
            path = Path(self.directory)
            path.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError('无法打开截图文件夹。')
        except OSError as exc:
            self.report_error(exc)
