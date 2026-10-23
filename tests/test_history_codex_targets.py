"""Existing-chat navigation stays distinct from normal recommendation execution."""
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from test_history_recommendations import InferenceRPC, evidence, hud, output
from proximic_ring.agent_workflow.history.history_backend import HistoryBackend
from proximic_ring.agent_workflow.history.history_contract import (
    execution_command, load_cache, make_snapshot, save_cache, validate_items)
from proximic_ring.agent_workflow.history.history_threads import select_threads, with_threads
from proximic_ring.agent_workflow.recording.codex_launch import main, open_codex_thread

THREAD = "01a152f2-6362-78a2-8ce1-f7fad005ad7f"
OTHER = "01a152f2-6362-78a2-8ce1-f7fad005ad70"
TITLE = "原始 Codex 任务标题"
TASK_TITLE = "补齐推荐浮窗的会话跳转"


def thread_row(tid=THREAD, title=TITLE, **extra):
    return dict(id=tid, name=title, ephemeral=False, cwd="/example/project",
                preview="演示资料与测试计划", **extra)


def target_output(tid=THREAD):
    value = output(1)
    value["items"][0].update(title=TASK_TITLE, action="open_codex_thread", thread_id=tid)
    return value


def target_snapshot():
    context = with_threads(evidence(), select_threads([thread_row()]))
    return make_snapshot(target_output(), context, time.time())


def test_catalog_uses_real_names_and_keeps_same_named_conversations_distinct():
    rows = [thread_row(), thread_row(OTHER), thread_row(),
            dict(thread_row(), id="new"), dict(thread_row(), ephemeral=True),
            dict(thread_row(), name=None), dict(thread_row(), name="api_key=secret"),
            dict(thread_row(), archived=True)]
    selected = select_threads(rows)
    assert [r["thread_id"] for r in selected] == [THREAD, OTHER]
    assert [r["title"] for r in selected] == [TITLE, TITLE]
    assert select_threads([thread_row(title="  原始标题  ")])[0]["title"] == "  原始标题  "


def test_catalog_shares_budget_and_a_rename_changes_the_fingerprint():
    rows = [thread_row(tid=f"01a152f2-6362-78a2-8ce1-{n:012x}", title="任务"+str(n)) for n in range(60)]
    threads = select_threads(rows)
    assert 0 < len(threads) <= 20
    context = evidence()
    context["summaries"] = [dict(ref=f"m{i}", at=context["source_at"], text="活动" * 1500) for i in range(4)]
    context["events"] = [dict(ref=f"e{i}", at=context["source_at"], text="详细" * 700) for i in range(5)]
    combined = with_threads(context, threads)
    cost = sum(len(json.dumps(row, ensure_ascii=False)) for row in
               combined["summaries"] + combined["events"] + combined["codex_threads"])
    assert cost <= 18000 and combined["summaries"]
    renamed = [dict(threads[0], title="更新后的标题"), *threads[1:]]
    assert with_threads(context, renamed)["fingerprint"] != combined["fingerprint"]
    assert len(context["events"]) == 5  # original history evidence was not mutated


def test_target_binding_separates_action_title_from_original_conversation_name():
    context = with_threads(evidence(), select_threads([thread_row(), thread_row(OTHER)]))
    value = output(1)
    value["items"] += target_output()["items"] + target_output(OTHER)["items"]
    result = make_snapshot(value, context, time.time())
    assert result["items"][0]["action"] == "run_agent"
    assert [row["title"] for row in result["items"][1:]] == [TASK_TITLE, TASK_TITLE]
    assert [row["thread_title"] for row in result["items"][1:]] == [TITLE, TITLE]
    assert len({row["id"] for row in result["items"]}) == 3
    with pytest.raises(ValueError):
        execution_command(result["items"][1], result)
    value["items"][2] = target_output()["items"][0]
    assert len(make_snapshot(value, context, time.time())["items"]) == 2


@pytest.mark.parametrize("change", [
    {"thread_id": OTHER}, {"thread_id": THREAD + "?prompt=run"},
    {"action": "run_agent"}, {"action": "unknown"}, {"thread_id": None}])
def test_unverified_or_malformed_targets_are_rejected(change):
    value = target_output()
    value["items"][0].update(change)
    context = with_threads(evidence(), select_threads([thread_row()]))
    with pytest.raises(ValueError):
        make_snapshot(value, context, time.time())


def test_target_cache_preserves_binding(tmp_path):
    value = target_snapshot()
    save_cache(tmp_path / "cache", value)
    assert load_cache(tmp_path / "cache") == value
    value["items"][0]["thread_id"] = "new"
    save_cache(tmp_path / "cache", value)
    assert load_cache(tmp_path / "cache") is None


def test_old_navigation_cards_wait_for_refresh_instead_of_displaying_conversation_name(tmp_path):
    value = target_snapshot()
    value["version"] = 4
    row = value["items"][0]
    row["title"] = row.pop("thread_title")
    row["evidence_refs"] = ["a"]
    value["items"] += output(1)["items"]
    save_cache(tmp_path / "cache", value)
    loaded = load_cache(tmp_path / "cache")
    assert loaded["version"] == 5 and len(loaded["items"]) == 1
    assert loaded["items"][0]["action"] == "run_agent"
    assert loaded["generated_at"] == value["generated_at"]
    assert "evidence_refs" not in loaded["items"][0]


