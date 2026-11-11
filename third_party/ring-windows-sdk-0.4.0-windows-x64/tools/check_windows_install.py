"""Run with the installed SDK's Windows Python. Uses the model and simulated events."""
import asyncio
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import sysconfig
import time

from aiohttp import ClientSession


async def check_studio(env):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "ring_mac_sdk.cli",
                                "ui", "--demo", "--port", str(port)], env=env)
    try:
        async with asyncio.timeout(30), ClientSession() as client:
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Ring Studio exited with code {process.returncode}")
                try:
                    async with client.get(base) as page:
                        assert page.status == 200 and "Ring Studio" in await page.text()
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            async with client.ws_connect(base + "/ws", origin=base) as ws:
                async def until(predicate):
                    while True:
                        state = await ws.receive_json()
                        assert state["demo"] and not state["cursor"]["enabled"]
                        if predicate(state):
                            return state

                await ws.send_json({"action": "scan"})
                state = await until(lambda s: s["devices"])
                await ws.send_json({"action": "connect", "identifier": state["devices"][0]["identifier"]})
                await until(lambda s: s["link"] == "CONNECTED" and not s["busy"])
                await ws.send_json({"action": "start"})
                await until(lambda s: s["stage"] == "RUNNING" and len(s["points"]) >= 2)
                await ws.send_json({"action": "cursor_enable"})
                await until(lambda s: s["error"] and "模拟模式" in s["error"]["message"])
                await until(lambda s: s["click"] and s["click"]["button"] == "LEFT")
                await ws.send_json({"action": "disconnect"})
                await until(lambda s: s["link"] == "DISCONNECTED" and not s["has_ring"])
    finally:
        process.terminate()
        process.wait(timeout=10)


def main():
    from bleak.backends.client import get_platform_client_backend_type
    from bleak.backends.scanner import get_platform_scanner_backend_type
    from bleak.backends.winrt.client import BleakClientWinRT
    from bleak.backends.winrt.scanner import BleakScannerWinRT

    assert platform.system() == "Windows"
    assert get_platform_client_backend_type() is BleakClientWinRT
    assert get_platform_scanner_backend_type() is BleakScannerWinRT
    cli = Path(sysconfig.get_path("scripts")) / "ring-win.exe"
    assert cli.is_file(), f"Missing installed command {cli}"
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    result = subprocess.run([cli, "model-check"], env=env, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    model = json.loads(result.stdout)
    assert model["backend"] == "CPUExecutionProvider"
    assert model["recurrent_state_floats"] == 1216 and len(model["output"]) == 2
    started = time.monotonic()
    asyncio.run(check_studio(env))
    print(f"WinRT imports, CPU model, simulated Studio events and cursor guard passed ({time.monotonic() - started:.1f}s).")


if __name__ == "__main__":
    main()
