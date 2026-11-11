# Windows 11 x64 使用说明

本包为 Ringo / R1 Python SDK 1.18.0。用 Windows 11 x64（内部版本 22000 或更高）、64 位 Python 3.11 和支持 BLE 的蓝牙适配器。触摸板在 CPU 上推理，需要 Microsoft Visual C++ v14 x64 运行库。[Bleak 平台要求](https://pypi.org/project/bleak/3.0.2/) · [ONNX Runtime 要求](https://onnxruntime.ai/docs/install/#requirements) · [微软运行库下载](https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist)

## 安装

解压 Windows 发行包，在包含 `.whl` 文件的文件夹里打开 PowerShell。以下命令直接使用虚拟环境里的 Python。首次安装需要联网下载 PyTorch 等依赖。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -c .\constraints-windows-x64.txt .\mythring_sdk-1.18.0-py3-none-win_amd64.whl
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import mythring; print(mythring.__version__)"
```

版本应为 `1.18.0`。可以先在不连接戒指的情况下运行随包测试，检查本机能否加载加密触摸板模型及健康 DLL。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_touchpad_inference.py
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_health_real_models.py
```

## 连接 dev15 戒指

打开系统蓝牙，将戒指靠近电脑并置于可连接状态。先扫描，再把命令中的示例地址换成扫描结果中的完整蓝牙地址。

```powershell
.\.venv\Scripts\python.exe -m mythring scan --timeout 10
.\.venv\Scripts\python.exe -m mythring info AA:BB:CC:DD:EE:FF
.\.venv\Scripts\python.exe -X utf8 .\examples\touchpad.py AA:BB:CC:DD:EE:FF --seconds 30
```

1. `info` 应显示产品 `ringo`、固件 `2.1.6-dev.touchpad.15.debug`、协商的 MTU 和电量。Raw2 要求 MTU ≥ 67。Windows 扫描时能从广播读取 RSSI，连接后的 `read_rssi()` 返回 `None`。
2. 轻触、滑动并抬起手指，观察终端里的“接触”“运动”“点击”事件。示例打印事件，应用可在 `ring.on("touchpad_event", callback)` 里处理这些事件。
3. 结束后再次运行 `info` 和示例，验证释放连接后可以重连。如果 Windows 弹出配对提示，应先完成系统配对。

## 固件及验证范围

收到的 ZIP 含 `manifest.json`、`build-info.json` 和签名镜像。虽然文件名以 `r1-` 开头，镜像内含 Ringo 设备标识和 `2.1.6-dev.touchpad.15.debug` 版本串。构建清单记录的是 `2.1.6-dev.touchpad.15`。SDK 按连接后的 INFO 确认硬件标识 1、完整版本文本和数值版本 `(2, 1, 6, 0)`，随后校验 Raw2 模型 ID `0x77762A23`、200/100 Hz、八维特征和首帧。原 ZIP 随 wheel 收录在 `ringo-touchpad-dev15` 条目中。

dev15 麦克风协议尚待验证，Ringo 在线升级保持关闭。安装和以上示例均不会刷写固件。Windows 11 实机的蓝牙配对、连接、连续触摸和断开重连仍待联调。

已在 Wine 运行的 Windows x64 Python 3.11.9 中安装本 wheel，通过 `pip check`，导入 Bleak WinRT 后端，并执行真实 Raw2 模型和健康 DLL。用相同合成输入比较 Windows x64 与 macOS arm64 的 50 步推理，坐标分量的最大绝对差低于 0.00018，接触与手指数分类的原始输出差低于 0.00930。实际触感和点击识别仍需佩戴测试。
