# 安装、升级和连接

## 支持范围

- Mac：macOS 13或更高、Apple Silicon arm64，Python 3.11～3.13；建议使用原生 arm64 Python 3.12，不使用 Rosetta 的 x86 Python。
- 戒指：Ringo、nRF54L15、硬件 v1；本固件使用原 legacy 应用分区及相应签名配置。
- 本次实测设备为 RingoA4BA。名称相似不代表 bootloader 兼容：其他设备如果已经迁移私有/加密 bootloader，须先让固件维护者确认可接受本包签名和布局。不要把本包当作 bootloader 迁移包。
- SDK 使用 Mac CPU 后端，不是眼镜 QNN/HTP 运行环境，也不包含眼镜 APK。

## 安装 SDK

在解压后的交付目录运行：

```bash
shasum -a 256 -c SHA256SUMS
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install ./sdk/*.whl
python -m pip install 'aiohttp>=3.10,<4' 'pynput>=1.7,<2' \
  'pyobjc-framework-Quartz>=11,<14' 'pyobjc-framework-ApplicationServices>=11,<14'
ring-mac model-check
ring-mac scan --seconds 8
```

SDK wheel 固定了所需 ONNX Runtime 版本；不要独立升级模型推理依赖。扫描结果中的 `identifier` 是这台 Mac 的 CoreBluetooth UUID，不能照抄另一台电脑的 UUID。记录完整设备名和 identifier，不要自动选择信号最强的设备。

macOS 需要允许实际运行服务的终端/Python/宿主访问蓝牙。系统光标另外需要“系统设置 → 隐私与安全性 → 辅助功能”权限；仅查看页面轨迹不要求辅助功能权限。

## 可选：安装本包固件

如果设备已经运行 `2.1.6-dev.touchpad.9.debug`，无需重复升级。升级前在页面停止系统控制、停止触控板、断开连接，并让手机、眼镜和其他程序释放戒指。

以下是原应用 OTA 流程，不刷 bootloader，不使用 HEX：

```bash
python -m pip install 'smpmgr==0.18.1'
# 将实际扫描到的 identifier 放到变量里，保留引号
RING_ID='替换成你这台Mac扫描到的目标identifier'
ring-mac info "$RING_ID"
python -m zipfile -e firmware/ringo-touchpad-dev9.zip .ota
smpmgr --ble "$RING_ID" --timeout 60 upgrade --slot 1 --format=any .ota/ring-firmware.signed.bin
```

`--format=any` 沿用本版本已验证的升级工具调用，跳过工具端的 MCUboot 格式检查；设备 bootloader 仍验证签名。本交付包已核对镜像哈希和 legacy 头信息。**只用于本包中的已核验签名应用，并在确认设备兼容后执行，不能用它绕过未知镜像的兼容检查。** 电源须稳定，不要在上传/交换期间断电。

升级成功后等待设备重新广播，再检查：

```bash
ring-mac scan --seconds 8
ring-mac info "$RING_ID"
smpmgr --ble "$RING_ID" --timeout 60 image state-read
```

INFO 应显示 `2.1.6-dev.touchpad.9.debug`；SMP 活动镜像应 `active=True` 且 `confirmed=True`，摘要见 `release-manifest.json`。数值版号 `2.1.6` 不含开发编号，不能只看它判断 `.8`/`.9`。应用正常启动后自行确认镜像，不要在不明状态下强制确认。若未出现预期固件，停止反复升级并保留日志交给维护者。

## 页面使用

```bash
ring-mac ui --open
```

打开 `http://127.0.0.1:8765/`，按“扫描 → 选择自己的戒指 → 连接 → 开始触控板”操作。只能有一个控制页面占用会话，CLI 和页面也不要同时连接同一枚戒指。启动服务不会自动连接或自动控制鼠标。

- 确认状态 `RUNNING` 后再佩戴试用。
- 页面轨迹倍率只影响画布；系统光标倍率和滚动倍率各自独立，范围0.25～8，默认1。
- 需要系统输出时，检查辅助功能权限，再点“开启系统控制”。修改系统参数前先停止系统控制，再重新开启。
- 单指移动，开启双指映射时双指纵向滚动。正式点击需要另行勾选，默认关闭。
- “停止系统控制”不停止模型和页面观察。页面获得键盘焦点时 Esc 可停止系统控制，**不是全局热键**。终端 Ctrl+C 可退出服务。
- 断开、触控板停止/失败、session/generation变化后，系统控制关闭，不自动重新开启。

## 排错

| 现象 | 检查 |
|---|---|
| 扫描不到 | 设备电量和距离；手机/眼镜是否占用；macOS蓝牙权限；唤醒设备后重新扫描 |
| INFO可读、START超时 | Ringo配对加密及通知是否就绪；SDK已对初始STOP做有界重试；保留错误，不反复重发START |
| START返回 `device_code=-16` | 这是busy类拒绝，可能为充电、低电压、模式冲突或前次发送未退出；不能一律解释为充电。先查电量、充电后取下再试 |
| 模型加载失败 | 确认使用包内wheel和其固定依赖、arm64 Python；运行 `model-check` |
| 页面动、系统光标不动 | 是否明确开启系统控制，辅助功能是否允许，是否刚发生generation变化导致自动停用 |
| 戒指不动却有明显漂移 | 停止流、取下静置，按校准文档查询/重校准 |

反馈请附 SDK/固件版本、设备名称、macOS/Python版本、操作步骤和完整错误 JSON；不要仅发送“Ring refused START”一句。
