# Mac 触摸板 SDK 开发接口

修正后的 Android AAR 触摸板适配已集成至 `src/ring_python_sdk/touchpad`，由现有 `RingSession` 收取 token 并运行原模型。模型无修改，不进行静止校准、偏置补偿或静止冻结；原 AAR 的预热、预测平均、增益映射和点击判定保留。电脑端触摸板模型与固件手势识别是两个独立功能。

## 安装与入口

支持本次验证的 Apple Silicon Mac / Python 3.11 / MNN 3.6.1。主项目兼容 NumPy 1.26.4。MNN 按需导入，未启用触摸板时不会加载模型，也不会影响 ADPCM 和固件手势。

```sh
python -m pip install -c requirements-macos.lock -e '.[ring,touchpad]'
python examples/ring_touchpad.py --device 8F56 --seconds 90
# 明确启用系统鼠标移动、左键单击；需辅助功能权限：
python examples/ring_touchpad.py --device 8F56 --seconds 90 --mouse
```

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

- `TouchpadMove(dx, dy, contact_probability, step, timestamp)`：`dx/dy` 是原 AAR 后处理后的相对位移，默认 Mac 适配器以屏幕坐标单位应用；正 x 向右、正 y 向下。`contact_probability` 是最新已确认概率，属于诊断值，不是额外移动开关。
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

它复用主程序已有连接，在固件手势启动后启动触摸板，关闭/断线时自动清理。`source.touchpad_active` 和 `source.touchpad_error` 可供 UI 展示。接收事件不代表自动发送系统鼠标动作，产品层可以选择将事件传给 `MacSystemMouse.handle` 或其他应用逻辑。桌面导航栏的“触摸板”页面使用 `duration_s=None` 持续控制鼠标，正常使用由“停止触摸板”按钮结束。设置只包含指针速度、轻触点击、反转上下方向；已移除自动停止时长、倒计时、鼠标线程截止时间及旧 `touchpad/seconds` 设置。桌面输出设置 `MacSystemMouse.stop_on_escape=False`，Esc 留给当前应用使用。断线、锁屏、退出和异常仍清理输出，重连后需手动再次开启。SDK／CLI 的可选限时参数保留，桌面页面不会启用。

## 互斥与清理

- 触摸板 token、原始 IMU、四元数共用 `21 00` / `21 01` 数据流，三个模式互斥，冲突时抛 RuntimeError；先关闭已有模式再切换。
- 语音保持 ADPCM；手势保持固件 `26 06` / `26 07` 识别。它们在主机端独立路由；实际固件并发稳定性仍需设备验证。
- 队列最多 64 包，溢出清空并重置推理；序号缺口、250 ms 断流、无效包会重新预热。超过 8 秒无有效 token，停止并报告 TimeoutError。
- STOP、disconnect、stop_all 都会关闭触摸板输出；START 失败或被取消会尝试 STOP，STOP 写入失败仍释放模型和线程。断线不会自动恢复触摸板。

## 离线模型／录制数据接口

```python
from ring_python_sdk.touchpad import TouchpadBackbone, TouchpadProcessor

model = TouchpadBackbone()
predictions = model.step([0., 0., 0., 0., 0., 0.])  # shape=(5,3)
model.reset()

processor = TouchpadProcessor()
processor.feed(packet_bytes, arrival=received_monotonic, now=now_monotonic)
events = processor.poll(now_monotonic)  # 定时约 1–5 ms 调用，排出待处理位移
```

同一个 model/processor 只在单一线程使用。`feed` 输入完整 `21 05` 通知包；`poll` 不连接蓝牙或发送鼠标事件。原生模型输出三列为 vx、vy、接触 logit，SDK 负责 sigmoid 和后处理。

## 适配依据与验证

模型来源与 SHA-256：`src/ring_python_sdk/touchpad/assets/provenance.json`。MNN runtime 许可证不涵盖供应商模型，发布权限沿用主项目 `THIRD_PARTY_NOTICES.md` 的说明。

必须保持“推理前上传全部状态；推理后缓存全部状态”的顺序，不能边读输出边回写输入。详见 `docs/TOUCHPAD_ADAPTATION_AUDIT.md`；原数据 27,790 帧回放已验证修复效果，无校准或静止冻结。

测试入口：`tests/test_touchpad_sdk.py`（会话、互斥、线程、清理、超时、路由与主项目适配），`tests/test_touchpad_backbone.py`（真实 MNN 状态覆盖回归）。仍未宣称 Android 设备逐帧输出完全一致，也未进行本轮 SDK 与语音/手势并发实机验收。

## 桌面动态控制

