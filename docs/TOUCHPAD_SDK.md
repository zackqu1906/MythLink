# 触摸板 SDK 开发接口（macOS / Windows）

修正后的 Android AAR 触摸板适配已集成至 `src/ring_python_sdk/touchpad`，由现有 `RingSession` 收取 token 并运行原模型。模型无修改，不进行静止校准、偏置补偿或静止冻结；原 AAR 的预热、预测平均、增益映射和点击判定保留。电脑端触摸板模型与固件手势识别是两个独立功能。

## 安装与入口

支持已验证的 Apple Silicon Mac / Python 3.11 / MNN 3.6.1。Windows x64 / Python 3.11 已通过真实模型推理、重置和虚拟 BLE token 流测试；尚未进行 Windows 戒指实机验收。主项目兼容 NumPy 1.26.4。MNN 按需导入，未启用触摸板时不会加载模型，也不会影响 ADPCM 和固件手势。

```sh
python -m pip install -c requirements-macos.lock -e '.[ring,touchpad]'
python examples/ring_touchpad.py --device 8F56 --seconds 90
# 明确启用系统鼠标移动、左键单击；需辅助功能权限：
python examples/ring_touchpad.py --device 8F56 --seconds 90 --mouse
```

Windows PowerShell（在项目根目录）：

```powershell
python -m pip install -e ".[ring,touchpad]"
python examples/ring_touchpad.py --device "戒指名称或蓝牙地址" --seconds 90
```

Windows 示例只打印移动与点击事件，`--mouse` 目前仅支持 macOS；它不会控制 Windows 光标。Windows 的 `touchpad` extra 会安装 MNN 3.6.1，需使用 Python 3.11 x64。没有戒指时可安装 `.[ring,touchpad,dev]`，再运行 `python -m pytest tests/test_touchpad_backbone.py tests/test_touchpad_sdk.py` 做模型和虚拟 BLE 测试。

`--device` 支持名称、扫描序号或 Mac BLE UUID；多个同名设备时请用 UUID。接到已有应用时复用其 `RingSession`，不要再创建第二个连接。例子默认打印移动／点击，只有 `--mouse` 才发送系统鼠标事件。模型和 MNN notice 已纳入 wheel / macOS 应用打包，不依赖 Downloads 或实验目录。

## RingSession：启动、订阅、停止

```python
from ring_python_sdk import RingSession
from ring_python_sdk.touchpad import TouchpadMove, TouchpadClick

async def use_existing_session(session: RingSession):
    def on_event(event):
        if isinstance(event, TouchpadMove):
            print(event.dx, event.dy, event.contact_probability)
        elif isinstance(event, TouchpadClick):
            print('left click')

    await session.touchpad_on(
        on_event=on_event,
        on_stats=lambda stats: print(stats.tokens, stats.warmup_frames),
        on_stopped=lambda error: print('stopped', error),
        duration_s=90,
    )
    # 应用继续运行自己的事件循环，需要停止时：
    # await session.touchpad_off()
```

必须在 session 所属 asyncio/BLE 事件循环调用。`touchpad_on` 在模型准备好、START 写出后返回；此时仍需约 200 帧（1 秒）原算法预热。可查询 `session.touchpad_active`、`session.touchpad_error` 和 `session.touchpad.stats`（停止后 touchpad 为 None）。

| 参数 | 语义 |
| --- | --- |
| `on_event` | 必填，同步回调，接收 `TouchpadMove` 或 `TouchpadClick` |
| `on_stats` | 可选，约每 0.5 秒回调一次不可变 `TouchpadStats` |
| `on_stopped` | 可选，停止后回调一次；正常停止/超时为 None，异常为 Exception |
| `duration_s=90` | 从 START 成功后开始计时，只停止触摸板，不关闭应用/蓝牙/语音；传 None 可显式长期运行 |
| `model_path=None` | 默认使用随 SDK 附带、校验哈希的原模型；自定义路径仍需满足相同输入输出约定 |

SDK 的 90 秒是**触摸板流时长**。下载文件夹中的独立测试窗口仍是**窗口启动后 90 秒退出**，两者生命周期不同。

所有回调在 BLE 事件循环执行，需快速返回；GUI 更新通过 Qt Signal 或自己的消息队列转交。推理在独立单线程 executor 执行。不要在回调里同步等待同一事件循环的 coroutine。事件／统计回调抛错会停止触摸板并通过 `on_stopped` 报告；`on_stopped` 本身抛错只记录日志。

