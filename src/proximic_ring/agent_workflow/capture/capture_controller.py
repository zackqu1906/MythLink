"""Screenshot transaction: freeze → trace → preview → attach (never submit).

The generation and phase revision travel with queued gestures/clicks. A swipe
made while drawing cannot confirm a later preview, and a cancelled capture
cannot add an attachment after its worker completes. Original task ownership
is pinned just like the file picker. Full-screen frames live only in memory.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QCursor
from PySide6.QtWidgets import QPushButton

from proximic_ring.runtime_paths import cache_root
from .capture_backend import capture_screens, CapturePermissionError
from .capture_geometry import Selection
from .capture_overlay import CaptureOverlay


class CaptureController(QObject):
    _ready = Signal(int, object, object)
    _input = Signal(object, str, object, float)

    def __init__(self, agent):
        super().__init__(agent)
        self.agent = agent
        self.active = False
        self.phase = 'idle'
        self.notice = ''
        self.selection = None
        self.selected_screen = -1
        self.windows = []
        self.epoch = self.revision = 0
        self.cancelled = threading.Event()
        self.backend = capture_screens
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='AgentScreenCapture')
        self._started_pointer = False
        self._connection = None
        self._ready.connect(self._loaded, Qt.QueuedConnection)
        self._input.connect(self._handle_input, Qt.QueuedConnection)
        agent.owner.connectedChanged.connect(self._connection_changed)
        app = QGuiApplication.instance()
        app.screenAdded.connect(self._screen_changed)
        app.screenRemoved.connect(self._screen_changed)
        self.screens = []

    @property
    def context(self):
        return ((self.epoch, self.revision), 'capture')

    def start(self, key):
        agent = self.agent
        if self.active or agent._closed or key != 'task' or key != agent.inputKey:
            return
        touchpad = agent.owner._touchpad
        if agent._hold_editor_input() or touchpad.strokeSession.engaged or touchpad.inputMode != 'pointer':
            agent._error = '请先完成当前语音或笔画输入，再截图。'
            agent.changed.emit()
            return
        self.screens = list(QGuiApplication.screens())
        if not self.screens:
            agent._error = '没有可截图的显示器。'; agent.changed.emit(); return
        self.epoch += 1
        self.active = True
        self.cancelled = threading.Event()
        self.target_turn = agent.workspace.draft_turns.get('task', agent.workspace.state.get('turnId', ''))
        self._connection = agent.owner._disconnect_event if agent.owner.connected else None
        self.selection = None
        self.selected_screen = -1
        self._phase('preparing')
        agent._picking_attachments = True
        agent._error = ''
        try:
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
            agent.changed.emit()
            self._started_pointer = not touchpad.active and touchpad.available
            touchpad._capture_epoch += 1
            touchpad._capture_target = self
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

    def tap(self, screen, x, y):
        if not self.active: return
        if self.phase == 'locating':
            self.selected_screen = screen
            self.selection = Selection(self.windows[screen].spec['geometry'])
            self.selection.add(x, y)
            self._phase('drawing')
        elif self.phase == 'drawing':
            if screen != self.selected_screen:
                self.notice = '请回到开始圈选的屏幕，再 tap 完成。'
                self._refresh(); return
            self.selection.add(x, y)
            if self.selection.valid: self._phase('preview')
            else: self.reset('选区太小，请重新圈选。')

    def move(self, x, y):
        if self.active and self.phase == 'drawing':
            self.selection.add(x, y)
            self.windows[self.selected_screen].update()

    def reset(self, notice=''):
        if not self.active or self.phase not in ('drawing', 'preview'): return
        self.selection = None; self.selected_screen = -1
        self._phase('locating', notice if isinstance(notice, str) else '')

    def back(self):
        """Left swipe is layered: discard the selection, then leave capture.

        Drawing already owns a selection, even if it is not a valid rectangle
        yet. Keep the frozen frame for another attempt. With no selection
        (including preparation), exit and invalidate pending capture results.
        """
        if not self.active: return
        if self.phase in ('drawing', 'preview'):
            self.reset('选区已清除 · tap 重新圈选 · 左滑退出')
        else:
            self.cancel()

    def confirm(self):
        if not self.active or self.phase != 'preview': return
        path = None
        try:
            image = self.windows[self.selected_screen].image
            crop = image.copy(*self.selection.pixels(image.width(), image.height()))
            directory = cache_root() / 'desktop-agent' / 'attachments'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f'截图-{uuid4().hex}.png'
            if not crop.save(str(path), 'PNG'): raise OSError('无法保存截图')
            if not self.agent._add_attachments('task', [path], target_turn=self.target_turn):
                raise RuntimeError('任务输入位置已变化，请重试截图')
        except Exception as exc:
            if path is not None: path.unlink(missing_ok=True)
            self.notice = str(exc); self._refresh(); return
        self.cancel()

    def consume(self, event, connection):
        """BLE thread: queue only; never crop, read cursor, or call native APIs."""
        if not self.active: return False
        name = getattr(event, 'name', '')
        # Active Touchpad emits an SDK click as well as a firmware tap. Only
        # the SDK click owns capture tapping; otherwise one tap starts AND ends.
        pointer = self.agent.owner._touchpad.active
        if name != 'tap' or not pointer:
            self._input.emit(self.context, name, connection, time.monotonic())
        return True  # Even unmapped gestures cannot reach voice/app shortcuts.

    def pointer_click(self, context):
        self._input.emit(context, 'pointer-click', self._connection, time.monotonic())

    def _handle_input(self, context, name, connection, created):
        if (not self.active or context != self.context or time.monotonic() - created > 1
                or (connection is not None and (connection is not self.agent.owner._disconnect_event
                                                or connection.is_set()))): return
        if name in ('tap', 'pointer-click'):
            point = QCursor.pos()
            for window in self.windows:
                if window.geometry().contains(point):
                    child = window.childAt(window.mapFromGlobal(point))
                    if isinstance(child, QPushButton):
                        if child.isEnabled(): child.click()
                    elif not window.toolbar.geometry().contains(window.mapFromGlobal(point)):
                        self.tap(window.index, point.x(), point.y())
                    break
        elif name == 'swipe-right': self.confirm()
        elif name == 'swipe-left': self.back()

    def _refresh(self):
        for window in self.windows: window.refresh()

    def _connection_changed(self):
        if self.active and self._connection is not None and (not self.agent.owner.connected
                or self._connection is not self.agent.owner._disconnect_event or self._connection.is_set()):
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
        self.agent._error = str(error)
        self.cancel()
        if isinstance(error, CapturePermissionError):
            self.agent.owner._inline_input.permissions.openScreenRecordingSettings()

    def cancel(self, *, restore=True):
        if not self.active: return
        self.active = False; self.epoch += 1; self.phase = 'idle'
        self.cancelled.set()
        touchpad = self.agent.owner._touchpad
        touchpad._capture_target = None
        if self._started_pointer: touchpad.stop()
        self._started_pointer = False
        for screen in self.screens:
            try: screen.geometryChanged.disconnect(self._screen_changed)
            except (RuntimeError, TypeError): pass
            try: screen.availableGeometryChanged.disconnect(self._available_geometry_changed)
            except (RuntimeError, TypeError): pass
        self.screens = []
        self.agent._picking_attachments = False
        if restore and not self.agent._closed:
            self.agent._emit()
            self.agent.focusRequested.emit('task')
            QTimer.singleShot(0, self.agent._reconcile_input)
        windows, self.windows = self.windows, []
        for window in windows:
            window.image = None; window.hide(); window.close(); window.deleteLater()
        self.selection = None

    def close(self):
        self.cancel(restore=False)
        self.worker.shutdown(wait=False, cancel_futures=True)