`RingAudioSource.start_touchpad(**options)` 和 `stop_touchpad()` 从 GUI / 其他线程调用，立即返回 `concurrent.futures.Future`，通过现有 BLE 事件循环执行 SDK 操作。不要在 GUI 线程调用 `.result()` 等待。运行期间无需修改构造参数，也不重启语音。

`ui/touchpad_controller.py` 管理页面状态和连接代次；`touchpad_mouse.py` 将高频事件放入最多 32 项的队列，在专用线程发送系统鼠标事件。连续位移合并但不越过点击，超过 100 ms 的旧事件丢弃；停止立即关闭本地输出门。推理仍由 SDK 的单线程 executor 执行，BLE 回调只入队，GUI 仅接收低频统计和状态变化。

桌面鼠标输出通过 `native_touchpad.py` 使用独立的 `NativeAccessChannel` 工作进程，与设置页沿用同一套可重建权限检测机制。拒绝授权的工作进程会丢弃，重新开启时由新进程检测；不再依赖 GUI 进程里可能缓存的 CoreGraphics 拒绝状态。权限检查和实际鼠标输出在同一工作进程完成，仍同时要求辅助功能和事件发送权限。独立通道避免鼠标输出与场景读取互相等待；停止管道能在一个批次中途关闭输出，且不会重放失败或超时的点击。SDK 的独立 `MacSystemMouse` 适配器保持原样。

设置仅在关闭时修改并持久保存，开启状态不持久化。触摸板失败单独显示，不终止语音或固件手势；SDK 的原始 IMU / 四元数互斥约束仍然适用。

## 笔画输入

### 8F56 双击实机测试

先在主程序里断开 Ring，再在项目目录运行：

```bash
bash scripts/test-touchpad-double-click.sh
```

默认连接名称包含 `8F56` 的设备，使用主程序同一套 `TouchpadClicks`，固定最大间隔 400 ms（含边界）。等终端出现 `[就绪]` 后做单击／双击，每组之间停顿约 1 秒。终端显示原始 `[CLICK]`、相邻 click 的间隔、`单击`／`双击` 事件及累计次数。第一下立即输出单击；第二下在 400 ms 内到达则输出双击，已经输出的第一下单击保留，因此一次双击的事件顺序是 `single → double`。超过 400 ms 的下一下立即作为新单击；连续三击是 `single → double → single`。每两秒显示预热、接触概率和 token 状态。不移动系统鼠标、不启用语音、不输入文字，也不切换实际输入模式。

`Ctrl+C` 停止并断开，事件和统计保存在 `data/touchpad_double_click/<时间>/events.jsonl` 与 `summary.json`。如果只有接触却没有 click，可加 `--contacts` 查看接触起落和模型最终判定。可用 `--seconds 120` 限时，或用 `--device <名称或 UUID>` 指定设备。`--demo` 只检查程序和日志，明确标记为模拟，不连接 Ring，也不代表实机识别效果。

终端每两秒汇总「过期轨迹清理」「数据流重置」次数，不逐帧打印清理提示。单帧轨迹过期清理不会打断已识别 click 的双击配对；整条流重置（SDK `contact reset` 的 `step=0`）、停止或断线清空配对记录。单击在识别到时已经触发，重置不会撤销或补发单击。日志记录每个重置事件的范围及 `click_pair_reset`，已删除旧的待定点击取消计数。

### 主程序中的切换

「触摸板」页保留鼠标设置与本地笔画试写框，两种实际输出共用一个「开启／停止触摸板」开关。每次开启默认鼠标模式；点入可编辑输入框后 Touchpad 双击进入笔画，与 tap 语音使用当前已聚焦的输入框，之后再次双击或离开当前输入框回到鼠标。切换复用原来的 SDK 数据流，不重新预热，不需要在 UI 手动切换输入模式。响指不再负责笔画唤起；旧的笔画唤起开关、手势和上次输入模式设置已移除。

双击由原始 `TouchpadClick` 的时间戳判定，最大间隔为 400 ms。第一下立即发送普通单击，鼠标模式下直接点击当前位置；第二下在窗口内到达就发送双击手势，结束这一组配对，已经触发的第一下不会撤回或重放。超过窗口的下一下立即作为新单击。双击仍专用于切换笔画，第二下不会另发系统鼠标双击或补点光标；它使用当时的焦点，包括第一下正常点击带来的应用／输入框切换。笔画模式中的 Touchpad 单击只报告手势，不确认候选字也不向外部应用点击，确认仍用原来的固件 `tap`；左／右滑切换候选，上滑退一笔，下滑清空。SDK 原始 click 识别阈值与鼠标移动算法不变。

