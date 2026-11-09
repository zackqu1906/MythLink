"""Two-finger axis recognition, live settings and bounded navigation output."""
from dataclasses import replace
import os
import time
from types import SimpleNamespace

import pytest

from proximic_ring.touchpad.touchpad_swipe import TouchpadSwipe
from ring_python_sdk.touchpad.events import Raw2PointerContact, Raw2PointerMove, Raw2Reset
from test_touchpad_raw2_integration import output, drain
from test_app_shortcuts import desktop


def edge(phase='BEGIN', identifier=1, generation=0):
    now = time.monotonic()
    return Raw2PointerContact(phase, 'TWO_FINGER', identifier, 1, now, now,
                             end_timestamp=now, session_id='test', generation=generation)


def move(dx, dy=0, identifier=1, **kwargs):
    return Raw2PointerMove(dx, dy, 1, 1, time.monotonic(), mode='TWO_FINGER',
                          episode_id=identifier, session_id='test', **kwargs)


@pytest.mark.parametrize('dx,expected', [(-10, 'forward'), (10, 'back')])
def test_horizontal_action_fires_once_per_contact_and_rearms_after_up(dx, expected):
    detector = TouchpadSwipe()
    for identifier in (1, 2):
        detector.contact(edge(identifier=identifier))
        values = [detector.move(move(dx, .5, identifier)) for _ in range(20)]
        assert [action for _, action in values if action] == [expected]
        assert sum(dy for dy, _ in values) == 0
        detector.contact(edge('END', identifier))


@pytest.mark.parametrize('points', [[(2, 8)]*20 + [(10, 0)]*20,
                                    [(5, 5)]*20, [(2, 0), (-2, 0)]*40])
def test_vertical_diagonal_and_jitter_do_not_navigate(points):
    detector = TouchpadSwipe()
    detector.contact(edge())
    values = [detector.move(move(dx, dy)) for dx, dy in points]
    assert not any(action for _, action in values)
    assert sum(dy for dy, _ in values) == sum(dy for _, dy in points)


def test_missing_begin_cancel_and_wrong_episode_cannot_navigate():
    detector = TouchpadSwipe()
    assert detector.move(move(100, 3)) == (3, None)
    detector.contact(edge())
    assert detector.move(move(100, 3, identifier=2)) == (3, None)
    detector.contact(edge('CANCEL'))
    assert detector.move(move(100, 3)) == (3, None)


@pytest.mark.parametrize('inverted,expected', [(False, -8), (True, 8)])
def test_scroll_direction_applies_live_before_dispatch(inverted, expected):
    worker, mouse, done = output()
    worker.submit(move(0, 4))  # No BEGIN: ordinary scrolling is still allowed.
    worker.configure(clicks=True, double_click=True, right_click=True, scroll=True,
                     scroll_gain=2, invert_scroll=inverted)
    drain(worker, mouse, done, 1)
    assert mouse.calls[0]['kind'] == 'scroll' and mouse.calls[0]['dy'] == expected


@pytest.mark.parametrize('dx,name,direction', [(-20, 'two-finger-swipe-left', 'forward'),
                                             (20, 'two-finger-swipe-right', 'back')])
def test_worker_routes_navigation_once_and_shows_recognition_hint(dx, name, direction):
    hints = []
    worker, mouse, done = output(on_gesture=hints.append)
    mouse.navigate = lambda value, **_: mouse.calls.append(dict(kind='navigate', direction=value))
    worker.submit(edge())
    for _ in range(6): worker.submit(move(dx, .5))
    worker.submit(edge('END'))
    drain(worker, mouse, done, 6)
    assert [event['direction'] for event in mouse.calls if event['kind'] == 'navigate'] == [direction]
    assert hints == [name]
    assert all(event['dy'] == 0 for event in mouse.calls if event['kind'] == 'scroll')


@pytest.mark.parametrize('blocked', ['disabled', 'capture', 'reset', 'expired'])
def test_worker_drops_ineligible_horizontal_navigation(blocked):
    hints = []
    worker, mouse, done = output(on_gesture=hints.append)
    mouse.navigate = lambda *_args, **_kwargs: pytest.fail('ineligible navigation')
    worker.submit(edge())
    if blocked == 'disabled':
        worker.configure(clicks=True, double_click=True, right_click=True, scroll=True,
                         scroll_gain=1, horizontal_swipe=False)
    elif blocked == 'capture': worker.click_context = lambda: (1, 'capture')
    elif blocked == 'reset':
        worker.submit(Raw2Reset('reset', 0, time.monotonic(), session_id='test', generation=1))
    sample = move(100, 0)
    if blocked == 'expired': sample = replace(sample, timestamp=time.monotonic()-1)
    worker.submit(sample)
    worker.submit(move(0, 4, generation=1 if blocked == 'reset' else 0))
    drain(worker, mouse, done, 1)
    assert not any('swipe' in hint for hint in hints)


