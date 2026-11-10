"""Real CoreGraphics event fields, intercepted before any system input is posted."""
from types import SimpleNamespace

import pytest

from ring_python_sdk.touchpad.macos import MacSystemMouse

pytestmark = pytest.mark.mac_only


@pytest.fixture
def pointer():
    import Quartz as Q
    posts, position = [], [10., 10.]
    class Quartz:
        def __getattr__(self, name):
            return getattr(Q, name)
        def CGPreflightPostEventAccess(self):
            return True
        def CGEventPost(self, _, event):
            posts.append(event)
            point = Q.CGEventGetLocation(event)
            position[:] = [point.x, point.y]
    mouse = MacSystemMouse(quartz=Quartz(), accessibility=SimpleNamespace(AXIsProcessTrusted=lambda: True))
    mouse._position = lambda: tuple(position)
    mouse._clamp = lambda x, y: (min(18., max(0., x)), min(18., max(0., y)))
    mouse.stop_on_escape = False
    mouse.enable()
    yield mouse, posts, position, Q
    mouse.disable()


@pytest.mark.parametrize('kind', ['move', 'drag'])
@pytest.mark.parametrize('gain,invert,expected', [
    (1., False, [(3, 4), (5, 4)]),
    (2., True, [(6, -8), (2, -2)]),
])
def test_native_motion_deltas_follow_gain_inversion_and_clipping(pointer, kind, gain, invert, expected):
    mouse, posts, _, Q = pointer
    mouse.gain, mouse.invert_y = gain, invert
    if kind == 'drag':
        mouse.apply([dict(kind='button_down')])
    mouse.apply([dict(kind=kind, dx=3, dy=4), dict(kind=kind, dx=10, dy=10)])
    events = [e for e in posts if Q.CGEventGetType(e) in (Q.kCGEventMouseMoved, Q.kCGEventLeftMouseDragged)]
    assert [(Q.CGEventGetIntegerValueField(e, Q.kCGMouseEventDeltaX),
             Q.CGEventGetIntegerValueField(e, Q.kCGMouseEventDeltaY)) for e in events] == expected
    mouse.release_drag()
    if kind == 'drag':
        assert Q.CGEventGetType(posts[-1]) == Q.kCGEventLeftMouseUp
        assert Q.CGEventGetIntegerValueField(posts[-1], Q.kCGMouseEventDeltaX) == 0


def test_subpixel_drags_retain_total_delta_across_batches(pointer):
    mouse, posts, _, Q = pointer
    mouse.apply([dict(kind='button_down')])
    for _ in range(4):
        mouse.apply([dict(kind='drag', dx=.25, dy=-.25)])
    events = [e for e in posts if Q.CGEventGetType(e) == Q.kCGEventLeftMouseDragged]
    assert sum(Q.CGEventGetIntegerValueField(e, Q.kCGMouseEventDeltaX) for e in events) == 1
    assert sum(Q.CGEventGetIntegerValueField(e, Q.kCGMouseEventDeltaY) for e in events) == -1
