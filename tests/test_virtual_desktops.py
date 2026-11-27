import threading
import sys
from types import SimpleNamespace

import pytest
from proximic_ring.desktop import virtual_desktops as desktops


@pytest.mark.parametrize("start,direction,destination,steps", [
    (0, 1, 1, [1]), (2, -1, 1, [-1]), (2, 1, 0, [-1, -1]),
    (0, -1, 2, [1, 1]),
])
def test_rotation_verifies_every_step_including_wrap(start, direction, destination, steps):
    order, current, sent = ("a", "b", "c"), [start], []
    def step(delta):
        sent.append(delta)
        current[0] += delta
    result = desktops.cycle_desktop(direction, threading.Event(),
        layout=lambda: (order, order[current[0]]), step=step)
    assert result == (destination + 1, 3) and sent == steps


def test_single_desktop_needs_no_shortcut_and_cancel_stops_a_wrap():
    cancelled = threading.Event()
    assert desktops.cycle_desktop(1, cancelled, layout=lambda: ((1,), 1), step=pytest.fail) == (1, 1)
    sent = []
    def cancel(delta):
        sent.append(delta)
        cancelled.set()
    assert desktops.cycle_desktop(-1, cancelled, layout=lambda: ((1, 2, 3), 1), step=cancel) is None
    assert sent == [1]


def test_external_desktop_switch_during_wrap_cannot_post_another_key():
    current, sent = [3], []
    def external(delta):
        sent.append(delta)
        current[0] = 1  # Another user action skipped the expected intermediate desktop.
    with pytest.raises(RuntimeError, match="桌面顺序"):
        desktops.cycle_desktop(1, threading.Event(), layout=lambda: ((1, 2, 3), current[0]), step=external)
    assert sent == [-1]


def test_mac_layout_uses_spaces_on_focused_display(monkeypatch):
    screens = [{"Spaces": [{"ManagedSpaceID": n} for n in ids]} for ids in ((1, 2), (5, 7, 9))]
    monkeypatch.setattr(desktops, "_space_functions", lambda: {
        "SLSMainConnectionID": lambda: 10, "SLSGetActiveSpace": lambda _: 7,
        "SLSCopyManagedDisplaySpaces": lambda _: screens})
    assert desktops.mac_layout() == ((5, 7, 9), 7)


@pytest.mark.parametrize("direction,arrow", [(-1, 0x25), (1, 0x27)])
def test_windows_posts_verified_desktop_chord_even_without_a_foreground_window(monkeypatch, direction, arrow):
    if sys.platform != "win32":
        pytest.skip("Windows keyboard backend")
    sent = []
    def send(count, events, size):
        sent.extend((events[i].ki.wVk, bool(events[i].ki.dwFlags & 2)) for i in range(count))
        return count
    user = SimpleNamespace(GetAsyncKeyState=lambda _: 0, SendInput=send)
    monkeypatch.setattr("proximic_ring.desktop.windows_shortcuts.api", lambda: (user, None, None))
    desktops.windows_step(direction)
    assert sent == [(91, False), (17, False), (arrow, False), (arrow, True), (17, True), (91, True)]


def test_mac_uses_configured_mission_control_shortcut_and_respects_disabled_action(monkeypatch):
    posted = []
    hotkeys = {"81": {"enabled": True, "value": {"parameters": [0, 55, 12345]}}, "79": {"enabled": False}}
    defaults = SimpleNamespace(persistentDomainForName_=lambda _: {"AppleSymbolicHotKeys": hotkeys})
    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(NSUserDefaults=SimpleNamespace(
        standardUserDefaults=lambda: defaults)))
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(kCGEventFlagMaskControl=1, kCGHIDEventTap=0,
        CGPreflightPostEventAccess=lambda: True, CGEventCreateKeyboardEvent=lambda _, key, down: [key, down],
        CGEventSetFlags=lambda event, flags: event.append(flags), CGEventPost=lambda _, event: posted.append(event)))
    desktops.mac_step(1)
    with pytest.raises(RuntimeError, match="调度中心"):
        desktops.mac_step(-1)
    assert posted == [[55, True, 12345], [55, False, 12345]]
