"""Gesture-owned temporary field selection; one monotonic idle clock."""
import os
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt

from ..mac_permissions import MacPermissionError
from ..text_focus import MESSAGES


class FieldSelectionController(QObject):
    shown = Signal(object)
    exited = Signal(str)
    progress = Signal(float, bool)
    _result = Signal(object, object)

    def __init__(self, fields):
        super().__init__(fields)
        self.fields = fields
        self.active = threading.Event()
        self._lock = threading.Lock()
        self._epoch = 0
        self._deferred = False
        self._working = False
        self._queued = None
        self._token = ""
        self._snapshot = None
        self._serial = 0
        self._deadline = self._motion_deadline = None
        self._shown_ratio = 1.0
        self.clock = time.monotonic
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.tick)
        self._probe = QTimer(self)
        self._probe.setInterval(250)
        self._probe.timeout.connect(self.poll)
        self._result.connect(self.receive, Qt.QueuedConnection)

    def claim(self, name, stamp, *, voice_busy=False):
        """Reserve on the model thread before a Tap can reach the ASR gate."""
        if not self.fields._enabled:
            return None
        with self._lock:
            if not self.active.is_set():
                if name != "swipe-down":
                    return None
                self._epoch += 1
                self.active.set()
                action = "enter"
            elif name == "tap":
                action = "confirm" if self._deferred and not voice_busy else "voice"
            elif name in {"swipe-up", "swipe-down", "swipe-left", "swipe-right"}:
                action = "move"
            else:
                return None
            ticket = {"epoch": self._epoch, "action": action, "stamp": stamp,
                      "direction": name.removeprefix("swipe-"), "expires": self.clock()+1.3}
            if action == "voice":
                self.active.clear()  # Ordinary field is already focused; preserve the existing audio path.
            else:
                self.fields.pending.set()
                self._deadline = None  # Freeze at recognition, before the queued GUI callback.
            return ticket

    def discard(self, ticket):
        if ticket and ticket["epoch"] == self._epoch:
            self.stop("timeout")

    def handle(self, ticket):
        if ticket["epoch"] != self._epoch:
            return
        if ticket["action"] == "voice":
            self.stop("confirm")
            return
        if not self.fields._eligible() or self.clock() > ticket["expires"]:
            self.stop("timeout")
            return
        if self.fields.ring.speech_busy():
            self.stop("timeout")
            self.fields.notice.emit("请先结束本句")
            return
        if ticket["stamp"] is None:
            self.stop("timeout")
            self.fields.notice.emit(MESSAGES["unavailable"])
            return
        self._deadline = None
        self._queued = ticket
        if not self._working:
            self._drain()

    def _drain(self):
        ticket, self._queued = self._queued, None
        if ticket is None:
            return
        if (ticket["epoch"] != self._epoch or not self.fields._eligible()
                or self.clock() > ticket["expires"] or self.fields.ring.speech_busy()):
            self.stop("timeout")
            return
        self.fields.pending.set()
        self._submit(ticket, "focus_selection", action=ticket["action"], expected=ticket["stamp"],
                     selection=self._token, direction=ticket["direction"])

    def _submit(self, ticket, operation, **params):
        self._working = True
        def work():
            try:
                if (operation == "focus_apply" and (ticket["epoch"] != self._epoch
                        or not self.fields._eligible() or self.fields.ring.speech_busy())):
                    result = {"status": "cancelled", "count": 0, "index": 0}
                else:
                    catalog = self.fields.ring.owner._app_gestures.catalog
                    options = {}
                    if catalog.configured_scenes():
                        options["scene_apps"] = catalog.configured_scenes()
                    if catalog.voice_disabled_bundles():
                        options["voice_disabled_apps"] = catalog.voice_disabled_bundles()
                    result = self.fields._channel.call(operation, ignored_pid=os.getpid(),
                                                       **options, **params)
            except MacPermissionError:
                result = {"status": "permission", "count": 0, "index": 0}
            except Exception:
                result = {"status": "unavailable", "count": 0, "index": 0}
            try:
                self._result.emit({**ticket, "operation": operation}, result)
            except RuntimeError:
                pass
        threading.Thread(target=work, name="RingFieldSelection", daemon=True).start()

    @Slot(object, object)
    def receive(self, ticket, result):
        self._working = False
        if ticket["action"] != "inspect" or result.get("status") not in {"available", "focused"}:
            self.fields.ring.owner._event_log(
                "RING_FIELD_SELECTION", action=ticket["action"], operation=ticket["operation"],
                status=result.get("status"), count=result.get("count", 0), index=result.get("index", 0),
                focus_method=result.get("focus_method", ""))
        if ticket["operation"] == "focus_apply":
            self.fields.applying.clear()
        if ticket["epoch"] != self._epoch or not self.active.is_set() or not self.fields._eligible():
            if not self.active.is_set():
                self.fields.pending.clear()
            if self._queued is not None:
                self._drain()
            return
        if self._queued is not None:
            self._drain()
            return
        if result.get("status") not in {"available", "focused"} or not result.get("selection"):
            self.stop("timeout")
            self.fields._set_state(result)
            if result.get("status") not in {"stale", "cancelled"}:
                self.fields.notice.emit(self.fields.hint)
            return
        self.fields._set_state({k: v for k, v in result.items() if k not in {"fields", "frame", "plan"}})
        # The picker can enter a window before the passive 500 ms probe sees
        # it. Hand its accepted scope back so exiting selection isn't mistaken
        # for a new window entry that requires another autofocus.
        self.fields._last_scope = result["scope"]
        self._token = result["selection"]
        if result.get("plan"):
            if self.fields.ring.speech_busy() or self.clock() > ticket["expires"]:
                self.stop("timeout")
                if self.fields.ring.speech_busy():
                    self.fields.notice.emit("请先结束本句")
                return
            self.fields.applying.set()
            self._submit({**ticket, "bounce": result.get("bounce", False)}, "focus_apply", plan=result["plan"])
            return
        if ticket["action"] == "confirm":
            self.stop("confirm")
            return
        with self._lock:
            self._deferred = result["deferred"]
        if ticket["action"] == "inspect":
            if self._snapshot != result:
                self._snapshot = result
                self.shown.emit({**result, "motion": "update", "serial": self._serial})
            return
        self._serial += 1
        motion = ("enter" if ticket["action"] == "enter" else
                  "bounce" if ticket.get("bounce", result.get("bounce")) else "move")
        duration = {"enter": 520, "move": 340, "bounce": 420}[motion]
        self._snapshot = result
        if motion == "enter":
            self._shown_ratio = 1.0
            self.progress.emit(1.0, False)
        self._motion_deadline = self.clock()+duration/1000+1
        self._timer.start()
        self._probe.start()
        self.fields.ring.hideRequested.emit()
        self.shown.emit({**result, "motion": motion, "serial": self._serial,
                         "direction": ticket.get("direction", "down")})

    @Slot(int)
    def landed(self, serial):
        if self.active.is_set() and serial == self._serial and self._motion_deadline is not None:
            self._motion_deadline = None
            self._deadline = self.clock()+5
            if not self._working:
                self.fields.pending.clear()

    def tick(self):
        if not self.active.is_set():
            return
        now = self.clock()
        if self._motion_deadline is not None and now >= self._motion_deadline:
            self.landed(self._serial)
        if self._deadline is None:
            return
        remaining = self._deadline-now
        if remaining <= 0:
            self.stop("timeout")
            return
        ratio = min(1.0, remaining/5)
        self._shown_ratio = (min(ratio, self._shown_ratio+max(.05, (ratio-self._shown_ratio)*.34))
                             if ratio > self._shown_ratio else ratio)
        self.progress.emit(self._shown_ratio, remaining <= 1.2)

    def poll(self):
        if not self.active.is_set() or self._working:
            return
        if not self.fields._eligible() or self.fields.ring.speech_busy():
            self.stop("timeout")
        elif not self.fields.pending.is_set() and self._token:
            self._submit({"epoch": self._epoch, "action": "inspect"}, "focus_selection",
                         action="inspect", selection=self._token)

    def stop(self, reason="timeout"):
        visible = self._snapshot is not None
        with self._lock:
            self._epoch += 1
            self.active.clear()
            self._deferred = False
        self._snapshot = None
        self._token = ""
        self._queued = None
        self._deadline = self._motion_deadline = None
        self._timer.stop()
        self._probe.stop()
        if not self.fields.applying.is_set():
            self.fields.pending.clear()
        if visible:
            self.exited.emit(reason)
        # Keep native focus. Never focus an unconfirmed address bar on exit.
        # Native selection references are bounded and replaced at next entry.
