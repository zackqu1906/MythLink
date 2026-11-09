# Ring macOS SDK 接入记录

## 0.4.0（2026-11-09）

Apple Silicon Mac 使用[原厂 0.4.0 交付](../third_party/ring-mac-sdk-0.4.0-macos-arm64/)；`src/ring_mac_sdk/` 的 23 个文件与 wheel 逐字节相同。协议、配套模型和音频/手势共用的 BLE 连接沿用上一版，本次交付没有固件。独立 CLI 仍可用 `requirements-ring-mac-sdk.txt` 安装。

新版把推理和回调分开调度。主程序继续接收完整笔画轨迹和独立的指针事件；恢复到新一代数据时，清掉上一代未处理的输入。等待首包或运行中短暂断流会告警、查询设备并继续等待；点「取消开启」无需等到下一包。可确认是本机队列丢包的缺口可以恢复，来源不明的缺口仍会报错。

SDK 保留迟到的运动和点击候选。Mythlink 实际向 macOS 派发输入时仍检查会话、有效期和目标上下文。拖动中断流会松开鼠标；恢复数据后，断流前按下的触点不会触发新点击。独立试用台的播放规则见[供应方说明](../third_party/ring-mac-sdk-0.4.0-macos-arm64/docs/SYSTEM_CURSOR.md)。

离线验证使用同一个模拟 BLE 连接和本机真实 CPU 模型连续启停两次，每轮 41 个 token 产生 82 帧笔画数据，停止均获确认。手势数据在两轮中正常接收。此检查没有连接戒指或派发系统鼠标事件。

以下是 0.3.0 和配套固件的历史记录；其中的真机数据不代表 0.4.0 已经实测。

## 0.3.0 与 dev15 固件历史

本次以用户提供的 `ringo-touchpad-delivery-20261105.zip` 为准，将交付版完整纳入主项目，并按后续要求接入 Apple Silicon Mac 的触摸板业务和界面。新版并不是旧 `ring_python_sdk` 的同名替换包；它提供触控板、固件手势和设备连接接口，没有旧 SDK 的 MIC、PPG、健康记录等完整接口。

当前更新：2026-11-09 纳入用户提供的 dev.touchpad.15 固件，并撤除主机侧 Swipe / Raw2 启动顺序绕行。刷写后实测 11/11 次启动收到首包，未复现原超时；完整启停 10/11 通过，另一次在主动停止时报告推理失效，不能将本轮记为全部通过。2026-11-06 的 dev9 诊断、临时兼容和实测结果作为历史记录保留在文末。

## dev.touchpad.15 固件更新（2026-11-09）

新附件 `r1-2.1.6-dev.touchpad.15.zip` 仅含 `manifest.json`、
`build-info.json` 和 `ring-firmware.signed.bin`，没有新的接收端 SDK、
固件源代码或补丁说明。首包启动顺序问题已在此固件修复是用户提供的信息；
主项目不能从二进制确认其内部实现，需刷入后复现原场景验证。

- 原件与构建元数据：[固件来源目录](../third_party/ringo-touchpad-firmware-20261109/)。
- 可上传的 [OTA ZIP](../outputs/ringo-firmware-20261109-dev15/r1-2.1.6-dev.touchpad.15.zip)；
  工具要求 BIN 时使用[签名镜像](../outputs/ringo-firmware-20261109-dev15/ring-firmware.signed.bin)。两者内容相同，只选一种格式。
- INFO 目标版本由新包记录为 `2.1.6-dev.touchpad.15`；MCUboot 版本仍为 `2.1.6+0`。
- ZIP SHA-256：`eebe16b0b6b29f8ca2ae39c8e27fd8b9e82242eff7083c203802c86683bce973`。
- BIN：646,068 字节；SHA-256：`6ef922d79c70152cff3529c9657dbfab0f20da7bb23a137ef07376c7bffb9b91`。
- ZIP CRC、BIN 大小、镜像头和构建信息的镜像哈希均已核对。manifest 的 board/soc、加载地址和槽位与旧 dev9 包一致；没有重新打包、改写镜像或验证签名公钥。

产品端删除了 dev9 的 Swipe STOP → 300 ms → Raw2 首包 → Swipe START，
以及 Raw2 结束/失败后的强制 Swipe STOP/START 恢复。触摸板启动、停止、
取消、模型失败和首包超时均不再改动已开启的手势或 MIC；它们继续使用各自
的处理器和回调。旧 dev9 也不再启用临时补丁，需要先升级配套固件。

