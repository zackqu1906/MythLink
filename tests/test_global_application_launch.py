"""Global application selection, persistence, arbitration and asynchronous launch."""
from copy import deepcopy
from dataclasses import replace
import json
import threading

import pytest
from PySide6.QtCore import QObject, QCoreApplication, QProcess, Signal

from proximic_ring.gestures.gesture_settings import (GlobalApplication, GlobalGestureBindings,
    GLOBAL_BINDINGS_KEY, GLOBAL_APPLICATIONS_KEY, GESTURE_LABELS)
from test_app_gestures import route
from test_ring_gestures import request, deliver_gestures


def test_application_bindings_roundtrip_swap_replace_and_remove():
    old = GlobalGestureBindings()
    legacy = json.loads(old.to_json())
    del legacy['applications']
    assert GlobalGestureBindings.from_json(json.dumps(legacy)) == old
    bindings = old.with_application('snap', 'com.apple.TextEdit', '文本编辑')
    bindings = bindings.with_custom('middle-pinch', '查找', 'Cmd+F')
    swapped = bindings.with_action('middle-pinch', 'application:snap')
    assert swapped.application_for('middle-pinch').bundle == 'com.apple.TextEdit'
    assert swapped.custom_for('snap').shortcut == 'Cmd+F'
    assert swapped.action_for('middle-pinch') == 'application:middle-pinch'
    bindings = swapped.with_application('clench', 'com.apple.Terminal', '终端')
    assert bindings.window_selector == ''
    assert len(bindings.applications) == 2
    assert GlobalGestureBindings.from_json(bindings.to_json()) == bindings
    assert bindings.labels()['application:clench'] == '打开 终端'
    assert 'clench' in bindings.reserved
    bindings = bindings.with_action('middle-pinch', '')
    assert bindings.application_for('middle-pinch') is None
    assert bindings.application_for('clench').bundle == 'com.apple.Terminal'
    assert bindings.with_custom('clench', '查找', 'Cmd+F').applications == ()


@pytest.mark.parametrize('changes', [dict(gesture='unknown'), dict(bundle=''), dict(bundle='-a'),
    dict(bundle='com.apple.TextEdit --args'), dict(application_name=''), dict(application_name=None)])
def test_invalid_launch_targets_are_rejected(changes):
    with pytest.raises(ValueError):
        GlobalApplication(**(dict(gesture='snap', bundle='com.apple.TextEdit', application_name='文本编辑') | changes))


def test_launch_settings_reject_duplicate_gestures_and_malformed_records():
    with pytest.raises(ValueError):
        GlobalGestureBindings(applications=(GlobalApplication('clench', 'com.apple.TextEdit', '文本编辑'),))
    for value in [None, {}, [None], [{'gesture': 'snap'}]]:
        raw = json.loads(GlobalGestureBindings().to_json())
        raw['applications'] = value
        with pytest.raises(ValueError):
            GlobalGestureBindings.from_json(json.dumps(raw))


def select_application(c, gesture='snap', bundle='com.apple.TextEdit', label='文本编辑'):
    c.appGestures.catalog._candidates = [dict(value=bundle, label=label)]
    assert c.gestureMappingHistory.apply('global', '', 'regular', gesture,
        dict(id='open_application', applicationBundle=bundle))


def test_selection_saves_without_adding_app_scope_and_undo_restores_previous_action(route):
    c, service, inline, *_ = route
    before, apps = c._global_gesture_bindings, deepcopy(service.catalog._apps)
    select_application(c, 'clench')
    saved = c._settings.value(GLOBAL_BINDINGS_KEY)
    restored = GlobalGestureBindings.from_json(saved)
    assert restored.application_for('clench').bundle == 'com.apple.TextEdit'
    assert service.catalog._apps == apps
    assert service.catalog.globalOccupancy['clench'] == '打开 文本编辑'
    options = {item['id']: item for item in c.ringGestures.globalActionOptions('clench')}
    assert options['application-choice:com.apple.TextEdit']['applicationBundle'] == 'com.apple.TextEdit'
    assert c.ringGestures.globalBindingAction('clench')['id'] == 'application:clench'
    assert 'open_application' in options
    inline._view['phase'] = 'listening'
    assert not c.ringGestures.setGlobalApplicationBinding('snap', 'com.apple.TextEdit')
    inline._view['phase'] = 'idle'
    assert not c.ringGestures.setGlobalApplicationBinding('snap', 'missing.app')
    assert c._settings.value(GLOBAL_BINDINGS_KEY) == saved
    assert c.gestureMappingHistory.undo('global/clench')
    assert GlobalGestureBindings.from_json(c._settings.value(GLOBAL_BINDINGS_KEY)) == before
    assert json.loads(c._settings.value(GLOBAL_APPLICATIONS_KEY)) == [
        dict(bundle='com.apple.TextEdit', application_name='文本编辑')]


