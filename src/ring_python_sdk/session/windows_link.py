"""Ask Windows for a throughput-optimized BLE link while a ring is connected.

Windows connects the ring at a 30 ms interval with slave latency 4. Raw2
streams 100 tokens/s; at that interval the firmware's send queue overflows,
it drops tokens and, after ~37 drops, stops the stream (the product then
reports "Raw2 数据断流超过 1 秒"). macOS already negotiates ~15 ms.
``BluetoothLEPreferredConnectionParameters.throughput_optimized`` (Windows 11)
moves the link to 15 ms / latency 0; measured on dev15: 6048 of 6048 tokens
sent in 60 s, 0 dropped. Windows keeps the preference only while the returned
request object is alive.
"""
import logging
import sys

log = logging.getLogger(__name__)

_STATUS = {0: 'unspecified', 1: 'success', 2: 'device_not_available', 3: 'access_denied'}


class LinkPreference:
    def __init__(self, request=None, status='unsupported'):
        self._request, self.status = request, status

    def release(self):
        request, self._request = self._request, None
        if request is not None:
            try:
                request.close()
            except Exception:
                pass


def request_throughput(client) -> LinkPreference:
    """``client`` is a connected Bleak client; never raises."""
    if sys.platform != 'win32' or client is None:
        return LinkPreference()
    try:
        from winrt.windows.devices.bluetooth import BluetoothLEPreferredConnectionParameters as Params
        device = client._backend._requester
        request = device.request_preferred_connection_parameters(Params.throughput_optimized)
        status = _STATUS.get(int(request.status), str(request.status))
    except Exception as exc:  # Windows 10, Bleak internals changed, or link already gone.
        log.warning('[SDK_CONNECTION] throughput link request unavailable: %r', exc)
        return LinkPreference()
    log.info('[SDK_CONNECTION] throughput link request status=%s', status)
    return LinkPreference(request, status)
