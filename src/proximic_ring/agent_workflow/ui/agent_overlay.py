"""A bottom-anchored stack of small, focusable Agent windows."""
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, QTimer, QPoint, QRect, QPropertyAnimation, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtQuick import QQuickView

from proximic_ring.ui.shared.overlay_stacking import OverlayVisibilityGuard
from proximic_ring.agent_workflow.agent.workspace import bounded_frame, stack_frames


def _activate_input_owner():
    """Bind native IME activation to the keyed Agent panel, without raising the main UI."""
    if QGuiApplication.platformName() != 'cocoa': return
    from AppKit import NSRunningApplication, NSWorkspace, NSApplicationActivateIgnoringOtherApps
    application = NSRunningApplication.currentApplication()
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    if not front or front.processIdentifier() != application.processIdentifier():
        # A nonactivating panel can own keyboard focus while NSWorkspace still
        # names the previous app. The IME helper uses that PID as its target.
        # isActive can be true for a keyed nonactivating Qt panel while another
        # app is still frontmost. Match the actual target PID, not that flag.
        # Key focus must belong to the panel first; never use AllWindows here.
        application.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)


def _set_input_focus_enabled(window, enabled):
    """Only explicit Agent entry may make a dormant macOS panel key again."""
    if QGuiApplication.platformName() != 'cocoa': return
    disabled = bool(window.flags() & Qt.WindowDoesNotAcceptFocus)
    if disabled == (not enabled): return
    window.setFlag(Qt.WindowDoesNotAcceptFocus, not enabled)
    if not enabled and window.isVisible():
        import ctypes
        import objc
        native = objc.objc_object(c_void_p=ctypes.c_void_p(int(window.winId()))).window()
        # AppKit restores its cached key window on Dock/Cmd-Tab activation even
        # after canBecomeKeyWindow becomes false. Remove that cached entry while
        # retaining the Qt view, editor and visibility. Neither call activates
        # the application or orders the main window.
        native.orderOut_(None)
        native.orderFrontRegardless()


