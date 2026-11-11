# 安装和使用

目标环境为 Windows 11 x64、64 位 CPython 3.11、支持 BLE 的蓝牙适配器。只用系统鼠标和触摸板推理无需独立显卡。ONNX Runtime 依赖 [Microsoft Visual C++ x64 运行库](https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist)；导入时报 DLL 加载失败时，先确认 Python 位数，再安装该运行库。

解压整个 ZIP，双击 `install.cmd`，或在解压目录打开 PowerShell 执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -c .\constraints-windows-x64.txt ".\sdk\ring_windows_sdk-0.4.0-py3-none-win_amd64.whl[web]"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\ring-win.exe model-check
```

模型自检会认证随包密文并执行一次 CPU 推理。结果应含 `CPUExecutionProvider` 和 `"recurrent_state_floats": 1216`。这一步不访问蓝牙。仅使用 API/CLI 时可省略 `[web]`。升级前先停止触摸板、断开设备并退出旧服务；安装变更后的同版 wheel 请新建虚拟环境。

## 试用台

双击 `studio.cmd`，或运行 `.\.venv\Scripts\ring-win.exe ui --open`。要先看模拟轨迹，运行 `.\studio.cmd --demo`。若默认端口 8765 已被占用，可运行 `.\.venv\Scripts\ring-win.exe ui --port 8766 --open`。同一时刻只用一个宿主控制戒指。

## 命令行

```powershell
.\.venv\Scripts\ring-win.exe scan --seconds 8
.\.venv\Scripts\ring-win.exe info AA:BB:CC:DD:EE:FF
.\.venv\Scripts\ring-win.exe gestures AA:BB:CC:DD:EE:FF --seconds 30
.\.venv\Scripts\ring-win.exe touchpad AA:BB:CC:DD:EE:FF --seconds 30
```

用本机扫描返回的完整蓝牙地址替换示例地址；Mac 上保存的 CoreBluetooth UUID 不能直接使用。扫描从附近广播中筛选候选，连接后通过 INFO 确认身份。找不到设备时，先检查 Windows 蓝牙开关、戒指电量及是否已被其他宿主连接，并让设备重新进入广播状态。如果固件要求加密配对，请先在 Windows「设置 → 蓝牙和设备」完成配对，再重新扫描；SDK 不清除已有绑定。不要把 Python 脚本命名为 `bleak.py`。

## 用戒指验收

1. 扫描、连接，确认固件版本与电量正确。正常启动触摸板需要协商 MTU ≥ 67、Raw2 前端模型 ID 和协议参数一致。Ringo、R1 的手势能力由 INFO 决定。
2. 启动触摸板，等到 RUNNING。在页面上持续滑动、抬手、单击和双击；在实际佩戴条件下检查方向、点击和断流诊断。`touchpad --seconds` 从启动成功后计时；缺少首个有效数据包时会继续等待并告警，可按 Ctrl+C 取消。
3. 需要系统输出时，在普通桌面窗口中单独开启系统光标，测试位移及双指滚动；最后再勾选正式点击进行试用。停止或断开后，确认系统输出已关闭。
4. 再次连接同一戒指、启动触摸板。关闭控制标签页后，确认能在新标签页重新连接。

旧 Ringo 普通手势启停没有业务 ACK，写入成功不代表固件已切换。部分旧固件先开手势会导致触摸板没有首包：先关手势，留出切换时间（历史验证使用 0.7 秒），开触摸板，等 RUNNING 后再开手势。设备返回 -16 时也可能是低电量、充电或模式冲突，仅凭该数值不能确定原因。