正常流程是「鼠标 → 点入输入框后双击 → 笔画 → 双击回鼠标」，不会启动或结束一轮语音。若语音正在录入／处理，先在原输入框按听写结束并上屏，再在当前输入框进入笔画；空语音直接切换。两种功能可以同时启用，笔画会话期间暂停语音输入，回到鼠标后恢复待命。选字后保留笔画模式，退出取消未确认笔画、保留已上屏文字和 ProxiMic 系统输入法。

笔画识别、离线字库、32 点 DTW 模板和本地联想来自 `origin/yyf` 的 `741517b`，接入当前版本的连接、场景和输入法架构。

点击与笔画处理保留 yyf `741517b` 的行为，包括积压轨迹补发、连续包间隔处理和 2 秒空闲重置规则。公共后处理已重构，`core.py` 不再要求与 yyf 文件逐字相同；回放测试核对其位移、接触边界、click 和 click verdict 的数值、顺序与时间不变。`stroke_input.py`、`stroke_dtw.py`、笔画字库和模板继续沿用该版本；不另设识别置信度门槛。应用层使用即时单击与 400 ms 双击切换、固件 tap 选字和当前外部输入框会话接入。

### 统一后的处理流程

1. **一次接收、一次推理。** Ring 通过已有 BLE 连接发送 token；BLE 回调入队，专用推理线程运行同一个 MNN 模型。每个 token 只运行一次模型，前后台采用相同规则，切换鼠标／笔画也不重新预热。
2. **共用缓存和位移计算。** `trajectory.py` 的 `TrajectoryBuffer` 只保存一份帧时间和重叠速度预测；`core.py` 的 `Postprocessor` 只计算一次接触概率，并持有唯一的 `ClickDetector`。平均速度、增益曲线、微小位移处理都只维护一份实现。相同样本的计算结果可复用；后续预测补入该帧时使缓存失效，避免鼠标读到提前固定的旧结果。
3. **两个读取进度。** 每个进度只记录下一帧、下一发送时刻和追赶状态，不复制模型或速度样本。识别进度按 yyf 规则，每轮最多补齐 20 个到期帧；鼠标进度按原 `27c773d` 节奏，每轮至多一帧，通常约 5 ms，原追赶间隔为 4.75 ms。鼠标晚读时仍能拿到后续补入的预测，因此位移值和发送时间均可与旧版逐帧对照。两者都读完或过期后才释放这份样本。
4. **识别和显示分别交付。** `on_event` 输出及时轨迹、接触边界和 click／verdict，笔画模式使用这些事件；`on_pointer_move` 只输出逐帧鼠标位移，鼠标模式使用它并从 `on_event` 接收点击。鼠标路径不再运行一遍无用的点击计算，其慢调度或过期也不会重置识别路径的 click。
5. **独立的系统鼠标输出。** 鼠标线程消费最多 32 项的队列，同轮来不及发送的连续移动可以合并，但不会越过点击。使用独立控制进程，启用时同步检查一次辅助功能和事件发送权限，此后由该进程内的独立线程每隔约 1 秒复查；移动、单击和双击目标读取只读取内存结果，不等待权限查询。复查发现拒绝或异常后，下一次输出立即停止；空闲或笔画暂停期间，主程序每秒读取一次子进程缓存状态以报告错误，这个读取不触发系统权限查询。系统自身的授权限制始终生效，应用发现撤权的时间取决于复查周期、系统查询耗时和状态读取时机。手动停止、断线与停止管道不依赖复查；停止也不等待正在进行的权限查询。重新授权后重新开启触摸板使用新控制进程，不自动重放旧移动或点击。实际系统事件率仍取决于调度和原生调用耗时，不能用模型 200 Hz 推导光标一定刷新 200 次／秒。
6. **当前交互逻辑。** 第一下一到就触发单击，400 ms 内第二下触发双击手势；超过窗口则是新单击，没有等待计时器、首击位置暂存或超时补发。双击按当前会话层的规则，在当时已聚焦的输入框唤起笔画，与 tap 语音共用输入法准备流程。笔画模式用固件 tap 选字，Touchpad 双击退出。切换复用当前数据流，恢复鼠标时忽略退出前的残留笔画位移。浮窗、候选、联想以及语音正在输入时先结束听写的规则保持现有行为。

`pointer.py` 和第二个 `Postprocessor` 实例已移除。`tests/test_touchpad_pointer.py` 分别使用旧版鼠标与重构前 yyf 事件的固定回放摘要验证行为，另检查模型不会推理两次、鼠标不调用点击判定器、慢调度时样本缓存不会持续增长。