原交付的 `ring_mac_sdk` 0.3.0 保持逐字节一致；build-info 中的 SDK 0.2.1
是固件构建元数据，不是要求主项目降级 SDK。统一控制调度、首包/推理超时
分类、正常 Raw2 STOP、断流/过期/去重保护，以及蓝牙连接取消闪退修复保留。

用户刷写并恢复配对后，设备 INFO 实际返回 `2.1.6-dev.touchpad.15.debug`。
2026-11-09 完整矩阵包含 Swipe+MIC 先开、Raw2 先开、MIC 先开各 3 轮，
以及主业务 `RingAudioSource` 2 轮；11/11 次启动成功，START 发出至首包
27.50–179.15 ms，中位数 39.48 ms。运行期间三路数据持续增长，停止触摸板后
普通手势和 MIC 继续出包；启停触摸板期间没有额外 Swipe STOP/START。

完整启停为 10/11 通过：Swipe+MIC 先开第 3 轮主动停止时，设备已确认 STOP，
但主机接收端报 `Stream invalidated during inference`。此前中止的一轮测试
也出现同一停止阶段错误，日志均保留。此错误来自接收端推理后的流有效性检查，
与未收到首包的原问题阶段不同；本次未修改原供应方 SDK、未屏蔽错误，亦未恢复
旧启动顺序补丁。左右键、滚轮操作手感与双指横向识别不属于本轮实机验收。

最初的 `CBErrorDomain Code=14 / Peer removed pairing information` 出现在
INFO 之前；由用户恢复连接后才开始上述测试。测试结束已释放连接并重开源码应用。
完整记录：[dev15 实机报告](../outputs/ringo-dev15-verification-20261109/README.md)。

代码侧验证：**184 passed**（33.55 秒），覆盖不再暂停/重启手势、两轮
触摸板启停期间与停止后的 MIC/手势数据、模型失败/取消/首包超时/STOP
失败收尾、旧协议、业务界面、蓝牙取消回归，以及原供应方 SDK 逐字节一致性。
`git diff --check` 通过。模拟包验证的是主机行为，不能证明新固件已修复硬件问题。

## 滚动方向、双指横滑与运行中调节（2026-11-09）

触摸板的鼠标设置新增「翻转上下滚动方向」和默认开启的「双指左右滑动」。
翻转只改变纵向 `dy × scrollGain` 的符号，不改变指针或横滑方向。
双指左滑默认前进（`Cmd+]`），右滑默认后退（`Cmd+[`），发送给识别后的当前前台应用；
实际导航由应用对这两个快捷键的支持决定。原生发送前复查前台 PID 和截止时间，
过期、已停止或目标改变的请求丢弃，不重试。

横滑使用 SDK 的双指模式及连续 `dx/dy`。在同一次接触内累积位移，初始轴判定
距离为 6 像素、主轴占比为 1.5；横向累计 60 像素触发一次导航，抬手后才重新允许。
已锁定纵向的接触只滚动，横向接触不输出纵向抖动；较大的模糊斜向轨迹按滚动处理。
阈值使用 SDK 指针增益前的位移，不随用户调节速度而改变。缺少接触开始的残留位移不触发导航。

识别提示沿用原有「手势触发提示」设置，增加「双指左滑 · 前进」与「双指右滑 · 后退」卡片。
提示表示识别结果，不代表目标应用已经完成页面切换。

指针速度、反转指针上下方向、滚动速度及上述新开关均可运行中修改并保存。
速度和指针方向通过现有指针输出通道在下一次位移发送前更新，包括按住拖动期间，
无需停止或重连触摸板。旧协议的指针速度/方向也按相同方式在输出线程更新。
本次使用模拟输入、原生发送替身及 1440×940 / 940×700 的真实 QML 渲染检查；
未以真实戒指验证横滑阈值手感，未重建 DMG。源码应用需重启加载本次代码。
相关回归 **373 passed**（49.14 秒），覆盖横滑去重与轴隔离、提示卡片、
实时设置保存/恢复、拖动中调速、原生 IPC 的参数更新及导航过期/前台变化拒绝，
并包含既有点击、滚动、笔画、旧协议、原 SDK 文件一致性及快捷键平衡释放检查。
界面截图保存在 `outputs/touchpad-navigation-20261109/`。

