"""Windows x64 Raw2: delivered SDK model, platform gates and Win32 button/wheel output."""
import ctypes
import hashlib
from pathlib import Path
import platform
import sys
import threading
import time
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest

from ring_python_sdk.touchpad import raw2_model
from ring_python_sdk.touchpad.windows import WindowsSystemMouse, WHEEL_UNITS_PER_PIXEL, _Input, _Point
from ring_python_sdk.touchpad.events import Raw2PointerClick, Raw2PointerMove
from proximic_ring.touchpad.touchpad_mouse import TouchpadMouseOutput

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src/ring_windows_sdk'
DELIVERY = ROOT / 'third_party/ring-windows-sdk-0.4.0-windows-x64'

windows_x64 = pytest.mark.skipif(
    not (sys.platform == 'win32' and platform.machine().lower() in {'amd64', 'x86_64'}
         and raw2_model._windows_modules_available()),
    reason='Windows x64 with onnxruntime only')


def test_vendored_package_matches_the_delivered_wheel():
    wheel = DELIVERY / 'sdk/ring_windows_sdk-0.4.0-py3-none-win_amd64.whl'
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == \
        '92d1185c9e7969242ebcf53f3168952e2aaa06a6032a893c6427f43efde53d10'
    prefix = 'ring_windows_sdk-0.4.0.data/purelib/ring_mac_sdk/'
    with zipfile.ZipFile(wheel) as archive:
        names = [name[len(prefix):] for name in archive.namelist() if name.startswith(prefix)]
        assert 'native/ring_touchpad_model.dll' in names
        for name in names:
            # Git may check out the provider's text files with CRLF.
            assert (PACKAGE / name).read_bytes().replace(b'\r\n', b'\n') == \
                archive.read(prefix + name).replace(b'\r\n', b'\n'), name
        # The Windows delivery's encrypted model is the same file the Mac SDK ships.
        assert archive.read(prefix + 'assets/ring_runtime.bin') == \
            (ROOT / 'src/ring_mac_sdk/assets/ring_runtime.bin').read_bytes()


@windows_x64
def test_windows_model_decrypts_in_memory_and_matches_provider_prediction():
    from ring_windows_sdk.model import MODEL_SHA256, load_model_bytes
    assert hashlib.sha256(load_model_bytes()).hexdigest() == MODEL_SHA256
    started = time.perf_counter()
    model = raw2_model.default_model_factory()()
    # Well inside the receiver's 20 s model-load budget.
    assert time.perf_counter() - started < 5
    first = model.step(np.zeros(8, dtype=np.float32))
    assert first.shape == (2, 4) and np.isfinite(first).all()
    # mythring-sdk 1.18.0 test_bundled_model_first_prediction (x64 tolerances).
    expected = np.array([[.049265, .121755, 1.016825, -4.135263],
                         [.046626, .117180, 1.155314, -4.161733]], dtype=np.float32)
    tolerance = np.array([1e-4, 1e-4, 2e-3, 2e-3], dtype=np.float32)
    assert (np.abs(first - expected) <= tolerance).all()
    assert model.state_vector().shape == (1216,)
    model.reset_for_epoch()
    np.testing.assert_array_equal(model.step(np.zeros(8, dtype=np.float32)), first)


@windows_x64
def test_windows_gate_selects_sdk_connection_and_can_be_disabled(monkeypatch):
    from proximic_ring.audio import ring
    from proximic_ring.ui.touchpad.touchpad_controller import TouchpadController
    supported = TouchpadController.raw2Supported.fget
    def controller(raw2):
        return SimpleNamespace(_source=SimpleNamespace(connected_raw2_firmware=lambda: raw2))
    assert raw2_model.raw2_platform_supported() and ring._connection_backend() == 'sdk'
    # Before INFO is known, and for touchpad firmware, Raw2 stays selected.
    assert supported(SimpleNamespace(_source=None)) and supported(controller(None)) and supported(controller(True))
    # Public firmware (1.2.133 / 2.0.2) keeps the legacy touchpad on Windows.
    assert not supported(controller(False))
    monkeypatch.setenv('PROXIMIC_WINDOWS_RAW2', '0')
    assert not raw2_model.raw2_platform_supported() and ring._connection_backend() == 'legacy'
    assert raw2_model.default_model_factory() is None
    assert not supported(controller(True))


