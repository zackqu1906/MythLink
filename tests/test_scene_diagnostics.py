"""Background diagnostics retain causes without changing dispatch or leaking content."""
from dataclasses import replace
import json
import subprocess
import sys
import threading
from types import SimpleNamespace
import pytest
from proximic_ring.scene_diagnostics import SceneDiagnostics, SceneActionError, safe_data
from proximic_ring.scene_recognition.engine import detect_scene
from proximic_ring.scene_recognition.models import PRESENTATION, PDF, IMAGE, VIDEO, MUSIC
from proximic_ring.native_access import NativeAccessChannel
from proximic_ring.diagnostic_log import RotatingDiagnosticLog
from test_gesture_scenes import presentation, window_tree, metadata
from test_app_gestures import route
from test_app_shortcuts import desktop
from test_activity_scenes import content
from test_presentation_portability import native_backend


def records(service):
    return [json.loads(line) for line in service._diagnostics.path.read_text().splitlines()]


def test_background_session_identifies_running_build_and_has_no_ui_controls(route):
    _, service, _, _, _, _, _ = route
    event = records(service)[0]
    assert event['stage'] == 'session' and event['host_pid'] > 0 and event['system_release']
    assert event['code_fingerprints']['scene_recognition/engine.py']
    assert not hasattr(service, 'copySceneDiagnostics') and not hasattr(service, 'diagnosticRevision')


def test_scene_success_is_one_correlated_trace_with_unverified_delivery(presentation):
    c, service, _, _, _, sent, _ = presentation
    event = service.scene_envelope(SimpleNamespace(name='swipe-right'))
    service.handle_override(event)
    assert sent == [('com.microsoft.Powerpoint', 'Right')]
    rows = [r for r in records(service) if r['trace'] == event.trace_id]
    assert [r['stage'] for r in rows] == ['received', 'capture', 'route', 'route', 'dispatch', 'outcome']
    assert rows[-1]['reason'] == 'shortcut_posted' and rows[-1]['delivery'] == 'posted_unverified'
    assert rows[-1]['shortcut'] == 'Right' and rows[-1]['pid'] == 42
    assert all(r['run'] == c._diagnostic_run_id for r in rows)
    assert event.trace_id in c._diagnostic_log.path.read_text()
    assert service._diagnostics.recent(event.target.bundle)[0]['stage'] == 'received'


@pytest.mark.parametrize('change,reason', [('recording', 'shortcut_recording'),
    ('settings', 'settings_changed'), ('age', 'event_expired'), ('sentence', 'sentence_changed'),
    ('busy', 'speech_busy'), ('phase', 'phase_blocked'), ('missing_binding', 'binding_missing'),
    ('blocked', 'blocked_window'), ('mode_generation', 'ring_route_blocked'), ('disconnect', 'disconnecting')])
def test_every_consumed_scene_stop_records_why_without_replay(presentation, monkeypatch, change, reason):
    c, service, inline, _, _, sent, _ = presentation
    event = service.scene_envelope(SimpleNamespace(name='swipe-right'))
    if change == 'recording': service._recording = True
    if change == 'settings': service._generation += 1
    if change == 'age': event = replace(event, created=event.created - 2)
    if change == 'sentence': inline._utterance_id = 'changed'
    if change == 'busy': monkeypatch.setattr(c._ring_gestures, 'speech_busy', lambda: True)
    if change == 'phase': event = replace(event, phase='listening')
    if change == 'missing_binding': monkeypatch.setattr(service._catalog, 'for_scene', lambda *a: {})
    if change == 'blocked': event = replace(event, target=replace(event.target, blocked=True))
    if change == 'mode_generation':
        from proximic_ring.ui.ring_gesture_controller import ModeGestureEvent
        c._apply_gesture(ModeGestureEvent(event, c._ring_gestures._generation - 1), c._disconnect_event)
    elif change == 'disconnect':
        c._disconnect_event.set()
        c._apply_gesture(event, c._disconnect_event)
    else: service.handle_override(event)
    assert records(service)[-1]['reason'] == reason and records(service)[-1]['trace'] == event.trace_id
    assert not sent and service._pending is None