## 轻点后按住拖动适配（2026-11-09）

macOS Raw2 指针通道现在接收 SDK 的 `BEGIN / CLASSIFIED / END / CANCEL`。
普通单指按住只移动光标，不按住左键。第一次 `down → up` 必须由 SDK 确认为轻点，
随后在 250 ms 双击窗口内再次 `down` 才开始拖动；移动发送 `LeftMouseDragged`，
最后 `up` 松开左键。`BEGIN` 的指头类型可能为 `UNKNOWN`，此时等待 SDK 判定为
`SINGLE` 再按下，避免双指滚动误触左键。

「鼠标设置」增加独立的「轻点后按住拖动」开关，默认开启，运行中可修改并保存。
关闭后仍保留普通移动和 SDK 单击/双击；拖动中关闭则立即松开左键。
启用时使用 SDK 轻点预览发送第一次单击，正式 click 不再重复发送，
也不会在第二次长按中途补发单击而打断拖动。
启用双击时第二次按下/松开的点击次数为 2；关闭双击时为 1。
拖动开关独立于单击和双击开关，双指滚动、右键及截图画布点击继续走各自路径。

延迟的 up 仍执行释放。停止、断连、会话重置、暂停、切换输入上下文或切换点击/拖动选项会
取消当前按住；原生子进程在停止管道关闭及正常终止时也释放自己的左键。
供应方 `ring_mac_sdk` 文件不改动，适配位于主项目的 Raw2 事件、鼠标输出和原生输出层。
`tests/test_touchpad_drag.py` 使用真实 SDK 识别器的普通移动、轻点后长按拖动、连续双击序列，
经 Raw2 适配到模拟 Quartz 输出验证上述行为；
这不代表真实戒指手感验收，也未重建 DMG。运行中的源码应用需要重启加载修改。
本次相关回归 **386 passed**，覆盖普通接触移动、轻点后拖动、双击去重、
运行中关闭拖动、异常释放、实时设置保存/恢复及供应方 SDK 文件一致性；
1440×940 / 940×700 真实 QML 渲染检查和 `git diff --check` 均通过。
界面截图保存在 `outputs/touchpad-tap-drag-20261109/`。

## 0.3.0 文件与来源

| 位置 | 内容 |
| --- | --- |
| `src/ring_mac_sdk/` | wheel 中全部 21 个包文件，逐字节保留：Python、模型、原生库、网页、许可证及类型标记 |
| `third_party/ringo-touchpad-delivery-20261105/` | 原始交付包全部 12 个文件，含 wheel、固件、文档、工具、清单和哈希 |
| `requirements-ring-mac-sdk.txt` | 独立 SDK 环境安装入口，使用原 wheel 的 web/cursor extras |
| `tests/test_ring_mac_sdk_delivery.py` | 离线完整性、协议、生命周期、模型、光标映射、校准及模拟页面检查 |
| `src/ring_python_sdk/touchpad/raw2.py` | 新接收端与主项目事件/笔画输出的适配层 |
| `src/ring_python_sdk/session/sdk_transport.py` | 以供应方 Transport 管理主项目 BLE 连接、INFO 校验和统一控制入口 |
| `src/ring_python_sdk/session/control.py` | 把旧接口的回复等待纳入同一控制事务，隔离断连前后的排队请求 |
| `tests/test_ring_sdk_connection.py` | 新版真实连接流程、统一命令调度、断连/旧通知隔离检查（模拟 BLE） |
| `tests/test_touchpad_raw2_integration.py` | 新协议到产品鼠标、笔画轨迹、MIC 共存及异常收尾检查 |
| `outputs/ringo-firmware-20261105/` | 单独提取的 OTA ZIP、签名 BIN、manifest、build-info 和使用说明（本地交付目录） |

供应方 SDK 版本：**0.3.0**；提交：`4769eaebad8755e8d679305c5b459124a5b38df7`。

- 原始外层 ZIP SHA-256：`9667923a3cfae039a6aa3751fced86b6ce7934917a79410d328f27e94487bd80`
- SDK wheel SHA-256：`2cbd85231a27eb4c9f13b22ad246a41f23c705217c2f90e3ea9fba30a31d97d9`
- 完整来源记录：[release-manifest.json](../third_party/ringo-touchpad-delivery-20261105/release-manifest.json)。