def recent_bundles(ring, gesture='snap'):
    return [item['applicationBundle'] for item in ring.globalActionOptions(gesture) if item.get('applicationBundle')]


def test_shared_history_caps_at_five_deduplicates_and_reuses_without_moving_other_bindings(route):
    c, *_ = route
    ring = c.ringGestures
    gestures = ['snap', 'middle-pinch', 'clench', 'swipe-up', 'swipe-down', 'index-pinch']
    for index, gesture in enumerate(gestures):
        select_application(c, gesture, f'com.example.App{index}', f'应用 {index}')
    expected = [f'com.example.App{index}' for index in range(5, 0, -1)]
    for gesture in GESTURE_LABELS:
        assert recent_bundles(ring, gesture) == expected
    # Eviction is only from the reusable list, never from an existing mapping.
    assert len(c._global_gesture_bindings.applications) == 6
    assert ring.globalBindingAction('snap')['applicationBundle'] == 'com.example.App0'
    c.appGestures.catalog._candidates = []  # Works before an installed-app scan.
    assert ring.setGlobalApplicationBinding('tap', 'com.example.App2')
    assert c._global_gesture_bindings.application_for('clench').bundle == 'com.example.App2'
    assert c._global_gesture_bindings.application_for('tap').bundle == 'com.example.App2'
    expected.remove('com.example.App2')
    expected.insert(0, 'com.example.App2')
    assert recent_bundles(ring) == expected
    assert ring.setGlobalGestureAction('tap', '')
    assert recent_bundles(ring) == expected
    assert [item['bundle'] for item in json.loads(c._settings.value(GLOBAL_APPLICATIONS_KEY))] == expected
    prior = c._settings.value(GLOBAL_APPLICATIONS_KEY)
    assert not ring.setGlobalApplicationBinding('tap', 'com.example.Missing')
    assert c._settings.value(GLOBAL_APPLICATIONS_KEY) == prior


def test_recent_history_survives_restart_and_migrates_existing_bindings_only_once(route, tmp_path, monkeypatch):
    from test_interaction_controls import _controller, _close
    c, *_ = route
    select_application(c, 'snap', 'com.apple.TextEdit', '文本编辑')
    select_application(c, 'clench', 'com.apple.Terminal', '终端')
    c._settings.remove(GLOBAL_APPLICATIONS_KEY)  # Configuration from the previous version.
    fresh = _controller(tmp_path, monkeypatch)
    try:
        assert recent_bundles(fresh.ringGestures) == ['com.apple.Terminal', 'com.apple.TextEdit']
        assert fresh._global_gesture_bindings == c._global_gesture_bindings
        saved_history = fresh._settings.value(GLOBAL_APPLICATIONS_KEY)
        assert json.loads(saved_history)[0]['bundle'] == 'com.apple.Terminal'
        fresh._settings.setValue(GLOBAL_BINDINGS_KEY, GlobalGestureBindings().to_json())
    finally:
        fresh.ringGestures.close()
        _close(fresh)
    restarted = _controller(tmp_path, monkeypatch)
    try:
        assert not restarted._global_gesture_bindings.applications
        assert recent_bundles(restarted.ringGestures) == ['com.apple.Terminal', 'com.apple.TextEdit']
        assert restarted._settings.value(GLOBAL_APPLICATIONS_KEY) == saved_history
    finally:
        restarted.ringGestures.close()
        _close(restarted)


def test_invalid_history_does_not_change_saved_gesture_bindings(route):
    c, *_ = route
    select_application(c)
    before = c._global_gesture_bindings
    for invalid in ['null', '{}', '[null]', '[{"bundle": "--args", "application_name": "invalid"}]']:
        c._settings.setValue(GLOBAL_APPLICATIONS_KEY, invalid)
        assert c.ringGestures._load_recent_applications() == ()
        assert c._global_gesture_bindings == before
        assert c._settings.value(GLOBAL_APPLICATIONS_KEY) == invalid


