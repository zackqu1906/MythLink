"""One serial background worker with cache, change coalescing and retry backoff."""
import threading
import time
from pathlib import Path

from .history_contract import load_cache, make_snapshot, save_cache, usable
from .history_source import HistoryUnavailable, read_history
from .history_threads import with_threads

POLL_SECONDS = 15
REFRESH_SECONDS = 300
CHANGE_COOLDOWN = 60
CHANGE_DEBOUNCE = 15


class RefreshPolicy:
    def __init__(self):
        self.last_attempt = float("-inf")
        self.last_success = float("-inf")
        self.fingerprint = ""
        self.pending = ""
        self.changed_at = 0
        self.retry_at = 0
        self.failures = 0

    def due(self, fingerprint, now):
        if fingerprint == self.fingerprint:
            self.pending = ""
        elif not self.pending:
            self.pending, self.changed_at = fingerprint, now
        if not fingerprint or now < self.retry_at:
            return False
        if now - self.last_success >= REFRESH_SECONDS:
            return True
        return (fingerprint != self.fingerprint
                and now - self.last_attempt >= CHANGE_COOLDOWN
                and now - self.changed_at >= CHANGE_DEBOUNCE)

    def success(self, fingerprint, now):
        self.fingerprint, self.last_success = fingerprint, now
        self.pending = ""
        self.failures, self.retry_at = 0, 0

    def failed(self, now):
        self.failures += 1
        self.retry_at = now + min(300, 30 * 2 ** min(self.failures - 1, 4))


class HistoryService:
    def __init__(self, cache_path, backend_factory, callback):
        self.cache_path, self.backend_factory, self.callback = cache_path, backend_factory, callback
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.policy = RefreshPolicy()

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="ComputerHistoryRecommendations", daemon=True)
        self.thread.start()

    def _emit(self, **state):
        if not self.stop.is_set():
            self.callback(state)

    def _clear_cache(self):
        try:
            Path(self.cache_path).unlink(missing_ok=True)
        except OSError:
            pass

    def _run(self):
        backend = None
        snapshot = load_cache(self.cache_path)
        if snapshot:
            self._emit(snapshot=snapshot, message="已载入上次推荐，正在检查近期活动")
            # Restarting the application must not immediately spend another turn.
            age = max(0, time.time() - snapshot["generated_at"])
            self.policy.success(snapshot["fingerprint"], time.monotonic() - age)
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                if now >= self.policy.retry_at:
                    try:
                        backend = backend or self.backend_factory()
                        evidence = read_history(backend.status(), time.time())
                        if evidence["events"] or evidence.get("summaries"):
                            evidence = with_threads(evidence, getattr(backend, "threads", lambda: [])())
                        self._emit(available=True)
                        if not evidence["events"] and not evidence.get("summaries"):
                            snapshot = None
                            self._clear_cache()
                            self.policy.last_success = float("-inf")
                            self._emit(snapshot=None, message="还没有可用的活动记忆，稍后再来看看", clear=True)
                        elif self.policy.due(evidence["fingerprint"], now):
                            self.policy.last_attempt = now
                            self._emit(refreshing=True, message="正在更新下一步推荐")
                            result = backend.generate(evidence, self.stop)
                            if self.stop.is_set():
                                break
                            # If collection stops during an event-based request,
                            # discard it; the next request can use saved memory.
                            status = backend.status()
                            if status.get("state") not in ("running", "paused", "stopped"):
                                raise HistoryUnavailable("Computer History 状态暂不可用")
                            if evidence["events"] and status.get("state") != "running":
                                raise HistoryUnavailable("Computer History 已暂停")
                            snapshot = make_snapshot(result, evidence, time.time())
                            if not usable(snapshot):
                                raise ValueError("活动已过期")
                            save_cache(self.cache_path, snapshot)
                            self.policy.success(evidence["fingerprint"], time.monotonic())
                            message = ("后台推荐已更新" if status.get("state") == "running"
                                       else "采集已暂停，已根据保存的活动记忆更新")
                            self._emit(snapshot=snapshot, refreshing=False, message=message)
                    except HistoryUnavailable as exc:
                        self.policy.failed(time.monotonic())
                        snapshot = None
                        self._clear_cache()
                        self.policy.last_success = float("-inf")
                        self._emit(snapshot=None, available=False, refreshing=False, message=str(exc), clear=True)
                        if backend:
                            backend.close()
                            backend = None
                    except Exception:
                        # No raw history, prompts or transport errors in UI/logs.
                        self.policy.failed(time.monotonic())
                        self._emit(refreshing=False, message="更新暂未完成，稍后自动重试")
                        if backend:
                            backend.close()
                            backend = None
                self.wake.wait(POLL_SECONDS)
                self.wake.clear()
        finally:
            if backend:
                backend.close()

    def close(self):
        self.stop.set()
        self.wake.set()