## 相对于当前项目触控板路径的变化

| 能力 | 新版提供的内容 |
| --- | --- |
| 新触控板协议 | `0x3C v2`；200 Hz 采样、100 Hz token，每 token 为 8 维 int8 特征；支持单包和 2～15 token 批包 |
| 新配套后端 | 原加密运行资产、原生模型加载库、ONNX Runtime 1.21.0 CPU 推理；校验模型身份 `0x77762a23`，维护循环状态 |
| 单指/双指 | `Motion.mode` 可区分 SINGLE、TWO_FINGER、UNKNOWN；可选输出层将双指纵向位移映射为滚动 |
| 点击与接触事件 | `ContactUpdate` 的 BEGIN/CLASSIFIED/END/CANCEL；左键单击/双击、右键单击；点击预告与正式点击分开，`click_id` 去重 |
| 会话管理 | 所有交互事件携带 session/generation/sequence/source time；epoch/reset 清空状态并重新预热；序号缺口、输入超时和积压会结束流 |
| 可选系统输出 | `SystemCursor` 管理权限、队列、停止和失效；倍率可调，正式点击默认关闭；不提供鼠标按住/拖拽接口 |
| 独立调试页面 | 中文 Ring Studio、扫描/连接/启停、电量、轨迹与事件展示、独立页面倍率和系统倍率、无硬件 demo |
| 配套工具 | `ring-mac` CLI、`tools/touchpad_events.py`、dev9 陀螺仪零偏查询及显式重校准工具 |

设备信息、电量、固件手势和陀螺仪校准在旧 SDK 中已有相应能力，本次增加的是新版入口和配套工具，不把它们全部算作从无到有的新功能。校准工具严格限制 Ringo 硬件 v1 和本包 dev9 固件，并不是新增的通用 `Ring.calibrate()` 公开 API。

新版手势接口同时包含 R1 的低功耗/佩戴手/滑动方向设置，但 **Ringo 当前只支持旧式 Swipe START/STOP**，SDK 会拒绝其余设置。这里没有新增可确认的 Ringo 固件手势类别。

API 细节：[供应方 API](../third_party/ringo-touchpad-delivery-20261105/docs/API.md)；校准条件与操作：[CALIBRATION.md](../third_party/ringo-touchpad-delivery-20261105/docs/CALIBRATION.md)。

## 当前业务与界面

Apple Silicon Mac 的触摸板默认使用 `0x3C v2` 和配套 ONNX 接收端。macOS 主业务使用 `RingSession(connection_backend="sdk")`，由新版 `Transport.connect()` 创建唯一 BLE 连接、验证 NUS/INFO 并订阅通知。适配入口 `ring_python_sdk.touchpad.raw2.Raw2Stream` 直接使用该 Transport；音频和旧外设保留处理器，通过同一个连接收发。供应方 `src/ring_mac_sdk/` 的 21 个文件保持原样。

鼠标设置新增四个独立开关：左键单击、左键双击、右键单击、双指滚轮翻页；均默认开启、保存到 QSettings，触摸板开启期间可以即时切换。滚动速度独立于指针速度。双指只产生纵向像素滚轮事件，横向位移不滚动，也不移动指针；关闭双指滚动时忽略双指动作。单指/未分类动作维持移动指针。

开启设置中的「手势触发提示」后，右下角卡片也显示左键单击、左键双击、右键单击和双指纵向滚动。新版提示遵循触摸板及各功能开关，并在会话、过期和去重检查之后发出；连续滚动同一会话每秒最多提示一次，零纵向位移不提示。卡片沿用 1 秒独立消失、不抢焦点的行为，只表示识别到手势，系统点击是否发送仍以点击日志回执为准。

新版最终左键单击输出一组 down/up，左键双击输出两组且系统 click state 为 1、2，右键单击输出右键 down/up。预告不点击，确认后的双击不再进入旧的主机双击配对，也不再触发笔画入口。原截图/窗口选择器的输入所有权限制保留，截图期间左键用于画布操作，右键不会点击底层应用。

会话重置、断流检测、过期丢弃和点击去重自动启用，不提供关闭这些保护的开关：

