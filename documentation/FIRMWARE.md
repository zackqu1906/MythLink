# 戒指固件更新

## 更新已连接戒指

在「设置 → 戒指 → 戒指固件更新」查看设备当前固件和应用附带的固件。连接目标戒指、读取电量并结束语音输入或编辑后，点击「开始更新」。电量至少为 30%。

升级期间应用暂停语音、手势和触摸板，并阻止退出或切换连接。上传进度到达 100% 后还需要重启、重新连接和镜像确认；以最终成功提示为准。失败时查看错误并重新连接后重试。

## 替换应用附带的固件

在已安装项目依赖的 Python 环境中执行：

```sh
python tools/bundle_ring_firmware.py /absolute/path/new-firmware.zip
```

命令校验并替换 `src/proximic_ring/assets/firmware/ring-firmware.zip` 与 `bundle.json`。版本来自 ZIP 的 `build-info.json`，缺少时使用 MCUboot 版本。将这两个资源一起提交即可固定下一次发布附带的固件；该工具不会连接或升级戒指。

仅一次构建使用不同固件：

```sh
bash scripts/build-macos.sh --firmware /absolute/path/test-firmware.zip
```

```powershell
.\scripts\build-windows-installer.ps1 -Firmware C:\firmware\test-firmware.zip
```

直接运行 PyInstaller 可设置 `MYTHLINK_RING_FIRMWARE`。构建会先校验，再将所选资源写到 `.build/ring-firmware/`；无效固件中止构建。

## 包格式与发布验证

使用当前 Ringo nRF54L15 的单应用 MCUboot DFU ZIP，包含 `manifest.json`、一个 `.signed.bin`，以及可选的 `build-info.json`。芯片、board、分区、镜像编号、大小、版本与哈希必须满足 `src/proximic_ring/firmware/bundle.py` 的约束。

升级通过 SMP 上传候选镜像、设置测试启动、重启并核对实际运行哈希，必要时确认镜像。只完成传输不代表启动成功。替换固件后，在目标硬件验证传输、断连与重连、启动确认、失败恢复及普通输入功能。
