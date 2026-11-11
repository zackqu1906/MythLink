"""The delivered Ring SDK for this platform, behind one import path.

Windows uses ``ring_windows_sdk`` 0.4.0; other platforms use ``ring_mac_sdk``
0.4.0. The two differ only in their clock (perf_counter vs monotonic). Both packages stay unchanged provider files, so product code imports the
receiver, transport and protocol from here instead of naming a delivery.
"""
import sys
import time

if sys.platform == 'win32':
    from ring_windows_sdk import protocol
    from ring_windows_sdk.errors import RingError
    from ring_windows_sdk.gesture import GestureClient
    from ring_windows_sdk.interaction import Engine, delta
    from ring_windows_sdk.touchpad import Touchpad
    from ring_windows_sdk.transport import Transport
    from ring_windows_sdk.types import Click, ContactUpdate, EventMeta, Motion

    clock_ns = time.perf_counter_ns  # the SDK's event clock

    def host_seconds(source_ns):
        """Map an SDK source time onto the product's time.monotonic() clock.

        The Windows SDK stamps events with perf_counter, a different origin.
        """
        return time.monotonic() - (time.perf_counter_ns() - source_ns) / 1e9
else:
    from ring_mac_sdk import protocol
    from ring_mac_sdk.errors import RingError
    from ring_mac_sdk.gesture import GestureClient
    from ring_mac_sdk.interaction import Engine, delta
    from ring_mac_sdk.touchpad import Touchpad
    from ring_mac_sdk.transport import Transport
    from ring_mac_sdk.types import Click, ContactUpdate, EventMeta, Motion

    clock_ns = time.monotonic_ns

    def host_seconds(source_ns):
        return source_ns / 1e9

__all__ = ['Click', 'ContactUpdate', 'Engine', 'EventMeta', 'GestureClient', 'Motion', 'RingError',
           'Touchpad', 'Transport', 'clock_ns', 'delta', 'host_seconds', 'protocol']