@pytest.mark.mac_only
@pytest.mark.parametrize('direction,code', [('forward', 30), ('back', 33)])
def test_navigation_uses_balanced_command_bracket_to_current_pid(desktop, monkeypatch, direction, code):
    from proximic_ring.platforms.macos.native_touchpad import WorkerTouchpadMouse
    from proximic_ring.platforms.macos import mac_workspace
    _, state, quartz = desktop
    monkeypatch.setattr(mac_workspace, 'frontmost_application',
                        lambda: SimpleNamespace(processIdentifier=lambda: 42))
    read_fd, write_fd = os.pipe()
    mouse = WorkerTouchpadMouse(read_fd, quartz=quartz, accessibility=SimpleNamespace())
    mouse.enabled = True
    try:
        result = mouse.navigate(direction, expires_at=time.monotonic()+1)
        assert result['navigations_posted'] == 1
        assert [(e['code'], e['down']) for _, e in state.sent] == [(55, True), (code, True), (code, False), (55, False)]
        assert all(pid == 42 for pid, _ in state.sent) and state.sent[-1][1]['flags'] == 0
        state.sent.clear()
        assert not mouse.navigate(direction, expires_at=time.monotonic()-1)['navigations_posted']
        assert not state.sent
    finally:
        mouse.disable()
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.mac_only
def test_navigation_target_change_during_key_allocation_sends_nothing(desktop, monkeypatch):
    from proximic_ring.platforms.macos.native_touchpad import WorkerTouchpadMouse
    from proximic_ring.platforms.macos import mac_workspace
    _, state, quartz = desktop
    target = [42]
    monkeypatch.setattr(mac_workspace, 'frontmost_application',
                        lambda: SimpleNamespace(processIdentifier=lambda: target[0]))
    allocate = quartz.CGEventCreateKeyboardEvent
    def changed(*args):
        target[0] = 99
        return allocate(*args)
    quartz.CGEventCreateKeyboardEvent = changed
    read_fd, write_fd = os.pipe()
    mouse = WorkerTouchpadMouse(read_fd, quartz=quartz, accessibility=SimpleNamespace())
    mouse.enabled = True
    try:
        assert not mouse.navigate('back', expires_at=time.monotonic()+1)['navigations_posted']
        assert not state.sent
    finally:
        mouse.disable()
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.mac_only
def test_navigation_crosses_native_ipc_and_expired_request_is_not_replayed(desktop, monkeypatch):
    from proximic_ring.platforms.macos.native_touchpad import WorkerTouchpadMouse, NativeTouchpadMouse
    from proximic_ring.platforms.macos import native_access_worker, mac_workspace
    _, state, quartz = desktop
    monkeypatch.setattr(mac_workspace, 'frontmost_application',
                        lambda: SimpleNamespace(processIdentifier=lambda: 42))
    read_fd, write_fd = os.pipe()
    mouse = WorkerTouchpadMouse(read_fd, quartz=quartz, accessibility=SimpleNamespace())
    mouse.enabled = True
    dispatcher = native_access_worker.Dispatcher()
    dispatcher.touchpad_mouse = mouse
    class Channel:
        def call(self, operation, **options):
            return dispatcher.handle(dict(operation=operation, **options))['result']
        def close(self): mouse.disable()
    parent = NativeTouchpadMouse(channel_factory=lambda **_: Channel())
    parent.enabled = True
    try:
        assert parent.navigate('forward', expires_at=time.monotonic()+1)['navigations_posted'] == 1
        state.sent.clear()
        reply = dispatcher.handle(dict(operation='touchpad_navigate', direction='back',
                                       created=time.monotonic()-1, expires_at=time.monotonic()+1))
        assert reply['result']['reason'] == 'ipc_expired' and not state.sent
        parent.disable()
        assert not parent.navigate('back', expires_at=time.monotonic()+1)['navigations_posted']
        assert not state.sent
    finally:
        parent.close()
        os.close(read_fd)
        os.close(write_fd)
