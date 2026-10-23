"""Background freshness, evidence boundaries, safe inference and instant HUD."""
from datetime import datetime, timezone
import json
import os
import queue
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, Signal
from PySide6.QtWidgets import QApplication

from proximic_ring.agent_workflow.history.history_contract import (
    execution_command, load_cache, make_snapshot, save_cache, usable, validate_items)
from proximic_ring.agent_workflow.history.history_service import HistoryService, RefreshPolicy
from proximic_ring.agent_workflow.history.history_source import HistoryUnavailable, read_history
from proximic_ring.agent_workflow.ui.history_recommendation_controller import HistoryRecommendationController
from proximic_ring.agent_workflow.ui.history_recommendation_overlay import HistoryRecommendationOverlay, placement
from proximic_ring.agent_workflow.history.history_backend import HistoryBackend


@pytest.fixture(autouse=True)
def isolated_history_memories(tmp_path, monkeypatch):
    # These tests must never read the signed-in user's actual activity memories.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))


def output(n=4):
    return {"items": [{"title": f"整理会议记录 {i}",
                       "plan": (f"整理会议记录 {i}，产出可以直接跟进的行动清单。\n"
                                "1. 阅读纪要，区分已经完成的事项和待办。\n"
                                "2. 提取每项任务、负责人和截止时间，缺失信息标为待确认。\n"
                                "3. 按优先级整理成表格，不猜测未记录的信息。"),
                       "action": "run_agent", "thread_id": None} for i in range(n)]}


def evidence(now=None, fingerprint="one"):
    now = time.time() if now is None else now
    return dict(events=[dict(ref="a", at=now)], source_at=now,
                captured_at=now, fingerprint=fingerprint)


def snapshot(n=4, now=None):
    now = time.time() if now is None else now
    return make_snapshot(output(n), evidence(now), now)


def event(at, title="会议记录", **overrides):
    row = dict(timestamp=datetime.fromtimestamp(at, timezone.utc).isoformat(),
                app={"name": "Notes", "processIdentifier": 100},
                window={"title": title}, ax={"mode": "diffFromPrevious", "text": "待整理"})
    return dict(row, **overrides)


def history(tmp_path, rows):
    folder = tmp_path / "segments" / "2026-10-20T00-00-00Z"
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    return dict(state="running", eventStreamRootPath=str(tmp_path)), folder / "events.jsonl"


def memory(root, at, content, kind="10min", slug="fixture"):
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    path = root / f"{stamp}-abcd-{kind}-{slug}.md"
    path.write_text(content)
    return path


def test_history_bounds_partial_lines_time_order_redaction_and_self_filter(tmp_path):
    now = time.time()
    status, path = history(tmp_path, [
        event(now - 50), event(now - 2000, "old"), event(now + 100, "future"),
        event(now - 5, "new", ax={"text": "api_key=topsecret", "mode": "diffFromPrevious"}),
        event(now - 1, "Mythlink · 下一步"), event(now - 10),
        event(now, "secure", mouse={"target": {"role": "AXSecureTextField"}}),
    ])
    with path.open("ab") as stream:
        stream.write(b'{"timestamp":')
    result = read_history(status, now)
    assert len(result["events"]) == 2
    assert [row["window"] for row in result["events"]] == ["会议记录", "new"]
    assert result["source_at"] == pytest.approx(now - 5)
    assert "topsecret" not in json.dumps(result)
    assert result["events"][-1]["ax_mode"] == "diffFromPrevious"


def test_unchanged_ui_events_keep_same_fingerprint(tmp_path):
    now = time.time()
    status, path = history(tmp_path, [event(now - 10)])
    first = read_history(status, now)
    with path.open("a") as stream:
        stream.write(json.dumps(event(now)) + "\n")
    second = read_history(status, now)
    assert first["fingerprint"] == second["fingerprint"]
    assert first["source_at"] < second["source_at"]


@pytest.mark.parametrize("state", ["unknown", None])
def test_history_requires_known_observer_state(tmp_path, state):
    with pytest.raises(HistoryUnavailable):
        read_history(dict(state=state, eventStreamRootPath=str(tmp_path)), time.time())