@pytest.mark.parametrize('success', [True, False])
def test_gesture_launch_uses_selected_app_and_reports_failure(route, monkeypatch, success):
    c, service, _, backend, sent, *_ = route
    select_application(c)
    backend.target = replace(backend.target, input_context='nontext')
    calls, traces = [], []
    monkeypatch.setattr(service, '_trace', lambda *args, **kwargs: traces.append((args, kwargs)))
    def launch(application, completed):
        calls.append(application.bundle)
        completed(success, '' if success else 'open_exit_1')
    monkeypatch.setattr(c.ringGestures._application_launcher, 'open', launch)
    assert not request(c, 'snap')
    assert calls == ['com.apple.TextEdit'] and not sent
    reason = 'global_action_started' if success else 'application_open_failed'
    assert any(args[2] == reason for args, _ in traces)
    if not success:
        assert '无法打开「文本编辑」' in service.notice


def test_application_mapping_and_voice_binding_keep_priority_over_launch(route, monkeypatch):
    from test_application_menus import ACTION, configure
    c, service, _, backend, sent, *_ = route
    calls = []
    monkeypatch.setattr(c.ringGestures._application_launcher, 'open', lambda *args: calls.append(True))
    select_application(c, 'snap')
    backend.target = replace(backend.target, input_context='nontext')
    catalog = configure(service, monkeypatch, backend.target.bundle)
    assert catalog.setBinding(backend.target.bundle, 'snap', ACTION['id'])
    request(c, 'snap')
    assert sent and not calls
    select_application(c, 'tap')
    backend.target = replace(backend.target, input_context='text')
    assert request(c, 'tap')
    assert not calls


def test_selecting_mythlink_raises_this_instance_including_source_runs(route, monkeypatch):
    from proximic_ring.desktop.host_application import HOST_BUNDLE
    c, _, _, backend, *_ = route
    select_application(c, bundle=HOST_BUNDLE, label='Mythlink')
    backend.target = replace(backend.target, input_context='nontext')
    shown = []
    c.ringGestures.mainWindowRequested.connect(lambda: shown.append(True))
    monkeypatch.setattr(c.ringGestures._application_launcher, 'open', lambda *args: pytest.fail('second instance'))
    request(c, 'snap')
    assert shown == [True]


@pytest.mark.parametrize('change', ['settings', 'disconnect', 'busy', 'target'])
def test_launch_is_discarded_when_state_changes_during_validation(route, monkeypatch, change):
    c, service, inline, backend, *_ = route
    select_application(c)
    backend.target = replace(backend.target, input_context='nontext')
    entered, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(c.ringGestures._application_launcher, 'open', lambda *args: calls.append(True))
    def validate(*args, **kwargs):
        entered.set()
        assert release.wait(1)
        return change != 'target'
    monkeypatch.setattr(backend, 'same_target', validate)
    request(c, 'snap', deliver=False)
    QCoreApplication.processEvents()
    try:
        assert entered.wait(.5)
        if change == 'settings': select_application(c, bundle='com.apple.Terminal', label='终端')
        if change == 'disconnect': c._disconnect_event.set()
        if change == 'busy': inline._view['phase'] = 'listening'
    finally:
        release.set()
    deliver_gestures(c)
    assert not calls


@pytest.mark.parametrize('outcome', ['success', 'exit_error', 'start_error', 'timeout', 'close'])
def test_launcher_uses_bundle_identity_and_completes_once(monkeypatch, outcome):
    from proximic_ring.ui.desktop import application_launcher as module
    calls, results = [], []
    class Process(QObject):
        finished = Signal(int, object)
        errorOccurred = Signal(object)
        NotRunning, NormalExit, FailedToStart = QProcess.NotRunning, QProcess.NormalExit, QProcess.FailedToStart
        def __init__(self, parent):
            super().__init__(parent)
            self.running = True
        def start(self, program, args): calls.append((program, args))
        def state(self): return QProcess.Running if self.running else self.NotRunning
        def kill(self): self.running = False
    monkeypatch.setattr(module, 'QProcess', Process)
    owner = QObject()
    launcher = module.ApplicationLauncher(owner)
    launcher.open(GlobalApplication('snap', 'com.apple.TextEdit', '文本编辑'), lambda *args: results.append(args))
    assert calls == [('/usr/bin/open', ['-b', 'com.apple.TextEdit'])]
    process, timer = next(iter(launcher._pending.items()))
    if outcome == 'start_error': process.errorOccurred.emit(Process.FailedToStart)
    elif outcome == 'timeout': timer.timeout.emit()
    elif outcome == 'close': launcher.close()
    else: process.finished.emit(0 if outcome == 'success' else 1, Process.NormalExit)
    process.finished.emit(1, Process.NormalExit)
    assert not launcher._pending and not process.running
    assert results == ([] if outcome == 'close' else [(outcome == 'success', {
        'success': '', 'exit_error': 'open_exit_1', 'start_error': 'open_unavailable', 'timeout': 'open_timeout'}[outcome])])
