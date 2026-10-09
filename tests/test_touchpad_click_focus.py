"""Immediate single-click delivery and double-click routing to current focus."""
import os
from types import SimpleNamespace

import pytest

from proximic_ring.native_touchpad import WorkerTouchpadMouse


@pytest.fixture
def native_click(monkeypatch):
    import ring_python_sdk.touchpad.macos as macos
    monkeypatch.setattr(macos, "PERMISSION_CHECK_INTERVAL", .02)
    import proximic_ring.text_focus as focus
    import proximic_ring.stroke_focus as stroke
    import proximic_ring.mac_workspace as workspace
    posted = []
    origin = {"pid": 123, "window": 42}
    state = dict(origin=origin, editable=True, node="editor", focused=None, permitted=True)
    monkeypatch.setattr(focus, "foreground_stamp", lambda: state["origin"])
    def unexpected_ax(*args, **kwargs):
        pytest.fail("Double-click must use native input-method readiness, not AX hit-testing")
    monkeypatch.setattr(stroke, "editable_at_point", unexpected_ax)
    monkeypatch.setattr(stroke, "editable_node_at_point", unexpected_ax)
    monkeypatch.setattr(focus, "MacAX", unexpected_ax)
    monkeypatch.setattr(workspace, "frontmost_application", lambda: SimpleNamespace(
        processIdentifier=lambda: state["origin"]["pid"], bundleIdentifier=lambda: "test.editor"))
    def post(tap, event):
        posted.append(dict(event))
        state["focused"] = state["node"]
    q = SimpleNamespace(CGPreflightPostEventAccess=lambda: state["permitted"], kCGHIDEventTap=0,
        kCGEventLeftMouseDown=1, kCGEventLeftMouseUp=2, kCGMouseEventClickState="count",
        CGEventPost=post, CGEventSetIntegerValueField=lambda e, k, v: e.update({k: v}))
    read_fd, write_fd = os.pipe()
    mouse = WorkerTouchpadMouse(read_fd, quartz=q, accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    mouse._position = lambda: (30, 40)
    mouse._event = lambda kind, point: dict(kind=kind, point=point)
    mouse.enable()
    yield mouse, posted, state, write_fd
    mouse.disable()
    os.close(read_fd); os.close(write_fd)


def test_single_posts_immediately_at_current_point(native_click):
    mouse, posted, _, _ = native_click
    mouse.apply([dict(kind='click')])
    assert [e["point"] for e in posted] == [(30, 40)] * 2
    mouse._position = lambda: (80, 90)
    mouse.apply([dict(kind='click')])
    assert [e["point"] for e in posted] == [(30, 40)] * 2 + [(80, 90)] * 2
    assert [e["count"] for e in posted] == [1] * 4


@pytest.mark.parametrize("pointer_over_text", [True, False])
def test_double_wakes_current_application_without_moving_caret(native_click, pointer_over_text):
    mouse, posted, state, _ = native_click
    state["editable"] = pointer_over_text
    mouse._position = lambda: (800, 900)
    assert mouse.double_click_target() == dict(bundle="test.editor", pid=123)
    assert not posted and state["focused"] is None


def test_first_click_can_focus_another_application_before_double(native_click):
    mouse, posted, state, _ = native_click
    mouse.apply([dict(kind='click')])
    state["origin"] = dict(pid=456, window=43)
    assert mouse.double_click_target() == dict(bundle="test.editor", pid=456)
    assert len(posted) == 2  # Do not replay the first click or post an OS double-click.


@pytest.mark.parametrize("change", ["stop", "permission", "clicks_disabled"])
def test_double_cannot_run_after_stop_or_revoked_permission(native_click, change):
    mouse, posted, state, stop_fd = native_click
    if change == "stop": os.write(stop_fd, b"x")
    elif change == "permission": state["permitted"] = False
    else: mouse.clicks_enabled = False
    if change == "permission":
        mouse._permissions.thread.join(1)
        assert mouse._permissions.error is not None
        with pytest.raises(PermissionError): mouse.double_click_target()
    else:
        assert mouse.double_click_target() is None
    assert not posted