@pytest.mark.parametrize("state", ["paused", "stopped"])
def test_paused_collection_uses_saved_memories_without_reading_raw_events(tmp_path, monkeypatch, state):
    import proximic_ring.agent_workflow.history.history_source as module
    now = time.time()
    root = module.memory_resources()
    memory(root, now - 4 * 3600, "用户持续比较演示方案，尚未表达具体任务。", kind="6h")
    def unexpected(*args):
        pytest.fail("Paused collection must not read raw events")
    monkeypatch.setattr(module, "_read_events", unexpected)
    context = read_history({"state": state}, now)
    assert not context["events"]
    assert len(context["summaries"]) == 1
    assert context["source_at"] == pytest.approx(now - 4 * 3600, abs=1)
    assert context["captured_at"] == now and context["collection_state"] == state
    result = output(1)
    value = make_snapshot(result, context, now)
    assert usable(value, now)
    assert value["source_at"] < now - 20 * 60


def test_memory_selection_is_bounded_and_changes_invalidate_fingerprint(tmp_path):
    from proximic_ring.agent_workflow.history.history_source import MAX_TEXT, SUMMARY_TEXT
    now = time.time()
    root = tmp_path / "memories"
    for index in range(12):
        memory(root, now - (index + 1) * 600, f"近期摘要 {index}", slug=str(index))
    for index in range(4):
        memory(root, now - (index + 1) * 21600, f"项目摘要 {index}", kind="6h", slug=str(index))
    status, _ = history(tmp_path, [event(now)])
    first = read_history(status, now, summaries_root=root)
    assert len([row for row in first["summaries"] if row["kind"] == "6h"]) == 2
    assert len([row for row in first["summaries"] if row["kind"] == "10min"]) == 6
    assert first["fingerprint"] == read_history(status, now + 1, summaries_root=root)["fingerprint"]
    memory(root, now - 600, "摘要补充：用户已完成比较，开始准备演示。", slug="0")
    changed = read_history(status, now, summaries_root=root)
    assert first["fingerprint"] != changed["fingerprint"]
    for path in root.glob("*.md"):
        path.write_text(path.name + "长摘要 " * 5000)
    large_status, _ = history(tmp_path / "large", [
        event(now - i, title=str(i), ax={"text": "详细活动 " * 1000}) for i in range(80)])
    bounded = read_history(large_status, now, summaries_root=root)
    cost = lambda rows: sum(len(json.dumps(row, ensure_ascii=False)) for row in rows)
    assert cost(bounded["summaries"]) <= SUMMARY_TEXT
    assert cost(bounded["summaries"] + bounded["events"]) <= MAX_TEXT
    assert bounded["summaries"] and bounded["events"]


def test_memories_use_activity_time_redact_and_ignore_future_or_external_files(tmp_path):
    from proximic_ring.agent_workflow.history.history_source import read_summaries
    now = time.time()
    root = tmp_path / "memories"
    memory(root, now - 100, "近期项目 api_key=topsecret")
    old = memory(root, now - 86400, "昨日背景")
    os.utime(old, (now, now))
    memory(root, now + 3600, "未来资料")
    external = memory(tmp_path / "outside", now - 10, "不应读取的目录外文本")
    (root / external.name).symlink_to(external)
    (root / "instructions.md").write_text("不属于活动记忆")
    summaries = read_summaries(root, now)
    assert len(summaries) == 2
    assert summaries[0]["at"] > summaries[1]["at"]
    assert summaries[1]["at"] == pytest.approx(now - 86400, abs=1)
    assert "topsecret" not in json.dumps(summaries)


def test_missing_raw_directory_falls_back_to_memories_from_codex_home(monkeypatch, tmp_path):
    from proximic_ring.agent_workflow.history.history_source import memory_resources
    codex_home = tmp_path / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    assert memory_resources() == codex_home / "memories/extensions/skysight/resources"
    memory(memory_resources(), time.time() - 7200, "持续项目背景")
    value = read_history({"state": "running"}, time.time())
    assert len(value["summaries"]) == 1
    assert value["events"] == []


def test_history_cannot_follow_segment_symlink_outside_root(tmp_path):
    external = tmp_path / "elsewhere"
    external.mkdir()
    (external / "events.jsonl").write_text(json.dumps(event(time.time())) + "\n")
    root = tmp_path / "root"
    (root / "segments").mkdir(parents=True)
    (root / "segments" / "escape").symlink_to(external)
    assert not read_history(dict(state="running", eventStreamRootPath=str(root)), time.time())["events"]