def test_duplicate_and_log_write_failure_do_not_replay_or_cancel_valid_gesture(presentation, monkeypatch):
    _, service, _, _, _, sent, _ = presentation
    event = service.scene_envelope(SimpleNamespace(name='swipe-right'))
    service.handle_override(event); service.handle_override(event)
    assert len(sent) == 1 and records(service)[-1]['reason'] == 'duplicate_suppressed'
    def broken(_): raise OSError('disk full')
    monkeypatch.setattr(service._diagnostics.writer, 'append', broken)
    service._last_dispatch = None
    service.handle_override(event)
    assert len(sent) == 2


def test_capture_failure_preserves_reason_and_stack_without_message(presentation, monkeypatch):
    _, service, _, backend, _, sent, _ = presentation
    backend.last_diagnostic = {'reason': 'channel_timeout', 'operation': 'capture'}
    def broken(**kw): raise TimeoutError('private document secret.pdf')
    monkeypatch.setattr(backend, 'capture', broken)
    service.handle_override(service.scene_envelope(SimpleNamespace(name='swipe-right')))
    rows = records(service)
    assert rows[-1]['reason'] == 'channel_timeout' and not sent
    captured = next(r for r in rows if r['stage'] == 'capture')
    assert captured['error_type'] == 'TimeoutError' and captured['error_frames'][-1]['function'] == 'broken'
    assert 'secret.pdf' not in service._diagnostics.path.read_text()


def test_unknown_delivery_is_logged_and_never_retried(presentation, monkeypatch):
    _, service, _, backend, _, _, _ = presentation
    calls = []
    def fail(*a, **kw):
        calls.append(True)
        backend.last_diagnostic = {'reason': 'channel_disconnected', 'delivery': 'unknown'}
        raise RuntimeError('lost reply')
    monkeypatch.setattr(backend, 'post', fail)
    service.handle_override(service.scene_envelope(SimpleNamespace(name='swipe-right')))
    assert calls == [True] and records(service)[-1]['reason'] == 'channel_disconnected'
    assert records(service)[-1]['native']['delivery'] == 'unknown' and '无法确认' in service.notice


def test_text_focus_keeps_regular_route_and_explains_why(presentation):
    _, service, _, backend, _, sent, _ = presentation
    backend.target = replace(backend.target, input_context='text')
    assert service.scene_envelope(SimpleNamespace(name='swipe-right')) is None
    assert records(service)[-1]['reason'] == 'text_focus'
    assert records(service)[-1]['fallback'] == 'regular' and not sent


@pytest.mark.parametrize('scene', [PRESENTATION, PDF, IMAGE, VIDEO, MUSIC])
def test_all_detectors_return_evidence_reasons_and_no_content(scene):
    window, focus = window_tree('Slide Show - private-plan.pptx') if scene == PRESENTATION else content(scene)
    result = detect_scene('test.app', {scene: 'generic'}, window, focus, metadata)
    assert result.scene == scene and result.diagnostic['reason'] == 'recognized'
    assert result.diagnostic['evidence'] and result.diagnostic['elapsed_ms'] >= 0 and result.diagnostic['attempts']
    assert 'private-plan' not in json.dumps(result.diagnostic)
    window['AXModal'] = True
    assert detect_scene('test.app', {scene: 'generic'}, window, focus, metadata).diagnostic['reason'] == 'modal_window'
    assert detect_scene('test.app', {scene: 'generic'}, window, focus, metadata, budget=0).diagnostic['reason'] == 'recognition_timeout'


def test_presentation_negative_evidence_is_not_lost_to_generic_detector():
    window, focus = window_tree('Regular editor')
    result = detect_scene('test.app', {PRESENTATION: 'generic'}, window, focus, metadata)
    assert result.diagnostic['reason'] == 'no_presentation_evidence'
    assert result.diagnostic['canvas_reason'] == 'canvas_bounds_mismatch' and result.diagnostic['scanned_nodes'] > 0


