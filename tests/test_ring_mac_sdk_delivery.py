"""Offline acceptance of the staged 2026-11-05 SDK; never uses real BLE/input."""
import asyncio
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tomllib
from types import SimpleNamespace
import zipfile

import pytest

from ring_mac_sdk import GestureMode, Motion, ProtocolError, Ring
from ring_mac_sdk import protocol as p
from ring_mac_sdk.system_cursor import ApplicationSink, CursorConfig
from ring_mac_sdk.types import Click, EventMeta

ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "third_party/ringo-touchpad-delivery-20261105"
WHEEL = DELIVERY / "sdk/ring_mac_sdk-0.3.0-py3-none-macosx_13_0_arm64.whl"
PREFIX = "ring_mac_sdk-0.3.0.data/purelib/ring_mac_sdk/"


def test_delivery_and_vendored_package_are_byte_identical_to_provider():
    manifest = json.loads((DELIVERY / "release-manifest.json").read_text())
    assert manifest["sdk"]["version"] == "0.3.0"
    assert hashlib.sha256(WHEEL.read_bytes()).hexdigest() == (
        "2cbd85231a27eb4c9f13b22ad246a41f23c705217c2f90e3ea9fba30a31d97d9"
    )
    for entry in manifest["files"]:
        raw = (DELIVERY / entry["path"]).read_bytes()
        assert len(raw) == entry["bytes"]
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
    for line in (DELIVERY / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        assert hashlib.sha256((DELIVERY / name.lstrip("*")).read_bytes()).hexdigest() == digest
    with zipfile.ZipFile(WHEEL) as wheel:
        record = wheel.read("ring_mac_sdk-0.3.0.dist-info/RECORD").decode()
        for name, digest, size in csv.reader(io.StringIO(record)):
            if digest:
                raw = wheel.read(name)
                actual = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
                assert digest == "sha256=" + actual
                assert len(raw) == int(size)
        names = {name.removeprefix(PREFIX) for name in wheel.namelist() if name.startswith(PREFIX)}
        source = ROOT / "src/ring_mac_sdk"
        actual = {f.relative_to(source).as_posix() for f in source.rglob("*")
                  if f.is_file() and "__pycache__" not in f.parts}
        assert actual == names
        for name in names:
            assert (source / name).read_bytes() == wheel.read(PREFIX + name)


def test_native_model_web_and_license_files_are_in_package_data():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["ring_mac_sdk"]
    with zipfile.ZipFile(WHEEL) as wheel:
        for name in wheel.namelist():
            if name.startswith(PREFIX) and not name.endswith(".py"):
                relative = Path(name.removeprefix(PREFIX))
                assert any(relative.match(pattern) for pattern in patterns), name


def test_import_does_not_load_ble_inference_or_native_input():
    result = subprocess.run([sys.executable, "-c", """
import sys
import ring_mac_sdk
assert ring_mac_sdk.__version__ == '0.3.0'
assert not {'bleak', 'onnxruntime', 'pynput', 'Quartz', 'ApplicationServices'} & sys.modules.keys()
"""], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def status(state=1, *, epoch=4):
    raw = bytearray(64)
    raw[:4] = bytes((0x3C, 0x11, 2, state))
    struct.pack_into("<II", raw, 8, 42, epoch)
    struct.pack_into("<IHH4B", raw, 44, p.MODEL_ID, 200, 100, 8, 255, 1, 1)
    return bytes(raw)


def token(sequence=0, *, epoch=4, reset=True):
    return (bytes((0x3C, 2, 2, reset)) + struct.pack("<4I", 42, epoch, sequence, 100 + sequence * 10)
            + struct.pack("8b", 32, 64, -32, 16, -16, 32, -64, 0))


def test_v2_batch_quantization_epoch_reset_and_atomic_gap_rejection():
    tracker = p.Tracker(p.status(status()))
    batch = p.tokens(b"\x3c\x13\x02\x02" + token() + token(1, reset=False))
    assert batch[0].features == (1., 1., -1., .5, -.5, .5, -1., 0.)
    assert tracker.accept_all(batch) == [True, False]
    with pytest.raises(ProtocolError, match="sequence gap"):
        tracker.accept_all([p.token(token(2, reset=False)), p.token(token(4, reset=False))])
    assert tracker.sequence == 1  # A bad batch must not partly advance the model.
    assert tracker.accept(p.token(token(2, epoch=5))) is True
    with pytest.raises(ProtocolError, match="model ID"):
        p.status(status()[:44] + bytes(4) + status()[48:])
    with pytest.raises(ProtocolError, match="Reserved"):
        p.token(token()[:20] + b"\x80" + token()[21:])


class FakeClient:
    def __init__(self, *_args, disconnected_callback, **_kwargs):
        self.is_connected = False
        self.mtu_size = 247
        self.commands = []
        self.callback = None
        self.disconnected_callback = disconnected_callback
        self.services = SimpleNamespace(
            get_service=lambda _: object(),
            get_characteristic=lambda _: SimpleNamespace(properties=["write", "notify"]),
        )

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False
        self.disconnected_callback(self)

    async def start_notify(self, _uuid, callback):
        self.callback = callback

    async def write_gatt_char(self, _uuid, command, *, response):
        assert response is True
        self.commands.append(command)
        if command == p.INFO:
            self.callback(None, bytes.fromhex("2a 02 01 01 02 01 06 00 01 08 01 01 00 00"))
        elif command == p.BATTERY:
            self.callback(None, bytes.fromhex("29 02 00 00 55"))
        elif command == p.STOP:
            self.callback(None, status(0))
        elif command == p.START:
            self.callback(None, status())
            # The first token may arrive synchronously with the START ACK.
            self.callback(None, token())
        else:
            assert command in (b"\x26\x00", b"\x26\x01")


class FakeModel:
    def reset_for_epoch(self):
        pass

    def step(self, features):
        assert len(features) == 8
        return SimpleNamespace(reshape=lambda _: SimpleNamespace(tolist=lambda: [.1, .2, -8., -8.] * 2))


@pytest.mark.parametrize("gap", [False, True])
def test_mock_ring_lifecycle_and_stream_failure_cleanup(gap):
    async def run():
        events, states, gestures = [], [], []
        ring = Ring("offline", on_touchpad=events.append, on_touchpad_status=states.append,
                    on_gesture=gestures.append, _client_factory=FakeClient, _model_factory=FakeModel)
        async with ring:
            client = ring._transport.client
            assert ring.device_info.product == "Ringo"
            assert await ring.battery_level() == 85
            ack = await ring.gesture.set_mode(GestureMode.NORMAL)
            assert not ack.acknowledged  # Ringo's legacy START has no business ACK.
            client.callback(None, b"\x26\x07" + struct.pack("<HBIf", 1, 8, 1234, .75))
            assert gestures[0].class_id == 8
            await asyncio.wait_for(ring.touchpad.start(), 2)
            assert ring.touchpad.status.stage == "RUNNING"
            assert events and isinstance(events[0], Motion)
            assert events[0].meta.session_id == ring.touchpad.status.session_id
            if gap:
                client.callback(None, token(3, reset=False))
                await asyncio.wait_for(asyncio.shield(ring.touchpad.task), 2)
                assert ring.touchpad.status.stage == "FAILED"
                assert "sequence gap" in ring.touchpad.status.message
            else:
                await ring.touchpad.stop()
                assert ring.touchpad.status.stage == "STOPPED"
            count = len(events)
            client.callback(None, token(4, reset=False))
            assert len(events) == count
        assert ring.link_state == "DISCONNECTED"
        assert client.commands.count(p.START) == 1
        assert client.commands.count(p.STOP) == 2  # Initial handshake and final cleanup.
        assert not ring._transport.pending
    asyncio.run(run())


def test_two_finger_scroll_and_confirmed_click_dedup_without_native_output():
    calls = []
    output = SimpleNamespace(move=lambda *a: calls.append(("move", *a)),
                             scroll=lambda *a: calls.append(("scroll", *a)),
                             click=lambda *a: calls.append(("click", *a)))
    sink = ApplicationSink(output, CursorConfig(clicks=True))
    meta = EventMeta("offline", 0, 1, 1)
    sink.emit(Motion(meta, 1, 2., 3., "SINGLE", 1))
    sink.emit(Motion(meta, 2, 20., 4., "TWO_FINGER", 2))
    sink.emit(Click(meta, 10, "LEFT", 1, True))
    click = Click(meta, 10, "LEFT", 2, False)
    sink.emit(click)
    sink.emit(click)
    sink.emit(Click(meta, 11, "RIGHT", 1, False))
    assert calls == [("move", 2, 3), ("scroll", -4), ("click", "LEFT", 2), ("click", "RIGHT", 1)]


def test_real_receiver_load_reset_and_finite_output():
    pytest.importorskip("onnxruntime")
    import platform
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        pytest.skip("Provider native library requires macOS arm64")
    import numpy as np
    from ring_mac_sdk.model import TouchpadModel
    model = TouchpadModel()
    inputs = [p.token(token()).features, [0.] * 8]
    first = [model.step(features) for features in inputs]
    states = model.state_vector().copy()
    assert all(value.shape == (2, 4) and np.isfinite(value).all() for value in first)
    assert states.shape == (1216,) and np.isfinite(states).all()
    model.reset_for_epoch()
    second = [model.step(features) for features in inputs]
    for a, b in zip(first, second):
        np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(states, model.state_vector())


@pytest.mark.parametrize("run", [False, True])
def test_calibration_utility_is_read_only_unless_explicitly_requested(run):
    utility = runpy.run_path(str(DELIVERY / "tools/gyro_calibration.py"))

    async def check():
        operations, stopped = [], []

        async def stop():
            stopped.append(True)

        async def request(command, key, decode):
            operations.append(command)
            operation = int(command == b"\x21\x07")
            state = 1 if operation else 2 if b"\x21\x07" in operations else 0
            data = bytes((0x21, 8, operation, 0, state, 3 if state == 2 else 0, 1, 1))
            return decode(data + struct.pack("<HHhhh", 400, 2000, -1, 2, 3))

        ring = SimpleNamespace(
            device_info=SimpleNamespace(hardware_revision=1, firmware_version="2.1.6-dev.touchpad.9.debug"),
            touchpad=SimpleNamespace(_initial_stop=stop),
            _transport=SimpleNamespace(request=request),
        )
        with pytest.raises(ValueError, match="stationary"):
            await utility["calibrate"](ring, run=True, stationary=False)
        assert not stopped and not operations
        result = await utility["calibrate"](ring, run=run, stationary=run,
                                           timeout=1, interval=0, emit=lambda _: None)
        assert stopped == [True]
        assert operations == ([b"\x21\x06", b"\x21\x07", b"\x21\x06"] if run else [b"\x21\x06"])
        assert utility["completed"](result) is run
    asyncio.run(check())


def test_playground_demo_lifecycle_does_not_use_ble_or_system_input():
    from ring_mac_sdk.web_server import Controller

    async def check():
        async def scanner(_timeout):
            pytest.fail("Demo must not scan real Bluetooth devices")

        controller = Controller(demo=True, scanner=scanner)
        try:
            assert controller.ring is None
            await controller.execute({"action": "scan"})
            await controller.execute({"action": "connect", "identifier": "demo-ring"})
            await controller.execute({"action": "start"})
            await asyncio.sleep(0)  # Let the synthetic event task publish its first point.
            snapshot = controller.snapshot()
            assert snapshot["stage"] == "RUNNING" and snapshot["points"]
            assert snapshot["demo"] and not controller.cursor.enabled
            await controller.execute({"action": "cursor_enable"})
            assert "模拟模式" in controller.state["error"]["message"]
            assert not controller.cursor.enabled
        finally:
            await controller.release()
        assert controller.ring is None and controller.state["link"] == "DISCONNECTED"
    asyncio.run(check())
