"""Nonactivating QML window cards, using the same Cocoa ordering as the Ring HUD."""
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage
from PySide6.QtQuick import QQuickImageProvider, QQuickView

from ..gestures.gesture_hud import foreground_window_bounds, screen_for_window
from .window_previews import WindowPreviews


class AppIcons(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.images = {}

    def prepare(self, cards):
        images = {}
        if sys.platform != "darwin": return images
        import AppKit
        for card in cards:
            pid = str(card["pid"])
            if pid in images: continue
            try:
                app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
                icon = app.icon() if app else None
                if icon is None: continue
                rep = AppKit.NSBitmapImageRep.imageRepWithData_(icon.TIFFRepresentation())
                data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
                image = QImage.fromData(bytes(data), "PNG")
                images[pid] = image.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            except Exception:
                pass
        return images

    def requestImage(self, identifier, size, requestedSize):
        image = self.images.get(identifier.split("/")[0], QImage())
        size.setWidth(image.width())
        size.setHeight(image.height())
        return image


class WindowSelectorOverlay(QObject):
    _iconsReady = Signal(int, object)

    def __init__(self, controller, parent=None, *, diagnostic=None):
        super().__init__(parent)
        self.controller = controller
        self._diagnostic = diagnostic or (lambda message: print(message, file=sys.stderr))
        self._epoch = 0
        self._icon_revision = 0
        self._material = None
        self._mouse_monitor = None
        self._panel = None
        self.window = QQuickView()
        self.window.setObjectName("ringWindowSelector")
        self.window.setTitle("Ring 窗口选择")
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus)
        self.window.setColor(QColor("transparent"))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.icons = AppIcons()
        self.window.engine().addImageProvider("windowApps", self.icons)
        self.previews = WindowPreviews(self)
        self.window.engine().addImageProvider("windowPreviews", self.previews.provider)
        self.window.rootContext().setContextProperty("windowSelector", controller)
        self.window.rootContext().setContextProperty("windowPreviews", self.previews)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "qml/WindowSelector.qml")))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError("窗口选择层加载失败：" + "\n".join(str(e) for e in self.window.errors()))
        self._last_pointer = QPointF(QCursor.pos())
        self.window.installEventFilter(self)
        controller.presentRequested.connect(self.show)
        controller.dismissRequested.connect(self.hide)
        controller.pageChanged.connect(self._refresh_cards)
        controller.selectionChanged.connect(lambda: self.previews.select(controller.selected))
        self.previews.permissionRequested.connect(controller.cancel)
        self._iconsReady.connect(self._accept_icons, Qt.QueuedConnection)
        self.window.frameSwapped.connect(self._painted)
        self._awaiting_paint = False
        QGuiApplication.instance().aboutToQuit.connect(self.close)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.MouseMove:
            self._pointer_moved(event.globalPosition(), event.position())
        return super().eventFilter(watched, event)

    def _pointer_moved(self, position, local):
        moved = position != self._last_pointer
        self._last_pointer = QPointF(position)
        if moved and self.window.isVisible() and self.controller.phase == 'ready':
            # Layout/scale/scroll hover events must not reclaim a selection
            # made by gestures under a stationary pointer.
            self.window.rootObject().setProperty('pointerPosition', local)

    def _monitor_mouse(self, native):
        if self._mouse_monitor is not None:
            return
        import AppKit
        # Qt Cocoa discards mouseMoved while NSApp is inactive. Observe events
        # for this panel before Qt filters them; no global hook or AX polling.
        def moved(event):
            if event.window() == native:
                view = native.contentView()
                point = view.convertPoint_fromView_(event.locationInWindow(), None)
                y = point.y if view.isFlipped() else view.bounds().size.height - point.y
                local = QPointF(point.x, y)
                self._pointer_moved(local + QPointF(self.window.position()), local)
            return event
        native.setAcceptsMouseMovedEvents_(True)
        self._mouse_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskMouseMoved, moved)

    def _prepare_native_panel(self, geometry):
        import AppKit as A
        import ctypes
        import objc
        # The nonactivating style must be present at NSPanel creation. Adding
        # it to Qt's existing panel later still lets clicks activate the host.
        # Keep Qt's view and renderer, hosted in a public AppKit panel.
        height = A.NSScreen.screens()[0].frame().size.height
        frame = A.NSMakeRect(geometry.x(), height - geometry.y() - geometry.height(),
                             geometry.width(), geometry.height())
        if self._panel is None:
            view = objc.objc_object(c_void_p=ctypes.c_void_p(int(self.window.winId())))
            panel = A.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                frame, A.NSWindowStyleMaskNonactivatingPanel, A.NSBackingStoreBuffered, False)
            panel.setReleasedWhenClosed_(False)
            panel.setTitle_(self.window.title())
            panel.setBecomesKeyOnlyIfNeeded_(True)
            panel.setContentView_(view)
            self._panel = panel
        else:
            # Qt leaves native geometry to the host when its view is embedded.
            self._panel.setFrame_display_(frame, True)

    def show(self):
        bounds = self.controller._snapshot.get("bounds")
        if bounds is None:
            try:
                rect = foreground_window_bounds()
                bounds = (rect.x(), rect.y(), rect.width(), rect.height()) if rect else None
            except Exception:
                pass
        screen = screen_for_window(QGuiApplication.screens(), QRect(*map(round, bounds)) if bounds else None)
        screen = screen or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            self.controller.cancel()
            return
        already_visible = self.window.isVisible()
        if not already_visible:
            self._last_pointer = QPointF(QCursor.pos())
            self.window.rootObject().setProperty('pointerPosition', QPointF(-1, -1))
            self._epoch += 1
            self._awaiting_paint = True
        self.window.setScreen(screen)
        self.window.setGeometry(screen.geometry())
        if QGuiApplication.platformName() == "cocoa":
            try:
                self._prepare_native_panel(screen.geometry())
            except Exception as exc:
                self._diagnostic(f"[WINDOW SELECTOR] 输入面板创建失败：{type(exc).__name__}")
                self.controller.cancel()
                return
            try:
                if self._material is None:
                    from ..shared.window_material import WindowMaterial
                    self._material = WindowMaterial(self.window)
                self._material.resize()
                self.window.rootObject().setProperty("nativeMaterial", True)
            except Exception as exc:
                self._diagnostic(f"[WINDOW SELECTOR] 磨砂背景不可用：{type(exc).__name__}")
        self.window.rootObject().setProperty("presented", True)
        self.window.show()
        if QGuiApplication.platformName() == "cocoa":
            try:
                from ..shared.notifications import _show_on_macos_spaces
                _show_on_macos_spaces(self.window)
                # Below the hint ring, which remains available via middle pinch.
                import ctypes
                import objc
                from AppKit import NSView
                view = objc.objc_object(c_void_p=ctypes.c_void_p(int(self.window.winId())))
                native = view.window() if isinstance(view, NSView) else view
                native.setLevel_(25)
                self._monitor_mouse(native)
            except Exception as exc:
                self._diagnostic(f"[WINDOW SELECTOR] 显示失败：{type(exc).__name__}")
                self.controller.cancel()
        else:
            self.window.raise_()
        self._refresh_cards()

    def _refresh_cards(self):
        if self.controller.phase == "ready" and self.window.isVisible():
            self._epoch += 1  # Drop icon replies from the previous page.
            self.previews.stop()
            self.previews.setVisibleRange(0, 8)
            self.previews.start(self.controller.cards, self.controller.selected)
            epoch, cards = self._epoch, list(self.controller.cards)
            def icons():
                images = self.icons.prepare(cards)
                try: self._iconsReady.emit(epoch, images or {})
                except RuntimeError: pass
            threading.Thread(target=icons, name="RingWindowIcons", daemon=True).start()

    @Slot(int, object)
    def _accept_icons(self, epoch, images):
        if epoch != self._epoch or not self.window.isVisible(): return
        self.icons.images = images
        self._icon_revision += 1
        self.window.rootObject().setProperty("iconRevision", self._icon_revision)

    @Slot()
    def _painted(self):
        if not self._awaiting_paint: return
        self._awaiting_paint = False
        started = getattr(self.controller, "_started", None)
        if started is not None:
            self.controller.ring.owner._event_log("RING_WINDOW_SELECTOR", action="first_frame",
                                                  elapsed_ms=round((time.monotonic()-started)*1000))

    def hide(self):
        if self._mouse_monitor is not None:
            import AppKit
            AppKit.NSEvent.removeMonitor_(self._mouse_monitor)
            self._mouse_monitor = None
        self._epoch += 1
        self._awaiting_paint = False
        self.previews.stop()
        self.window.rootObject().setProperty("presented", False)
        self.window.hide()
        self.icons.images.clear()

    def close(self):
        self.hide()
        self.previews.close()
        if self._material is not None:
            self._material.close()
            self._material = None
        self.window.close()
        if self._panel is not None:
            self._panel.close()
            self._panel = None