- 新 session/generation 清空鼠标待发队列和旧点击身份；旧代次事件丢弃。点击按 SDK session/generation/click_id 去重，原生进程也防止重复派发。
- SDK 对序号缺口、非法 epoch、输入超时、模型异常和输入积压结束当前流。产品指针队列溢出也停止输出，不继续使用不完整事件。
- 位移从来源时间起限 200 ms；确认点击从正式产生时间起限 250 ms（不把供应方等待双击配对的时间误算作主机排队）。IPC 内继续携带绝对截止时间，原生进程在实际发送前再检查。
- 手动停止、设备断开、权限失效继续使用现有即时输出闸门。已经交给系统的一次输入调用不能撤回。

旧 SDK 的音频、固件手势、PPG 等接口保留。触摸板启停不会主动关闭 Ring/电脑麦克风；仅实际笔画会话仍独占 IME。笔画输入沿用显式书写区入口，在同一 Raw2 流上使用每帧完整轨迹和原笔画接触/轻触判定，包含零位移帧，避免用稀疏指针事件拼接笔画。笔画准确率仍需真机验证。

Windows/Intel 未获得供应方新版模型支持，继续走 legacy / MNN 兼容路径，新版专属开关在不支持的平台禁用。独立调用 `RingSession.touchpad_on()` 默认仍为 legacy；使用 `protocol="raw2"` 必须先通过 `connection_backend="sdk"` 建立新版连接，不再借用旧客户端伪造就绪。产品控制器在支持平台显式传 `protocol="raw2"`。旧固件不会被静默回退到另一模型，需升级配套固件后使用新版。

项目 macOS 依赖与原交付对齐为 `bleak==1.1.1`、`onnxruntime==1.21.0`；Windows 保留原 Bleak 3.0.1 锁定。PyInstaller 配置已加入新版模型、原生库和 ONNX Runtime，包自检覆盖新旧模型。本次未重打 DMG、未替换 `/Applications` 中的应用。

## 独立环境与运行

以下是供应方独立 CLI 的环境，位于 `.build/ring-mac-sdk-venv`。产品 `.runtime/venv` 和 `.build/packaging-venv` 现已同样使用 Bleak 1.1.1 / ONNX Runtime 1.21.0，并保留主业务的 NumPy 1.26.4。独立 CLI 仍建议使用独立环境，避免把网页/光标演示依赖引入产品运行环境。

在项目根目录运行：

```bash
.runtime/venv/bin/python -m venv .build/ring-mac-sdk-venv
.build/ring-mac-sdk-venv/bin/python -m pip install -r requirements-ring-mac-sdk.txt

# 原始 wheel 提供的 CLI：离线模型检查，不连接设备。
.build/ring-mac-sdk-venv/bin/ring-mac model-check

# 使用主项目内的同一份源代码，显式启动模拟页面。
PYTHONPATH=src .build/ring-mac-sdk-venv/bin/python -m ring_mac_sdk.cli ui --demo

# 真机页面（启动后仍需手动选择设备并开始）。
PYTHONPATH=src .build/ring-mac-sdk-venv/bin/python -m ring_mac_sdk.cli ui --open
```

供应方校准脚本和事件示例保留在 `third_party/ringo-touchpad-delivery-20261105/tools/`，使用独立环境的 Python 执行。校准前请先阅读原校准文档；本次未连接戒指、未清除零偏、未发送校准命令。

## 固件

已单独提取：

- [ringo-touchpad-dev9.zip](../outputs/ringo-firmware-20261105/ringo-touchpad-dev9.zip)：原 OTA 包，444,045 字节；供应方原件也保留在 [firmware/](../third_party/ringo-touchpad-delivery-20261105/firmware/)。
- [ring-firmware.signed.bin](../outputs/ringo-firmware-20261105/ring-firmware.signed.bin)：签名应用镜像，646,048 字节；升级工具要求 BIN 时使用。
- INFO 目标版本：`2.1.6-dev.touchpad.9.debug`；MCUboot 镜像版本：`2.1.6+0`。
- ZIP SHA-256：`16f0eeae2d4634f6f3b82b9b35fe6f58eea569be9ec4918bf878bdd0302d1411`
- BIN SHA-256：`3ac1bce6dcdb525e1fa9b8f8e0c25b927c624a74230b9225790d141cdcf17411`

这是 Ringo nRF54L15 / 硬件 v1 的应用测试固件，使用 legacy 应用分区和相应签名配置。不能给 R1 使用；已迁移私有/加密 bootloader 的戒指需先确认兼容。原始 `build-info.json` 保留旧构建来源及 SDK 组件记录，未修改为本次 SDK 版本。