## 事件约定

- `TouchpadMove(dx, dy, contact_probability, step, timestamp)`：`dx/dy` 是原 AAR 后处理后的相对位移，默认 Mac 适配器以屏幕坐标单位应用；正 x 向右、正 y 向下。`contact_probability` 是最新已确认概率，仅作诊断，不判断笔画起落。
- `TouchpadContact(state, step, timestamp, confirmed_step)`：原 ClickDetector 确认的 `down`／`up` 帧边界，或数据失效时的 `reset`。`step` 是原检测给出的实际边界，`confirmed_step` 是确认时的模型帧。笔画采集使用这一结果，独立于短时点击的时长／距离限制。
- `TouchpadClick(step, timestamp, button='left')`：已经通过原算法短时、小位移条件的左键单击。
- `timestamp`：主机 `time.monotonic()` 秒数，表示事件产生时刻，不是墙钟或固件 uptime。`step` 是本次连续推理段的索引，断流重置后从头计数。
- `TouchpadStats`：包数、token 数、预热帧数、重置次数、无效/重复/过期包数、队列溢出次数、接触概率和最近有效数据年龄。

## 系统鼠标适配器（明确启用）

```python
from ring_python_sdk.touchpad.macos import MacSystemMouse

mouse = MacSystemMouse()       # 不发送任何鼠标事件
if not mouse.permitted():
    mouse.request_permission()  # 只在用户明确选择授权时调用
mouse.enable()
mouse.gain = 1.0
mouse.invert_y = False
mouse.clicks_enabled = True
await session.touchpad_on(
    on_event=mouse.handle,
    on_stopped=lambda error: mouse.disable(),
    duration_s=90,
)
# 主动停止：先 mouse.disable()，再 await session.touchpad_off()
```

构造、import 或 `touchpad_on` 本身不会移动系统鼠标。`MacSystemMouse` 支持移动与左键单击、多屏边界、权限复查、Esc 检查和过期事件丢弃。它不创建光标窗口，也不支持拖动、右键或滚轮。嵌入 GUI 时按独立测试窗口的方式每 25 ms 调用 `escape_pressed()`，即可在无数据时也立即响应 Esc；CLI 例子可用 Ctrl+C 停止。

## 接入主项目 RingAudioSource

`RingAudioSource` 增加了可选构造参数，默认 None 不启动触摸板：

```python
from proximic_ring.audio.ring import RingAudioSource

source = RingAudioSource(
    selector='你的 Mac BLE UUID',
    encoding='adpcm',
    gesture_observer=on_firmware_gesture,
    touchpad_observer=on_touchpad_event,
    touchpad_state_observer=on_touchpad_running,  # bool
    touchpad_duration_s=90,
)
```

它复用主程序已有连接，关闭/断线时自动清理。`source.touchpad_active` 和 `source.touchpad_error` 可供 UI 展示。接收事件不代表自动发送系统鼠标动作，产品层可以选择将事件传给系统鼠标适配器或其他应用逻辑。桌面导航栏的“触摸板”页面可动态开启/关闭系统鼠标控制，默认 90 秒自动停止。开启期间主程序暂停 Ring 麦克风和固件手势，关闭后恢复；语音识别运行中需先暂停才能开启触摸板。设置包含指针速度、轻触点击、反转上下方向和自动停止时长。Esc、断线或锁屏会停止控制，重连后需手动再次开启。

## 互斥与清理

- 触摸板 token、原始 IMU、四元数共用 `21 00` / `21 01` 数据流，三个模式互斥，冲突时抛 RuntimeError；先关闭已有模式再切换。
- SDK 允许 MIC、固件手势和触摸板共用连接；Windows 实机测试发现并发时触摸板缺包显著增多，因此主程序的触摸板模式会暂停 MIC 与固件手势，关闭后恢复。
- 队列最多 64 包，溢出清空并重置推理；真实序号缺口、无效包会重新预热。Windows 短暂通知停顿若序号连续则保留模型状态；空闲超过 2 秒才主动重置，超过 8 秒无有效 token 则停止并报告 TimeoutError。
- STOP、disconnect、stop_all 都会关闭触摸板输出；START 失败或被取消会尝试 STOP，STOP 写入失败仍释放模型和线程。断线不会自动恢复触摸板。

## 离线模型／录制数据接口

