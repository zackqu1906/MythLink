"""Manual test-tool logging and BLE lifecycle, without connecting a real Ring."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from ring_python_sdk.touchpad import TouchpadClick, TouchpadContact, TouchpadClickVerdict, TouchpadStats

spec = importlib.util.spec_from_file_location("touchpad_click_tool", Path(__file__).parents[1] / "tools/test_touchpad_double_click.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def summary(folder):
    return json.loads((folder / "summary.json").read_text())


def test_demo_uses_production_detector_and_clearly_labels_its_log(tmp_path, capsys):
    folder = tmp_path / "demo"
    assert tool.main(["--demo", "--output", str(folder)]) == 0
    text = capsys.readouterr().out
    assert "最大间隔 400 ms" in text and "立即触发" in text
    assert "[模拟] 不连接 Ring" in text and "双击 #1" in text
    result = summary(folder)
    assert result["raw_clicks"] == 5 and result["single_clicks"] == 4 and result["double_clicks"] == 1
    rows = [json.loads(row) for row in (folder / "events.jsonl").read_text().splitlines()]
    assert all(row["mode"] == "demo" for row in rows)
    assert next(row for row in rows if row["event"] == "double")["interval_ms"] == pytest.approx(400)
    assert [row['event'] for row in rows if row['event'] in {'single', 'double'}] == [
        'single', 'single', 'double', 'single', 'single']


def test_contact_and_verdict_are_not_clicks_and_reset_keeps_delivered_single(tmp_path):
    folder = tmp_path / "contact"
    recorder = tool.ClickRecorder(folder, device="8F56")
    try:
        recorder.observe(TouchpadContact("down", 1, 10), now=10)
        recorder.observe(TouchpadClickVerdict(1, 2, True, 10.1), now=10.1)
        assert recorder.raw == recorder.double == recorder.single == 0
        recorder.observe(TouchpadClick(2, 10.1), now=10.1)
        recorder.observe(TouchpadContact("reset", 0, 10.2), now=10.2)
        assert recorder.raw == recorder.single == 1 and recorder.double == 0
    finally:
        recorder.finish("test")


def test_repeated_trajectory_cleanup_is_summarized_without_claiming_clicks_were_lost(tmp_path, capsys):
    folder = tmp_path / "cleanup"
    recorder = tool.ClickRecorder(folder, device="8F56")
    try:
        for step in range(300, 400):
            recorder.observe(TouchpadContact("reset", step, 10.), now=10.)
        assert capsys.readouterr().out == ""
        recorder.status()
        output = capsys.readouterr().out
        assert "过期轨迹清理=100" in output and "已触发的单击不撤回" in output
        recorder.observe(TouchpadClick(400, 10.1), now=10.1)
        recorder.observe(TouchpadContact("reset", 0, 10.2), now=10.2)
        assert recorder.stream_resets == 1 and recorder.single == 1
        rows = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
        assert sum(row.get("reset_scope") == "expired_motion" for row in rows) == 100
        assert rows[-1]["click_pair_reset"] is True
    finally:
        recorder.finish("test")


def test_confirmed_click_survives_trajectory_cleanup_in_manual_tester(tmp_path):
    recorder = tool.ClickRecorder(tmp_path / "retained", device="8F56")
    try:
        recorder.observe(TouchpadClick(300, 10.), now=10.)
        recorder.observe(TouchpadContact("reset", 310, 10.1), now=10.1)
        recorder.observe(TouchpadClick(340, 10.2), now=10.2)
        assert recorder.raw == 2 and recorder.double == recorder.single == 1
        assert recorder.motion_resets == 1
    finally:
        recorder.finish("test")


class Session:
    target_name, target_address = "Ringo_8F56", "FAKE-BLE-ID"
    def __init__(self, **options):
        self.options = options
        self.client = SimpleNamespace(is_connected=True)
        self.calls = []
        self.task = None
    async def connect_target(self, target):
        self.calls.append(("connect", target))
        return True
    async def touchpad_on(self, **options):
        self.calls.append(("touchpad", options["duration_s"]))
        async def emit():
            options["on_stats"](TouchpadStats(tokens=200, warmup_frames=200))
            options["on_event"](TouchpadClick(1, time.monotonic()))
            await asyncio.sleep(.03)
            options["on_event"](TouchpadClick(2, time.monotonic()))
            await asyncio.sleep(.06)
            options["on_stopped"](None)
        self.task = asyncio.create_task(emit())
    async def disconnect(self):
        self.calls.append(("disconnect",))
        self.client.is_connected = False
        if self.task and not self.task.done():
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass


def test_live_path_connects_8f56_starts_only_touchpad_and_disconnects(tmp_path, capsys):
    folder = tmp_path / "live"
    args = tool.parser().parse_args(["--output", str(folder)])
    sessions = []
    def factory(**kw):
        sessions.append(Session(**kw))
        return sessions[-1]
    asyncio.run(tool.run(args, session_factory=factory))
    assert sessions[0].calls == [("connect", "8F56"), ("touchpad", None), ("disconnect",)]
    assert sessions[0].options["auto_reconnect"] is False
    assert sessions[0].options["battery_poll_enabled"] is False
    result = summary(folder)
    assert result["mode"] == "ring" and result["raw_clicks"] == 2 and result["double_clicks"] == 1
    assert result["single_clicks"] == 1 and result["reason"] == "completed"
    assert "[就绪]" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["connect", "start", "stream", "disconnect"])
def test_live_failures_close_connection_and_save_reason(tmp_path, failure):
    folder = tmp_path / failure
    args = tool.parser().parse_args(["--output", str(folder)])
    class Failed(Session):
        async def connect_target(self, target):
            await super().connect_target(target)
            return failure != "connect"
        async def touchpad_on(self, **options):
            if failure == "start": raise RuntimeError("model unavailable")
            if failure == "stream": options["on_stopped"](RuntimeError("token timeout"))
            if failure == "disconnect": self.client.is_connected = False
    session = Failed()
    with pytest.raises((RuntimeError, ConnectionError)):
        asyncio.run(tool.run(args, session_factory=lambda **kw: session))
    assert session.calls[-1] == ("disconnect",)
    assert summary(folder)["reason"].startswith("error:")


def test_interrupt_keeps_immediate_single_and_disconnects(tmp_path):
    folder = tmp_path / "interrupt"
    args = tool.parser().parse_args(["--output", str(folder)])
    class Clicked(Session):
        async def touchpad_on(self, **options):
            options["on_event"](TouchpadClick(1, time.monotonic()))
    session = Clicked()
    async def exercise():
        task = asyncio.create_task(tool.run(args, session_factory=lambda **kw: session))
        await asyncio.sleep(.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(exercise())
    assert session.calls[-1] == ("disconnect",)
    result = summary(folder)
    assert result["raw_clicks"] == result["single_clicks"] == 1 and result["double_clicks"] == 0
    assert result["reason"] == "interrupted"
