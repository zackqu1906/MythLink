# 连续两次 pinch 测试

当前项目内置的 `ring_python_sdk` 能接收固件确认的食指捏合（`index-pinch`，ID 8）
和中指捏合（`middle-pinch`，ID 9），但固件协议没有单独的 double-pinch 类别。
`DoublePinchDetector` 在这些单次事件上组合出双 pinch；固件必须先识别出两次动作。

## 运行

先在 Mythlink 主程序或其他 BLE 工具中断开戒指，然后在项目根目录运行：

```bash
bash scripts/test-double-pinch.sh
```

Windows 运行 `scripts\test-double-pinch.cmd`。默认扫描名称含 `Ringo` 的戒指，
有多枚戒指时用 `--selector 完整设备名或UUID` 指定；`--scan` 只扫描。
Ctrl+C 结束并保存 CSV 和统计摘要，终端会显示输出路径。

做「食指捏合 → 松开 → 再捏合」，两次动作间隔默认 **120–600ms**，每组后停顿约一秒。
终端先打印每个固件 `TRIGGER`，识别成功时显示：

```text
>>> DOUBLE PINCH #0001：连续两次食指捏合，动作间隔=300ms (seq 1 → 2)
```

调整最大间隔或测试中指：

```bash
bash scripts/test-double-pinch.sh --max-interval-ms 800
bash scripts/test-double-pinch.sh --pinch-name middle-pinch
```

旧版七类模型只提供 `tap` / click-pinch，需显式选择 `--pinch-name tap`；它不区分手指。
没有固件手势模型的戒指不能通过这个测试补上模型。

不连接硬件，验证协议与组合判断：

```bash
bash scripts/test-double-pinch.sh --demo
```

默认参数下演示应识别两组，包含重复包、过快、超时、其他手势打断和三连 pinch。
演示使用合成数据，不代表硬件识别率。

## 判断规则和记录

- 只使用确认的 `TRIGGER`，忽略连续分类 `EVENT`；两次必须同类，中间其他手势会取消配对。
- V2 使用动作中心时间，旧协议用设备 uptime，避免蓝牙批量到达改变间隔；支持时钟和包序回绕。
- 重复或乱序包不会计数；触发包序缺口会取消配对。断线后需重新开始。
- 小于 120ms 的重复触发忽略；超过 600ms 时，当前 pinch 作为新一组的第一次。
- 一次动作只用一次：三连 pinch 得到一组，四连得到两组。
- CSV 保留所有原始事件，成功配对的第二行记录 `double_pinch_index`、
  `double_pinch_interval_ms` 和 `double_pinch_first_seq`，JSON 摘要记录组数及阈值。

如果实际做了两次，但只出现一条 `index-pinch` TRIGGER，说明上游没有提供两次可配对事件；
若出现两条却没配对，检查类别、动作中心时间间隔、包序是否连续。
建议先做 10 组双 pinch，再做 10 次单 pinch 对照，并保留日志以便调节阈值。

## SDK 中复用

```python
from ring_python_sdk.swipe import DoublePinchDetector

detector = DoublePinchDetector(pinch_name="index-pinch", max_interval_ms=600)

def on_trigger(event):
    double = detector.feed(event)
    if double is not None:
        print(double.name, double.interval_ms)

# session 已连接；传入所有手势的 trigger，才能处理其他手势打断。
await session.swipe_on(on_trigger=on_trigger)
# 断线或开始新一轮采集时调用 detector.reset()。
```