def test_contract_needs_no_evidence_refs_and_limits_to_four():
    rows = validate_items(output(8))
    assert len(rows) == 4
    assert all("evidence_refs" not in row for row in rows)
    command = execution_command(rows[0], snapshot())
    assert "evidence_refs" not in command["context"] and "提问" not in command["text"]
    bad = output()
    bad["items"][0]["plan"] = ""
    with pytest.raises(ValueError):
        validate_items(bad)
    assert validate_items({"items": []}) == []


def test_cache_is_atomic_private_expiring_and_corruption_tolerant(tmp_path):
    path = tmp_path / "cache.json"
    value = snapshot()
    save_cache(path, value)
    assert load_cache(path) == value
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert not usable(value, value["generated_at"] + 1801)
    assert not usable(value, value["generated_at"] - 60)
    path.write_text('{"items":')
    assert load_cache(path) is None


def test_memory_cache_expires_by_lookup_and_generation_without_rewriting_source_time(tmp_path):
    now = time.time()
    context = dict(summaries=[dict(ref="a", at=now - 86400)], events=[],
                   source_at=now - 86400, captured_at=now, fingerprint="memory")
    value = make_snapshot(output(1), context, now)
    save_cache(tmp_path / "cache", value)
    assert load_cache(tmp_path / "cache") == value
    assert usable(value, now)
    assert value["source_at"] == now - 86400
    assert not usable(dict(value, source_checked_at=now - 1801), now)
    assert not usable(dict(value, generated_at=now - 1801), now)
    assert not usable(dict(value, source_at=now + 100), now)
    # Version 1 had no separate lookup timestamp: preserve its expiry contract.
    assert not usable(dict(value, version=1), now)
    assert usable(dict(value, version=1, source_at=now), now)
    assert execution_command(value["items"][0], value)["context"]["source_at"] == now - 86400


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_legacy_cache_migrates_task_to_plan_without_reviving_expired_data(tmp_path, version):
    value = snapshot(1)
    value["version"] = version
    if version == 1:
        value.pop("source_checked_at")
    row = value["items"][0]
    full_plan = row["plan"]
    row.pop("thread_title")
    row["evidence_refs"] = ["legacy-ref"]
    if version < 4:
        row.pop("action")
        row.pop("thread_id")
    if version < 3:
        row.pop("plan")
        row.update(reason="旧推荐理由", task=full_plan)
    path = tmp_path / "cache"
    save_cache(path, value)
    loaded = load_cache(path)
    assert loaded["version"] == 5
    assert loaded["generated_at"] == value["generated_at"]
    assert loaded["source_at"] == value["source_at"]
    assert loaded["items"][0]["plan"] == full_plan
    assert set(loaded["items"][0]) == {"id", "title", "plan", "action", "thread_id", "thread_title"}
    assert loaded["items"][0]["action"] == "run_agent" and loaded["items"][0]["thread_id"] is None
    assert full_plan in execution_command(loaded["items"][0], loaded)["text"]
    value["generated_at"] -= 1801
    save_cache(path, value)
    assert load_cache(path) is None


def test_refresh_cadence_changes_debounce_and_bounded_backoff():
    policy = RefreshPolicy()
    assert policy.due("one", 0)
    policy.last_attempt = 0
    policy.success("one", 0)
    assert not policy.due("one", 299)
    assert policy.due("one", 300)
    assert not policy.due("two", 40)
    assert not policy.due("two", 50)
    assert policy.due("two", 60)
    policy.failed(60)
    assert not policy.due("two", 89)
    assert policy.due("two", 90)
    for n in range(10):
        policy.failed(n)
        assert policy.retry_at - n <= 300


def test_service_publishes_only_valid_completed_result_and_closes(tmp_path, monkeypatch):
    import proximic_ring.agent_workflow.history.history_service as module
    backend = SimpleNamespace(status=lambda: {"state": "running"},
                              generate=lambda e, stop: output(), close=lambda: closed.append(True))
    closed, states = [], []
    monkeypatch.setattr(module, "read_history", lambda *a: evidence())
    service = HistoryService(tmp_path / "cache", lambda: backend, lambda state: receive(state))
    def receive(state):
        states.append(state)
        if state.get("snapshot"):
            service.close()
    service._run()
    assert states[-1]["snapshot"]["items"]
    assert states[-1]["refreshing"] is False
    assert load_cache(tmp_path / "cache")
    assert closed == [True]