def test_ax_failures_keep_attribute_codes_not_document_values(monkeypatch):
    window, focus = window_tree('Slide Show - secret.pptx')
    backend, _, _ = native_backend(monkeypatch, window, focus)
    ax = sys.modules['ApplicationServices']; original = ax.AXUIElementCopyAttributeValue
    ax.AXUIElementCopyAttributeValue = lambda node, name, ignored: (-25212, None) if name == 'AXDocument' else original(node, name, ignored)
    target = backend.capture(scene=True, menu_action=True)
    assert target.diagnostic['ax_errors']['AXDocument:-25212'] >= 1 and target.diagnostic['ax_reads'] > 0
    assert 'secret.pptx' not in json.dumps(target.diagnostic)


@pytest.mark.parametrize('field,value,reason', [('bundle','other','foreground_changed'), ('pid',99,'foreground_changed'),
    ('window','other-window','window_changed'), ('focus','search','focus_changed'), ('blocked',True,'blocked_window'),
    ('document_key','other-document','document_changed'), ('page_key','other-page','page_changed'), ('scene','video','scene_changed')])
def test_validation_reason_is_specific_and_posts_nothing(desktop, field, value, reason):
    backend, state, _ = desktop
    original = replace(state.target, scene_checked=True, scene='presentation', input_context='nontext')
    state.target = replace(original, **{field:value})
    with pytest.raises(SceneActionError) as error: backend.post(original, 'Right', require_focus=True)
    assert error.value.reason == reason and not state.sent and error.value.diagnostic['expected_pid'] == 42


def test_partial_native_post_failure_retains_uncertain_delivery(desktop):
    backend, state, quartz = desktop
    def post(pid, event):
        if not event['down']: raise RuntimeError('post failed')
        state.sent.append(event)
    quartz.CGEventPostToPid = post
    with pytest.raises(RuntimeError): backend.post(state.target, 'Right')
    assert len(state.sent) == 1 and backend.last_diagnostic['delivery'] == 'unknown'
    assert backend.last_diagnostic['events_posted'] == 1


def test_json_is_bounded_rotates_and_drops_private_values(tmp_path):
    path = tmp_path / 'events.jsonl'
    log = SceneDiagnostics(path, writer=RotatingDiagnosticLog(path, max_bytes=400, backup_count=2))
    for i in range(100):
        log.record(str(i), 'capture', 'ready', app='test.app', title='private', AXValue='private', native={'url':'https://private.test', 'reason':'recognized'})
    assert len(log.recent()) == 48 and len(list(tmp_path.iterdir())) == 3
    for file in tmp_path.iterdir():
        assert 'private' not in file.read_text()
        for line in file.read_text().splitlines(): assert json.loads(line)['schema'] == 1
    class AXObject:
        def __repr__(self): raise AssertionError('must not stringify AX objects')
    assert safe_data({'node':AXObject()})['node'] is None and len(safe_data(list(range(100)))) == 24


def test_worker_error_roundtrip_and_thread_local_diagnostics(monkeypatch):
    channel = NativeAccessChannel()
    script = '''import json,sys
for line in sys.stdin:
 m=json.loads(line)
 print(json.dumps({'id':m['id'],'error':'blocked','diagnostic':{'reason':m['reason'],'operation':m['operation']}}),flush=True)
'''
    def start():
        if channel._process is None:
            channel._process = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    monkeypatch.setattr(channel, '_start', start)
    results = {}; barrier = threading.Barrier(2)
    def call(reason):
        try: channel.call('shortcut', reason=reason)
        except SceneActionError as error:
            barrier.wait(timeout=5)
            results[reason] = (error.reason, channel.last_diagnostic['reason'])
    workers = [threading.Thread(target=call, args=(r,)) for r in ['focus_changed','document_changed']]
    try:
        for thread in workers: thread.start()
        for thread in workers: thread.join(timeout=6)
        assert results == {r:(r,r) for r in ['focus_changed','document_changed']} and channel.last_diagnostic == {}
    finally: channel.close()
