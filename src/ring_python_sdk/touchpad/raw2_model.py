"""Select the provider Raw2 model for the current platform.

macOS arm64 keeps the unchanged ``ring_mac_sdk`` native loader. Windows x64
uses the native loader delivered in ``ring_windows_sdk`` 0.4.0. Each decrypts
the same ``ring_runtime.bin`` in memory; plaintext is never written to disk.
"""
from __future__ import annotations

import functools
import importlib.util
import os
import platform
import sys
import threading

_WINDOWS_MODULES = ('onnxruntime',)


def _windows_x64():
    return sys.platform == 'win32' and platform.machine().lower() in {'amd64', 'x86_64'}


def _mac_arm64():
    return sys.platform == 'darwin' and platform.machine() == 'arm64'


def windows_raw2_enabled():
    """Windows x64 with the loader dependencies installed.

    Set PROXIMIC_WINDOWS_RAW2=0 to keep the legacy connection and MNN touchpad.
    """
    if not _windows_x64() or os.environ.get('PROXIMIC_WINDOWS_RAW2', '1').strip() == '0':
        return False
    return _windows_modules_available()


@functools.lru_cache(maxsize=1)
def _windows_modules_available():
    return all(importlib.util.find_spec(name) is not None for name in _WINDOWS_MODULES)


def raw2_firmware(info) -> bool:
    """Whether connected INFO is a Ringo touchpad build that streams Raw2.

    Matches the provider's ``ringo_touchpad_v2`` profile (dev9, dev15, ...):
    hardware 1 with a ``2.x.y-dev.touchpad.N`` version. Public 1.x/2.x
    builds such as 1.2.133.public or 2.0.2.public_dev do not.
    """
    version = str(getattr(info, 'firmware_version', '') or '')
    return getattr(info, 'hardware_revision', None) == 1 and '-dev.touchpad.' in version


def raw2_platform_supported():
    """Whether this host has a provider-validated Raw2 model loader."""
    return _mac_arm64() or windows_raw2_enabled()


_timer_lock = threading.Lock()
_timer_holders = 0


def hold_windows_timer():
    """Raise the Windows timer resolution to 1 ms while a Raw2 stream runs.

    The provider receiver ticks its interaction engine every 5 ms with
    asyncio.sleep(); at the default 15.6 ms resolution each tick takes
    ~15.5 ms, delaying click/contact timing and letting input queue up.
    timeBeginPeriod(1) gives ~6.5 ms. Returns an idempotent release callable;
    a no-op outside Windows or if winmm is unavailable.
    """
    global _timer_holders
    if sys.platform != 'win32':
        return lambda: None
    try:
        import ctypes
        winmm = ctypes.WinDLL('winmm')
    except OSError:
        return lambda: None
    with _timer_lock:
        if winmm.timeBeginPeriod(1) != 0:
            return lambda: None
        _timer_holders += 1
    released = False

    def release():
        nonlocal released
        global _timer_holders
        with _timer_lock:
            if released:
                return
            released = True
            _timer_holders -= 1
            winmm.timeEndPeriod(1)
    return release


def default_model_factory():
    """Return a zero-argument model constructor, or None for the SDK default."""
    if windows_raw2_enabled():
        from ring_windows_sdk.model import TouchpadModel
        return TouchpadModel
    return None  # ring_mac_sdk.touchpad uses its own TouchpadModel on macOS.
