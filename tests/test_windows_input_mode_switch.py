"""Windows Raw2 must keep the return gesture alive while voice owns input."""
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_stroke_integration import idle
from test_input_source_activation import wait_until
from proximic_ring.touchpad.touchpad_mode_switch import TouchpadModeSwitchOutput
from ring_python_sdk.touchpad.events import Raw2PointerClick, Raw2PointerMove


def double_click(index=1, **values):
    return Raw2PointerClick(index, time.monotonic(), count=2, click_id=index,
                           session_id='mode-switch', **values)


def test_voice_sink_accepts_only_one_current_final_double_click():
    changes = []
    connection = threading.Event()
    output = TouchpadModeSwitchOutput(connection=connection, on_ready=lambda: None,
                                     on_mode_switch=changes.append)
    output.start()
    output.submit(double_click())
    assert not changes
    output.activate()
    event = double_click()
    for ignored in (replace(event, count=1), replace(event, button='right'),
                    replace(event, is_preview=True), replace(event, timestamp=output.input_since - .01),
                    replace(event, timestamp=time.monotonic() - 1),
                    Raw2PointerMove(5, 5, 1, 1, time.monotonic(), mode='TWO_FINGER')):
        output.submit(ignored)
    assert not changes
    output.submit(event)
    output.submit(event)
    assert changes == [event]
    output.stop()
    output.activate()
    output.submit(double_click(2))
    assert not output.active and changes == [event]


def test_real_windows_pointer_routes_double_click_without_posting_mouse_buttons():
    from test_raw2_windows import User32
    from ring_python_sdk.touchpad.windows import WindowsSystemMouse
    from proximic_ring.touchpad.touchpad_mouse import TouchpadMouseOutput
    api = User32()
    changed, ended = threading.Event(), threading.Event()
    received = []
    def switched(event):
        received.append(event)
        changed.set()
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=lambda: None, on_end=lambda *_: ended.set(), raw2=True,
        on_double_click=lambda *_: pytest.fail('Unexpected stroke entry'),
        on_mode_switch=switched, mouse_factory=lambda: WindowsSystemMouse(api))
    output.activate()
    event = double_click()
    output.submit(event)
    output.submit(event)
    output.start()
    try:
        assert changed.wait(1)
    finally:
        output.stop()
        assert ended.wait(1)
    assert received == [event] and not api.calls


@pytest.fixture
def windows_pointer(inline_ui, monkeypatch):
    import proximic_ring.ui.touchpad.touchpad_controller as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(module.TouchpadController, 'raw2Supported', property(lambda _: True))
    c, bridge, root, tp, source = idle(inline_ui)
    tp._windows_ime = True
    monkeypatch.setattr(tp, '_prepare_raw2_heap', lambda: None)
    tp.start()
    wait_until(lambda: bool(source.starts))
    source.future.set_result(None)
    wait_until(lambda: tp.state == 'running')
    yield c, root, tp, source
    tp.close()
    QTest.qWait(20)


def test_double_click_round_trips_reuse_stream_and_restore_voice_routes(windows_pointer):
    c, root, tp, source = windows_pointer
    run = tp._run
    stream = source.starts[0]
    for index in range(3):
        pointer = run.output
        first = double_click(index * 2 + 1)
        pointer.options['on_mode_switch'](first)
        pointer.options['on_mode_switch'](first)  # Two queued callbacks, one transition.
        wait_until(lambda: tp.inputMode == 'voice' and run.output_ready)
        voice = run.output
        assert isinstance(voice, TouchpadModeSwitchOutput) and voice.active
        assert pointer.stopped and not tp.blocksVoice and c._recognition_event.is_set()
        assert tp._run is run and len(source.starts) == 1 and source.stops == 0
        assert '单指双击' in tp.message
        stream['on_pointer_move'](first)  # Late click from the previous pointer mode.
        stream['on_pointer_move'](replace(double_click(99), count=1))
        stream['on_pointer_move'](Raw2PointerMove(9, 9, 1, 100, time.monotonic()))
        QTest.qWait(10)
        assert tp.inputMode == 'voice'
        # The Raw2 sink owns double taps; firmware copies cannot toggle again.
        assert tp.consume_gesture(SimpleNamespace(name='double-tap'), c._disconnect_event)
        QTest.qWait(10)
        assert tp.inputMode == 'voice'
        run.switch_gesture_until = 0  # Past the transition gesture's suppression window.
        assert c._ring_gestures.filter(SimpleNamespace(name='tap'), True, c._disconnect_event)
        stream['on_pointer_move'](double_click(index * 2 + 2))
        wait_until(lambda: tp.inputMode == 'pointer' and run.output_ready)
        assert not voice.active and tp._run is run and source.stops == 0
        assert not c._ring_gestures.filter(SimpleNamespace(name='tap'), True, c._disconnect_event)
        pointer.options['on_mode_switch'](double_click(100))  # Retired worker.
        QTest.qWait(10)
        assert tp.inputMode == 'pointer'


@pytest.mark.parametrize('reason', ['manual', 'disconnect'])
def test_voice_listener_stops_with_the_touchpad_session(windows_pointer, reason):
    c, _, tp, source = windows_pointer
    tp._run.output.options['on_mode_switch'](double_click())
    wait_until(lambda: tp.inputMode == 'voice' and tp._run.output_ready)
    listener = tp._run.output
    if reason == 'disconnect':
        c._apply_runtime_finished('disconnected')
    else:
        tp.stop()
    wait_until(lambda: tp._run is None)
    assert not listener.active and source.stops == 1
    listener.submit(double_click(2))
    QTest.qWait(10)
    assert tp._run is None and not tp.active