过期与恢复规则保持既有值：轨迹超过 100 ms 会由各自读取进度清理；超过 250 ms 的旧通知包丢弃，连续但到达间隔变长的包不会仅因此重置模型；包序号缺失、设备重启、队列溢出或超过 2 秒无数据会重置。原始 click 已确认后，单帧轨迹清理不清除它的双击配对记录；整条流重置、断线或停止会清空配对，已经发出的单击保留。8 秒没有有效 token 时结束流。笔画线程仍沿用原来的 150 ms 事件时效、轨迹长度／点数要求和 500 ms verdict 等待；该 verdict 等待与已删除的单击延迟无关。

前后台诊断写入统一日志：`TOUCHPAD_VISIBILITY` 标记 Mythlink 是否处于激活状态，`TOUCHPAD_STATS` 每约 2 秒记录有效 token 处理帧率 `token_hz`、SDK 轨迹输出率 `move_hz`、推理耗时、包队列延迟、Qt 排队延迟、原始 click 和最终单／双击数量，以及轨迹过期、整流重置、笔画等待判定超时等原因计数。统计来自现有 SDK 回调，不更改处理判断，也不记录文字或轨迹坐标。跨前后台的窗口标记 `mixed_visibility=true`，比较时应排除。实测需重启主程序，在前台和后台分别保持相同操作约 30 秒；用稳定且预热完成的窗口比较，不能把模拟调度测试当作前后台实测。

新版日志标识为 `pipeline="unified-trajectory-v4"`：v2 将鼠标权限查询移至独立低频复查，v3 改为即时单击与 400 ms 双击配对，v4 增加按输入会话持有的 App Nap 保护及批处理 CPU 耗时诊断。SDK 轨迹、原始 click 与笔画识别算法沿用 v1。鼠标另记 `pointer_frame_hz`（逐帧调度交给鼠标线程的帧率）和 `pointer_post_hz`（权限进程实际发出的非零鼠标移动事件率）。`move_hz` 仅表示识别轨迹的数据处理量，不能用它判断鼠标输出是否平滑；静止时 `pointer_post_hz=0` 正常。

### macOS 后台输入保护

`mac_activity.py` 使用 Apple 的 `NSProcessInfo.beginActivityWithOptions:reason:`，持有返回对象并在结束时调用 `endActivity:`。固件手势 START 期间使用 `NSActivityUserInitiatedAllowingIdleSystemSleep`；触摸板 token 会话（包括鼠标和笔画）以及独立鼠标输出进程额外使用 `NSActivityLatencyCritical`。参考 [WebKit 的 UserActivityMac.mm](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/platform/mac/UserActivityMac.mm)，移除 sudden/automatic termination 选项，不阻止正常系统睡眠或让显示器常亮。[Apple 的说明](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/power_efficiency_guidelines_osx/PrioritizeWorkAtTheAppLevel.html)明确将此接口用于持续的用户任务。

保护只随实际会话开始、停止、异常、取消或断线申请／释放。窗口隐藏、切后台和鼠标／笔画切换不释放主进程的触摸板保护；停止触摸板后仍在监听固件手势时保留基础保护。重新开启／连接使用新的活动对象；非 macOS 不调用 Foundation。`APP_ACTIVITY` 日志记录申请、释放和失败，鼠标子进程的申请结果回传主进程。所有系统调用都在会话边界，不加入每帧识别或鼠标发送路径。

`inference_batch_ms` 是一次非空批次开始解析至完成推理和轨迹／点击处理的经过时间；`inference_thread_cpu_ms` 是同一段中该工作线程实际获得的 CPU 时间（不包括可能存在的其他原生工作线程）；`inference_batch_packets` 是本批包数。空轮询保留最后一次非空批次的这三项，日志是定期快照，不是每批分布或真实全程最大值。两种耗时差距大说明该线程在计时区间内有等待／未执行时间，不能单凭差值认定 App Nap；需要结合系统状态与前后台实测。`sdk_contact_reset_expired_motion` 按过期轨迹帧计数，不是独立卡顿次数。

外部输入框唤起后立即显示候选条与旁边的透明轨迹区，跟随光标、避让屏幕边缘，不抢焦点。主界面和浮窗共享 `stroke_display.py` 的颜色、线宽、五类标准笔形和停留／淡出时长：划动时显示实时轨迹，抬手后变为横／竖／撇／点捺／折对应的标准笔形，只展示当前一笔；笔画序列保留在候选条。主界面仍支持 `h/s/p/n/z`。

