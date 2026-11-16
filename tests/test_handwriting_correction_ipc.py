"""Real IME socket, handwriting controller and native editor; no external writes."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import pytest

from proximic_ring.voice.ime.ime_bridge import IMEBridge
from proximic_ring.touchpad.stroke.stroke_menu import two_finger_selection
from test_inline_ui import inline_ui
from test_stroke_session import stroke, until
from test_stroke_ipc import native_stroke_harness


@pytest.mark.parametrize('with_voice', [False, True], ids=['existing-text', 'after-voice'])
def test_preceding_text_correction_highlight_and_undo_round_trip(stroke, native_stroke_harness, monkeypatch, with_voice):
    c, _, tp, _, session = stroke
    inline = c._inline_input
    tp.handwritingCorrectionEnabled = True
    received, sent, matches = [], [], []
    def match(original, word, settings):
        matches.append((original, word))
        return '青华'
    monkeypatch.setattr(c._text_processing_worker._processor, 'locate_handwriting_correction', match, raising=False)
    class Bridge(IMEBridge):
        def send(self, message):
            sent.append(message)
            return super().send(message)
    def receive(message):
        received.append(message)
        inline._received.emit(message)

    with tempfile.TemporaryDirectory(prefix='handwriting-correction-', dir='/private/tmp') as folder:
        bridge = Bridge(receive, path=Path(folder) / 'bridge.sock')
        inline._bridge = bridge
        process = None
        prefix, suffix, original = '已有🙂。', '后文保留', '明天去青华大学开会。'
        initial_prefix = prefix if with_voice else prefix + original
        try:
            bridge.start()
            process = subprocess.Popen([str(native_stroke_harness)], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, env={**os.environ, 'PROXIMIC_IME_SOCKET': str(bridge.path),
                    'STROKE_TEST_TEXT': initial_prefix + suffix,
                    'STROKE_TEST_CARET': str(len(initial_prefix.encode('utf-16-le')) // 2)})
            until(lambda: inline.ready and inline._epoch == bridge.epoch)
            client = inline._client_id
            if with_voice:
                bridge.send(dict(type='begin', client_id=client, utterance_id='voice-before-stroke', seq=1))
                until(lambda: any(m.get('utterance_id') == 'voice-before-stroke' and m.get('phase') == 'listening' for m in received))
                bridge.send(dict(type='update', client_id=client, utterance_id='voice-before-stroke', seq=2,
                                 text=original, final=True))
                until(lambda: any(m.get('type') == 'settled' and m.get('text') == original for m in received))
            tp._run.output.options['on_double_click']()
            until(lambda: session.phase == 'source')
            session._source.event.emit(dict(event='selected', already_selected=True))
            until(lambda: session.phase == 'running')
            until(lambda: tp._handwriting_correction.state.get('available'))
            for char in '清华':
                inline.send_stroke('stroke_update', code='一', candidates=[char], selected=0)
                assert inline.stroke_commit(char)
                until(lambda: inline._stroke_request is None)
            until(lambda: tp.handwritingCorrectionText == '清华')
            run = tp._run
            state = two_finger_selection([(0., 0.), (18., 0.), (30., 0.)], correction_enabled=True)
            tp._receive(run, 'selection_state', (run.output, state))
            until(lambda: any(m.get('event') == 'test_correction_feedback' and m.get('visible')
                              and m.get('progress') == 1 and m.get('scope') == '修正用词「清华」'
                              and m.get('scope_visible') and m.get('scope_highlighted') for m in received))
            tp._receive(run, 'selection', (run.output, dict(decision=state)))
            until(lambda: tp.handwritingCorrectionCanUndo)
            assert tp.handwritingCorrectionMessage == '青华 → 清华 · 左滑撤销'
            assert matches == [(prefix + original, '清华')]
            bridge.send(dict(type='test_snapshot'))
            corrected = prefix + '明天去清华大学开会。' + suffix
            until(lambda: any(m.get('event') == 'test_snapshot' and m.get('text') == corrected for m in received))
            assert not tp.handwritingCorrectionText
            assert not c._ring_gestures.filter(SimpleNamespace(name='swipe-left'), False, c._disconnect_event)
            until(lambda: not tp.strokeCommitPending and tp.handwritingCorrectionText == '清华')
            assert len([m for m in sent if m['type'] == 'stroke_correction_undo']) == 1
            assert not any(m['type'] == 'undo' for m in sent)
            bridge.send(dict(type='test_snapshot'))
            restored = prefix + original + '清华' + suffix
            until(lambda: any(m.get('event') == 'test_snapshot' and m.get('text') == restored for m in received))
            session.cancel()
            bridge.send(dict(type='test_exit'))
            output, _ = process.communicate(timeout=5)
            assert process.returncode == 0, output
            assert 'STROKE_FINAL_TEXT=' + restored in output
        finally:
            session.cancel()
            if process is not None and process.poll() is None:
                process.terminate()
                process.communicate(timeout=3)
            bridge.close()
