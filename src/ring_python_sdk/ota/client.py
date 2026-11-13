"""High-level, transport-independent Ringo OTA orchestration."""

from __future__ import annotations

import re
import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ring_python_sdk.ota.backend import SmpOtaBackend
from ring_python_sdk.ota.image import load_ota_image
from ring_python_sdk.ota.types import (
    OtaError,
    OtaImageState,
    OtaPhase,
    OtaProgress,
    OtaResult,
)


class _Backend(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def read_state(self) -> tuple[OtaImageState, ...]: ...
    def upload(self, image: bytes, slot: int): ...
    async def mark_test(self, image_hash: bytes) -> None: ...
    async def confirm(self, image_hash: bytes) -> None: ...
    async def reset(self) -> None: ...


def _new_backend(target_name: str, timeout_s: float) -> _Backend:
    return SmpOtaBackend(target_name, timeout_s)


def _now() -> float:
    return time.monotonic()


def _active_state(states: tuple[OtaImageState, ...]) -> OtaImageState | None:
    return next((state for state in states if state.active), None)


class OtaClient:
    def __init__(self, address: str, timeout_s: float = 8.0) -> None:
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", address):
            raise ValueError("OTA requires the connected device's exact Bluetooth address/UUID")
        self.address = address
        self.timeout_s = timeout_s

    async def _target_name(self) -> str:
        return self.address

    async def read_state(self) -> tuple[OtaImageState, ...]:
        target_name = await self._target_name()
        backend = _new_backend(target_name, self.timeout_s)
        try:
            await backend.connect()
            return await backend.read_state()
        except asyncio.CancelledError:
            raise
        except OtaError:
            raise
        except Exception as exc:
            raise OtaError(OtaPhase.CONNECTING, str(exc)) from exc
        finally:
            try:
                await backend.disconnect()
            except Exception:
                pass

    async def update(
        self,
        image_path: str | Path,
        *,
        on_progress: Callable[[OtaProgress], None] | None = None,
        verify_timeout_s: float = 60.0,
        expected_image_hash: str | None = None,
    ) -> OtaResult:
        started = _now()
        phase = OtaPhase.PREFLIGHT

        def emit(
            current: OtaPhase,
            message: str,
            sent: int = 0,
            total: int = 0,
        ) -> None:
            if on_progress is not None:
                on_progress(OtaProgress(current, sent, total, message))

        emit(phase, "validating OTA image")
        image = load_ota_image(image_path)
        if expected_image_hash is not None and image.hash_hex != expected_image_hash:
            raise OtaError(phase, "bundled image changed before upload; start the update again")
        target_name = await self._target_name()
        backend = _new_backend(target_name, self.timeout_s)
        previous_version = ""
        candidate_hash = image.hash_bytes
        try:
            phase = OtaPhase.CONNECTING
            emit(phase, f"connecting to {target_name}")
            await backend.connect()
            initial = await backend.read_state()
            active = _active_state(initial)
            if active is None or not active.confirmed or any(s.pending for s in initial):
                raise OtaError(phase, "device has no confirmed active image or an update is already pending")
            previous_version = active.version if active is not None else "unknown"

            if active.hash_hex.lower() == image.hash_hex.lower():
                result = OtaResult(active.version, active.version, image.hash_hex,
                                  len(image.data), _now() - started, initial, active)
                emit(OtaPhase.COMPLETE, "image already installed", len(image.data), len(image.data))
                return result

            phase = OtaPhase.UPLOADING
            emit(phase, "uploading image", 0, len(image.data))
            async for offset in backend.upload(image.data, slot=0):
                if not 0 <= offset <= len(image.data):
                    raise OtaError(phase, "invalid upload offset")
                emit(phase, "uploading image", offset, len(image.data))

            uploaded = await backend.read_state()
            candidate = next(
                (
                    state
                    for state in uploaded
                    if state.slot == 1 and state.bootable
                    and state.hash_hex.lower() == image.hash_hex.lower()
                ),
                None,
            )
            if candidate is None:
                raise OtaError(
                    OtaPhase.UPLOADING,
                    "slot 1 hash does not match the uploaded image",
                )
            candidate_hash = bytes.fromhex(candidate.hash_hex)

            phase = OtaPhase.MARKING
            emit(phase, "marking image for test-swap")
            await backend.mark_test(candidate_hash)

            phase = OtaPhase.REBOOTING
            emit(phase, "rebooting into candidate image")
            await backend.reset()
        except asyncio.CancelledError:
            raise
        except OtaError:
            raise
        except Exception as exc:
            raise OtaError(phase, str(exc)) from exc
        finally:
            try:
                await backend.disconnect()
            except Exception:
                pass

        phase = OtaPhase.VERIFYING
        emit(phase, "waiting for confirmed candidate")
        deadline = _now() + max(0.0, verify_timeout_s)
        last_states: tuple[OtaImageState, ...] = ()
        last_error: Exception | None = None
        while _now() <= deadline:
            verify_backend = _new_backend(target_name, self.timeout_s)
            try:
                phase = OtaPhase.RECONNECTING
                emit(phase, f"reconnecting to {target_name}")
                await verify_backend.connect()
                phase = OtaPhase.VERIFYING
                emit(phase, "verifying running image hash")
                last_states = await verify_backend.read_state()
                running_candidate = next((s for s in last_states if s.active and s.bootable
                    and not s.pending and s.hash_hex.lower() == candidate_hash.hex()), None)
                if running_candidate is not None and not running_candidate.confirmed:
                    # Test & Confirm: never confirm a candidate before it boots,
                    # or confirm an old/rolled-back image merely by version string.
                    await verify_backend.confirm(candidate_hash)
                    last_states = await verify_backend.read_state()
                final_state = next(
                    (
                        state
                        for state in last_states
                        if state.active and state.bootable
                        and state.hash_hex.lower() == candidate_hash.hex().lower()
                        and state.confirmed
                        and not state.pending
                    ),
                    None,
                )
                if final_state is not None:
                    elapsed = _now() - started
                    result = OtaResult(
                        previous_version=previous_version,
                        version=final_state.version,
                        image_hash_hex=final_state.hash_hex,
                        image_size=len(image.data),
                        elapsed_s=elapsed,
                        images=last_states,
                        final_state=final_state,
                    )
                    emit(OtaPhase.COMPLETE, "OTA complete", len(image.data), len(image.data))
                    return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
            finally:
                try:
                    await verify_backend.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(1.0)

        detail = "candidate did not become active and confirmed"
        if last_states:
            detail += f"; final states={last_states!r}"
        elif last_error is not None:
            detail += f"; last error={last_error}"
        raise OtaError(OtaPhase.VERIFYING, detail)


__all__ = ["OtaClient"]
