# Ring Windows SDK 0.4.0

Windows 11 x64、64 位 Python 3.11，用于 Ringo / R1 戒指的蓝牙连接、手势事件和 Raw2 触摸板。附带中文 Ring Studio。Python 代码可以继续使用 `from ring_mac_sdk import Ring, scan`；Windows 命令为 `ring-win`。

**首次使用：** 安装 64 位 Python 3.11（含 Python Launcher），将 ZIP 完整解压到可写目录。双击 `install.cmd`，等待安装和模型自检结束。首次安装需要联网。以后双击 `studio.cmd` 就能打开试用台，不需要管理员权限。

打开 Windows 蓝牙，让戒指处于可连接状态，在页面依次点「扫描附近戒指」「连接设备」「开始触摸板」。先在页面上试划、轻点；系统光标和正式点击均默认关闭。试用台仅监听本机 `http://127.0.0.1:8765`。结束时先停止触摸板并断开，终端按 Ctrl+C 退出。

**无戒指演示：** 安装后，在解压目录的 PowerShell 中运行 `.\studio.cmd --demo`。模拟模式不会访问蓝牙或控制系统鼠标。

- [安装、命令和真机验收](docs/INSTALL.md)
- [Python 接入、回调及诊断](docs/INTEGRATION.md)
- [系统光标、滚动与点击](docs/SYSTEM_CURSOR.md)
- [移植范围与验证记录](docs/CHANGES.md)
- [第三方许可](THIRD_PARTY_NOTICES.md)

本版沿用 0.4.0 的协议和加密后端模型。不包含 OTA、固件、麦克风 API、戒指端模型或训练材料。
