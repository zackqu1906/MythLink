# Windows x64 新版触摸板（Raw2）适配

2026-11-09 起，Windows 使用供应方 `ring_windows_sdk 0.4.0`，Mac 继续使用 `ring_mac_sdk 0.3.0`。主程序和 `tools/` 下的测试工具都通过 `RingSession.touchpad_on(protocol='raw2')` 使用它，调用方式没有变化。

## 文件

| 位置 | 内容 |
| --- | --- |
| `third_party/ring-windows-sdk-0.4.0-windows-x64/` | 原始交付：wheel、文档、约束文件、示例 |
| `src/ring_windows_sdk/` | wheel 内 `ring_mac_sdk` 包的逐字节副本，只改了目录名，避免和 Mac 版同名 |
| `src/ring_python_sdk/sdk_provider.py` | 按平台选择 SDK；产品代码只从这里导入，不直接写包名 |
| `src/ring_python_sdk/touchpad/raw2.py` | 在产品的 BLE 连接上运行 SDK 接收端，并适配下面的行为差异 |
| `src/ring_python_sdk/touchpad/raw2_model.py` | 平台开关和模型工厂 |
| `tests/test_raw2_windows.py` | 交付一致性、真实模型首帧预测、平台开关、Win32 按键与滚轮输出 |

不要用 `pip install` 把这个 wheel 装进项目环境：它的导入名也是 `ring_mac_sdk`，会和 `src/ring_mac_sdk` 冲突。

## 依赖（仅 Windows x64）

`bleak==1.1.1`、`onnxruntime==1.21.0` 和 winrt 4.x 按供应方约束锁定，写在 `requirements-windows.lock` 和 `pyproject.toml`。SDK 的模型加载会拒绝其他 onnxruntime 版本。之前 mythring 加载方式需要的 `unicorn`、`PyNaCl`、`pyelftools` 已移除，`mythring-sdk 1.18.0` 交付仅作为历史资料保留在 `third_party/`。

## 0.4.0 相对 0.3.0 的行为差异，以及项目的处理

| 0.4.0 行为 | 项目的处理（`raw2.py`） |
| --- | --- |
| `start()` 时重建交互引擎 | `Receiver.engine` 始终创建 `FrameEngine`，笔画识别继续收到每一帧 |
| 同步回调改到独立线程，状态回调也排队 | 指针和诊断回调写成 `async def`，留在 BLE 事件循环；状态同步观察，保证 `Raw2Reset` 排在同代的帧之前 |
| `source_time_ns` 用 `perf_counter` | `host_seconds()` 换算到产品使用的 `time.monotonic()` |
| 等待首包不再自动超时 | `RAW2_START_TIMEOUT_S`（30 秒）作为宿主预算，超时报 `input_timeout` |
| 运行中断流只告警、继续等待，并查询固件 | 写入诊断日志 `[RAW2_DIAG]`；数据恢复后继续运行。界面暂时没有单独提示 |
| 固件自行正常停止时状态是 STOPPED | 非用户操作时转成 `device_error`，界面会提示 |
| 迟到的运动和点击继续处理，输入队列满时丢最旧的包 | 沿用产品自己的事件过期规则 |
| 模型单次调用预算 3 秒（原 200 毫秒） | `model_timeout` 显示中文错误 |

SDK 没有请求 Windows 高吞吐蓝牙参数，也没有提高定时器精度，所以项目继续使用 `session/windows_link.py` 和 `hold_windows_timer()`。

## 行为（仅 Windows x64）

- 依赖齐全时，主程序在 Windows 上使用 `connection_backend="sdk"`（与 macOS 一致），触摸板使用 `protocol="raw2"`，并开放左键双击、右键单击、双指滚动等独立开关。
- 缺少 onnxruntime、不是 x64，或设置了环境变量 `PROXIMIC_WINDOWS_RAW2=0` 时，回到原 legacy 连接和 MNN 触摸板。
- `WindowsSystemMouse` 支持右键（`SendInput` RIGHTDOWN/RIGHTUP）、左键双击（连续两组按下/抬起，由系统双击时间配对）和滚轮（`MOUSEEVENTF_WHEEL`）。滚动按每像素 2 个滚轮单位换算（120 单位约 60 px），需要真机调手感。
- 安装脚本 `scripts/setup.ps1` 和安装包启动自检会额外执行一次 Raw2 推理。PyInstaller 配置收录 `ring_windows_sdk` 的模型、DLL 和 onnxruntime 动态库。

## 2026-11-07 修复

