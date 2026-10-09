"""Production Qt → Unix socket → Swift controller/palette → Qt, with a local editor.

Ring events, AX and source selection are simulated. The actual IME transport
and floating window run; no keys or text are sent to other applications.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from proximic_ring.ime_bridge import IMEBridge
from proximic_ring.stroke_context import StrokeContext
from test_inline_ui import inline_ui
from test_stroke_session import stroke, until


@pytest.fixture(scope="module")
def native_stroke_harness():
    if sys.platform != "darwin":
        pytest.skip("native macOS input method")
    root = Path(__file__).resolve().parents[1]
    build = root / ".build/input-method"
    (build / "module-cache").mkdir(parents=True, exist_ok=True)
    binary = build / "stroke-ipc-tests"
    sources = sorted(p for p in (root / "native/ProxiMicInput/Sources").glob("*.swift")
                     if p.name != "main.swift")
    command = ["xcrun", "swiftc", "-swift-version", "5", "-module-cache-path", str(build / "module-cache"),
               "-framework", "AppKit", "-framework", "InputMethodKit", "-framework", "Carbon",
               *(str(p) for p in sources), str(root / "native/ProxiMicInput/Tests/StrokeIPC/main.swift"),
               "-o", str(binary)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert compiled.returncode == 0, compiled.stderr
    return binary


@pytest.mark.parametrize("initial_text, caret", [("", 0), ("你好世界", 2)])
def test_double_click_shows_native_palette_commits_and_returns_to_mouse(stroke, native_stroke_harness, initial_text, caret):
    c, _, tp, source, session = stroke
    inline = c._inline_input
    received, sent = [], []

    class ObservedBridge(IMEBridge):
        def send(self, message):
            sent.append(message)
            return super().send(message)

    def receive(message):
        received.append(message)
        inline._received.emit(message)

    with tempfile.TemporaryDirectory(prefix="stroke-ipc-", dir="/private/tmp") as folder:
        bridge = ObservedBridge(receive, path=Path(folder) / "bridge.sock")
        inline._bridge = bridge
        process = None
        try:
            bridge.start()
            process = subprocess.Popen([str(native_stroke_harness)], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                env={**os.environ, "PROXIMIC_IME_SOCKET": str(bridge.path),
                     "STROKE_TEST_TEXT": initial_text, "STROKE_TEST_CARET": str(caret)})
            until(lambda: inline.ready and inline._epoch == bridge.epoch)
            pointer = tp._run.output
            pointer.options["on_double_click"](dict(bundle="test.editor", pid=123))
            until(lambda: session.phase == "source")
            session._source.event.emit(dict(event="selected", already_selected=True))
            until(lambda: session.phase == "running")
            assert pointer.stopped and tp.inputMode == "stroke" and tp.blocksVoice
            assert any(m.get("type") == "state" and m.get("ready") for m in received)
            assert any(m.get("type") == "ping" for m in sent)
            assert any(m.get("type") == "stroke_ready" and m.get("success") and m.get("panel_visible")
                       for m in received)
            model = StrokeContext()
            prefix = initial_text[:caret]
            assert tp._stroke_context == prefix[-4:]
            assert tp.strokeCandidates == model.suggestions(prefix)[:5]
            tp.addStroke("h")
            assert not tp.predictionMode
            prefix += tp.strokeCandidates[0]
            assert tp.consume_gesture(SimpleNamespace(name="tap"), c._disconnect_event)
            until(lambda: any(m.get("type") == "stroke_result" and m.get("success") for m in received)
                  and not tp._pending_stroke_commit and not tp._code)
            assert session.phase == "running"  # Confirmation keeps the palette open.
            for count in [2, 3]:
                assert tp.predictionMode and not tp._code
                assert tp._stroke_context == prefix[-4:]
                assert tp.strokeCandidates == model.suggestions(prefix)[:5]
                assert tp.consume_gesture(SimpleNamespace(name="swipe-right"), c._disconnect_event)
                until(lambda: tp.selectedCandidate == 1)
                prefix += tp.strokeCandidates[1]
                assert tp.consume_gesture(SimpleNamespace(name="tap"), c._disconnect_event)
                until(lambda: sum(m.get("type") == "stroke_result" and bool(m.get("success")) for m in received) == count
                      and not tp._pending_stroke_commit)
            tp.addStroke("h")
            assert not tp.predictionMode  # New ink takes precedence over the predictions.
            tp.clearStrokes()
            assert tp.predictionMode and tp._stroke_context == prefix[-4:]
            tp._run.output.options["on_double_click"]()
            until(lambda: session.phase == "idle" and tp._run.output.active and tp.inputMode == "pointer")
            assert c._recognition_event.is_set() and not tp.blocksVoice
            assert len(source.starts) == 1 and source.stops == 0
            assert not {"begin", "finish", "finish_dictation", "cancel"} & {m["type"] for m in sent}
            bridge.send(dict(type="test_exit"))
            output, _ = process.communicate(timeout=3)
            assert process.returncode == 0, output
            assert "STROKE_PALETTE_VISIBLE" in output, output
            assert "STROKE_COMMIT_ACKNOWLEDGED" in output, output
            assert "STROKE_PREDICTIONS_VISIBLE" in output, output
            assert "STROKE_FINAL_TEXT=" + prefix + initial_text[caret:] in output, output
            assert "STROKE_PALETTE_HIDDEN" in output, output
        finally:
            session.cancel()
            bridge.close()
            if process is not None and process.poll() is None:
                process.terminate()
                process.communicate(timeout=3)
