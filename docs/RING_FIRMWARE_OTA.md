# 戒指固件 OTA

设置 → 戒指 → 戒指固件更新，显示当前设备、当前固件和随应用固件。
连接戒指并读取电量后可启动，点击「开始更新」才会写入设备。
默认随包固件为 `2.1.6-dev.touchpad.15`。

## 以后替换固件

在已安装项目依赖的 Python 环境中执行一次：

```sh
python tools/bundle_ring_firmware.py /absolute/path/new-firmware.zip
```

macOS 也可直接使用构建环境：

```sh
.build/packaging-venv-macos13/bin/python tools/bundle_ring_firmware.py /absolute/path/new-firmware.zip
```

命令校验新 ZIP，并替换 `src/proximic_ring/assets/firmware/` 中的 `ring-firmware.zip`
和 `bundle.json`。版本来自固件内的 `build-info.json`，没有该文件时使用 MCUboot 版本。
之后正常 build 即自动携带新固件；将这两个文件一起提交即可固定发布默认值。
该命令只更换应用资源，不会连接或升级戒指。

单次 build 使用另一份固件，无需更改默认资源：

```sh
bash scripts/build-macos.sh --firmware /absolute/path/test-firmware.zip
# 可与 --app-only、--version X.Y.Z 组合
```

Windows：

```powershell
.\scripts\build-windows-installer.ps1 -Firmware C:\firmware\test-firmware.zip
```

直接运行 PyInstaller 时可设置 `MYTHLINK_RING_FIRMWARE` 环境变量。
PyInstaller 每次都将选定固件校验并复制到 `.build/ring-firmware/`，再收集进应用资源。
无效包会中止构建。发布应用的 startup probe 会再验证随包 ZIP、元数据与 OTA 依赖。

## 固件要求与流程

- 使用 Ringo nRF54L15 单应用 MCUboot DFU ZIP：`manifest.json`、一个 `.signed.bin`，可选 `build-info.json`。
- 校验目标 SoC、board、manifest 布局、镜像编号 0、文件大小、版本、MCUboot 头部、哈希和签名 TLV。
  主机验证文件完整性；签名可信性由戒指 MCUboot 验证。拒绝加密、压缩及非启动镜像。
- 电量至少 30%，结束当前语音输入/编辑后启动。上传期间不提供中途取消，应用退出和切换连接操作会被阻止。
- 停止语音、手势和触摸板，并等待原 BLE worker 实际退出。OTA 使用连接时记录的完整平台 UUID/MAC，禁止按名称匹配另一枚戒指。
- SMP 服务：`8D53DC1D-1DB7-4CD3-868B-8A527460AA84`。
  上传镜像编号 **0**，验证备用槽 **1** 的镜像哈希，标记 test，重启。
- 重启后验证 active + bootable 的运行镜像哈希。若固件尚未自确认，发送 confirm 并再次读取状态，
  必须 confirmed 且非 pending 才成功。超时或回滚不会被显示为成功，启动前不会永久确认候选镜像。
- 同一哈希已在运行时跳过写入。失败后尝试恢复普通连接，保留错误，允许重新连接后重试。
- 完成普通连接恢复后恢复用户保存的输入偏好。传输百分比达到 100% 不代表更新已完成。

OTA 协议层基于本地 `ring-python-sdk-main/src/ring_python_sdk/ota/`，在本项目中修正了精确设备寻址、
SMP image index、连接失败清理、重启断连处理以及固件内容哈希验证。
macOS 与 Windows SDK 均固定 `bleak==1.1.1`，OTA 使用兼容的 `smpclient==5.2.0`；其他平台使用
`smpclient[ble]==7.0.0`。后者的 BLE extra 要求 `bleak>=2.0.0`，不能与 Windows SDK 的 1.1.1 固定版本合用。
macOS OTA 复用进程生命周期的 BLE 事件循环，避免关闭循环后 CoreBluetooth 回调异常。

协议参考：[Nordic Device Manager](https://github.com/nordicsemi/ios-nrf-connect-device-manager)、
[Zephyr MCUmgr](https://docs.zephyrproject.org/latest/services/device_mgmt/mcumgr.html)。

## 验证范围

自动测试覆盖固件校验与替换、镜像编号、候选哈希不匹配、未确认超时、同镜像跳过、待处理升级阻断、
精确设备寻址、低电量、连接释放、重复启动、失败恢复和真实 QML 设置入口。
模拟测试不证明真实戒指的升级能力。发布前需用目标硬件验收 SMP 服务、传输、重启、镜像确认和输入恢复；
新固件的芯片/启动布局与 SMP 确认行为必须继续满足上述协议。
