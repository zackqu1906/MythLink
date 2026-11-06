# Ringo 触控板交付包

本包用于在兼容的 Ringo 戒指与 Apple Silicon Mac 上试用触控板：戒指计算特征，Mac SDK 运行配套后端，输出移动、接触和点击事件；页面可选择控制系统光标。

这是联调测试交付版，不是量产安全发布版。固件是 A4BA 已安装验证的 **2.1.6-dev.touchpad.9.debug** 原始 OTA 包；没有因为本次交付重新编译镜像。配套 SDK 为 **0.3.0**，Git 提交、完整文件哈希以 `release-manifest.json` 和 `SHA256SUMS` 为准。

## 内容

- `firmware/`：一个应用 OTA ZIP，内部只有签名固件、manifest、build-info。
- `sdk/`：可安装的 Python wheel，含 SDK、页面、配套接收端运行资产和原生交互库。
- `tools/gyro_calibration.py`：读取零偏状态或明确执行重新校准。
- `tools/touchpad_events.py`：使用公开 SDK 接口接收触控板事件。
- `docs/`：安装、接口、校准和版本限制说明。
- `release-manifest.json`、`SHA256SUMS`：版本和完整性记录。

包内不提供戒指前端源码、独立前端权重、训练 checkpoint、参考数据集、固件 ELF/MAP、HEX、私钥或固件仓库。SDK 中的接收端资产是运行后端所必需，不是戒指前端。

二进制固件必然包含执行前端所需的机器码和参数。本包的边界是“不交付源码和独立权重”，不承诺二进制不可逆向；本次未增加镜像加密、防提取或量产读保护。不要把 debug 签名镜像称为量产加密固件。

## 第一次使用

1. 阅读 [安装与连接](docs/INSTALL.md)，确认硬件和已有 bootloader 兼容，再决定是否 OTA。
2. 安装本地 wheel，执行 `ring-mac model-check` 验证运行环境。
3. 执行 `ring-mac ui --open`，扫描并选择自己的 Ringo，连接后开始触控板。
4. 如需控制桌面光标，先检查辅助功能权限，再明确开启系统控制；正式点击默认关闭。
5. 静止时明显漂移，先停止并断开页面，再按 [零漂校准](docs/CALIBRATION.md) 操作。不要边佩戴边校准。

详细接口见 [SDK 与 BLE 接口](docs/API.md)，测试范围和限制见 [发行说明](docs/RELEASE_NOTES.md)。

安装依赖需要联网；本包没有打包 Python、全部第三方依赖或 Nordic 手机升级工具，不是完全离线安装包。
