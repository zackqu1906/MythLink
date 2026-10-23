# 双击 tap 独立测试

主项目默认使用 `double-tap` 开始语音、单次 `tap` 结束语音；两者可在手势设置中分别配置。
此工具只接收固件手势、打印和保存记录，不开启语音。

## 启动

先在主程序或其他蓝牙工具中断开这枚戒指，然后在项目目录运行：

```bash
bash scripts/test-double-tap.sh
```

默认连接名称包含 `8F56` 的戒指，找不到则报错，不回退到其他 Ringo。
Windows 使用 `scripts\test-double-tap.cmd`。按 `Ctrl+C` 停止并保存摘要。
其他设备可通过 `--name 名称片段` 或 `--selector MAC或蓝牙UUID` 指定。

## 如何测试

- 做 10 组双击：轻点、松开、再轻点，每组后停顿约 1 秒。
- 每组应出现一次 `>>> DOUBLE TAP #0001`，同时显示两次动作的实际间隔。
- 再做 10 次单击，每次间隔超过 1 秒，应只有原始 `TRIGGER tap`，不增加双击计数。
- 三连击只计一组；双击间插入其他手势时取消配对。

默认间隔为 **100–200 毫秒（含边界）**。小于最小间隔的重复不算第二击，
超过最大间隔则重新从第一击开始；重复包、乱序包及分类 EVENT 不会组成双击。
序号缺口、时钟或协议变化会取消待配对动作。每次重新启动测试使用全新状态。
V2 优先使用动作中心时间，其他包使用设备 uptime，避免蓝牙接收抖动影响配对。
固件必须先识别出两次 tap；如果只收到一次 TRIGGER，配对器无法补出第二次。

放宽间隔或运行离线演示：

```bash
bash scripts/test-double-tap.sh --max-interval-ms 800
bash scripts/test-double-tap.sh --demo
bash scripts/test-double-tap.sh --dry-run
```

记录默认保存到 `data/double_tap/<时间戳>/`：

- `events.csv`：原始手势，第二击所在行带 `double_tap_index`、`double_tap_interval_ms` 和 `double_tap_first_seq`。
- `events.summary.json`：设备信息、tap 数量、双击数量及本次阈值。
- SDK 原始 CSV：用于区分固件漏识别、重复包和间隔不匹配。

`--demo` 使用合成协议包，不代表实机识别效果；默认阈值下输出两组双击。
主项目在第一击后等待 250ms 才发单击结束事件，给 200ms 的配对窗口预留 50ms 蓝牙传输余量；
有效第二击立即发双击，且两次 tap 都不会发成单击。若蓝牙通知延迟超过余量，
软件无法同时保证低单击延迟和不误发单击，可按实测再调等待时间。

主项目集成入口为 `ring_python_sdk.swipe.DoubleTapDetector`，向 `feed()` 传入同一枚戒指
的全部 `SwipeResult`；成功时返回名称为 `double-tap` 的事件，断开连接时调用 `reset()`。
