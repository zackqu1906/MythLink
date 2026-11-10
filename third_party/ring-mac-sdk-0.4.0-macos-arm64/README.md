# Ring macOS SDK 0.4.0

面向 Ringo / R1 戒指的 Apple Silicon Python SDK，提供蓝牙连接、普通手势事件、触摸板 CPU 推理，以及中文本地试用台 Ring Studio。

要求 **macOS 13+、原生 ARM64 Python 3.11–3.13**。不支持 Intel/Rosetta；本包不是 Swift Package 或 XCFramework。

## 安装与启动

解压完整交付 ZIP，在解压目录执行：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install './sdk/ring_mac_sdk-0.4.0-py3-none-macosx_13_0_arm64.whl[web,cursor]'
.venv/bin/ring-mac model-check
.venv/bin/ring-mac ui --open
```

首次安装需要联网下载依赖。页面地址为 `http://127.0.0.1:8765`，需手动扫描、选择戒指并连接。蓝牙访问需要启动宿主的 macOS 蓝牙权限。系统光标和正式点击默认关闭，需辅助功能权限及明确开启。

无硬件演示：`.venv/bin/ring-mac ui --demo --open`。模拟模式不连接戒指，也不控制系统鼠标。

仅拿到 wheel 时，在 wheel 所在目录安装 `./ring_mac_sdk-0.4.0-py3-none-macosx_13_0_arm64.whl[web,cursor]`，不加 `sdk/` 前缀。

## 文档

- [安装、升级和设备使用](docs/INSTALL.md)
- [Python 接入、回调及诊断](docs/INTEGRATION.md)
- [系统光标与播放规则](docs/SYSTEM_CURSOR.md)
- [0.4.0 变化与交付范围](docs/CHANGES.md)
- [第三方许可说明](THIRD_PARTY_NOTICES.md)

本包不提供 OTA、Owner 解绑、固件烧录或零漂校准命令。戒指端前端模型源码、权重及训练材料不属于交付内容；运行包只附 Mac 端必需的后端模型密文和加载器。代码及模型按权利方授权使用。
