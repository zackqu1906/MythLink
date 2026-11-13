import asyncio

import pytest
from smp.image_management import ImageState, ImageStatesReadResponse, ImageStatesWriteResponse
from smpclient.requests.image_management import ImageStatesRead, ImageStatesWrite
from smpclient.transport import SMPTransportDisconnected

from ring_python_sdk.ota.backend import SmpOtaBackend


def test_reads_real_smp_models_and_confirms_exact_hash():
    async def run():
        state = ImageState(slot=0, image=0, version='2.1.6', hash=b'x' * 64,
                           bootable=True, active=True, confirmed=False)
        requests = []
        class Client:
            async def request(self, request, timeout_s):
                requests.append(request)
                if isinstance(request, ImageStatesRead):
                    return ImageStatesReadResponse(images=[state], sequence=0)
                return ImageStatesWriteResponse(images=[state], sequence=0)
        backend = SmpOtaBackend('11:22:33:44:55:66', 8)
        backend._client = Client()
        states = await backend.read_state()
        assert states[0].hash_hex == (b'x' * 64).hex()
        assert states[0].active and states[0].bootable and not states[0].pending
        await backend.mark_test(b'x' * 64)
        await backend.confirm(b'x' * 64)
        assert all(isinstance(r, ImageStatesWrite) for r in requests[1:])
        assert requests[1].confirm is False and requests[2].confirm is True
        assert requests[2].hash == b'x' * 64
    asyncio.run(run())


def test_reset_disconnect_is_expected_but_other_errors_propagate():
    async def run():
        class Client:
            error = SMPTransportDisconnected('reboot')
            async def request(self, request, timeout_s): raise self.error
        client = Client()
        backend = SmpOtaBackend('11:22:33:44:55:66', 8)
        backend._client = client
        await backend.reset()
        client.error = TimeoutError('no response')
        with pytest.raises(TimeoutError): await backend.reset()
    asyncio.run(run())


def test_cleanup_after_partial_connection_failure():
    async def run():
        events = []
        class Client:
            async def connect(self, timeout):
                events.append('connected')
                raise RuntimeError('SMP characteristic missing')
            async def disconnect(self): events.append('disconnected')
        backend = SmpOtaBackend('11:22:33:44:55:66', 8)
        backend._client = Client()
        with pytest.raises(RuntimeError): await backend.connect()
        await backend.disconnect()
        await backend.disconnect()
        assert events == ['connected', 'disconnected']
    asyncio.run(run())
