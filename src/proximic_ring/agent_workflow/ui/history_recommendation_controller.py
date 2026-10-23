"""Instant cached suggestions; Qt owns presentation and explicit task dispatch."""
import time

from PySide6.QtCore import QObject, Property, QTimer, Qt, Signal, Slot

from proximic_ring.runtime_paths import cache_root
from ..history.history_contract import execution_command, load_cache, usable
from ..history.history_service import HistoryService


IDLE_DISMISS_MS = 7000


class HistoryRecommendationController(QObject):
    changed = Signal()
    _result = Signal(int, object)
    _gesture = Signal(str, object, float, int)

    def __init__(self, agent, *, cache_path=None, service_factory=None, overlay_factory=None, codex_opener=None):
        super().__init__(agent)
        self.agent = agent
        self.path = cache_path or cache_root() / "history-recommendations" / "cache.json"
        settings = getattr(agent.owner, "_settings", None)
        self._enabled = settings.value("agent/historyRecommendations", True, type=bool) if settings else True
        self._latest = load_cache(self.path) if self._enabled else None
        self._display = None
        self._items = []
        self._selected = 0
        self._visible = self._refreshing = self._closed = False
        self._message = "后台每五分钟更新，活动变化时提前刷新"
        self._service = None
        self._retiring = None
        self._service_factory = service_factory or HistoryService
        self._overlay_factory = overlay_factory
        self._overlay = None
        self._generation = self._epoch = 0
        self._connection = None
        self._consumed = set()
        self._action_error = ""
        self._codex_opener = codex_opener or self._open_codex
        self._result.connect(self._receive, Qt.QueuedConnection)
        self._gesture.connect(self._apply_gesture, Qt.QueuedConnection)
        agent.focusRequested.connect(self.dismiss)
        self._guard = QTimer(self)
        self._guard.setInterval(250)
        self._guard.timeout.connect(self._reconcile)
        self._restart = QTimer(self)
        self._restart.setSingleShot(True)
        self._restart.setInterval(250)
        self._restart.timeout.connect(self.start)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setTimerType(Qt.PreciseTimer)
        self._idle.setInterval(IDLE_DISMISS_MS)
        self._idle.timeout.connect(self.dismiss)

    @Property(bool, notify=changed)
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        if self._enabled == bool(value):
            return
        self._enabled = bool(value)
        settings = getattr(self.agent.owner, "_settings", None)
        if settings:
            settings.setValue("agent/historyRecommendations", self._enabled)
        if self._enabled:
            self.start()
        else:
            self._generation += 1
            self.dismiss()
            self._restart.stop()
            if self._service:
                self._service.close()
                self._retiring, self._service = self._service, None
            self._refreshing = False
            self._message = "下一步推荐已关闭"
        self.changed.emit()

    @Property(bool, notify=changed)
    def visible(self):
        return self._visible

    @Property(bool, notify=changed)
    def refreshing(self):
        return self._refreshing

    @Property(str, notify=changed)
    def status(self):
        return self._message

    @Property(int, notify=changed)
    def count(self):
        return len(self._items)

    @Property(int, notify=changed)
    def position(self):
        return self._selected + 1 if self._items else 0

    @Property(str, notify=changed)
    def title(self):
        if self._items:
            return self._items[self._selected]["title"]
        return "暂时没有合适的推荐" if self._display else "下一步推荐"

    @Property(str, notify=changed)
    def plan(self):
        return self._action_error or (self._items[self._selected]["plan"] if self._items else self._message)

    @Property(bool, notify=changed)
    def opensCodex(self):
        return bool(self._items and self._items[self._selected].get("action") == "open_codex_thread")

    @Property(str, notify=changed)
    def threadTitle(self):
        return (self._items[self._selected].get("thread_title") or "") if self._items else ""

    @Property(str, notify=changed)
    def actionLabel(self):
        return "回到任务" if self.opensCodex else "交给 Agent"

    @Property(str, notify=changed)
    def interactionHint(self):
        return "↑ ↓ 切换 · tap 打开" if self.opensCodex else "↑ ↓ 切换 · tap 执行"

    @staticmethod
    def _open_codex(thread_id):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ..recording.codex_launch import open_codex_thread
        return open_codex_thread(thread_id, opener=lambda url: QDesktopServices.openUrl(QUrl(url)))

    @Property(str, notify=changed)
    def age(self):
        if not self._display:
            return ""
        minutes = max(0, int((time.time() - self._display["generated_at"]) / 60))
        return "刚刚更新" if minutes == 0 else f"{minutes} 分钟前更新"

    @Slot()
    def start(self):
        if self._closed or not self._enabled or self._service:
            return
        # Rapidly toggling the switch never leaves two inference workers active.
        if self._retiring and self._retiring.thread and self._retiring.thread.is_alive():
            self._restart.start()
            return
        self._retiring = None
        from .agent_controller import find_codex
        from ..history.history_backend import HistoryBackend
        self._generation += 1
        generation = self._generation
        def receive(state):
            if not self._closed:
                try:
                    self._result.emit(generation, state)
                except RuntimeError:
                    pass
        self._service = self._service_factory(self.path, lambda: HistoryBackend(find_codex()), receive)
        self._message = "正在检查 Computer History"
        self._service.start()
        self.changed.emit()

    @Slot(int, object)
    def _receive(self, generation, state):
        if self._closed or not self._enabled or generation != self._generation:
            return
        self._refreshing = state.get("refreshing", self._refreshing)
        self._message = state.get("message", self._message)
        if "snapshot" in state:
            self._latest = state["snapshot"]
            # Freeze nonempty cards while browsing, including their provenance.
            if self._visible and not self._items:
                self._take_snapshot()
        if state.get("clear"):
            self._display = None
            self._items = []
            self._selected = 0
        self.changed.emit()

    def _take_snapshot(self):
        self._action_error = ""
        self._display = self._latest if usable(self._latest) else None
        self._items = [dict(item) for item in (self._display or {}).get("items", [])[:4]
                       if item["id"] not in self._consumed]
        self._selected = 0
        if self._display and not self._items:
            self._message = "暂无合适的下一步，有新活动后会自动更新"

    def _blocked(self):
        owner = self.agent.owner
        ring = getattr(owner, "_ring_gestures", None)
        selector = getattr(ring, "_selector", None)
        stroke = getattr(getattr(owner, "_touchpad", None), "strokeSession", None)
        return (self.agent._closed or self.agent.busy or self.agent._starting
                or self.agent._input_busy() or self.agent._composing
                or self.agent._picking_attachments
                or bool(getattr(self.agent, "contextCapturing", False))
                or bool(stroke and stroke.engaged)
                or bool(selector and selector.blocked.is_set())
                or bool(ring and ring.session_blocked()))

    @Slot()
    def open(self, trigger="manual"):
        if self._closed or not self._enabled or self._blocked():
            return
        if self._visible:
            self.dismiss()
            return
        self.start()
        self._take_snapshot()
        if self._overlay is None:
            from .history_recommendation_overlay import HistoryRecommendationOverlay
            self._overlay = (self._overlay_factory or HistoryRecommendationOverlay)(self)
        self._epoch += 1
        self._connection = self.agent.owner._disconnect_event if trigger == "gesture" else None
        self._visible = True
        self.changed.emit()
        ring = getattr(self.agent.owner, "_ring_gestures", None)
        if ring and hasattr(ring, "hideRequested"):
            ring.hideRequested.emit()
        self._overlay.show()
        if self._visible:
            self.interact()
            self._guard.start()

    @Slot()
    def interact(self):
        """Only user input renews the HUD lifetime; background updates do not."""
        if self._visible and not self._closed:
            self._idle.start()

    @Slot()
    def dismiss(self):
        self._epoch += 1
        self._visible = False
        self._guard.stop()
        self._idle.stop()
        self._connection = None
        self._action_error = ""
        if self._overlay:
            self._overlay.hide()
        self._display = None
        self._items = []
        self.changed.emit()

    @Slot(int)
    def step(self, direction):
        if not self._visible or self._blocked():
            return
        self.interact()
        if not usable(self._display) or not self._items:
            return
        self._selected = (self._selected + (1 if direction > 0 else -1)) % len(self._items)
        self._action_error = ""
        self.changed.emit()

    @Slot()
    def execute(self):
        if not self._visible or self._blocked():
            return
        self.interact()
        if not self._items:
            return
        if not usable(self._display):
            self._reconcile()
            return
        item = self._items[self._selected]
        if item.get("action") == "open_codex_thread":
            self._epoch += 1  # discard queued taps, including when opening fails
            try:
                if self._codex_opener(item["thread_id"]) is False:
                    raise RuntimeError("Codex 未接受打开请求")
            except (ValueError, RuntimeError, OSError):
                self._action_error = "无法打开该 Codex 会话，请确认 Codex 已安装后重试。"
                self.changed.emit()
                return
            self.dismiss()
            return  # navigation never creates a task or sends the plan
        if item["id"] in self._consumed:
            return
        command = execution_command(item, self._display)
        self._consumed.add(item["id"])
        self.dismiss()  # invalidate queued taps before dispatch
        if self.agent._send(command) is False:
            self._consumed.discard(item["id"])
            return
        self.agent._visible = self.agent._focused = True
        self.agent._emit()
        self.agent.focusRequested.emit("task")

    def filter_gesture(self, event, connection):
        name = getattr(event, "name", "")
        invocation = self.agent.owner._global_gesture_bindings.workflow_recommendations
        if not self._enabled or self._closed:
            return False
        if self._visible and name in {"tap", "swipe-up", "swipe-down", invocation}:
            self._gesture.emit(name, connection, time.monotonic(), self._epoch)
            return True
        # Opening uses the shared app-first/global route. The visible panel
        # alone owns its navigation gestures, including its current invocation.
        return False

    @Slot(str, object, float, int)
    def _apply_gesture(self, name, connection, created, epoch):
        if (self._closed or not self._enabled or epoch != self._epoch
                or connection is not self.agent.owner._disconnect_event
                or connection.is_set() or time.monotonic() - created > 1 or self._blocked()):
            return
        invocation = self.agent.owner._global_gesture_bindings.workflow_recommendations
        if name == invocation:
            self.open("gesture")
        elif self._visible and name == "tap":
            self.execute()
        elif self._visible and name in ("swipe-up", "swipe-down"):
            self.step(-1 if name == "swipe-up" else 1)

    def _reconcile(self):
        if self._blocked() or (self._connection and (
                self._connection is not self.agent.owner._disconnect_event or self._connection.is_set())):
            self.dismiss()
        elif self._display and not usable(self._display):
            self._items = []
            self._display = None
            self._message = "推荐已过期，正在等待近期活动"
            self.changed.emit()
        elif self._visible:
            self.changed.emit()

    def close(self):
        self._closed = True
        self._generation += 1
        self._restart.stop()
        self.dismiss()
        if self._service:
            self._service.close()
        if self._overlay:
            self._overlay.close()
