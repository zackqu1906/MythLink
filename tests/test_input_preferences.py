"""Restore input switches across Ring connections and app instances, without hardware."""
import threading
from concurrent.futures import Future

import pytest
from PySide6.QtCore import QCoreApplication

from test_interaction_controls import _controller, _close
from test_input_source_activation import wait_until
from test_ui_touchpad import Output, Source


@pytest.fixture
def controllers(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from proximic_ring.ui.touchpad.touchpad_controller import TouchpadController
    from proximic_ring.ui.proximity import proximity_controller
    from test_proximity import Process
    app = QCoreApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip('Input controllers require a QApplication process')
    app = app or QApplication(['input-preferences', '-platform', 'offscreen'])
    monkeypatch.setattr(TouchpadController, 'supported', property(lambda self: True))
    helper = tmp_path / 'helper'; helper.touch()
    monkeypatch.setattr(proximity_controller, 'helper_path', lambda: helper)
    created = []

    def make():
        c = _controller(tmp_path, monkeypatch)
        c.touchpad.output_factory = Output
        c.proximity._factory = Process
        monkeypatch.setattr(c.proximity, 'useDevice', lambda *_: None)
        created.append(c)
        return c

    yield make
    for c in created:
        _close(c)
    QCoreApplication.processEvents()


def connect(c, *, source_first=True):
    c._disconnect_event = threading.Event()
    c._runtime_active = True
    c._device_stop_handled = False
    c._busy = True
    c._apply_runtime_connected()
    source = Source()
    source.future.set_result(None)
    if source_first:
        c.touchpad.attach(source, c._disconnect_event)
    assert not c.recognitionEnabled and not c.touchpad.active
    c._apply_runtime_started()
    if not source_first:
        c.touchpad.attach(source, c._disconnect_event)
    return source


def enable_proximity(c):
    from test_proximity import prepare
    p = c.proximity
    p._target = 'a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3'
    c._selector = p._target  # A lock request must belong to the main runtime's ring.
    prepare(p); p.enabled = True
    return p


@pytest.mark.parametrize('source_first', [True, False])
def test_first_connection_enables_both_once_and_logs_actual_state(controllers, tmp_path, source_first):
    c = controllers()
    source = connect(c, source_first=source_first)
    wait_until(lambda: c.touchpad.state == 'running')
    assert c.recognitionEnabled and c._recognition_event.is_set()
    assert c.touchpad.inputMode == 'pointer' and len(source.starts) == 1
    c._apply_runtime_started()
    c.touchpad.changed.emit()
    assert len(source.starts) == 1
    log = (tmp_path / 'logs/diagnostic.log').read_text()
    assert '[EVENT INPUT_PREFERENCES_RESTORE]' in log
    assert 'voice_enabled=true touchpad_enabled=true' in log
    assert '[EVENT TOUCHPAD_STATE]' in log and 'state="running"' in log


@pytest.mark.parametrize('voice,touchpad', [(True, True), (False, True), (True, False), (False, False)])
def test_manual_choices_survive_disconnect_quit_and_new_app(controllers, monkeypatch, voice, touchpad):
    c = controllers()
    connect(c)
    wait_until(lambda: c.touchpad.state == 'running')
    c.pauseRecognition()
    if voice:
        c.startRecognition()
    c.touchpad.toggle()
    wait_until(lambda: not c.touchpad.busy)
    if touchpad:
        c.touchpad.toggle()
        wait_until(lambda: c.touchpad.state == 'running')

    def check_preferences(controller):
        assert controller._settings.value('input/voiceEnabled', type=bool) == voice
        assert controller._settings.value('touchpad/enabled', type=bool) == touchpad

    check_preferences(c)
    c._apply_runtime_finished('test disconnect')
    wait_until(lambda: c.touchpad._run is None)
    check_preferences(c)
    source = connect(c)
    if touchpad:
        wait_until(lambda: c.touchpad.state == 'running')
    assert c.recognitionEnabled == voice and c.touchpad.active == touchpad
    assert len(source.starts) == int(touchpad)

    monkeypatch.setattr(QCoreApplication, 'quit', lambda: None)
    c.requestQuit()
    QCoreApplication.processEvents()
    check_preferences(c)
    reopened = controllers()
    check_preferences(reopened)
    connect(reopened)
    if touchpad:
        wait_until(lambda: reopened.touchpad.state == 'running')
    assert reopened.recognitionEnabled == voice and reopened.touchpad.active == touchpad
    assert reopened.touchpad.inputMode == 'pointer'


def test_lock_pause_keeps_saved_switches(controllers):
    c = controllers()
    connect(c)
    wait_until(lambda: c.touchpad.state == 'running')
    c.proximity.lockPreparing.emit()
    c.proximity.locked.emit()
    wait_until(lambda: not c.touchpad.busy)
    assert not c.recognitionEnabled and not c.touchpad.active
    assert c._bool_setting('input/voiceEnabled', True)
    assert c._bool_setting('touchpad/enabled', True)


@pytest.mark.parametrize('lock_event', ['prepare_lock', 'locked', 'monitor'])
@pytest.mark.parametrize('voice,touchpad', [(True, True), (False, True), (True, False), (False, False)])
def test_lock_after_ready_restores_saved_switches_on_unlock(controllers, lock_event, voice, touchpad):
    c = controllers()
    c._settings.setValue('input/voiceEnabled', voice)
    c._settings.setValue('touchpad/enabled', touchpad)
    source = connect(c)
    if touchpad:
        wait_until(lambda: c.touchpad.state == 'running')
    p = enable_proximity(c)
    p._process.send(dict(event=lock_event, locked=True, permission=True))
    wait_until(lambda: not c.touchpad.busy)
    assert not c.recognitionEnabled and not c.touchpad.active
    assert c._bool_setting('input/voiceEnabled', True) == voice
    assert c._bool_setting('touchpad/enabled', True) == touchpad
    # Duplicate locked reports must not consume the pending restoration.
    p._process.send(dict(event='monitor', locked=True, permission=True))
    p._process.send(dict(event='unlocked'))
    p._process.send(dict(event='monitor', locked=False, lock_pending=False, permission=True))
    if touchpad:
        wait_until(lambda: c.touchpad.state == 'running')
    assert c.recognitionEnabled == voice and c.touchpad.active == touchpad
    assert len(source.starts) == (2 if touchpad else 0)


@pytest.mark.parametrize('voice,touchpad', [(True, True), (False, True), (True, False), (False, False)])
def test_reconnect_while_locked_defers_restore_until_unlock(controllers, voice, touchpad):
    c = controllers()
    c._settings.setValue('input/voiceEnabled', voice)
    c._settings.setValue('touchpad/enabled', touchpad)
    p = enable_proximity(c)
    p._process.send(dict(event='locked'))
    source = connect(c)
    assert not c.recognitionEnabled and not c.touchpad.active and not source.starts
    p._process.send(dict(event='unlocked'))
    if touchpad:
        wait_until(lambda: c.touchpad.state == 'running')
    assert c.recognitionEnabled == voice and c.touchpad.active == touchpad
    assert len(source.starts) == int(touchpad)


def test_lock_pause_handles_synchronous_touchpad_cleanup_callbacks(controllers, monkeypatch):
    c = controllers()
    connect(c)
    wait_until(lambda: c.touchpad.state == 'running')
    p = enable_proximity(c)
    original_stop = c.touchpad.stop
    calls = []
    def stop(reason=''):
        calls.append(reason)
        c.touchpad.changed.emit()  # Stroke teardown can notify before the stop completes.
        original_stop(reason)
    monkeypatch.setattr(c.touchpad, 'stop', stop)
    p._process.send(dict(event='locked'))
    wait_until(lambda: not c.touchpad.busy)
    assert len(calls) == 1 and not c.recognitionEnabled and not c.touchpad.active


@pytest.mark.parametrize('boundary', ['cancel_prepare', 'helper_failure'])
def test_only_confirmed_unlocked_state_restores_suspended_inputs(controllers, boundary):
    c = controllers()
    source = connect(c)
    wait_until(lambda: c.touchpad.state == 'running')
    p = enable_proximity(c)
    p._process.send(dict(event='prepare_lock'))
    wait_until(lambda: not c.touchpad.busy)
    if boundary == 'helper_failure':
        p._timeout()
        c.touchpad.changed.emit()
        assert not c.recognitionEnabled and not c.touchpad.active
        assert len(source.starts) == 1
    else:
        p._process.send(dict(event='monitor', locked=False, lock_pending=False, permission=True))
        wait_until(lambda: c.touchpad.state == 'running')
        assert c.recognitionEnabled and len(source.starts) == 2


def test_late_lifecycle_callbacks_from_previous_connection_cannot_stop_restored_inputs(controllers):
    c = controllers()
    connect(c)
    wait_until(lambda: c.touchpad.state == 'running')
    old = c._disconnect_event
    c._apply_runtime_finished('old drop', old)
    wait_until(lambda: c.touchpad._run is None)
    source = connect(c)
    wait_until(lambda: c.touchpad.state == 'running')
    current = c._disconnect_event
    c._runtimeConnected.emit(old)
    c._runtimeStarted.emit(old)
    c._runtimeDisconnected.emit(old)
    c._runtimeFinished.emit('late cleanup', old)
    QCoreApplication.processEvents()
    assert c._disconnect_event is current and not current.is_set()
    assert c.connected and c.recognitionEnabled and c.touchpad.active
    assert c.touchpad._run.source is source and len(source.starts) == 1


def test_fast_reconnect_waits_for_previous_touchpad_cleanup(controllers):
    c = controllers()
    old = connect(c)
    wait_until(lambda: c.touchpad.state == 'running')
    stopped = Future()
    old.stop_touchpad = lambda: stopped
    c._apply_runtime_finished('test disconnect')
    assert c.touchpad.state == 'stopping'
    source = connect(c)
    assert not source.starts and 'touchpad/enabled' in c._input_restore_pending
    stopped.set_result(None)
    wait_until(lambda: c.touchpad.state == 'running')
    assert len(source.starts) == 1 and c.touchpad._run.source is source


def test_pending_restore_cannot_restart_after_disconnect_or_accept_old_source(controllers):
    c = controllers()
    c._runtime_active = True
    c._apply_runtime_connected()
    c._apply_runtime_started()
    assert 'touchpad/enabled' in c._input_restore_pending
    old_connection = c._disconnect_event
    c._apply_runtime_finished('test disconnect')
    c.touchpad.attach(Source(), old_connection)
    c.touchpad.changed.emit()
    c._apply_runtime_started()
    assert not c.recognitionEnabled and not c.touchpad.active
    source = connect(c)
    wait_until(lambda: c.touchpad.state == 'running')
    c.touchpad.attach(Source(), old_connection)
    assert c.touchpad._source is source and len(source.starts) == 1


def test_failed_auto_start_is_logged_without_retry_loop_or_erasing_preference(controllers, tmp_path):
    c = controllers()
    attempts = []

    class Denied(Output):
        def start(self):
            attempts.append(True)
            self.options['on_end']('', PermissionError('test permission denial'))

    c.touchpad.output_factory = Denied
    source = connect(c)
    wait_until(lambda: c.touchpad.state == 'error')
    for _ in range(5):
        c.touchpad.changed.emit()
    assert len(attempts) == 1 and not source.starts
    assert c._bool_setting('touchpad/enabled', True)
    assert c.recognitionEnabled
    log = (tmp_path / 'logs/diagnostic.log').read_text()
    assert 'state="error"' in log and '辅助功能' in log