def test_gate_is_off_without_loader_dependencies(monkeypatch):
    monkeypatch.setattr(raw2_model, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(raw2_model.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(raw2_model, '_windows_modules_available', lambda: False)
    assert not raw2_model.windows_raw2_enabled() and raw2_model.default_model_factory() is None


class User32:
    def __init__(self):
        self.position = [0, 0]
        self.calls = []
        self.metrics = {76: 0, 77: 0, 78: 300, 79: 200}
    def GetCursorPos(self, value):
        point = ctypes.cast(value, ctypes.POINTER(_Point)).contents
        point.x, point.y = self.position
        return 1
    def SetCursorPos(self, x, y):
        self.position[:] = [x, y]
        self.calls.append(('move', x, y))
        return 1
    def GetSystemMetrics(self, code): return self.metrics[code]
    def GetAsyncKeyState(self, key): return 0
    def SendInput(self, count, inputs, size):
        self.calls.append([(inputs[i].mi.dwFlags, ctypes.c_int32(inputs[i].mi.mouseData).value)
                           for i in range(count)])
        return count


def enabled_mouse():
    api = User32()
    mouse = WindowsSystemMouse(api)
    mouse.enable()
    return mouse, api


def test_right_and_double_clicks_post_receipts_and_ignore_duplicates():
    mouse, api = enabled_mouse()
    right = mouse.apply([dict(kind='click', button='right', count=1, click_id='s:0:1')])
    assert api.calls == [[(0x0008, 0), (0x0010, 0)]]
    assert right == dict(moves=0, clicks_posted=1, down_posts=1, up_posts=1, reason='posted')
    assert mouse.apply([dict(kind='click', button='right', count=1, click_id='s:0:1')])['reason'] == 'duplicate_click'
    double = mouse.apply([dict(kind='click', button='left', count=2, click_id='s:0:2')])
    assert api.calls[1:] == [[(0x0002, 0), (0x0004, 0)]] * 2
    assert double['clicks_posted'] == 2 and double['reason'] == 'posted'
    with pytest.raises(ValueError):
        mouse.apply([dict(kind='click', button='middle', count=1)])
    assert not mouse.enabled


def test_expired_raw2_entries_and_disabled_clicks_post_nothing():
    mouse, api = enabled_mouse()
    past = time.monotonic() - 1
    result = mouse.apply([dict(kind='move', dx=5, dy=0, expires_at=past),
                          dict(kind='click', button='left', count=1, expires_at=past)])
    assert not api.calls and result['reason'] == 'native_expired' and result['moves'] == 0
    mouse.clicks_enabled = False
    assert mouse.apply([dict(kind='click', button='left', count=1)])['reason'] == 'clicks_disabled'
    assert not api.calls


def test_wheel_accumulates_partial_units_per_stream():
    mouse, api = enabled_mouse()
    step = .3 / WHEEL_UNITS_PER_PIXEL  # 0.3 wheel units each: nothing posted yet.
    result = mouse.apply([dict(kind='scroll', dy=step, stream=['s', 0])] * 3)
    assert result['scrolls'] == 0 and not api.calls
    mouse.apply([dict(kind='scroll', dy=step, stream=['s', 0])])  # 1.2 units: post 1, keep .2
    mouse.apply([dict(kind='scroll', dy=-30, stream=['s', 0])])  # .2 - 60 = -59.8
    assert api.calls == [[(0x0800, 1)], [(0x0800, -59)]]
    # A new stream discards the previous remainder.
    mouse.apply([dict(kind='scroll', dy=step, stream=['s', 1])] * 3)
    assert len(api.calls) == 2
    with pytest.raises(ValueError):
        mouse.apply([dict(kind='scroll', dy=float('inf'))])


def test_raw2_pointer_output_drives_win32_buttons_and_wheel(monkeypatch):
    import proximic_ring.touchpad.touchpad_mouse as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    api = User32()
    done = threading.Event()
    worker = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=lambda: None, on_end=lambda *_: done.set(), mouse_factory=lambda: WindowsSystemMouse(api),
        on_double_click=lambda *_: pytest.fail('Raw2 double click must not enter handwriting'), raw2=True)
    worker.activate()
    now = time.monotonic()
    for event in [Raw2PointerMove(3., 4., 1., 1, now, mode='TWO_FINGER', session_id='w'),
                  Raw2PointerClick(1, now, button='right', click_id=1, session_id='w'),
                  Raw2PointerClick(1, now, count=2, click_id=2, session_id='w')]:
        worker.submit(event)
    worker.start()
    try:
        deadline = time.monotonic() + 1
        while len(api.calls) < 4 and time.monotonic() < deadline:
            time.sleep(.001)
    finally:
        worker.stop()
        assert done.wait(1)
    assert api.calls == [[(0x0800, int(-4 * WHEEL_UNITS_PER_PIXEL))], [(0x0008, 0), (0x0010, 0)],
                         [(0x0002, 0), (0x0004, 0)], [(0x0002, 0), (0x0004, 0)]]


@pytest.mark.parametrize('hardware,version,expected', [
    (1, '2.1.6-dev.touchpad.9.debug', True), (1, '2.1.6-dev.touchpad.15.debug', True),
    (1, '1.2.133.public', False), (1, '2.0.2.public_dev', False),
    (2, '2.1.6-dev.touchpad.15.debug', False), (1, '', False)])
def test_raw2_firmware_matches_ringo_touchpad_builds_only(hardware, version, expected):
    info = SimpleNamespace(hardware_revision=hardware, firmware_version=version)
    assert raw2_model.raw2_firmware(info) is expected
    assert raw2_model.raw2_firmware(None) is False


def test_audio_source_reports_connected_firmware_from_sdk_info():
    from proximic_ring.audio.ring import RingAudioSource
    source = SimpleNamespace(_touchpad_endpoint=None)
    probe = RingAudioSource.connected_raw2_firmware
    assert probe(source) is None
    session = SimpleNamespace(_sdk_transport=SimpleNamespace(device_info=SimpleNamespace(
        hardware_revision=1, firmware_version='1.2.133.public')))
    source._touchpad_endpoint = (None, session)
    assert probe(source) is False
    session._sdk_transport.device_info = SimpleNamespace(hardware_revision=1, firmware_version='2.1.6-dev.touchpad.15.debug')
    assert probe(source) is True
    source._touchpad_endpoint = (None, SimpleNamespace(_sdk_transport=None))  # legacy connection
    assert probe(source) is None


def test_windows_timer_hold_is_reference_counted_and_idempotent():
    first, second = raw2_model.hold_windows_timer(), raw2_model.hold_windows_timer()
    expected = 2 if sys.platform == 'win32' else 0
    assert raw2_model._timer_holders == expected
    first(); first()
    assert raw2_model._timer_holders == expected // 2
    second()
    assert raw2_model._timer_holders == 0
