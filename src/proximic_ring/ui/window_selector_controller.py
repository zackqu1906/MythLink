"""Exclusive, cancellable Ring window selection; all Accessibility IPC stays off Qt."""
import os
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, Qt, Signal, Slot

from ..mac_permissions import MacPermissionError
from ..native_access import NativeAccessChannel
from ..text_focus import foreground_stamp
from ..window_selector import MESSAGES, grid_move
from ..gesture_settings import GESTURE_LABELS


class WindowSelectorController(QObject):
    changed = Signal()
    cardsChanged = Signal()
    selectionChanged = Signal()
    pageChanged = Signal()
    presentRequested = Signal()
    dismissRequested = Signal()
    notice = Signal(str)
    _result = Signal(int, str, object)
    _gesture = Signal(str, int, object, float, bool)

    def __init__(self, ring, *, enabled=None, channel=None, clock=time.monotonic, stamp_reader=None):
        super().__init__(ring)
        self.ring = ring
        ring.changed.connect(self.changed)
        self._enabled = (sys.platform == "darwin" and os.environ.get("PROXIMIC_STARTUP_PROBE") != "1"
                         if enabled is None else enabled)
        self._channel = channel or NativeAccessChannel()
        self._clock = clock
        self._stamp_reader = stamp_reader or foreground_stamp
        self.blocked = threading.Event()
        self._closed = False
        self._epoch = 0
        self._phase = "closed"
        self._snapshot = {}
        self._groups = []
        self._app_index = None
        self._window_selections = {}
        self._selected = 0
        self._columns = 3
        self._connection = None
        self._generation = 0
        self._deadline = 0
        self._remaining = 10
        self._preparing = False
        self._main_window = None
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.poll)
        self._result.connect(self._receive, Qt.QueuedConnection)
        self._gesture.connect(self._handle_gesture, Qt.QueuedConnection)

    @Property("QVariantList", notify=cardsChanged)
    def cards(self):
        if self._app_index is not None:
            return self._groups[self._app_index]["windows"]
        return [group["card"] for group in self._groups]
    @Property(bool, notify=cardsChanged)
    def atApps(self): return self._app_index is None
    @Property(str, notify=cardsChanged)
    def heading(self):
        return "选择应用" if self.atApps else self._groups[self._app_index]["card"]["app"]
    @Property("QVariantMap", notify=changed)
    def globalLabels(self):
        return {key: GESTURE_LABELS[value].split("（")[0] for key, value in self.ring.globalBindings.items()}
    @Property(int, notify=selectionChanged)
    def selected(self): return self._selected
    @Property(int, notify=changed)
    def remaining(self): return self._remaining
    @Property(bool, notify=cardsChanged)
    def partial(self): return bool(self._snapshot.get("partial"))
    @Property(str, notify=changed)
    def phase(self): return self._phase

    def capture_stamp(self):
        try:
            return self._stamp_reader() if self._enabled else None
        except Exception:
            return None

    def set_main_window(self, window):
        self._main_window = window

    def _host_window(self):
        if self._main_window is None or sys.platform != "darwin":
            return {}
        try:
            import ctypes
            import objc
            from PySide6.QtGui import QGuiApplication
            if QGuiApplication.platformName() != "cocoa":
                return {}
            view = objc.objc_object(c_void_p=ctypes.c_void_p(int(self._main_window.winId())))
            return {"number": int(view.window().windowNumber()), "title": self._main_window.title()}
        except Exception:
            return {}  # Never substitute an overlay if the main window is unavailable.

    def prepare(self):
        """Pay native imports after connection, not after the first clench."""
        if not self._enabled or self._closed or self._preparing:
            return
        self._preparing = True
        def warm():
            try:
                if not self._closed: self._channel.call("selector_warmup")
            except Exception:
                pass  # Permission errors are shown only for an actual user request.
            finally:
                self._preparing = False
        threading.Thread(target=warm, name="RingSelectorWarmup", daemon=True).start()

    def _valid(self, epoch):
        owner = self.ring.owner
        return (not self._closed and epoch == self._epoch and self.blocked.is_set()
                and self._connection is owner._disconnect_event and not self._connection.is_set()
                and owner._connected and owner._runtime_active and not self.ring.speech_busy()
                and self._generation == self.ring._generation)

    def start(self, request, connection):
        if self.blocked.is_set() or self._closed:
            return
        if not self._enabled:
            self.notice.emit(MESSAGES["unsupported"])
            return
        if request.stamp is None:
            self.notice.emit(MESSAGES["unavailable"])
            return
        self._connection, self._generation = connection, request.generation
        self._epoch += 1
        self.blocked.set()
        if not self._valid(self._epoch) or time.monotonic() - request.created >= 1:
            self.cancel()
            return
        self._phase = "loading"
        self._snapshot = {}
        self._groups = []
        self._app_index = None
        self._window_selections = {}
        self._started = request.created
        self._deadline = self._clock() + 3
        self.ring.owner._app_gestures.cancel_pending()
        self.ring._fields.picker.stop()
        self.ring.hideRequested.emit()
        self.changed.emit()
        self.cardsChanged.emit()
        self.presentRequested.emit()  # Paint now; do not await Accessibility or images.
        self._timer.start()
        self._submit("selector_list", host_pid=os.getpid(), host_window=self._host_window())

    def enqueue(self, name, connection, busy=False):
        # This runs on the model thread. Every selector gesture is consumed
        # synchronously, especially Tap: it can never start an utterance.
        if name in {"clench", "index-pinch", "middle-pinch", "tap", "swipe-up", "swipe-down", "swipe-left", "swipe-right"}:
            self._gesture.emit(name, self._epoch, connection, time.monotonic(), busy)

    @Slot(str, int, object, float, bool)
    def _handle_gesture(self, name, epoch, connection, created, busy):
        if connection is not self._connection or epoch != self._epoch or time.monotonic() - created > 1:
            return
        if self._phase == "activating":
            return  # Confirmation already owns a short native mutation; no queued gestures.
        if name == "clench":
            self.cancel()
            return
        if not self._valid(epoch) or busy:
            self.cancel()
            return
        if name == "index-pinch":
            if self._phase == "ready": self._touch()
            self.ring.showMenu()
            return
        if self._phase != "ready":
            return
        if name == "tap":
            self.confirm()
        elif name == "middle-pinch":
            self.back()
        elif name.startswith("swipe-"):
            self.pick(grid_move(self._selected, len(self.cards), self._columns, name[6:]))

    @Slot(int)
    def setColumns(self, columns):
        self._columns = max(1, min(6, columns))

    def _touch(self):
        self._deadline = self._clock() + 10
        self._remaining = 10
        self.changed.emit()

    @Slot(int)
    def pick(self, index):
        if self._phase == "ready" and self._valid(self._epoch) and 0 <= index < len(self.cards):
            self._selected = index
            self._touch()
            self.selectionChanged.emit()

    @Slot()
    def confirm(self):
        if self._phase != "ready" or not self._valid(self._epoch):
            return
        if not self.cards:
            return
        if self.atApps and self.cards[self._selected]["windowCount"] > 1:
            self._app_index = self._selected
            windows = self.cards
            self._selected = self._window_selections.get(self._app_index,
                next((i for i, card in enumerate(windows) if card.get("focused")), 0))
            self._change_page()
            return
        self._phase = "activating"
        self._deadline = self._clock() + 3
        self.changed.emit()
        self.dismissRequested.emit()
        self.ring.hideRequested.emit()
        self._submit("selector_activate", token=self._snapshot["token"], target=self.cards[self._selected]["id"])

    @Slot()
    def back(self):
        if self._phase != "ready" or self.atApps or not self._valid(self._epoch):
            return
        self._window_selections[self._app_index] = self._selected
        self._selected = self._app_index
        self._app_index = None
        self._change_page()

    def _change_page(self):
        self._touch()
        self.cardsChanged.emit()
        self.pageChanged.emit()
        self.selectionChanged.emit()

    def _set_snapshot(self, result):
        self._snapshot = result
        grouped = {}
        for card in result["cards"]:
            # Bundle identity merges multiple processes of the same app;
            # missing bundle IDs fall back to PID, never a display-name guess.
            key = ("bundle", card["bundle"]) if card.get("bundle") else ("pid", card["pid"])
            grouped.setdefault(key, []).append(card)
        self._groups = []
        for windows in grouped.values():
            representative = next((c for c in windows if c.get("focused")), windows[0])
            self._groups.append({"windows": windows, "card": {
                **representative, "windowCount": len(windows),
                "title": f"{len(windows)} 个窗口 · Tap " + ("展开" if len(windows) > 1 else "进入")}})
        self._app_index = None
        self._window_selections = {}
        self._selected = next((i for i, group in enumerate(self._groups)
                               if any(c.get("focused") for c in group["windows"])), 0)

    def _submit(self, operation, **params):
        epoch = self._epoch
        def work():
            try:
                result = (self._channel.call(operation, **params) if self._valid(epoch)
                          else {"status": "cancelled"})
            except MacPermissionError:
                result = {"status": "permission"}
            except Exception:
                result = {"status": "unavailable"}
            try:
                self._result.emit(epoch, operation, result)
            except RuntimeError:
                pass
        threading.Thread(target=work, name="RingWindowSelector", daemon=True).start()

    @Slot(int, str, object)
    def _receive(self, epoch, operation, result):
        if epoch != self._epoch or self._closed:
            if operation == "selector_list":
                self._release_session(result.get("token"))
            return
        if not self._valid(epoch):
            if operation == "selector_list":
                self._release_session(result.get("token"))
            self.cancel()
            return
        status = result.get("status", "unavailable")
        if status == "ready" and operation == "selector_list":
            self.ring.owner._event_log("RING_WINDOW_SELECTOR", action="list_ready",
                                      elapsed_ms=round((time.monotonic() - self._started) * 1000),
                                      count=len(result["cards"]))
            self._set_snapshot(result)
            self._phase = "ready"
            self._touch()
            self.cardsChanged.emit()
            self.selectionChanged.emit()
            self.presentRequested.emit()
            return
        self.ring.owner._event_log("RING_WINDOW_SELECTOR", status=status, action=operation,
                                  reason=result.get("reason", ""))
        self.cancel()
        if status not in {"activated", "cancelled"}:
            self.notice.emit(MESSAGES.get(status, MESSAGES["unavailable"]))

    @Slot()
    def poll(self):
        if not self.blocked.is_set(): return
        if not self._valid(self._epoch) or self._clock() >= self._deadline:
            self.cancel()
            return
        if self._phase != "ready": return
        remaining = max(0, int(self._deadline - self._clock() + .999))
        if remaining != self._remaining:
            self._remaining = remaining
            self.changed.emit()

    def _release_session(self, token):
        if not token or self._closed:
            return
        def release():
            try:
                self._channel.call("selector_cancel", token=token)
            except Exception:
                pass  # A stopped/replaced worker has already lost its handles.
        threading.Thread(target=release, name="RingSelectorRelease", daemon=True).start()

    @Slot()
    def cancel(self):
        token = self._snapshot.get("token")
        self._epoch += 1
        self._timer.stop()
        self._phase = "closed"
        self._snapshot = {}
        self._groups = []
        self._app_index = None
        self._window_selections = {}
        self.dismissRequested.emit()
        self.blocked.clear()
        self._release_session(token)
        self.cardsChanged.emit()
        self.changed.emit()

    def close(self):
        self._closed = True
        self.cancel()
        threading.Thread(target=self._channel.close, name="RingSelectorClose", daemon=True).start()