笔画消费者还接收 `TouchpadClickVerdict(start_step, step, is_click, timestamp)`：它观察原检测器对该接触的最终判定，包含“是点击”和“不是点击”，不改变原点击门槛或系统鼠标事件。
笔画提交必须等待对应结束帧的明确判定；后续移动帧数或短暂超时不代表点击判定已经交付。缺失判定的接触会丢弃，不会自动写入笔画。

```python
from ring_python_sdk.touchpad import TouchpadBackbone, TouchpadProcessor

model = TouchpadBackbone()
predictions = model.step([0., 0., 0., 0., 0., 0.])  # shape=(5,3)
model.reset()

processor = TouchpadProcessor()
processor.feed(packet_bytes, arrival=received_monotonic, now=now_monotonic)
events = processor.poll(now_monotonic)  # 定时调用；Windows 约 15 ms 唤醒时会批量排出位移
```

同一个 model/processor 只在单一线程使用。`feed` 输入完整 `21 05` 通知包；`poll` 不连接蓝牙或发送鼠标事件。原生模型输出三列为 vx、vy、接触 logit，SDK 负责 sigmoid 和后处理。

## 适配依据与验证

模型来源与 SHA-256：`src/ring_python_sdk/touchpad/assets/provenance.json`。MNN runtime 许可证不涵盖供应商模型，发布权限沿用主项目 `THIRD_PARTY_NOTICES.md` 的说明。

必须保持“推理前上传全部状态；推理后缓存全部状态”的顺序，不能边读输出边回写输入。详见 `docs/TOUCHPAD_ADAPTATION_AUDIT.md`；原数据 27,790 帧回放已验证修复效果，无校准或静止冻结。

测试入口：`tests/test_touchpad_sdk.py`（会话、互斥、线程、清理、超时、路由、主项目适配及真实 MNN 虚拟 BLE 流），`tests/test_touchpad_backbone.py`（真实 MNN 状态覆盖回归）。Windows 实机已验证主程序在触摸板开启时暂停 MIC 与手势、关闭后恢复；真实鼠标手感仍需界面复测。尚未宣称 Android 设备逐帧输出完全一致。

## 桌面动态控制

`RingAudioSource.start_touchpad(**options)` 和 `stop_touchpad()` 从 GUI / 其他线程调用，立即返回 `concurrent.futures.Future`，通过现有 BLE 事件循环执行 SDK 操作。不要在 GUI 线程调用 `.result()` 等待。主程序会在开启前暂停 Ring 麦克风及固件手势，在停止后恢复，保持同一 BLE 连接。

`ui/touchpad_controller.py` 管理页面状态和连接代次；`touchpad_mouse.py` 将高频事件放入最多 32 项的队列，在专用线程发送系统鼠标事件。连续位移合并但不越过点击，超过 100 ms 的旧事件丢弃；停止立即关闭本地输出门。推理仍由 SDK 的单线程 executor 执行，BLE 回调只入队，GUI 仅接收低频统计和状态变化。

同一页面可手动切换「光标控制 / 笔画输入」。切换只替换输出端，沿用已开启的触摸板 token 流，不重新开启 Ring 麦克风。光标模式保持固件手势通道关闭；笔画模式单独启用固件手势触发事件，右滑选下一个、左滑选上一个、上滑退一笔、下滑清空。固件手势直接送入笔画控制器，不经过语音或应用手势映射；切回光标模式再次关闭通道，结束触摸板后恢复原有映射。笔画模式在工作线程按模型接触概率分笔，仅把完成的轨迹交给 UI；UI 使用原型的 32 点模板识别横、竖、撇、点／捺、折，离线字库按笔画前缀给出候选。界面中的五类按钮可单独测试候选流程。Ring 轻触确认高亮候选；外部文本框仍获焦点时 Windows 使用 Unicode 输入、macOS 对可确认的文本焦点发送 Cmd+V。点击本窗口里的候选时，该字复制到剪贴板供用户粘贴，避免误投递到当前窗口。macOS 的笔画输入路径尚需 Mac 实机验证；笔画模式同时传输触摸板 token 与固件手势，需在真实 Ring 上复查丢包率和轨迹质量。

Windows 使用 `ring_python_sdk.touchpad.windows.WindowsSystemMouse` 发送系统指针事件；macOS 继续使用 `MacSystemMouse`。触摸板默认关闭，仅在页面手动开启。

设置仅在关闭时修改并持久保存，开启状态不持久化。触摸板失败单独显示并尝试恢复暂停的通道；SDK 的原始 IMU / 四元数互斥约束仍然适用。