def test_backend_catalog_is_read_only_cached_and_failure_is_optional(monkeypatch):
    class CatalogRPC(InferenceRPC):
        failed = False
        def call(self, method, params, **kwargs):
            if method == "thread/list":
                self.calls.append((method, params))
                if self.failed:
                    raise TimeoutError()
                return {"data": [thread_row()]}
            return super().call(method, params, **kwargs)
    backend = HistoryBackend("/test/codex", rpc_factory=CatalogRPC)
    try:
        backend.status()
        assert backend.threads() == select_threads([thread_row()])
        backend.threads()
        calls = [p for method, p in backend.rpc.calls if method == "thread/list"]
        assert len(calls) == 1 and calls[0]["useStateDbOnly"] and not calls[0]["archived"]
        assert "appServer" in calls[0]["sourceKinds"]
        assert not any(method in ("thread/read", "thread/resume", "turn/start") for method, _ in backend.rpc.calls)
        backend._threads_checked = float("-inf")
        backend.rpc.failed = True
        assert backend.threads() == []
    finally:
        backend.close()


def test_service_passes_catalog_to_model_and_validates_returned_target(tmp_path, monkeypatch):
    import proximic_ring.agent_workflow.history.history_service as module
    seen = []
    def generate(context, stop):
        seen.append(context)
        return target_output()
    backend = SimpleNamespace(status=lambda: {"state": "running"}, threads=lambda: select_threads([thread_row()]),
                              generate=generate, close=lambda: None)
    monkeypatch.setattr(module, "read_history", lambda *args: evidence())
    def receive(state):
        if state.get("snapshot"):
            service.close()
    service = module.HistoryService(tmp_path / "cache", lambda: backend, receive)
    service._run()
    assert seen[0]["codex_threads"][0]["title"] == TITLE
    assert load_cache(tmp_path / "cache")["items"][0]["thread_id"] == THREAD


def test_open_button_and_tap_navigate_once_without_submitting_a_task(hud):
    controller, agent, _ = hud
    controller._latest = target_snapshot()
    opened = []
    controller._codex_opener = lambda tid: opened.append(tid) or True
    controller.open()
    assert controller.title == TASK_TITLE and controller.threadTitle == TITLE
    assert controller.actionLabel == "回到任务"
    connection, epoch = agent.owner._disconnect_event, controller._epoch
    controller._apply_gesture("tap", connection, time.monotonic(), epoch)
    controller._apply_gesture("tap", connection, time.monotonic(), epoch)
    assert opened == [THREAD] and not agent.commands and not controller.visible
    assert not controller._consumed
    controller.open()
    assert controller.count == 1  # opening a conversation is not completing its task


def test_failed_navigation_keeps_card_retryable_and_never_falls_back_to_agent(hud):
    controller, agent, _ = hud
    controller._latest = target_snapshot()
    controller._codex_opener = lambda _: False
    controller.open()
    controller.execute()
    assert controller.visible and "无法打开" in controller.plan and not agent.commands
    controller._codex_opener = lambda _: True
    controller.execute()
    assert not controller.visible and not agent.commands


def test_qml_button_tracks_selected_action_and_preserves_regular_execution(hud):
    from PySide6.QtCore import QObject
    from proximic_ring.agent_workflow.ui.history_recommendation_overlay import HistoryRecommendationOverlay
    controller, agent, app = hud
    value = target_snapshot()
    value["items"] += validate_items(output(1))
    controller._latest = value
    controller.open()
    overlay = HistoryRecommendationOverlay(controller)
    try:
        overlay.show()
        app.processEvents()
        button = overlay.window.rootObject().findChild(QObject, "historyRecommendationExecute")
        assert button.property("text") == "回到任务"
        tip = overlay.window.rootObject().findChild(QObject, "historyRecommendationThreadTooltip")
        assert tip.property("text") == TITLE
        controller.step(1)
        assert button.property("text") == "交给 Agent"
        assert controller.threadTitle == "" and tip.property("text") == ""
        plan = controller.plan
        button.clicked.emit()
        assert len(agent.commands) == 1 and plan in agent.commands[0]["text"]
    finally:
        overlay.close()


def test_launcher_and_cli_validate_ids_and_never_construct_a_prompt(tmp_path, monkeypatch, capsys):
    import proximic_ring.agent_workflow.recording.codex_launch as module
    calls = []
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kwargs: calls.append(args))
    assert open_codex_thread(THREAD) == f"codex://threads/{THREAD}"
    assert calls == [["open", f"codex://threads/{THREAD}"]]
    for tid in ("new", "../etc/passwd", THREAD+"?prompt=run", "https://example.com"):
        with pytest.raises(ValueError):
            open_codex_thread(tid)
    assert len(calls) == 1
    assert main(["--thread-id", THREAD, "--print-url"]) == 0
    assert capsys.readouterr().out.strip() == f"codex://threads/{THREAD}"
    assert len(calls) == 1
    assert main(["--thread-id", "new"]) == 1


def test_checkout_script_runs_outside_the_repository(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/open-codex-thread.py"
    result = subprocess.run([sys.executable, str(script), "--thread-id", THREAD, "--print-url"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == f"codex://threads/{THREAD}"