- **旧固件回退**：Windows 连接后按 INFO 判断。只有硬件 1 且版本含 `-dev.touchpad.`（dev9、dev15 等）时才走 Raw2；`1.2.133.public`、`2.0.2.public_dev` 等公开固件改用原来的 MNN 触摸板，在同一条 SDK 连接上收发（`RingAudioSource.connected_raw2_firmware()`、`TouchpadController.raw2Supported`）。连接确认前仍按支持 Raw2 显示。macOS 的策略不变（旧固件不静默回退）。
- **首次连接重试**：Windows 上 SDK 连接首次失败时（WinRT 打开 GATT 偶发失败），等待 0.75 秒后重试一次，与原 legacy 路径一致；`unsupported`（服务或身份不匹配）不重试，macOS 不重试。
- **定时精度**：Raw2 流运行期间调用 `timeBeginPeriod(1)`，结束时 `timeEndPeriod(1)`（引用计数，`hold_windows_timer()`）。供应方接收端每 5 ms 驱动一次交互引擎；在 Windows 默认精度下实际是 15.5 ms，开启后约 6.5 ms。佩戴实测（dev15，不发送系统输入）中没有出现超过 200 ms 的积压：单帧推理中位 2.8 ms、最大 7.5 ms，出队延迟最大 63 ms。
- **蓝牙连接参数（断流的原因）**：Windows 默认以 30 ms 连接间隔、从机延迟 4 连接戒指。Raw2 每秒 100 帧，这个链路发不完：数据包每 200～450 ms 才成批（每批最多 15 帧）到达，固件发送队列溢出丢帧，丢到约 37 帧后固件自己停止采样（STOP 回执中 `dropped=37`，`frames` 只有 258～293）。主程序随后报 “Raw2 数据断流超过 1 秒”，与是否触摸戒指无关。现在 SDK 连接成功后，Windows 上会调用 WinRT `BluetoothLEDevice.RequestPreferredConnectionParameters(ThroughputOptimized)`（`session/windows_link.py`），并在连接期间一直持有该请求，断开时释放；链路变为 15 ms、延迟 0。实测（不触摸）：修复前三次分别在 1.3～21 秒后停止，每次都丢约 37 帧；修复后 60 秒内固件生成并发出 6048 帧，丢 0 帧；走产品连接流程再测 60 秒，收到 6051 帧，没有断流。该接口需要 Windows 11；不可用时只记日志、连接照常。legacy 连接方式没有加这项设置。

## 双指运动接口（八方向）

Raw2 模型对双指同时给出横向和纵向位移。产品的系统输出仍然只把纵向部分映射为滚轮，横向不滚动；完整的双指运动通过 `ring_python_sdk.touchpad.two_finger` 提供给调用方，**不发送任何系统输入**：

```python
from ring_python_sdk.touchpad import TwoFingerGestures

gestures = TwoFingerGestures(
    on_scroll=lambda s: ...,   # TwoFingerScroll：每个双指帧的 dx、dy（指针像素）
    on_swipe=lambda s: ...,    # TwoFingerSwipe：每次双指手势结束时一次
)
await session.touchpad_on(protocol='raw2', on_event=..., on_pointer_move=gestures.feed)
# 每 20～50 ms 调用一次 gestures.tick()，手指停下后才能结束手势；停流时调用 gestures.flush()
```

- `TwoFingerSwipe` 包含 `direction`（`Direction` 的八个值：`up`、`up_right`、`right`、`down_right`、`down`、`down_left`、`left`、`up_left`）、总位移 `dx`/`dy`、`distance`、`angle_deg`（0° 为右，逆时针）、`duration_s`、`frames`，以及 session/generation。
- 坐标与指针一致：+dx 向右，+dy 向下。每个方向覆盖其轴线两侧各 22.5°。`classify_direction(dx, dy)` 可以单独使用。
- 默认参数：总位移小于 `min_distance=15` px 的手势丢弃；`end_gap_s=0.15` 秒内没有双指帧就结束手势；模型在单指/未分类之间短暂跳变不超过 `max_interrupt_s=0.08` 秒时，不拆分手势。新的 session/generation 或 `Raw2Reset` 会丢弃进行中的手势。
- 测试工具 `scripts\test-two-finger.cmd` 用这个接口，在每次双指手势结束时显示方向和角度，并在汇总中按八个方向计数。
- 用已录数据回放：校准前的 11 次手势全部判为 `down_left`（零偏导致）；重新校准后的 5 次上下滑方向全部正确。左右和斜向尚无真机数据。

## 真机双指测试

双击 `scripts\test-two-finger.cmd`（先关闭 Mythlink 主程序）。程序会扫描戒指，选好序号后提示测试时长，并询问是否向前台窗口发送真实滚轮。运行中实时显示每段双指的时长、帧数、纵向/横向位移和滚动方向，以及单指移动和点击；结束后打印汇总。每帧记录和汇总保存在 `outputs\two-finger-test\<时间>\`。命令行用法见 `tools\test_touchpad_two_finger.py --help`，`--demo` 可以不连戒指检查输出。

## 0.4.0 验证记录（2026-11-09）

- 本机（Windows 11 x64，Python 3.11.9）：真实模型经 SDK 的 DLL 加载，首帧预测与供应方参考值一致，递归状态 1216 维。
- 模拟 BLE 的自动化测试覆盖：首包超时、运行中断流后恢复、输入队列溢出、固件自行停止、回调线程与时钟、`Raw2Reset` 先于帧。
- 未验证：真实戒指上的连接、Raw2 连续流、指针、点击、滚轮、笔画输入和断线重连。供应方同样只在 Wine 中验证过，下面的实测记录属于 0.3.0 + mythring 加载方式时期。
- 未重新生成 Windows 安装包。

## 验证与未验证范围（0.3.0 + mythring 加载方式时期）

- 本机（Windows 11 x64，Python 3.11.9）：解密约 0.16 秒，ONNX 会话创建约 0.12 秒；首帧预测与供应方 x64 参考值一致；连续加载 10 次、4 线程并发加载均无错误。
- 真机（Ringo6B72，dev15 `2.1.6-dev.touchpad.15.debug`）：新版连接、INFO / 启动顺序校验、连续 60 秒 Raw2 流已实测通过；双指上下滑方向正确。
- 未验证：主程序里的指针、点击、滚轮手感，断线重连，双指左右和斜向。供应方自己也只在 Wine 中验证过。
- 未重新生成 Windows 安装包。
