import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from proximic_ring.ui.gestures.shortcut_recorder import shortcut_from_event, shortcut_from_key
from test_app_gestures import route


@pytest.mark.parametrize("key,modifiers,expected", [
    (Qt.Key_BraceLeft, Qt.ControlModifier | Qt.ShiftModifier, "Cmd+Shift+["),
    (Qt.Key_BraceRight, Qt.ControlModifier | Qt.ShiftModifier, "Cmd+Shift+]"),
    (Qt.Key_Exclam, Qt.ControlModifier | Qt.ShiftModifier, "Cmd+Shift+1"),
    (Qt.Key_Return, Qt.ControlModifier, "Cmd+Return"),
    (Qt.Key_Tab, Qt.MetaModifier, "Ctrl+Tab"),
    (Qt.Key_F8, Qt.NoModifier, "F8"),
    (Qt.Key_Space, Qt.ControlModifier, "Cmd+Space"),
])
def test_recording_normalizes_qt_key_symbols_to_injection_keys(key, modifiers, expected, monkeypatch):
    monkeypatch.setattr("proximic_ring.ui.gestures.shortcut_recorder.sys.platform", "darwin")
    assert shortcut_from_key(int(key), modifiers.value) == expected


@pytest.mark.parametrize("key", [Qt.Key_Shift, Qt.Key_Meta, Qt.Key_Control, Qt.Key_Alt])
def test_modifier_alone_does_not_finish_recording(key):
    assert shortcut_from_key(int(key), Qt.ControlModifier.value) == ""


def test_native_key_keeps_physical_key_despite_option_character(monkeypatch):
    monkeypatch.setattr("proximic_ring.ui.gestures.shortcut_recorder.sys.platform", "darwin")
    event = QKeyEvent(QEvent.KeyPress, 0x201C, Qt.ControlModifier | Qt.AltModifier,
                      0, 33, 0x180000, "“")
    assert shortcut_from_event(event) == "Cmd+Alt+["


def test_synthetic_zero_native_key_does_not_become_a():
    assert shortcut_from_event(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)) == "Return"


def test_fn_navigation_keeps_semantic_key(monkeypatch):
    monkeypatch.setattr("proximic_ring.ui.gestures.shortcut_recorder.sys.platform", "darwin")
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_PageDown, Qt.NoModifier, 0, 125, 0x800000)
    assert shortcut_from_event(event) == "PageDown"


def test_bare_text_is_not_saved_as_a_command():
    with pytest.raises(ValueError):
        shortcut_from_key(int(Qt.Key_A), 0)


@pytest.fixture
def recording(route, monkeypatch):
    from PySide6.QtCore import QObject
    from types import SimpleNamespace
    from proximic_ring.ui.gestures import app_gesture_controller as module
    service = route[1]
    calls = []
    service._recording_guard = SimpleNamespace(
        start=lambda: calls.append('start'), stop=lambda: calls.append('stop'))
    field = QObject()
    for key in ('shortcutRecorder', 'visible', 'focus'):
        field.setProperty(key, True)
    monkeypatch.setattr(module.QGuiApplication, 'focusObject', lambda: field)
    captured = []
    service.shortcutCaptured.connect(captured.append)
    service.shortcutCaptured.connect(lambda _: setattr(service, 'recording', False))
    service.recording = True
    yield service, field, calls, captured
    service.recording = False


def test_system_chord_protection_lasts_until_release(recording):
    service, _, calls, captured = recording
    down = QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.ControlModifier)
    up = QKeyEvent(QEvent.KeyRelease, Qt.Key_Space, Qt.ControlModifier)
    assert service.eventFilter(None, down)
    assert service.recording and calls == ['start'] and captured == []
    repeat = QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.ControlModifier, ' ', True)
    assert service.eventFilter(None, repeat)
    assert service.eventFilter(None, up)
    assert captured == ['Cmd+Space'] and calls == ['start', 'stop']
    assert not service.recording


@pytest.mark.parametrize('native', [False, True])
def test_shift_released_before_bracket_still_finishes_the_chord(recording, native):
    service, _, calls, captured = recording
    codes = (0, 33, 0x120000) if native else (0, 0, 0)
    down = QKeyEvent(QEvent.KeyPress, Qt.Key_BraceLeft, Qt.ControlModifier | Qt.ShiftModifier, *codes)
    codes = (0, 33, 0) if native else (0, 0, 0)
    up = QKeyEvent(QEvent.KeyRelease, Qt.Key_BracketLeft, Qt.NoModifier, *codes)
    service.eventFilter(None, down)
    service.eventFilter(None, up)
    assert captured == ['Cmd+Shift+['] and calls == ['start', 'stop']


def test_native_key_zero_matches_after_command_released(recording):
    service, _, calls, captured = recording
    service.eventFilter(None, QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.ControlModifier, 0, 0, 0x100000))
    service.eventFilter(None, QKeyEvent(QEvent.KeyRelease, Qt.Key_A, Qt.NoModifier, 0, 0, 0))
    assert captured == ['Cmd+A'] and calls == ['start', 'stop']


@pytest.mark.parametrize('cancel', ['escape', 'deactivate', 'focus', 'close'])
def test_cancel_releases_system_guard_and_discards_pending_chord(recording, cancel):
    service, field, calls, captured = recording
    service.eventFilter(None, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.ControlModifier))
    if cancel == 'escape':
        service.eventFilter(None, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    elif cancel == 'deactivate':
        service._recording_app_state_changed(Qt.ApplicationInactive)
    elif cancel == 'focus':
        field.setProperty('shortcutRecorder', False)
    else:
        service.close()
    assert not service.eventFilter(None, QKeyEvent(QEvent.KeyRelease, Qt.Key_Space, Qt.ControlModifier))
    assert captured == [] and calls == ['start', 'stop']
    assert not service.recording


def test_protection_failure_does_not_enter_unprotected_recording(recording):
    service, field, calls, _ = recording
    service.recording = False
    def fail():
        raise RuntimeError('需要辅助功能权限，请手动输入')
    service._recording_guard.start = fail
    service.recording = True
    assert not service.recording and not field.property('focus')
    assert '手动输入' in service.recordingError
    assert calls == ['start', 'stop']
