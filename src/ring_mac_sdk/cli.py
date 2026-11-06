import argparse
import asyncio
from dataclasses import asdict
import json

from . import GestureMode, Ring, scan


def show(value):
    print(json.dumps(asdict(value) if hasattr(value, "__dataclass_fields__") else value, ensure_ascii=False), flush=True)


async def run(args):
    if args.command == "scan":
        for device in await scan(args.seconds):
            show(dict(identifier=device.identifier, name=device.name, rssi=device.rssi))
        return
    if args.command == "model-check":
        from .model import TouchpadModel, MODEL_SHA256
        model = await asyncio.to_thread(TouchpadModel)
        result = model.step([0.0]*8)
        show(dict(model_sha256=MODEL_SHA256, backend="CPUExecutionProvider", output=result.tolist(),
                  recurrent_state_floats=len(model.state_vector())))
        return
    ring = Ring(args.identifier, on_gesture=lambda e: show(dict(type="gesture", **asdict(e))),
                on_touchpad=lambda e: show(dict(type=type(e).__name__, **asdict(e))),
                on_touchpad_status=lambda s: show(dict(type="status", **asdict(s))))
    async with ring:
        show(ring.device_info)
        if args.command == "info":
            show(dict(battery=await ring.battery_level()))
        elif args.command == "gestures":
            show(await ring.gesture.set_mode(GestureMode.LOW_POWER if args.low_power else GestureMode.NORMAL))
            try:
                await asyncio.sleep(args.seconds)
            finally:
                if ring.link_state == "CONNECTED":
                    show(await ring.gesture.set_mode(GestureMode.OFF))
        elif args.command == "touchpad":
            await ring.touchpad.start()
            await asyncio.sleep(args.seconds)
            if ring.touchpad.status.stage == "FAILED":
                raise RuntimeError(ring.touchpad.status.message)
            await ring.touchpad.stop()


def main():
    parser = argparse.ArgumentParser(description="Ringo/R1 macOS SDK diagnostics; outputs JSON lines")
    sub = parser.add_subparsers(dest="command", required=True)
    scanning = sub.add_parser("scan", help="Discover Ringo/R1 candidates via NUS/SMP advertisements")
    scanning.add_argument("--seconds", type=float, default=5)
    sub.add_parser("model-check", help="Offline authenticated model load and inference")
    ui = sub.add_parser("ui", help="Local Chinese SDK playground; no automatic BLE connection")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--open", action="store_true", help="Open the local page in your browser")
    ui.add_argument("--demo", action="store_true", help="Simulated events only; never access Bluetooth")
    for name in ("info", "gestures", "touchpad"):
        command = sub.add_parser(name)
        command.add_argument("identifier", help="Full CoreBluetooth UUID returned by scan")
        if name != "info":
            command.add_argument("--seconds", type=float, default=30)
        if name == "gestures":
            command.add_argument("--low-power", action="store_true")
    args = parser.parse_args()
    if hasattr(args, "seconds") and args.seconds <= 0:
        parser.error("--seconds must be positive")
    try:
        if args.command == "ui":
            if not 1 <= args.port <= 65535:
                parser.error("--port must be 1..65535")
            from .web_server import serve
            serve(port=args.port, demo=args.demo, open_browser=args.open)
        else:
            asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        response = getattr(exc, "response", None)
        diagnostic = dict(type="error", reason=getattr(exc, "reason", "error"), message=str(exc),
                          device_code=getattr(exc, "device_code", None), phase=getattr(exc, "phase", None),
                          response=asdict(response) if hasattr(response, "__dataclass_fields__") else response)
        parser.exit(1, json.dumps(diagnostic, ensure_ascii=False)+"\n")


if __name__ == "__main__":
    main()
