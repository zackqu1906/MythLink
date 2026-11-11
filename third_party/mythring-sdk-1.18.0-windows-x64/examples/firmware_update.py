"""python examples/firmware_update.py <address-or-macOS-UUID> <firmware.zip> [--verify-only]

默认执行实际固件上传、测试启动与结果核验；--verify-only 仅核验已安装固件。
"""
import argparse
import asyncio
from pathlib import Path

from mythring import Ring, RingOtaUpdater
from mythring.ota import FirmwarePackage


async def main(args):
    package = FirmwarePackage.from_file(args.firmware)
    print("镜像", package.version, "运行版本", package.runtime_version)
    last_percent = -1

    def progress(sent, total):
        nonlocal last_percent
        percent = sent * 100 // max(total, 1)
        if percent != last_percent:
            print(f"上传 {percent}% ({sent}/{total})")
            last_percent = percent

    async with Ring(args.address) as ring:
        updater = RingOtaUpdater(ring, on_state=print, on_log=print, on_progress=progress)
        try:
            if args.verify_only:
                result = await updater.verify_installed(args.firmware)
            else:
                result = await updater.update(args.firmware, confirm=args.confirm)
            print("核验成功", result.runtime_version, result.image_version)
        finally:
            updater.cancel()
    print("维护连接已关闭；使用其他功能前请正常重连。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", help="本机扫描返回的蓝牙地址或 macOS UUID")
    parser.add_argument("firmware", type=Path, help="R1 MCUboot .bin 或 .zip 固件包")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--verify-only", action="store_true", help="只读核验，不上传/重启/确认")
    modes.add_argument("--confirm", action="store_true", help="新镜像身份/版本/摘要核验通过后显式确认")
    options = parser.parse_args()
    if not options.firmware.is_file():
        parser.error("固件文件不存在")
    try:
        asyncio.run(main(options))
    except KeyboardInterrupt:
        print("已取消。若设备已接受上传或重启，请先用 --verify-only 核验结果。")
