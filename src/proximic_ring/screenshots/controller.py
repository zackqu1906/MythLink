"""Screenshot transaction: freeze → two corners → preview → destination.

The generation and phase revision travel with queued gestures/clicks. A swipe
made while selecting cannot confirm a later preview, and a cancelled capture
cannot save a result after its worker completes. Each destination owns its
completion behavior. Full-screen frames live only in memory.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QGuiApplication, QCursor
from PySide6.QtWidgets import QPushButton

from ..gestures.gesture_settings import GESTURE_LABELS
from ..ring_devices import matches_tap_gesture

from .backend import capture_screens, CapturePermissionError
from .geometry import Selection
from .overlay import CaptureOverlay


class ScreenCaptureController(QObject):
    _ready = Signal(int, object, object)
    _input = Signal(object, str, object, float)

    def __init__(self, owner, parent=None):
        super().__init__(parent or owner)
        self.owner = owner
        self.active = False
        self.phase = 'idle'
        self.notice = ''
        self.selection = None
        self.selected_screen = -1
        self.windows = []
        self.epoch = self.revision = self.pointer_revision = 0
        self.cancelled = threading.Event()
        self.backend = capture_screens
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ScreenCapture')
        self._started_pointer = False
        self._connection = None
        self.exit_gesture = ''
        self._ready.connect(self._loaded, Qt.QueuedConnection)
        self._input.connect(self._handle_input, Qt.QueuedConnection)
        owner.connectedChanged.connect(self._connection_changed)
        app = QGuiApplication.instance()
        app.screenAdded.connect(self._screen_changed)
        app.screenRemoved.connect(self._screen_changed)
        self.screens = []

    @property
    def context(self):
        return ((self.epoch, self.revision), 'capture')

    @property
    def pointer_context(self):
        # Consecutive SDK clicks share an owner across selection phases. Reset
        # and exit invalidate it; firmware confirmation still uses context.
        phase = 'loading' if self.phase == 'preparing' else 'capture'
        return ((id(self), self.epoch, self.pointer_revision), phase)

    confirm_label = '保存并复制 →'
    confirm_hint_action = '保存并复制后退出'

    @property
    def confirm_hint(self):
        gesture = '食指捏合' if getattr(self.owner, '_device_family', '') == 'R1' else 'tap'
        return f'{gesture} / 右滑{self.confirm_hint_action}'

    @property
    def exit_context(self):
        # Exit remains valid across loading/selecting/preview, but not sessions.
        return ((id(self), self.epoch), 'capture-exit')

    @property
    def exit_hint(self):
        name = GESTURE_LABELS.get(self.exit_gesture, '')
        return f'左滑 / {name}退出' if name else '左滑退出'

    @property
    def preview_hint(self):
        return f'点击画面重新选区\n{self.confirm_hint}'

    def can_start(self, key):
        return not self.owner._ring_gestures.speech_busy()

    def began(self, key): pass
    def restore(self): pass
    def report_error(self, error): raise NotImplementedError
    def save_crop(self, crop): raise NotImplementedError

    def start(self, key=None):
        if self.active or not self.can_start(key):
            return
        touchpad = self.owner._touchpad
        if touchpad._capture_target is not None:
            self.report_error('请先结束当前截图。')
            return
        if touchpad.strokeSession.engaged or touchpad.inputMode != 'pointer':
            self.report_error('请先完成当前笔画输入，再截图。')
            return
        self.screens = list(QGuiApplication.screens())
        if not self.screens:
            self.report_error('没有可截图的显示器。'); return
        self.epoch += 1
        self.active = True
        self.cancelled = threading.Event()
        self._connection = self.owner._disconnect_event if self.owner.connected else None
        self.selection = None
        self.selected_screen = -1
        self._phase('preparing')
        try:
            self.began(key)
            specs = []
            for screen in self.screens:
                rect = screen.geometry()
                available = screen.availableGeometry()
                specs.append({'geometry': (rect.x(), rect.y(), rect.width(), rect.height()),
                              'available_geometry': (available.x(), available.y(), available.width(), available.height()),
                              'scale': screen.devicePixelRatio()})
                screen.geometryChanged.connect(self._screen_changed)
                screen.availableGeometryChanged.connect(self._available_geometry_changed)
            self.windows = [CaptureOverlay(self, i, spec) for i, spec in enumerate(specs)]
            current = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            # Key a new panel BEFORE hiding the task, so AppKit never needs to
            # choose the main settings window as a replacement key window.
            for i, window in enumerate(self.windows):
                window.present(focus=self.screens[i] is current)
            self.presented()
            self._started_pointer = not touchpad.active and touchpad.available
            touchpad._capture_epoch += 1
            touchpad._capture_target = self
            touchpad._sync_voice_gate()
            touchpad.changed.emit()
            if self._started_pointer: touchpad.start()
            excluded = self._excluded_windows()
            epoch, cancelled = self.epoch, self.cancelled
            future = self.worker.submit(self.backend, specs, excluded, cancelled)
            def done(result):
                try: images, error = result.result(), None
                except Exception as exc: images, error = None, exc
                try: self._ready.emit(epoch, images, error)
                except RuntimeError: pass
            future.add_done_callback(done)
        except Exception as exc:
            self._fail(exc)

    @staticmethod
    def _excluded_windows():
        if QGuiApplication.platformName() != 'cocoa': return set()
        from proximic_ring.agent_workflow.ui.recommendation_focus import native_window
        # Exclude only our floating input surfaces, not the user's main app.
        result = set()
        for window in QGuiApplication.topLevelWindows():
            if (window.flags() & Qt.WindowType_Mask) in (Qt.Tool, Qt.Dialog):
                result.add(int(native_window(window).windowNumber()))
        return result

    def _loaded(self, epoch, images, error):
        if not self.active or epoch != self.epoch: return
        if error is not None: self._fail(error); return
        if len(images) != len(self.windows) or any(image.isNull() for image in images):
            self._fail(RuntimeError('截图不完整，请重试')); return
        for window, image in zip(self.windows, images): window.image = image
        self._phase('locating')

    def _phase(self, phase, notice=''):
        self.phase, self.notice = phase, notice
        self.revision += 1
        for window in self.windows: window.refresh()

    def presented(self): pass

    def select_point(self, screen, x, y):
        """Only a click starts/restarts a rectangle or fixes its second corner."""
        if not self.active or self.phase not in ('locating', 'selecting', 'preview'): return
        if self.phase in ('locating', 'preview'):
            self.selected_screen = screen
            self.selection = Selection(self.windows[screen].spec['geometry'])
            self.selection.add(x, y)
            self._phase('selecting')
        elif screen != self.selected_screen:
            self._phase('selecting', '请在同一屏幕点击另一个角。')
        else:
            self.move(x, y)
            if self.selection.valid: self._phase('preview')
            else: self._phase('selecting', '选区太小，请移动光标后再次点击另一个角。')

    def move(self, x, y):
        if self.active and self.phase == 'selecting':
            self.selection.set_endpoint(x, y)
            self.windows[self.selected_screen].update()

    def reset(self, notice=''):
        if not self.active or self.phase not in ('selecting', 'preview'): return
        self.selection = None; self.selected_screen = -1
        self.pointer_revision += 1
        self._phase('locating', notice if isinstance(notice, str) else '')

    def back(self):
        """Left swipe always exits without saving, including between clicks."""
        self.cancel()

    def confirm(self):
        if not self.active or self.phase != 'preview': return
        try:
            image = self.windows[self.selected_screen].image
            crop = image.copy(*self.selection.pixels(image.width(), image.height()))
            self.save_crop(crop)
        except Exception as exc:
            self.notice = str(exc); self._refresh(); return
        self.cancel()

    def consume(self, event, connection):
        """BLE thread: queue only; never crop, read cursor, or call native APIs."""
        if not self.active: return False
        name = getattr(event, 'name', '')
        if name == 'swipe-left' or (self.exit_gesture and name == self.exit_gesture):
            self._input.emit(self.exit_context, 'exit-capture', connection, time.monotonic())
            return True
        # Ring gestures and pointer clicks are separate inputs. The tap
        # gesture confirms; SDK pointer clicks select corners or use buttons.
        self._input.emit(self.context, name, connection, time.monotonic())
        return True  # Even unmapped gestures cannot reach voice/app shortcuts.

    def pointer_click(self, context):
        self._input.emit(context, 'pointer-click', self._connection, time.monotonic())

    def _handle_input(self, context, name, connection, created):
        expected = (self.exit_context if name == 'exit-capture' else
                    self.pointer_context if name == 'pointer-click' else self.context)
        if (not self.active or context != expected or time.monotonic() - created > 1
                or (connection is not None and (connection is not self.owner._disconnect_event
                                                or connection.is_set()))): return
        if name == 'exit-capture':
            self.cancel()
        elif name == 'pointer-click':
            point = QCursor.pos()
            for window in self.windows:
                if window.geometry().contains(point):
                    child = window.childAt(window.mapFromGlobal(point))
                    if isinstance(child, QPushButton):
                        if child.isEnabled(): child.click()
                    elif not window.toolbar.geometry().contains(window.mapFromGlobal(point)):
                        self.select_point(window.index, point.x(), point.y())
                    break
        elif name == 'swipe-right' or matches_tap_gesture(name, getattr(self.owner, '_device_family', '')):
            self.confirm()
        elif name == 'swipe-left': self.back()

    def _refresh(self):
        for window in self.windows: window.refresh()

    def _connection_changed(self):
        if self.active and self._connection is not None and (not self.owner.connected
                or self._connection is not self.owner._disconnect_event or self._connection.is_set()):
            self._fail(RuntimeError('Ring 已断开，截图已取消。'))

    def _screen_changed(self, *_):
        if self.active: self._fail(RuntimeError('显示器布局已变化，请重新截图。'))

    def _available_geometry_changed(self, *_):
        # Moving/resizing the Dock changes the usable area, not screenshot
        # coordinates. Reposition controls without losing the current selection.
        if not self.active: return
        for screen, window in zip(self.screens, self.windows):
            area = screen.availableGeometry()
            window.spec['available_geometry'] = (area.x(), area.y(), area.width(), area.height())
            window.refresh()

    def _fail(self, error):
        self.report_error(str(error))
        self.cancel()
        if isinstance(error, CapturePermissionError):
            self.owner._inline_input.permissions.openScreenRecordingSettings()

    def cancel(self, *, restore=True):
        if not self.active: return
        self.active = False; self.epoch += 1; self.phase = 'idle'
        self.cancelled.set()
        touchpad = self.owner._touchpad
        if touchpad._capture_target is self:
            touchpad._capture_target = None
            touchpad._sync_voice_gate()
            touchpad.changed.emit()
        if self._started_pointer: touchpad.stop()
        self._started_pointer = False
        for screen in self.screens:
            try: screen.geometryChanged.disconnect(self._screen_changed)
            except (RuntimeError, TypeError): pass
            try: screen.availableGeometryChanged.disconnect(self._available_geometry_changed)
            except (RuntimeError, TypeError): pass
        self.screens = []
        if restore: self.restore()
        windows, self.windows = self.windows, []
        for window in windows:
            window.image = None; window.hide(); window.close(); window.deleteLater()
        self.selection = None

    def close(self):
        self.cancel(restore=False)
        self.worker.shutdown(wait=False, cancel_futures=True)
