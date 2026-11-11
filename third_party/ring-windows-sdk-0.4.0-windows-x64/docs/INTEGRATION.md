# Python 接入、回调与诊断

```python
import asyncio
from ring_mac_sdk import Ring, Motion, Click

async def on_event(event):
    if isinstance(event, Motion):
        print(event.dx, event.dy, event.mode)
    elif isinstance(event, Click) and not event.is_preview:
        print(event.button, event.count)

async def on_diagnostics(value):
    print(value.severity, value.active_issues, value.counters)

async def main(identifier):
    async with Ring(identifier, on_touchpad=on_event,
                    on_touchpad_diagnostics=on_diagnostics) as ring:
        # 可设置宿主自己的等待预算，取消时 SDK 会尝试清理。
        await asyncio.wait_for(ring.touchpad.start(), timeout=30)
        await asyncio.sleep(30)
        await ring.touchpad.stop()
```

Windows wheel 保留原有 Python 导入名，已有回调无需改名。传给 Ring 的是 scan() 返回的 DiscoveredRing 对象或完整 Windows 蓝牙地址。完整 Python 示例见包内 `examples/touchpad.py`、`examples/gestures.py`。调用前先扫描选择目标；连接和控制 API 都在创建 Ring 的同一 asyncio loop 上使用。

## 并发与事件

模型推理在专用单线程 `RingModel` 执行器运行。同步触摸板回调在另一个专用单线程 `RingCallbacks` 执行器执行，二者不共享默认线程池。各自串行、有界排队，XY 应用回调不在模型推理调用栈中执行。它们仍属于同一进程，不构成进程级 CPU/GIL、内存或崩溃隔离。执行器由进程复用；设计用于一个活动戒指会话。

`async def` 触摸板回调在 Ring 的 owner loop 串行 await，必须快速返回或主动让出，不能执行同步阻塞操作。同步回调不要直接操作 asyncio 对象；向 UI/loop 投递时使用线程安全接口。手势与连接回调仍在 owner loop 同步调用。

运动、接触、点击按 meta.sequence 消费，保持 FIFO；实际应用前确认 session_id、generation 和 RUNNING 仍匹配。预告点击仅用于展示，正式 Click 才能产生动作。source_time_ns 与 Windows Python 的 time.perf_counter_ns() 同源，是主机接收时间域，不是手指动作开始时间。start 返回不保证应用回调已全部交付。

## 继续与停止

- 输入包和运动/点击候选处理年龄超过 200 ms：warning，继续处理；点击接触时长限制及双击配对规则不变。
- 64 包输入队列满：丢最旧保留最新，只对精确记录的本地缺口允许恢复，清模型/交互状态并增加 generation。
- 时间重复告警；超过 3000 ms 的设备时间跳变恢复。正常 uint32 回绕保留。
- 首 token 等待超过 3 秒、运行中静默超过 1 秒：warning，继续等待并异步查询固件。查询任务最多一个，启动间隔至少 3 秒。
- 未知序号缺口、非法 session/epoch、模型/协议不匹配、非有限输出仍失败。
- 模型单次调用超过 3 秒、模型与控制初始化超过 20 秒、600 帧后处理或 256 项业务回调队列容量耗尽仍停止。20 秒初始化预算不包括等待首 token 阶段。
- 固件确认正常停止为 STOPPED；故障为 FAILED。查询失败不猜测设备已停止。

停止会作废排队旧事件，不能撤回已进入应用的回调。未结束的模型原生调用或旧回调会阻止重新启动；若原生调用永久卡住，需要退出宿主释放资源。停止通常保留 BLE；底层未完成写入的异常仍遵循连接层清理规则。

## 诊断

通过 `on_touchpad_diagnostics` 接收通知，或读取 `ring.touchpad.diagnostics` 的独立快照。字段包括 session_id、generation、severity、issue、message、active_issues、recovered_issues、occurrence_counts、interval_counts、counters、device。

普通通知首次可立即交付，随后最少间隔 1 秒；多个原因合并到一个独立槽位，不占业务队列，实际交付才结算 interval_counts。停止额外交付最终快照。回调异常隔离，重复堆栈限频；诊断回调异常不产生自反馈通知。

计数包括 input_late_packets、input_dropped_packets、late_motion_frames、late_click_candidates、clock_anomalies、recoveries、callback_exceptions、max_input_age_ms；不同单位不要相加。恢复需要连续至少 1 秒的正常观测，观测间隔不超过 250 ms；仅无新告警不是恢复证据。device 为最近一次固件查询结果，不保证实时。