def test_service_generates_once_from_memory_while_collection_is_stopped(tmp_path, monkeypatch):
    import proximic_ring.agent_workflow.history.history_service as module
    now = time.time()
    context = dict(summaries=[dict(ref="a", at=now - 86400)], events=[],
                   source_at=now - 86400, captured_at=now, fingerprint="memory")
    calls, states = [], []
    def generate(value, stop):
        calls.append(value)
        return output(1)
    backend = SimpleNamespace(status=lambda: {"state": "stopped"}, generate=generate, close=lambda: None)
    monkeypatch.setattr(module, "read_history", lambda *a: context)
    def receive(state):
        states.append(state)
        if state.get("snapshot"):
            service.close()
    service = HistoryService(tmp_path / "cache", lambda: backend, receive)
    service._run()
    assert len(calls) == 1 and calls[0]["summaries"] == context["summaries"]
    assert calls[0]["events"] == []
    assert load_cache(tmp_path / "cache")["items"]
    assert "保存的活动记忆" in states[-1]["message"]


def test_failed_refresh_keeps_previous_cache(tmp_path, monkeypatch):
    import proximic_ring.agent_workflow.history.history_service as module
    path = tmp_path / "cache"
    value = snapshot(now=time.time() - 400)
    save_cache(path, value)
    def fail(*args):
        raise TimeoutError()
    backend = SimpleNamespace(status=lambda: {"state": "running"}, generate=fail, close=lambda: None)
    states = []
    monkeypatch.setattr(module, "read_history", lambda *a: evidence(fingerprint="new"))
    service = HistoryService(path, lambda: backend, lambda state: receive(state))
    def receive(state):
        states.append(state)
        if state.get("refreshing") is False:
            service.close()
    service._run()
    assert load_cache(path) == value
    assert not any(state.get("clear") for state in states)


def test_pause_during_generation_discards_result_and_clears_persisted_cache(tmp_path, monkeypatch):
    import proximic_ring.agent_workflow.history.history_service as module
    path = tmp_path / "cache"
    save_cache(path, snapshot(now=time.time() - 400))
    statuses = iter([{"state": "running"}, {"state": "paused"}])
    backend = SimpleNamespace(status=lambda: next(statuses), generate=lambda *a: output(), close=lambda: None)
    monkeypatch.setattr(module, "read_history", lambda *a: evidence())
    states = []
    service = HistoryService(path, lambda: backend, lambda state: receive(state))
    def receive(state):
        states.append(state)
        if state.get("clear"):
            service.close()
    service._run()
    assert not path.exists()
    assert states[-1]["available"] is False and states[-1]["snapshot"] is None