升级流程以[交付包安装文档](../third_party/ringo-touchpad-delivery-20261105/docs/INSTALL.md)为准。本次只提取和校验文件，没有刷写或验证用户戒指上的兼容性，也没有 MIC/Swipe 与新触控板并发验收。

## 首次纳入 SDK 时的验证记录

- 本机验证环境：macOS 15.7.5 / arm64 / Python 3.11.16；独立环境 Bleak 1.1.1、ONNX Runtime 1.21.0、NumPy 2.4.6、aiohttp 3.14.5。未进行真实 macOS 13 验收。
- 交付清单的 10 个文件大小/哈希、`SHA256SUMS`、wheel RECORD 以及全部 21 个 vendored 包文件均核对通过。
- 独立环境中 `tests/test_ring_mac_sdk_delivery.py`：**11 passed**。覆盖首次同步 token、序号缺口停止、会话清理、单/双指映射、正式点击去重、校准显式开关、模拟页面不访问 BLE/系统输入，以及真实原生模型加载、有限输出和 reset 后确定性。
- 主项目原环境运行最终版新增检查：**10 passed、1 skipped**，仅真实模型检查因原环境未安装 ONNX Runtime 而跳过。
- 源码与原始已安装 wheel 的 `model-check` 均成功；CPU 后端、1,216 个循环状态值，模型 SHA-256 校验通过。`pip check` 无依赖冲突。
- 从干净临时源码副本构建主项目 Python wheel，全部 21 个新版 SDK 文件与供应方原件逐字节一致，确认原生库、模型、网页和许可证均未漏包。此项不是 DMG 构建或安装包运行验收。
- 旧项目相关回归首次运行：**70 passed、1 skipped、1 failed**（当时新版检查为 8 项；主环境未装 ONNX Runtime，因此模型检查跳过）。失败为 `test_stroke_sdk.py::test_contact_metadata_does_not_displace_pointer_events`，它预期队列只含 click，现有业务代码实际存储 `(click, ticket, trace)`；单独运行仍复现，相关源码与测试均与 HEAD 相同。
- 供应方说明中的 109 项测试及 512-token 参考回放属于交付方验证记录，本包未附那些测试/参考样本；不作为本次重新执行的结果。真实手势准确率、戒指固件升级和长时间并发运行未测试。

## 业务接入验证（2026-11-06）

- 触摸板、鼠标、笔画会话、音频共存、QML 和打包相关整组回归：**306 passed**；随后针对「启动时关闭单击，仍可使用双击/右键」的补充与相关输出/UI 回归：**41 passed**。`git diff --check` 与主环境 `pip check` 通过。
- `test_touchpad_raw2_integration.py` 验证共享 BLE 连接和 MIC 分流、epoch 重置、序号缺口、无数据超时、队列溢出、START 中停止、四个独立开关、鼠标双击/右键/滚轮、重复点击和最终发送截止时间；全部使用模拟 BLE/Quartz，不触碰真实鼠标。
- QML 测试在 1440×940 和 940×700 下实际点击四个开关，检查运行中生效、持久化和布局；截图在 `outputs/raw2-touchpad-ui/`。
- 主运行环境加载真实新版模型成功；打包环境能收集 `ring_runtime.bin`、`libring_touchpad_model.dylib` 和 ONNX 原生库，并通过新旧模型自检。
- 已修正旧 `test_stroke_sdk` 对内部队列结构的过时断言，使其继续验证「接触元数据不挤占指针队列」的原意。
- 新版元数据只放在 `Raw2PointerMove`、`Raw2PointerClick`、`Raw2Reset`，旧事件序列化与三组录制回放哈希保持不变；Windows 测试明确注入模拟平台与 User32，不在 macOS 调用真实 Win32 API。
- 未执行戒指刷写、真实 BLE 吞吐/手势准确率/滚轮手感验收，也未生成新 DMG。供应方说明中 MIC/Swipe 与触控板并发仍未全面验收；主机不主动停音频不等于固件并发已通过实测。

## 2026-11-06 实际启动失败与恢复

