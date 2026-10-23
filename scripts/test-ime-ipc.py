"""Exercise real Swift/Python transport in a private temporary socket, with no UI."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from proximic_ring.voice.ime.ime_bridge import IMEBridge


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pxime-ipc-", dir="/private/tmp") as directory:
        events = []
        ready = threading.Event()
        pong = threading.Event()
        compatibility = threading.Event()
        polish_requested = threading.Event()
        polish_settled = threading.Event()
        hotwords_learned = threading.Event()

        def receive(message):
            events.append(message)
            if message.get("type") == "state" and message.get("ready"):
                ready.set()
            if message.get("type") == "pong" and message.get("echo") == "hotwords_learned":
                hotwords_learned.set()
            elif message.get("type") == "pong":
                pong.set()
            if message.get("type") == "wechat_key" and message.get("request_id") == "key-test":
                compatibility.set()  # Transport only: never execute a key here.
            if message.get("type") == "polish_requested":
                polish_requested.set()
            if message.get("type") == "settled" and message.get("utterance_id") == "polish-test":
                polish_settled.set()

        bridge = IMEBridge(receive, path=Path(directory) / "bridge.sock")
        bridge.start()
        process = None
        try:
            env = dict(os.environ, PROXIMIC_IME_SOCKET=str(bridge.path), PROXIMIC_IME_HARNESS_SECONDS="12")
            process = subprocess.Popen([sys.argv[1]], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            assert ready.wait(8), "Swift did not establish an authenticated session"
            assert compatibility.wait(2), "Swift compatibility request was dropped by transport"
            result = bridge.asr_context(timeout=2)
            assert result["text"] == "中文😀\n上下文", result
            assert result["source"] == "input_method", result
            assert result["context_complete"], result
            assert bridge.send({"type": "polish", "client_id": "ipc-test", "utterance_id": "polish-test",
                                "restore_raw_on_undo": False})
            assert polish_requested.wait(2), "Swift polish request was dropped by transport"
            request = next(message for message in events if message.get("type") == "polish_requested")
            assert request["text"] == "本句中文😀" and request["revision"] == 7, request
            assert bridge.send({"type": "polish_result", "client_id": request["client_id"],
                                "utterance_id": request["utterance_id"], "revision": request["revision"],
                                "text": "润色后的本句😀", "error": ""})
            assert polish_settled.wait(2), "Swift did not receive the polish result"
            assert bridge.send({"type": "hotwords_learned", "client_id": "ipc-test", "utterance_id": "edit-test",
                                "seq": 10, "revision": 2, "words": ["张玮", "Kubernetes"]})
            assert hotwords_learned.wait(2), "Swift did not receive the hotword notice"
            assert bridge.send({"type": "reset"})
            assert pong.wait(2), "Swift did not receive the Python command"
            assert any(message.get("type") == "pong" and message.get("echo") == "中文😀\n上下文" for message in events)
            output, _ = process.communicate(timeout=3)
            assert process.returncode == 0, output
            print(output.strip())
            print("PASS actual Swift IPCClient ↔ Python IMEBridge: handshake, epoch, UTF-8 context, polish request/result, hotword notice, command")
        finally:
            bridge.close()
            if process is not None and process.poll() is None:
                process.terminate()
                process.communicate(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
