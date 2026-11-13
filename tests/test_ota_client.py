from __future__ import annotations

import asyncio
from hashlib import sha512
from pathlib import Path

import pytest

from ring_python_sdk.ota import OtaClient, OtaError, OtaImageState, OtaPhase
from test_ota_image import make_signed_image


ACTIVE_OLD = OtaImageState(
    slot=0,
    version="1.1.186",
    hash_hex="11" * 64,
    bootable=True,
    pending=False,
    confirmed=True,
    active=True,
    permanent=False,
)


class FakeBackend:
    def __init__(self, states: list[tuple[OtaImageState, ...]], events: list[str]):
        self.states = list(states)
        self.events = events

    async def connect(self) -> None:
        self.events.append("connect")

    async def disconnect(self) -> None:
        self.events.append("disconnect")

    async def read_state(self) -> tuple[OtaImageState, ...]:
        self.events.append("state")
        return self.states.pop(0)

    async def upload(self, image: bytes, slot: int):
        self.events.append(f"upload:{slot}")
        yield len(image) // 2
        yield len(image)

    async def mark_test(self, image_hash: bytes) -> None:
        self.events.append(f"mark:{image_hash.hex()}")

    async def reset(self) -> None:
        self.events.append("reset")

    async def confirm(self, image_hash: bytes) -> None:
        self.events.append(f"confirm:{image_hash.hex()}")


def test_update_uploads_test_swaps_and_verifies_confirmed_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "ringo.signed.bin"
    image_path.write_bytes(make_signed_image())
    candidate = OtaImageState(
        slot=1,
        version="1.2.3+4",
        hash_hex=sha512(make_signed_image()[:32 + len(b"ringo-test-image")]).hexdigest(),
        bootable=True,
        pending=False,
        confirmed=False,
        active=False,
        permanent=False,
    )
    active = OtaImageState(
        slot=0,
        version="1.2.3+4",
        hash_hex=candidate.hash_hex,
        bootable=True,
        pending=False,
        confirmed=True,
        active=True,
        permanent=False,
    )
    events: list[str] = []
    backends = [
        FakeBackend([(ACTIVE_OLD,), (ACTIVE_OLD, candidate)], events),
        FakeBackend([(active,)], events),
    ]
    monkeypatch.setattr(
        "ring_python_sdk.ota.client._new_backend",
        lambda _name, _timeout: backends.pop(0),
    )
    seen = []

    result = asyncio.run(
        OtaClient("12345678-1234-1234-1234-123456789ABC", 1).update(image_path, on_progress=seen.append)
    )

    assert result.previous_version == "1.1.186"
    assert result.version == "1.2.3+4"
    assert result.final_state == active
    assert result.image_size == len(image_path.read_bytes())
    assert [item.phase for item in seen][-1] is OtaPhase.COMPLETE
    uploading = [item for item in seen if item.phase is OtaPhase.UPLOADING]
    assert uploading[-1].bytes_sent == uploading[-1].bytes_total
    assert events == [
        "connect",
        "state",
        "upload:0",
        "state",
        f"mark:{bytes.fromhex(candidate.hash_hex).hex()}",
        "reset",
        "disconnect",
        "connect",
        "state",
        "disconnect",
    ]


def test_read_state_connects_and_always_disconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    backend = FakeBackend([(ACTIVE_OLD,)], events)
    monkeypatch.setattr(
        "ring_python_sdk.ota.client._new_backend",
        lambda _name, _timeout: backend,
    )
    assert asyncio.run(OtaClient("12345678-1234-1234-1234-123456789ABC", 1).read_state()) == (ACTIVE_OLD,)
    assert events == ["connect", "state", "disconnect"]


def test_update_rejects_slot_hash_mismatch_and_disconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "ringo.signed.bin"
    image_path.write_bytes(make_signed_image())
    wrong = OtaImageState(
        slot=1,
        version="1.2.3+4",
        hash_hex="ff" * 64,
        bootable=True,
        pending=False,
        confirmed=False,
        active=False,
        permanent=False,
    )
    events: list[str] = []
    backend = FakeBackend([(ACTIVE_OLD,), (ACTIVE_OLD, wrong)], events)
    monkeypatch.setattr(
        "ring_python_sdk.ota.client._new_backend",
        lambda _name, _timeout: backend,
    )

    with pytest.raises(OtaError, match="slot 1 hash"):
        asyncio.run(OtaClient("12345678-1234-1234-1234-123456789ABC", 1).update(image_path))
    assert events[-1] == "disconnect"
    assert "mark:" not in " ".join(events)


