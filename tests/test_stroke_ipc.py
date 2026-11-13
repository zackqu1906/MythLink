"""Production Qt → Unix socket → Swift controller/palette → Qt, with a local editor.

Ring events, AX and source selection are simulated. The actual IME transport
and floating window run; no keys or text are sent to other applications.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QTest

from proximic_ring.voice.ime.ime_bridge import IMEBridge
from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_menu import two_finger_selection
from test_inline_ui import inline_ui
from test_stroke_session import stroke, until
from test_two_finger_stroke_toggle import Mouse, click
from test_stroke_focus_gate import contact
from proximic_ring.touchpad.touchpad_mouse import TouchpadMouseOutput
from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput


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


@pytest.mark.parametrize("initial_text, caret", [("", 0), ("你好世界", 2), ("你🙂好世界", 3)])
def test_two_finger_double_shows_native_palette_commits_and_returns_to_mouse(stroke, native_stroke_harness, initial_text, caret, monkeypatch):
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
                     "STROKE_TEST_TEXT": initial_text,
                     "STROKE_TEST_CARET": str(len(initial_text[:caret].encode('utf-16-le')) // 2)})
            until(lambda: inline.ready and inline._epoch == bridge.epoch)
            mouse = Mouse()
            tp.output_factory = lambda **options: TouchpadMouseOutput(**options, mouse_factory=lambda: mouse)
            tp.stroke_output_factory = TouchpadStrokeOutput
            run = tp._run
            run.output.stop()
            run.output_ready = False
            run.output = pointer = tp._new_output(run, "mode_ready")
            pointer.start()
            until(lambda: run.output_ready and pointer.active)
            pointer_event = source.starts[0]['on_pointer_move']
            pointer_event(click(1))
            pointer_event(click(2))
            until(lambda: session.phase == "source")
            session._source.event.emit(dict(event="selected", already_selected=True))
            until(lambda: session.phase == "running")
            assert pointer.stopped.is_set() and tp.inputMode == "stroke" and tp.blocksVoice
            assert not mouse.calls  # Neither tap may open a context menu or move the caret.
            assert next(m for m in sent if m['type'] == 'stroke_begin')['gesture_label'] == '双指双击'
            assert any(m.get("type") == "state" and m.get("ready") for m in received)
            assert any(m.get("type") == "ping" for m in sent)
            assert any(m.get("type") == "stroke_ready" and m.get("success") and m.get("panel_visible")
                       for m in received)
            model = tp._context_model()
            prefix = initial_text[:caret]
            assert tp._stroke_context == prefix[-4:]
            assert tp.strokeCandidates == model.suggestions(prefix, allowed_characters=common_characters())[:tp.candidatePageSize]
            tp.addStroke("h")
            assert not tp.predictionMode
            index = next(i for i, char in enumerate(tp.strokeCandidates)
                         if model.suggestions(prefix + char, allowed_characters=common_characters()))
            for step in range(index):
                assert tp.consume_gesture(SimpleNamespace(name="swipe-right"), c._disconnect_event)
                until(lambda: tp.selectedCandidate == step + 1)
            prefix += tp.strokeCandidates[index]
            tp.selectCandidate(index)
            until(lambda: any(m.get("type") == "stroke_result" and m.get("success") for m in received)
                  and not tp._pending_stroke_commit and not tp._code)
            assert session.phase == "running"  # Confirmation keeps the palette open.
            for count in [2, 3]:
                assert tp.predictionMode and not tp._code
                assert tp._stroke_context == prefix[-4:]
                assert tp.strokeCandidates == model.suggestions(prefix, allowed_characters=common_characters())[:tp.candidatePageSize]
                index = next(i for i, char in enumerate(tp.strokeCandidates)
                             if model.suggestions(prefix + char, allowed_characters=common_characters()))
                for step in range(index):
                    assert tp.consume_gesture(SimpleNamespace(name="swipe-right"), c._disconnect_event)
                    until(lambda: tp.selectedCandidate == step + 1)
                prefix += tp.strokeCandidates[index]
                tp.selectCandidate(index)
                until(lambda: sum(m.get("type") == "stroke_result" and bool(m.get("success")) for m in received) == count
                      and not tp._pending_stroke_commit)
            tp.addStroke("h")
            assert not tp.predictionMode  # New ink takes precedence over the predictions.
            tp.clearStrokes()
            assert tp.predictionMode and tp._stroke_context == prefix[-4:]
            # The default two-finger single confirms first, with no mouse click or space.
            tp.addStroke('h')
            prefix += tp.strokeCandidates[0]
            pointer_event(click(3))
            until(lambda: sum(m.get('type') == 'stroke_result' and bool(m.get('success')) for m in received) == 4
                  and not tp._pending_stroke_commit)
            QTest.qWait(270)  # The previous acknowledged commit has a tap cooldown.
            # A complete single-finger contact commits first + space through IMK once.
            tp.addStroke('s')
            prefix += tp.strokeCandidates[0] + ' '
            for event in contact(100, time.monotonic()):
                source.starts[0]['on_event'](event)
            until(lambda: sum(m.get('type') == 'stroke_result' and bool(m.get('success')) for m in received) == 5
                  and not tp._pending_stroke_commit)
            assert [m for m in sent if m['type'] == 'stroke_commit'][-1]['text'] == prefix[-2:]
            assert not mouse.calls
            joined = tp._joined_model
            points = (joined.bank[joined.labels.index('fan'), :2].T * 40).tolist()
            tp._receive(run, 'stroke', (run.output, points))
            tp.addStroke('z')
            tp.addStroke('n')
            update = next(m for m in reversed(sent) if m['type'] == 'stroke_update')
            index = update['candidates'].index('反')
            assert update['glyphs'][index]['status'] == '连笔 1→2 · 已写完'
            tp.selectCandidate(index)
            until(lambda: sum(m.get('type') == 'stroke_result' and bool(m.get('success')) for m in received) == 6
                  and not tp._pending_stroke_commit)
            prefix += '反'
            # The production host proves the real IMK focus. Only the final OS
            # key post is replaced with Backspace in this process's NSTextView.
            def backspace(application, pid):
                assert (application, pid) == ('test.editor', 123)
                assert bridge.send(dict(type='test_backspace'))
            monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_backspace', backspace)
            tp.addStroke('h'); tp.addStroke('s')
            for distance, progress in [(6, 0), (24, .5), (30, 1), (18, 0), (0, 0)]:
                count = sum(m.get('event') == 'test_pull_feedback' for m in received)
                tp._receive(run, 'selection_state', (run.output, two_finger_selection([(0., 0.), (-distance, 0.)])))
                until(lambda: sum(m.get('event') == 'test_pull_feedback' for m in received) > count)
                feedback = next(m for m in reversed(received) if m.get('event') == 'test_pull_feedback')
                assert feedback['progress'] == progress and feedback['visible'] == bool(distance)
                assert tp._code == 'hs'  # Previewing a clear never deletes before release.
            tp._receive(run, 'selection_state', (run.output, dict(phase='idle')))
            tp.deleteOne(); tp.deleteOne()
            assert not tp._code and not any(m['type'] == 'test_backspace' for m in sent)
            for count in range(1, len(prefix) + 2):
                prefix = prefix[:-1]
                tp.deleteOne()
                assert sum(m['type'] == 'test_backspace' for m in sent) == count, (tp.commitMessage, received[-3:])
                until(lambda: sum(m.get('event') == 'test_backspace_done' for m in received) == count)
                reply = next(m for m in reversed(received) if m.get('event') == 'test_backspace_done')
                assert reply['text'] == prefix + initial_text[caret:]
                assert session.phase == 'running' and not tp._code
                assert bridge.focus_state()['selection'] == [len(prefix.encode('utf-16-le')) // 2, 0]
            pointer_event(click(4))
            pointer_event(click(5))
            until(lambda: session.phase == "idle" and tp._run.output.active and tp.inputMode == "pointer")
            assert c._recognition_event.is_set() and not tp.blocksVoice
            assert len(source.starts) == 1 and source.stops == 0
            assert not {"begin", "finish", "finish_dictation", "cancel"} & {m["type"] for m in sent}
            bridge.send(dict(type="test_exit"))
            output, _ = process.communicate(timeout=3)
            assert process.returncode == 0, output
            assert "STROKE_PALETTE_VISIBLE" in output, output
            assert "STROKE_GLYPHS_RENDERED" in output, output
            assert "STROKE_COMMIT_ACKNOWLEDGED" in output, output
            assert "STROKE_PREDICTIONS_VISIBLE" in output, output
            assert "STROKE_FINAL_TEXT=" + prefix + initial_text[caret:] in output, output
            assert "STROKE_PALETTE_HIDDEN" in output, output
        finally:
            session.cancel()
            bridge.close()
            if process is not None and process.poll() is None:
                process.terminate()
                output, _ = process.communicate(timeout=3)
                if process.returncode != -15:
                    print(output)
