# Ring Python SDK 0.5.0

独立 Python 3.11 SDK，支持 Apple Silicon macOS 13+ 和 Windows 11 x64。
SDK 提供设备控制、音频、普通手势和 Raw2 触摸板事件，不依赖 Mythlink、Qt、
语音识别或输入法。安装包包含原厂 0.4.0 接收端、加密模型和原生加载器。

## 安装与使用

安装对应平台的 wheel：

```sh
python -m pip install ring_python_sdk-0.5.0-py3-none-macosx_13_0_arm64.whl
python examples/touchpad.py --device DEVICE_IDENTIFIER --seconds 30
```

示例只接收事件，不发送系统鼠标或键盘输入。Raw2 需要配套的触摸板固件。
`DEVICE_IDENTIFIER` 使用扫描所得的 macOS UUID 或 Windows 地址。

```python
import asyncio
import multiprocessing
from ring_python_sdk import RingSession

async def run():
    ring = RingSession("Ringo", 8, connection_backend="process", auto_reconnect=False)
    try:
        if not await ring.connect_target("DEVICE_IDENTIFIER"):
            raise RuntimeError("Ring not found")
        await ring.touchpad_on(
            protocol="raw2",
            on_event=lambda event: print(event.kind),
            on_pointer_move=lambda event: None,
            on_stopped=lambda error: print("stopped:", error),
            duration_s=None,
        )
        await asyncio.sleep(30)
        print(ring.process_diagnostics)
    finally:
        await ring.disconnect()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    asyncio.run(run())
```

使用 `spawn`，必须把程序入口放在 `if __name__ == "__main__"` 下；模型、蓝牙
对象均在所属子进程创建，不应向 SDK 传递主进程里的 Bleak 客户端或 Qt 对象。
`spawn` 会重新加载调用方入口，因此 UI、语音模型等应用初始化及其重量级导入
也应放在入口保护内，避免调用方自行把这些依赖带进子进程。
PyInstaller 等冻结程序必须在启动 UI 前调用 `multiprocessing.freeze_support()`，
并收录 `ring_python_sdk.process`、平台 provider 和模型数据。

## 进程边界

- **RingSDK-BLE**：唯一 BLE 连接，匹配控制回复、串行化完整控制事务，轻量分流。
  Raw2 包直接送往计算进程，不经过调用应用；通知回调不写文件或解码音频。
- **RingSDK-Touchpad**：连续模型状态、触点/点击判定、完整轨迹与指针事件。
  保留蓝牙进程记录的收包时间。不同应用都使用同一套 SDK 实现。
- **调用进程**：音频解码、记录、应用回调。应用自行决定鼠标、界面和输入法行为。
  SDK 不会导入应用代码，也不会自动注入系统输入。

进程后端保留 `RingSession` 的 MIC、手势、设备查询和 Raw2 API。
`connection_backend="sdk"` 保留原来的进程内 provider 后端，`"legacy"` 保留旧协议。
新应用应显式选用 `"process"`。Raw2 的 `on_event`、`on_pointer_move`、`on_stats`
经有容量限制的串行回调队列交付；同步回调在 SDK 回调线程执行。回调应快速入队，
由应用自己的线程处理耗时任务；异步回调使用调用者的事件循环。

## 故障与停止

设备故障或计算进程退出后停止触摸板，不自动重启，也不重放积压的点击。
错误的 `reason`、`device_code`、`response`、`phase` 跨进程保留。
触摸板进程退出时会尝试确认 Raw2 STOP，音频/普通手势仍使用原连接；BLE 进程退出
则整条连接失效。再次建立进程和连接需要调用方显式操作。

队列均有容量限制。Raw2 输入或事件交付溢出会停止触摸板；普通传感器交付队列
溢出会记录丢包计数，由现有协议组帧器处理缺包。慢消费者不会阻塞蓝牙通知回调。
`touchpad_off()` 立即关闭本地事件交付，并等待设备停止流程；`disconnect()`
关闭连接并回收工作进程。主进程退出后工作进程也会退出。

`process_diagnostics` 包含工作进程 PID、收包/计算计数、丢包数、事件循环延迟及
Raw2 进程间排队时间。它们帮助区分应用卡顿、计算积压和设备发送故障；不能据此
认为无线链路或固件队列不会阻塞。

## 构建

在应用仓库运行 `python tools/build_ring_sdk.py` 生成独立 wheel 和源码压缩包。
源码包包含 SDK、配套模型、这份说明、示例和离线测试，可脱离应用仓库构建：

```sh
python -m pip wheel . --no-deps
python -m pip install '.[dev]'
python -m pytest tests -q
```

旧版 MNN 触摸板可安装 `[legacy]`，OTA 可安装 `[ota]`，可选系统指针适配器的
依赖在 `[pointer]`。原厂 provider 文件和模型保持交付内容，许可证在各包的
`licenses/` 和 `touchpad/assets/` 内。

## 本版验证

在 Apple Silicon macOS 上将 wheel 安装到应用仓库外，在无法导入应用包的环境中
通过 12 项测试。覆盖调用方卡顿时持续收包/计算、真实配套模型推理、故障不重启、
慢消费者、启动取消、子进程退出和音频/手势共存。BLE 数据由离线设备模拟。
本版尚未完成真实戒指的 BLE 长时间测试，也未在 Windows 主机验证；不能据此
判定原来的 `Feature TX age exceeded` 已解决。
