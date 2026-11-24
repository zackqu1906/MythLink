"""Listener resource failures must not disconnect an accepted TSF editor."""
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipes")


def test_listener_recovers_after_next_instance_allocation_fails(monkeypatch):
    from proximic_ring.voice.ime import windows_bridge as module
    bridge = module.WindowsIMEBridge(lambda _: None)
    bridge._listener = 100
    accepted, closed, attempts = [], [], []

    def create(first):
        attempts.append(first)
        if len(attempts) == 1:
            raise OSError(231, "All pipe instances are busy")
        return 101

    def spawn(target, name, pipe):
        assert name == "tsf-client"
        accepted.append(pipe)
        if len(accepted) == 2:
            bridge._closed = True

    monkeypatch.setattr(module, "_create_pipe", create)
    monkeypatch.setattr(module, "_Pipe", lambda handle: handle)
    monkeypatch.setattr(module._winapi, "ConnectNamedPipe", lambda *args, **kwargs:
                        SimpleNamespace(event=1, GetOverlappedResult=lambda wait: (0, 0)))
    monkeypatch.setattr(module._winapi, "WaitForSingleObject", lambda *args: 0)
    monkeypatch.setattr(module._winapi, "CloseHandle", closed.append)
    monkeypatch.setattr(bridge, "_spawn", spawn)
    monkeypatch.setattr(bridge, "close", lambda: pytest.fail("live bridge was closed"))
    bridge._accept_pipe()
    assert accepted == [100, 101] and attempts == [False, False]
    assert not closed and bridge._listener is None



def test_named_pipe_delivers_correction_and_windows_delete_replies():
    import threading
    from multiprocessing import Pipe

    from proximic_ring.voice.ime import windows_bridge as module

    received = []
    ready = threading.Event()

    def notify(message):
        received.append(message)
        if message.get("type") == "stroke_delete_result":
            ready.set()

    bridge = module.WindowsIMEBridge(notify)
    bridge._token = ""
    winapi = module._winapi
    process = winapi.GetCurrentProcess()
    server, peer = Pipe(duplex=True)
    with server:
        handle = winapi.DuplicateHandle(process, server.fileno(), process, 0, False, winapi.DUPLICATE_SAME_ACCESS)
    connection = module._Pipe(handle)
    thread = threading.Thread(target=bridge._session, args=(connection,), daemon=True)
    thread.start()

    def receive():
        assert peer.poll(3)
        payload = peer.recv_bytes().decode()
        return dict(field.split("=", 1) for field in payload.split("\t")[1:])

    def send(kind, **fields):
        parts = [kind]
        for key, value in fields.items():
            parts.append(f"{key}=s{value.encode().hex()}")
        peer.send_bytes("\t".join(parts).encode())

    try:
        peer.send_bytes(b"hello\tprotocol=n1")
        epoch = bytes.fromhex(receive()["epoch"][1:]).decode()
        target = dict(epoch=epoch, client_id="editor", stroke_session="stroke")
        send("stroke_correction_state", **{**target, "epoch": "stale"}, text="stale")
        send("stroke_correction_requested", **target, request_id="correct", original="去青华。", handwriting="清华")
        send("stroke_delete_result", **target, request_id="delete")
        assert ready.wait(3)
        events = [message for message in received if message["type"].startswith("stroke_")]
        assert [message["type"] for message in events] == ["stroke_correction_requested", "stroke_delete_result"]
        assert events[0]["handwriting"] == "清华" and events[1]["request_id"] == "delete"
        assert bridge.send(dict(type="stroke_correction_result", **target, request_id="correct", original_text="青华"))
        assert bytes.fromhex(receive()["original_text"][1:]).decode() == "青华"
    finally:
        peer.close()
        bridge.close()
        thread.join(3)
    assert not thread.is_alive()
