"""Windows pointer output tests with a fake user32; no real pointer writes."""
import ctypes

from ring_python_sdk.touchpad.windows import WindowsSystemMouse, _Point


class FakeUser32:
    def __init__(self):
        self.point = [100, 200]
        self.click_flags = []
        self.escape = False

    def GetAsyncKeyState(self, key):
        assert key == 0x1B
        return 0x8000 if self.escape else 0

    def GetCursorPos(self, pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(_Point)).contents
        point.x, point.y = self.point
        return 1

    def SetCursorPos(self, x, y):
        self.point = [x, y]
        return 1

    def SendInput(self, count, inputs, size):
        self.click_flags.extend(inputs[index].mi.dwFlags for index in range(count))
        return count


def test_windows_pointer_applies_gain_inversion_fraction_and_click():
    user32 = FakeUser32()
    mouse = WindowsSystemMouse(user32)
    mouse.gain = 2
    mouse.invert_y = True
    mouse.enable()
    mouse.apply([dict(kind="move", dx=.3, dy=1), dict(kind="move", dx=.3, dy=1),
                 dict(kind="click")])
    assert user32.point == [101, 196]
    assert user32.click_flags == [0x0002, 0x0004]
    mouse.disable()
    mouse.apply([dict(kind="move", dx=100, dy=100)])
    assert user32.point == [101, 196]


def test_windows_pointer_escape_stops_output():
    user32 = FakeUser32()
    mouse = WindowsSystemMouse(user32)
    mouse.enable()
    user32.escape = True
    try:
        mouse.apply([dict(kind="move", dx=1, dy=1)])
    except InterruptedError:
        pass
    else:
        raise AssertionError("Escape should stop mouse output")
    assert not mouse.enabled and user32.point == [100, 200]