外部输入框和主界面共用 `StrokeContext` 离线下字联想。唤起及确认候选后，读取光标前最多四个字符生成候选；只显示联想，不自动写入。左／右滑选择、原固件 `tap` 确认，可连续联想；开始新笔画后改用笔画候选。无法读取外部上下文时只累积本次已成功确认的字，失败回执不推进上下文。键盘／鼠标接管时清掉旧联想，退出或更换输入框也不沿用旧上下文。

候选条高 32 pt，当前候选使用蓝底白字，不加【】。候选条距光标 4 pt：放在上方时底边贴近光标，放在下方时顶边贴近光标。右侧最多 140 pt 的轨迹区独立上移 24 pt，不改变候选条位置；屏幕边缘会限制上移距离，底部空间不足时按比例缩小轨迹区。语音和笔画浮窗共用 `InputPaletteStyle.swift` 的半透明背景（68% 不透明度）、10 pt 圆角、细边框和无阴影外观；语音默认高度也为 32 pt，并继续支持现有的大小设置。

`native_touchpad.py` 只在双击手势到达时读取当前前台应用，不保留首击前的应用或鼠标位置；第一下正常点击可以改变焦点。双击阶段不命中检测鼠标位置或补发光标点击。`ui/stroke_session_controller.py` 与 tap 语音共用 `InlineInputController` 的输入法准备流程：异步选中输入法、必要时恢复输入上下文、等待当前原生 client 就绪，随后等待 `stroke_begin` 确认再切换输出。准备期间的输入法重连保留同一请求；过期回调不能开启新会话。输入目标由原生输入法的 epoch、client 和笔画会话标识绑定；切换应用或输入框时退出。鼠标权限要求、SDK 识别输入和数据流不变。

原生输入法仅在浮窗实际可见后返回 `stroke_ready` 成功；无法定位或显示浮窗会明确返回失败，主程序恢复鼠标输出和语音待命。原生输入框拒绝、切换工具异常及输入法准备超时也会恢复鼠标，不重启 SDK 流。统一诊断日志中的 `TOUCHPAD_DOUBLE_CLICK` 和 `STROKE_SESSION` 记录双击路由、准备阶段及退出原因，不记录输入文字或笔画轨迹。输入法准备不创建语音句子，也不会发送语音 begin、finish 或 reset。

`IMEBridge` 必须转发共用准备流程的 `state` 就绪回复，以及 `stroke_ready`、`stroke_result`、`stroke_cleared`、`stroke_ink_cleared`。笔画与语音准备均使用普通 `ping` 读取原生状态；原有带 `stroke:` 编号的状态回复仍兼容转发。ASR 上下文回复仍仅供对应的上下文请求读取；所有输入法消息继续检查连接 epoch，笔画控制器再核验 client 和会话编号。

SDK 额外提供 `TouchpadContact(state, step, timestamp, confirmed_step)` 和 `TouchpadClickVerdict(start_step, step, is_click, timestamp)`。它们观察现有点击检测器的接触边界和最终判定，不改变鼠标增益、移动调度、点击阈值或断流重置规则。笔画等待对应接触的明确判定；超时、丢帧和无判定轨迹均不自动提交。鼠标输出忽略接触与判定元数据；完整流重置清掉双击等待，单个过期轨迹帧的清理保留已识别的 click。

回归入口：`tests/test_touchpad_clicks.py`、`tests/test_touchpad_click_routing.py`、`tests/test_touchpad_click_focus.py`、`tests/test_stroke_session.py`、`tests/test_stroke_integration.py`、`tests/test_stroke_input.py`、`tests/test_stroke_dtw.py`、`tests/test_stroke_structure.py`、`tests/test_stroke_context.py`，以及原生 `Tests/Activation` 的笔画提交与生命周期检查。硬件轨迹和外部应用兼容性仍需 Ring 实机验收。

真实通信回归：`tests/test_ime_bridge.py` 验证回复转发和旧连接过滤；macOS 上的 `tests/test_stroke_ipc.py` 编译 `native/ProxiMicInput/Tests/StrokeIPC`，用私有 Unix 套接字连接正式 Python 控制器、Swift 输入法和实际浮窗，验证显示、tap 选字、空框及文字中间的连续联想、再次双击退出及鼠标恢复。此测试只模拟 Ring、焦点检测、输入法选择和进程内编辑器，不连接戒指，不向其他应用发送鼠标事件或写入文字。

2026-10-08：持续运行回归 33 项通过，包含模拟一小时后继续输出、SDK 不限时流、手动停止、Esc 不停止桌面触摸板、页面切换、旧配置清理、断线／锁屏／退出清理，以及 1440×940／940×700 页面。预览位于 `.build/touchpad-manual-stop/`，使用模拟输入，不连接真实戒指或发送真实鼠标事件。