- `logs/diagnostic.log` 的源码运行 `15db4ecf`：15:12:40.766 开始鼠标控制，15:12:44.482 报 `Input/inference timeout`，未进入 RUNNING。普通手势接收计数停在 657；重连后先收到 5 个包，15:13:31.845 自动恢复触摸板后再次停止增长。电量仍有回复。CSV 与日志计数一致，问题发生在设备数据流到达主机这一侧，不能解释成 UI 没执行已经收到的手势。
- 供应方把首包等待（3 秒）、后续断流（1 秒）、模型加载（10 秒）和推理调度（200 毫秒）的超时合并为同一文案。旧日志没有 Raw2 ACK/包计数，不能据此断言是哪一次等待；时间形态更符合首包缺失。本机独立模型复测加载 182 毫秒、200 步中位数 1.843 毫秒、最大 4.210 毫秒，不代表出错时进程的调度情况。
- 适配层新增 `RAW2_CONTROL`（START/STOP ACK、固件状态计数）、`RAW2_INPUT`（首包）和 `RAW2_STATE`（失败阶段及推理数量）日志，并分别显示首包、断流、加载、推理超时。原供应方包与时间预算保持不变。
- 修复重复 `stop_now()` 取消正在等待 STOP ACK 的清理任务；Raw2 停止确认后，对仍开启的普通手势执行一次有界 STOP/START，保留原回调和麦克风。未确认 Raw2 停止时不抢占设备，记录恢复失败。恢复命令发送完成后仍显示 `recovering`，收到新的手势包才视为恢复确认。
- Raw2 协议/数据流失败后，本次应用运行内不再在重连或解锁时自动重启触摸板；保留用户设置，允许手动重新开启。这样重连恢复普通手势不会立即再次触发同一个启动故障。
- 上述为主机恢复及诊断修复。固件为什么没有持续输出 Raw2、MIC/Swipe 并发是否可用，仍需重启源码应用后的真实戒指状态日志确认，不能视为已解决固件并发或完成实机验收。
- 修复后相关回归 **132 passed**（29.19 秒）：延迟 STOP ACK 时重复停止、缺失首包、真正推理超时、手势恢复确认/失败、重连不自动重试及手动重试、输入偏好、音频、QML、旧协议与原始交付完整性；`git diff --check` 通过。测试使用模拟 BLE，不替代真实戒指并发验证。

## 蓝牙连接改为新版 SDK 管理

- 按用户要求移除旧 `SharedTransport`：不再赋值 `ready=True`、拼默认硬件身份或重写底层 close。连接、NUS 特征验证、通知订阅、INFO 解析、连接代次、写超时后的真实断连均执行原版 `Transport` 实现。供应方目录继续逐字节保留。
- 产品在 `SessionTransport` 中统一串行调度 `request` / `send`，覆盖 ACK 等待，避免多业务争用时各自发送。普通手势调用供应方 `GestureClient`，保留 Ringo 无业务 ACK / R1 有 ACK 的区分。电量/INFO 使用供应方解析；MIC 和旧外设通过同一 Transport 的 `send` 发送，不直接访问 Bleak。旧接口中另行等待回复的操作也持有这一事务队列，详见下节。
- 单个蓝牙通知订阅先由 SDK 匹配控制回复，再把未匹配数据分发至音频、手势和触摸板处理器。Raw2 不再把通知重新塞回 Transport。故障后的正常 Raw2 STOP 不断开音频；真实写入取消/超时遵循原 SDK，使整条不确定连接失效。
- macOS 主业务默认选择新版连接，旧 SDK 独立调用和其他平台的兼容连接保留。INFO 不合法或服务不匹配时直接失败，不悄悄回退旧握手。
- 真实戒指是否能在此连接路径下产生 Raw2 首包仍须运行验证；已运行进程需重启，不将模拟连接测试作为实机成功。
- 本次新版连接修改后相关回归 **336 passed**（64.57 秒），包含实际供应方连接方法的模拟 BLE 测试、命令并发、INFO/NUS 拒绝、旧连接通知隔离、写入取消后的连接失效，以及音频/手势/触摸板/UI/打包回归。原交付文件一致性通过；主运行与打包环境 `pip check` 通过，Bleak 1.1.1 的 CoreBluetooth 后端及源码模块导入通过。未重打 DMG或替换正在运行的进程。

## 旧控制接口的完整事务调度

进一步检查发现，原先统一了蓝牙写入，但时间、温度、身份、录音和健康记录接口仍在 `send` 返回后，于队列之外等待旧协议回复。这可能使其他控制命令插入等待窗口；两个相同查询还会清空彼此共用的回复状态。