def test_update_preserves_cancellation_and_disconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CancelBackend(FakeBackend):
        async def upload(self, image: bytes, slot: int):
            self.events.append(f"upload:{slot}")
            raise asyncio.CancelledError
            yield len(image)  # pragma: no cover

    image_path = tmp_path / "ringo.signed.bin"
    image_path.write_bytes(make_signed_image())
    events: list[str] = []
    backend = CancelBackend([(ACTIVE_OLD,)], events)
    monkeypatch.setattr(
        "ring_python_sdk.ota.client._new_backend",
        lambda _name, _timeout: backend,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(OtaClient("12345678-1234-1234-1234-123456789ABC", 1).update(image_path))
    assert events[-1] == "disconnect"


def test_update_times_out_when_candidate_never_confirms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "ringo.signed.bin"
    image_path.write_bytes(make_signed_image())
    image_hash = sha512(make_signed_image()[:32 + len(b"ringo-test-image")]).hexdigest()
    candidate = OtaImageState(1, "1.2.3+4", image_hash, True, False, False, False, False)
    unconfirmed = OtaImageState(0, "1.2.3+4", image_hash, True, False, False, True, False)
    events: list[str] = []
    backends = [
        FakeBackend([(ACTIVE_OLD,), (ACTIVE_OLD, candidate)], events),
        FakeBackend([(unconfirmed,), (unconfirmed,)], events),
    ]
    monkeypatch.setattr(
        "ring_python_sdk.ota.client._new_backend",
        lambda _name, _timeout: backends.pop(0),
    )
    clock = iter((0.0, 0.0, 0.0, 1.0))
    monkeypatch.setattr("ring_python_sdk.ota.client._now", lambda: next(clock))

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("ring_python_sdk.ota.client.asyncio.sleep", no_sleep)
    with pytest.raises(OtaError, match="did not become active and confirmed"):
        asyncio.run(
            OtaClient("12345678-1234-1234-1234-123456789ABC", 1).update(image_path, verify_timeout_s=0.5)
        )


@pytest.mark.parametrize('target', ['Ringo', 'RingoDFB2', '', 'nearby-ring'])
def test_ota_rejects_ambiguous_name_targets(target):
    with pytest.raises(ValueError, match='exact Bluetooth'):
        OtaClient(target)


def test_already_installed_hash_does_not_upload_or_reset(tmp_path, monkeypatch):
    from dataclasses import replace
    from ring_python_sdk.ota.image import load_ota_image
    path = tmp_path / 'same.signed.bin'
    path.write_bytes(make_signed_image())
    image = load_ota_image(path)
    active = replace(ACTIVE_OLD, hash_hex=image.hash_hex, version=image.version)
    events = []
    monkeypatch.setattr('ring_python_sdk.ota.client._new_backend', lambda *args: FakeBackend([(active,)], events))
    result = asyncio.run(OtaClient('11:22:33:44:55:66').update(path))
    assert result.image_hash_hex == image.hash_hex
    assert events == ['connect', 'state', 'disconnect']


def test_pending_image_blocks_overwriting_previous_update(tmp_path, monkeypatch):
    from dataclasses import replace
    path = tmp_path / 'new.signed.bin'
    path.write_bytes(make_signed_image())
    events = []
    pending = replace(ACTIVE_OLD, slot=1, active=False, pending=True)
    monkeypatch.setattr('ring_python_sdk.ota.client._new_backend', lambda *args: FakeBackend([(ACTIVE_OLD, pending)], events))
    with pytest.raises(OtaError, match='already pending'):
        asyncio.run(OtaClient('11:22:33:44:55:66').update(path))
    assert events == ['connect', 'state', 'disconnect']


def test_confirm_only_after_matching_candidate_is_running(tmp_path, monkeypatch):
    from dataclasses import replace
    from ring_python_sdk.ota.image import load_ota_image
    path = tmp_path / 'new.signed.bin'
    path.write_bytes(make_signed_image())
    image = load_ota_image(path)
    candidate = replace(ACTIVE_OLD, slot=1, active=False, confirmed=False, hash_hex=image.hash_hex)
    running = replace(candidate, slot=0, active=True)
    confirmed = replace(running, confirmed=True)
    events, targets = [], []
    backends = [FakeBackend([(ACTIVE_OLD,), (ACTIVE_OLD, candidate)], events),
                FakeBackend([(running,), (confirmed,)], events)]
    def factory(target, timeout):
        targets.append(target)
        return backends.pop(0)
    monkeypatch.setattr('ring_python_sdk.ota.client._new_backend', factory)
    result = asyncio.run(OtaClient('11:22:33:44:55:66').update(path))
    assert result.final_state.confirmed
    assert targets == ['11:22:33:44:55:66'] * 2
    assert events.index('reset') < events.index(f'confirm:{image.hash_hex}')
    assert events[-2:] == ['state', 'disconnect']


def test_changed_bundle_rejected_before_bluetooth(tmp_path, monkeypatch):
    path = tmp_path / 'new.signed.bin'
    path.write_bytes(make_signed_image())
    monkeypatch.setattr('ring_python_sdk.ota.client._new_backend', lambda *args: pytest.fail('must not connect'))
    with pytest.raises(OtaError, match='changed before upload'):
        asyncio.run(OtaClient('11:22:33:44:55:66').update(path, expected_image_hash='ff' * 64))
