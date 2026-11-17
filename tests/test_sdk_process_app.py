"""Application integration uses the same public SDK as an external consumer."""
from types import SimpleNamespace
import time

from process_sdk_fakes import Client, Model


def test_application_source_uses_isolated_sdk_without_system_input(monkeypatch, tmp_path):
    from proximic_ring.audio.ring import RingAudioSource
    from ring_python_sdk import RingSession
    from ring_python_sdk.process import ProcessTransport

    original = ProcessTransport.__init__
    def transport(self, session, target, **kwargs):
        session._process_model_factory = Model
        original(self, session, target, client_factory=Client)
    async def sync_time(*args, **kwargs):
        pass
    monkeypatch.setattr(ProcessTransport, '__init__', transport)
    monkeypatch.setattr(RingSession, 'sync_time', sync_time)
    source = RingAudioSource(device=SimpleNamespace(name='Ringo Test', address='offline'),
                             data_root=tmp_path)
    frames = []
    runtime = None
    try:
        source.connect()
        session = source._touchpad_endpoint[1]
        assert session.connection_backend == 'process'
        runtime = session._sdk_transport.runtime
        source.start_touchpad(protocol='raw2', on_event=frames.append, duration_s=None).result(5)
        deadline = time.monotonic()+3
        while not frames and time.monotonic() < deadline:
            time.sleep(.01)
        assert frames
        source.stop_touchpad().result(5)
        assert session.client.is_connected
    finally:
        source.close()
    assert runtime is not None
    assert not runtime.io_process.is_alive() and not runtime.touch_process.is_alive()