class InferenceRPC:
    def __init__(self, *, runtime_dir, codex_path):
        self.inbox, self.calls, self.writes, self.threads = queue.Queue(), [], [], 0
        self.exposed = False
        self.request_tool = False
    def initialize(self):
        pass
    def call(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "account/read":
            return {"requiresOpenaiAuth": True, "account": {"type": "chatgpt"}}
        if method == "config/read":
            return {"config": {"mcp_servers": {"node_repl": {"command": "unused"}}}}
        if method == "thread/start":
            self.threads += 1
            return {"thread": {"id": str(self.threads)}}
        if method == "mcpServerStatus/list":
            if params["threadId"] == "1":
                return {"data": [{"name": "computer-history", "pluginId": "computer-history@test",
                                 "runtimeStatus": "connected", "tools": {"computer_history_status": {}}}]}
            return {"data": [{"name": "node_repl", "tools": {},
                             "runtimeStatus": "starting" if self.exposed else "disabled"}]}
        if method == "mcpServer/tool/call":
            return {"content": [{"type": "text", "text": '{"state":"running"}'}]}
        if method == "turn/start":
            if self.request_tool:
                self.inbox.put({"id": 99, "method": "item/tool/call", "params": {"threadId": "2"}})
            self.inbox.put({"method": "item/completed", "params": {
                "threadId": "2", "item": {"type": "agentMessage", "text": json.dumps(output())}}})
            self.inbox.put({"method": "turn/completed", "params": {
                "threadId": "2", "turn": {"id": "turn", "status": "completed"}}})
            return {"turn": {"id": "turn"}}
        raise AssertionError(method)
    def send(self, method, params):
        self.calls.append((method, params))
    def write(self, value):
        self.writes.append(value)
    def close(self):
        pass


def test_backend_separates_status_and_inference_disables_tools_and_does_not_execute():
    backend = HistoryBackend("/test/codex", rpc_factory=InferenceRPC)
    try:
        assert backend.status() == {"state": "running"}
        rpc = backend.rpc
        assert not any(method == "turn/start" for method, _ in rpc.calls)
        assert backend.generate(evidence(), threading.Event()) == output()
        inference = [params for method, params in rpc.calls if method == "thread/start"][-1]
        assert inference["ephemeral"] and inference["sandbox"] == "read-only"
        assert inference["approvalPolicy"] == "never" and inference["environments"] == []
        assert inference["config"]["mcp_servers.node_repl.enabled"] is False
        assert inference["config"]["features"]["shell_tool"] is False
        assert inference["config"]["plugins"]["computer-history@test"]["enabled"] is False
        assert any(method == "thread/unsubscribe" for method, _ in rpc.calls)
    finally:
        backend.close()


@pytest.mark.parametrize("problem", ["tools", "request", "cancel"])
def test_backend_fails_closed_and_interrupts(problem):
    backend = HistoryBackend("/test/codex", rpc_factory=InferenceRPC)
    try:
        backend.status()
        backend.rpc.exposed = problem == "tools"
        backend.rpc.request_tool = problem == "request"
        stop = threading.Event()
        if problem == "cancel":
            stop.set()
        with pytest.raises((RuntimeError, TimeoutError)):
            backend.generate(evidence(), stop)
        if problem in ("tools", "cancel"):
            assert not any(method == "turn/start" for method, _ in backend.rpc.calls)
        else:
            assert any(method == "turn/interrupt" for method, _ in backend.rpc.calls)
        if problem == "request":
            assert backend.rpc.writes[-1]["error"]["code"] == -32601
    finally:
        backend.close()


class FakeOverlay:
    def __init__(self, controller):
        self.shown = False
    def show(self):
        self.shown = True
    def hide(self):
        self.shown = False
    def close(self):
        pass


class FakeService:
    thread = None
    def __init__(self, path, backend, callback):
        self.callback = callback
        self.started = self.closed = False
    def start(self):
        self.started = True
    def close(self):
        self.closed = True


class Agent(QObject):
    focusRequested = Signal(str)
    def __init__(self):
        super().__init__()
        self.owner = SimpleNamespace(
            _settings=None, _disconnect_event=threading.Event(),
            _global_gesture_bindings=SimpleNamespace(workflow_recommendations="double-index-pinch"))
        self._closed = self.busy = self._starting = self._composing = self._picking_attachments = False
        self.commands = []
        self._input_busy = lambda: False
        self._emit = lambda: None
    def _send(self, command):
        self.commands.append(command)
        return True


@pytest.fixture
def hud(tmp_path):
    app = QApplication.instance() or QApplication(["history-hud", "-platform", "offscreen"])
    agent = Agent()
    controller = HistoryRecommendationController(agent, cache_path=tmp_path / "cache",
        service_factory=FakeService, overlay_factory=FakeOverlay)
    controller._latest = snapshot()
    yield controller, agent, app
    controller.close()


def test_hud_opens_cache_without_waiting_and_freezes_first_four(hud):
    controller, agent, _ = hud
    controller.open()
    assert controller.visible and controller.count == 4
    assert controller._service.started
    for _ in range(5):
        controller.step(1)
    assert controller.position == 2
    old = controller.title
    updated = snapshot(1)
    updated["items"][0]["title"] = "新的推荐"
    controller._receive(controller._generation, {"snapshot": updated})
    assert controller.title == old and controller.count == 4
    controller.dismiss()
    controller.open()
    assert controller.title == "新的推荐" and controller.count == 1
    assert not agent.commands


def test_hud_idle_timeout_releases_gestures_and_only_user_input_extends_it(hud):
    from PySide6.QtTest import QTest
    controller, agent, _ = hud
    assert controller._idle.interval() == 7000
    controller._idle.setInterval(100)
    controller.open("gesture")
    connection, epoch = agent.owner._disconnect_event, controller._epoch
    QTest.qWait(60)
    controller.step(1)
    QTest.qWait(60)
    assert controller.visible and controller.position == 2
    controller._receive(controller._generation, {"snapshot": snapshot(), "refreshing": False})
    QTest.qWait(60)
    assert not controller.visible and not controller._overlay.shown
    assert controller._connection is None and not controller._guard.isActive()
    assert not controller.filter_gesture(SimpleNamespace(name="tap"), connection)
    controller._apply_gesture("tap", connection, time.monotonic(), epoch)
    assert not agent.commands
    controller.interact()
    assert not controller._idle.isActive()
    controller.open()
    assert controller.visible and controller._idle.isActive()


def test_tap_executes_exact_selected_task_once_and_invalidates_queued_gestures(hud):
    controller, agent, _ = hud
    controller.open()
    controller.step(1)
    selected_plan = controller.plan
    epoch = controller._epoch
    connection = agent.owner._disconnect_event
    controller._apply_gesture("tap", connection, time.monotonic(), epoch)
    controller._apply_gesture("tap", connection, time.monotonic(), epoch)
    controller.execute()
    assert len(agent.commands) == 1
    assert "记录 1" in agent.commands[0]["text"]
    assert selected_plan in agent.commands[0]["text"]
    assert agent.commands[0]["source"] == "computer_history_recommendation"
    assert not controller.visible
    controller.open()
    assert controller.count == 3


@pytest.mark.parametrize("issue", ["stale", "connection", "lock", "busy", "voice"])
def test_stale_or_blocked_gesture_never_executes(hud, issue):
    controller, agent, _ = hud
    controller.open()
    connection, created = agent.owner._disconnect_event, time.monotonic()
    if issue == "stale":
        created -= 2
    elif issue == "connection":
        connection = threading.Event()
    elif issue == "lock":
        agent.owner._ring_gestures = SimpleNamespace(session_blocked=lambda: True)
    elif issue == "busy":
        agent.busy = True
    elif issue == "voice":
        agent._input_busy = lambda: True
    controller._apply_gesture("tap", connection, created, controller._epoch)
    assert not agent.commands


def test_pinch_toggles_swipes_select_and_disconnect_dismisses(hud):
    controller, agent, app = hud
    connection = agent.owner._disconnect_event
    # Closed panels leave invocation to the configurable app/scene/global route.
    assert not controller.filter_gesture(SimpleNamespace(name="double-index-pinch"), connection)
    controller.open("gesture")
    assert controller.visible
    assert controller.filter_gesture(SimpleNamespace(name="swipe-down"), connection)
    app.processEvents()
    assert controller.position == 2
    connection.set()
    controller._reconcile()
    assert not controller.visible


def test_disable_discards_late_result_and_closes_worker(hud):
    controller, _, _ = hud
    controller.open()
    epoch, service = controller._generation, controller._service
    controller.enabled = False
    controller._receive(epoch, {"snapshot": snapshot(), "refreshing": True})
    assert not controller.visible and not controller.refreshing and service.closed


def test_expired_card_cannot_execute(hud):
    controller, agent, _ = hud
    controller.open()
    controller._display["generated_at"] -= 1801
    controller.execute()
    assert not agent.commands and controller.count == 0


@pytest.mark.parametrize("point,screen", [
    (QPoint(1900, 1070), QRect(0, 0, 1920, 1080)),
    (QPoint(-1910, 40), QRect(-1920, 0, 1920, 1080)),
    (QPoint(10, -1000), QRect(0, -1080, 1920, 1080)),
])
def test_hud_position_stays_on_pointer_monitor(point, screen):
    rect = placement(point, screen)
    assert screen.contains(rect)
    assert not rect.contains(point)


def test_actual_hud_qml_loads_and_contains_no_rich_history(hud):
    controller, _, app = hud
    controller.open()
    overlay = HistoryRecommendationOverlay(controller)
    try:
        overlay.show()
        app.processEvents()
        root = overlay.window.rootObject()
        assert root.findChild(QObject, "historyRecommendationExecute").property("enabled")
        from PySide6.QtQml import QQmlExpression
        title = root.findChild(QObject, "historyRecommendationTitle")
        expression = QQmlExpression(overlay.window.rootContext(), title, "textFormat === 0")
        assert expression.evaluate()[0] is True
        plan = root.findChild(QObject, "historyRecommendationPlan")
        assert plan.property("text") == controller.plan
        expression = QQmlExpression(overlay.window.rootContext(), plan, "textFormat === 0")
        assert expression.evaluate()[0] is True
        assert root.findChild(QObject, "historyRecommendationPlanTooltip").property("text") == controller.plan
    finally:
        overlay.close()
