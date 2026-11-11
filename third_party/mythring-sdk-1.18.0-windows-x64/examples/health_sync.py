"""python examples/health_sync.py <address-or-macOS-UUID> --account <账户标识> --output summary.json

下载并分析健康数据，保存可恢复的本地记录，导出包含页面摘要的 JSON。
"""
import argparse
import asyncio
from pathlib import Path

from mythring import Ring


async def main(args):
    last = None

    def progress(update):
        nonlocal last
        current = (update.stage, update.recording_id)
        if current != last:
            print("同步", update.stage, "记录", update.recording_id)
            last = current

    async with Ring(args.address, health_storage_path=args.storage) as ring:
        ring.on("error", lambda error: print("错误:", error))
        result = await ring.health.sync_automatic(
            account_scope=args.account, time_zone=args.time_zone, on_update=progress,
        )
        # 导出保留缺失值、错误和质量字段，不把空值改成 0。
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(result.to_json())
        print("状态", result.status, "错误", result.errors)
        print("已保存", args.output.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", help="本机扫描返回的蓝牙地址或 macOS UUID")
    parser.add_argument("--account", required=True, help="稳定的业务账户标识")
    parser.add_argument("--output", type=Path, required=True, help="新 JSON 文件；已有文件不覆盖")
    parser.add_argument("--storage", type=Path, help="健康数据存储根目录；默认 ~/.local/share/mythring/health")
    parser.add_argument("--time-zone", default="Asia/Shanghai", help="IANA 时区，默认 Asia/Shanghai")
    options = parser.parse_args()
    if options.output.exists():
        parser.error("--output 已存在，请使用新的文件名")
    try:
        asyncio.run(main(options))
    except KeyboardInterrupt:
        pass