- 上述操作现在在清空回复状态之前取得同一 `control_transaction`，直至回复、超时、报错或取消才释放。分包读取等待结束包；下载的每个读取请求独立排队。无业务 ACK 的 MIC 等控制仍按原协议在蓝牙写完成后结束，不添加不存在的 ACK。
- 事务仅允许同一个 asyncio Task 重入，支持身份验证中嵌套查询；并发任务仍需排队。断连终止当前旧回复等待，已排队操作绑定原连接代次，不能在新连接中继续发送。
- 数据通知不取得控制队列锁，MIC、普通手势和 Raw2 数据仍各自分流，不因控制回复等待而暂停音频。Windows 等 legacy 连接的行为保留。
- 模拟 BLE 回归覆盖时间查询与同步结果隔离、Raw2/MIC 排队、健康记录分包与音频并发、超时/取消/设备错误、嵌套身份查询、排队取消及重连后的旧命令拒绝。供应方 21 个文件仍逐字节一致。尚未进行真实戒指复测或重建 DMG，运行中的应用需重新启动加载修改。
- 本次修改后主业务相关回归 **344 passed**（65.67 秒），包含 SDK 连接与协议、触摸板、手势、音频、鼠标输出、QML、输入偏好和打包入口；`git diff --check` 通过。

## dev9 启动顺序的实机定位与修复

用户 16:05–16:06 的最新运行 `a6be0497` 已使用新版 Transport。两次 START 均返回 state=1/result=0，但 3 秒内没有 Raw2 首包，随后 STOP 的 frames/tokens/sent 均为 0；不是模型已经收到数据却计算超时。用户断开主程序后，使用独立 wheel 环境的原版 SDK 对同一只 Ringo8F56 做了以下对照，全程不输出系统鼠标事件：

| 启动组合 | 3 秒观察结果 |
| --- | --- |
| 只开 Raw2 | RUNNING，305 个主机推理 token |
| 先开 Swipe，再开 Raw2 | START 成功但 Raw2 为 0，首包超时；复现主项目故障 |
| 先开 Raw2，再开 Swipe 和 MIC | RUNNING，310 个 token，收到 28 个手势包和 56 个音频包 |
| 只先开 MIC，再开 Raw2 | RUNNING，325 个 token，收到 78 个音频包 |

由此确认这只戒指的 dev9 对启动顺序敏感，单纯串行化控制写入不足以解决。此证据没有定位固件内部具体哪条 IMU 引用/任务路径出错；交付包不含固件源代码。

兼容仅作用于 Ringo 硬件 v1、INFO 版本 `2.1.6-dev.touchpad.9.debug`：若普通手势已开启，先通过统一 SDK 控制入口发送 Swipe STOP，等待 300 ms，再使用原版触摸板流程等待首个 token；随后恢复 Swipe START。保留原手势处理器、回调和用户设置，MIC 不暂停。取消启动、模型加载失败时也会恢复被暂停的手势；已发 Raw2 控制而 STOP 未确认时，仍保留原有失败保护。原供应方 SDK 文件不修改。

修复后的真实主项目 `RingSession.connect_device`、`swipe_on`、`mic_on`、`touchpad_on/off` 连续运行两轮，每轮观察 5 秒：

- 两轮均保持 RUNNING；主机观测分别为 526 / 516 个 token，紧随其后的固件 STATUS 分别为 531 / 520 个 token，sent 等于 tokens、dropped=0。
- 手势包由 6 增至 56，再由 58 增至 107；音频块在两轮内持续增长，总计 143 块。两次停止后的 `touchpad_error` 均为空。
- 修复相关回归 **348 passed**（69.28 秒），包含启动顺序、MIC 保持、取消/用户停止/模型失败后的手势恢复，以及已有协议、UI、鼠标和打包测试；原始 21 个 SDK 文件一致性通过，`git diff --check` 通过。

脚本与完整计数日志保存在 [实机对照目录](../outputs/ringo-raw2-start-order-20261106/)，主项目修复后的记录为 [ringo-product-fixed.jsonl](../outputs/ringo-raw2-start-order-20261106/ringo-product-fixed.jsonl)。测试已释放连接；临时音频/传感器文件随测试目录清理。未刷写固件、未重建 DMG；此次实测确认短时三路数据流与重复启停，不代表长时间稳定性、真实点击/滚轮手感或手势类别准确率全部验收。