class AgentOverlay(QObject):
    def __init__(self, controller, host, parent=None):
        super().__init__(parent)
        self.controller, self.host = controller, host
        self.sessions = controller.sessions
        self.owners = {}
        from .recommendation_focus import HubFocusSession
        self.focus_session = HubFocusSession(self)
        self._dismissing = set()
        self._local_click_monitor = None
        from .agent_stack_drag import AgentStackDrag
        self.offset = QPoint()
        self.drag = AgentStackDrag(self)
        self.windows = {}
        self.animations = {}
        self.guards = {}
        self.retiring = set()
        self.parked = set()
        self._screens = set()
        self.screen = None
        self._closed = False
        self.reduce_motion = False
        if QGuiApplication.platformName() == 'cocoa':
            try:
                from AppKit import NSWorkspace
                self.reduce_motion = bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
            except (ImportError, AttributeError): pass
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self.layout)
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.timeout.connect(self._settle_focus)
        controller.cardsChanged.connect(self.refresh)
        controller.changed.connect(self.schedule)
        controller.focusRequested.connect(self.focus)
        controller.localFocusRequested.connect(self._focus_local)
        self.sessions.added.connect(self._session_added)
        self.sessions.closing.connect(self._dismiss_session)
        for session in self.sessions.members[1:]: self._session_added(session)
        QGuiApplication.instance().focusWindowChanged.connect(self._focus_changed)
        if QGuiApplication.platformName() == 'cocoa':
            import AppKit as A
            mask = A.NSEventMaskLeftMouseDown | A.NSEventMaskRightMouseDown | A.NSEventMaskOtherMouseDown
            self._local_click_monitor = A.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, self._native_mouse_down)
        QGuiApplication.instance().aboutToQuit.connect(self.close)
        QGuiApplication.instance().screenAdded.connect(self._watch_screen)
        QGuiApplication.instance().screenRemoved.connect(self._screen_removed)
        for screen in QGuiApplication.screens(): self._watch_screen(screen)

    def _session_added(self, session):
        session.cardsChanged.connect(self.refresh)
        session.changed.connect(self.schedule)
        session.focusRequested.connect(lambda key: self.focus(self.sessions.window_key(session, key)))
        session.localFocusRequested.connect(self._focus_local)
        self.refresh()

    def _native_mouse_down(self, event):
        # Native title-bar / first-click activation happens before Qt receives
        # a mouse event. Observe that explicit selection without swallowing it.
        native = event.window()
        if (native is not None and native.level() == 0
                and (self.focus_session.engaged or self.focus_session.return_pending)):
            self.focus_session.discard()
        return event

    def _dismiss_session(self, owner):
        keys = [key for key in self.windows if self.owners[key] is owner]
        self._dismissing.update(keys)
        self._focus_timer.stop()
        if owner is self.sessions.active:
            # Return the temporary application activation before resigning its
            # key panel. Otherwise late AppKit activation can target the main UI.
            self.focus_session.release()
            self._release_focus()
        else:
            for key in keys: _set_input_focus_enabled(self.windows[key], False)

    def _watch_screen(self, screen):
        if screen not in self._screens:
            self._screens.add(screen)
            screen.availableGeometryChanged.connect(self._screen_geometry_changed)
            screen.geometryChanged.connect(self._screen_geometry_changed)
        self.schedule()

    def _screen_removed(self, screen):
        self._screens.discard(screen)
        if self.screen is screen: self.screen = None
        self.schedule()

    def _screen_geometry_changed(self, *_):
        # A Dock/resolution change can invalidate an in-flight frame, too.
        for window in self.windows.values(): self._apply_frame(window,window.geometry())
        self.schedule()

    def _focus_changed(self, window):
        if self._closed: return
        if (self.sessions.picking_attachments if hasattr(self, 'sessions') else self.controller.pickingAttachments): return
        self._focus_timer.stop()
        if window is None:
            # Input-source activation briefly visits its own helper window.
            # Collapsing now disables the TextArea and drops its IME client.
            self._focus_timer.start(25)
            return
        if window in self.windows.values():
            key = next(k for k,v in self.windows.items() if v is window)
            owner = getattr(self, 'owners', {}).get(key, self.controller)
            if key in getattr(self, '_dismissing', ()) or not getattr(owner, 'visible', True):
                return
            if hasattr(self, 'focus_session') and not self.focus_session.engaged:
                self.focus_session.begin()
            owner.workspaceFocused(True)
            owner.windowFocused(key.split('/')[-1], True)
            self.guards[key].refresh()
            # Mouse/key-window entry needs the same native input ownership as
            # the explicit focus command. It must not depend on the main window.
            _activate_input_owner()
            if key.split('/')[-1] == 'task': window.rootObject().focusEditor()
        elif window is not None:
            if hasattr(self, 'focus_session') and not self.focus_session.return_pending:
                self.focus_session.discard()
            self._release_focus()
            owner = self.sessions.active if hasattr(self, 'sessions') else self.controller
            owner.rememberLocalWindow(window)

    def _release_focus(self):
        if hasattr(self, 'focus_session'):
            self.focus_session.release('external_click')
        for key, window in self.windows.items():
            _set_input_focus_enabled(window, False)
            self.guards[key].refresh()
        for owner in self.sessions.members if hasattr(self, 'sessions') else [self.controller]:
            owner.workspaceFocused(False)

    def _settle_focus(self):
        if self._closed: return
        if (self.sessions.picking_attachments if hasattr(self, 'sessions') else self.controller.pickingAttachments): return
        window = QGuiApplication.focusWindow()
        if window is not None:
            self._focus_changed(window)
            return
        source = self.host._inline_input._source_activation
        if source is not None and source.owns_foreground():
            self._focus_timer.start(25)
            return
        self._release_focus()

    def _focus_local(self, window):
        self.prepare_local_focus()
        try:
            if window.isVisible():
                if QGuiApplication.platformName() == 'cocoa':
                    from proximic_ring.platforms.macos.mac_window_focus import focus_window
                    focus_window(window)
                else:
                    window.raise_(); window.requestActivate()
                return
        except RuntimeError: pass
        self.sessions.active.rememberLocalWindow(None)
        self.sessions.active._request_focus('restore')

    def prepare_local_focus(self):
        """Yield input to an explicitly selected host window, keeping all tasks."""
        self.focus_session.discard()
        self._focus_timer.stop()
        for owner in self.sessions.members:
            # A capture or deferred voice-input return requested before this
            # user selection must not reclaim the panel after the main opens.
            owner._focus_epoch += 1
            owner._capture_pending = False
            owner._focus_after_input = False
            owner._input_timer.stop()
        self._release_focus()

    def schedule(self):
        if not self._closed: self._layout_timer.start(0)

    def _create(self, key, owner=None):
        owner = owner or self.controller
        window = QQuickView()
        from .message_document import MessageDocument
        window.message_document = MessageDocument(window)
        window.rootContext().setContextProperty('messageRenderer', window.message_document)
        window.setObjectName('agent-' + key)
        window.setTitle('Mythlink Agent · ' + ('任务' if key.split('/')[-1] == 'task' else '请求'))
        window.setFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        _set_input_focus_enabled(window, False)
        window.setColor(QColor('transparent'))
        window.setResizeMode(QQuickView.SizeRootObjectToView)
        window.rootContext().setContextProperty('agent', owner)
        window.rootContext().setContextProperty('hostController', self.host)
        window.setSource(QUrl.fromLocalFile(str(Path(__file__).parent/'qml/AgentCard.qml')))
        if window.status() == QQuickView.Error:
            raise RuntimeError('\n'.join(str(e) for e in window.errors()))
        window.rootObject().setProperty('motionEnabled',not self.reduce_motion)
        self.windows[key] = window
        self.owners[key] = owner
        window.installEventFilter(self.drag)
        self.guards[key] = OverlayVisibilityGuard(window, self, input_workspace=True,
            diagnostic=lambda error: self.host._append_background_diagnostic('Agent 浮层：' + str(error)))
        window.rootObject().implicitHeightChanged.connect(self.schedule)
        window.rootObject().implicitWidthChanged.connect(self.schedule)
        return window

    def refresh(self):
        if self._closed: return
        entries = list(self.sessions.entries())
        keys = {key for key, _, _ in entries}
        for key in list(self.windows):
            if key not in keys and key not in self.retiring:
                self._retire(key)
        for key, owner, card in entries:
            if owner.visible:
                window = self.windows.get(key) or self._create(key, owner)
                window.rootObject().setProperty('cardInfo', card)
        self.schedule()

    def _retire(self, key):
        self.retiring.add(key)
        self.parked.discard(key)
        window = self.windows[key]
        if self.drag.press_window is window: self.drag.reset()
        owner = self.owners[key]
        was_active = window.isActive()
        window.rootObject().setEnabled(False)
        self._animate(key,'opacity',0.0,duration=160)
        self._animate(key,'geometry',window.geometry().translated(20,0),duration=180)
        def finish():
            if self._closed or self.windows.get(key) is not window: return
            restore_focus = (was_active and owner.focused
                             and QGuiApplication.focusWindow() is window)
            for animation_key, animation in list(self.animations.items()):
                if animation_key[0] == key:
                    animation.stop(); animation.deleteLater(); del self.animations[animation_key]
            self.windows.pop(key)
            self.owners.pop(key)
            self.retiring.discard(key)
            self.guards.pop(key).deleteLater()
            window.hide(); window.setSource(QUrl()); window.close(); window.deleteLater()
            if restore_focus:
                owner.workspaceFocused(True)
                self.focus(self.sessions.window_key(owner, owner.focusKey))
        QTimer.singleShot(0 if self.reduce_motion else 190, finish)

    def _conceal(self, key):
        """Fade a paged-out card; keep its model, editor and draft alive."""
        if key in self.parked: return
        self.parked.add(key)
        window=self.windows[key]
        if self.drag.press_window is window: self.drag.reset()
        window.rootObject().setEnabled(False)
        self._animate(key,'opacity',0.0,duration=160)
        self._animate(key,'geometry',window.geometry().translated(16,0),duration=180)
        def finish():
            if not self._closed and key in self.parked and self.windows.get(key) is window:
                window.hide()
        QTimer.singleShot(0 if self.reduce_motion else 190,finish)

    def _animate(self, key, prop, end, *, duration=240):
        window = self.windows[key]
        running = self.animations.get((key,prop))
        if running and running.endValue() == end:
            return  # Streaming text must not restart an unchanged movement.
        old = self.animations.pop((key,prop), None)
        if old: old.stop(); old.deleteLater()
        start = window.geometry() if prop == 'geometry' else window.property(prop)
        if start == end: return
        if self.reduce_motion:
            if prop == 'geometry': self._apply_frame(window,end)
            else: window.setProperty(prop,end)
            return
        if prop == 'geometry':
            # QWindow exposes x/y/width/height properties, but geometry is a
            # method. Interpolate one QRect to keep all four edges in sync.
            animation = QVariantAnimation(self)
            animation.valueChanged.connect(lambda rect: self._apply_frame(window,rect))
        else:
            animation = QPropertyAnimation(window, prop.encode(), self)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.setStartValue(start)
        animation.setEndValue(end)
        self.animations[key,prop] = animation
        animation.start()

    def _apply_frame(self, window, rect):
        screen=window.screen() or QGuiApplication.primaryScreen()
        if screen:
            area=screen.availableGeometry()
            rect=QRect(*bounded_frame((rect.x(),rect.y(),rect.width(),rect.height()),
                                     (area.x(),area.y(),area.width(),area.height())))
        window.setGeometry(rect)

    def layout(self):
        if self._closed: return
        if self.drag.active: return
        if self.sessions.capture_active:
            # Retire stopped targets, too: _animate deliberately skips an
            # unchanged target, which would otherwise leave a restored card
            # at its new-show opacity of zero after taking a screenshot.
            for animation in self.animations.values():
                animation.stop(); animation.deleteLater()
            self.animations.clear()
            for window in self.windows.values(): window.hide()
            return
        if not any(s.visible for s in self.sessions.members):
            for window in self.windows.values(): window.hide()
            return
        if not self.windows:
            self.refresh(); return
        screens = QGuiApplication.screens()
        if self.screen not in screens:
            self.screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if self.screen is None: return
        area = self.screen.availableGeometry()
        ordered = [key for key, owner, _ in self.sessions.entries() if owner.visible]
        if any(key not in self.windows for key in ordered):
            self.refresh(); return
        for key, window in self.windows.items():
            if key not in ordered and key not in self.retiring: window.hide()
        sizes = {key:(int(self.windows[key].rootObject().implicitWidth()),
                      int(self.windows[key].rootObject().implicitHeight())) for key in ordered}
        active = self.sessions.active
        focus_key = self.sessions.window_key(active, active.focusKey)
        frames=stack_frames(sizes,(area.x(),area.y(),area.width(),area.height()),focus_key)
        bounds = QRect()
        for frame in frames.values(): bounds = bounds.united(QRect(*frame))
        dx = max(area.left()+8-bounds.left(), min(self.offset.x(), area.right()-8-bounds.right()))
        dy = max(area.top()+8-bounds.top(), min(self.offset.y(), area.bottom()-8-bounds.bottom()))
        self.offset = QPoint(dx, dy)
        for key in ordered:
            window = self.windows[key]
            if key not in frames:
                self._conceal(key); continue
            self.parked.discard(key)
            window.rootObject().setEnabled(True)
            x,y,w,h = frames[key]
            x += dx; y += dy
            window.setScreen(self.screen)
            if not window.isVisible():
                self._apply_frame(window,QRect(x+16,y+10,w,h)); window.setOpacity(0)
                window.show()
            self._animate(key,'geometry',QRect(x,y,w,h))
            self._animate(key,'opacity',1.0 if self.sessions.focused else .83,duration=240)

    def focus(self, key):
        if (self.sessions.capture_active if hasattr(self, 'sessions') else getattr(self.controller, 'captureActive', False)): return
        if hasattr(self, 'focus_session'):
            self._dismissing.discard(key)
            self.focus_session.begin()
        self.refresh(); self.layout()
        window = self.windows.get(key)
        if window:
            if QGuiApplication.platformName() == 'cocoa':
                _set_input_focus_enabled(window, True)
                # Set the nonactivating panel style and order just this window
                # before assigning key focus. Qt.raise_ activates every window
                # in the process, including the main settings UI.
                self.guards[key].refresh()
                from proximic_ring.platforms.macos.mac_window_focus import focus_window
                focus_window(window)
                _activate_input_owner()
            else:
                window.raise_(); window.requestActivate()
            window.rootObject().focusEditor()

    def close(self):
        if self._closed: return
        self._closed = True
        self.drag.reset()
        self.focus_session.discard()
        if self._local_click_monitor is not None:
            import AppKit as A
            A.NSEvent.removeMonitor_(self._local_click_monitor)
            self._local_click_monitor = None
        self._layout_timer.stop()
        self._focus_timer.stop()
        app=QGuiApplication.instance()
        app.screenAdded.disconnect(self._watch_screen)
        app.screenRemoved.disconnect(self._screen_removed)
        for screen in self._screens:
            screen.availableGeometryChanged.disconnect(self._screen_geometry_changed)
            screen.geometryChanged.disconnect(self._screen_geometry_changed)
        for animation in self.animations.values(): animation.stop()
        for window in self.windows.values():
            window.hide(); window.setSource(QUrl()); window.close(); window.deleteLater()
        self.windows.clear()
