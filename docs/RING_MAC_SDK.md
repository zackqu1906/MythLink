# Ringo SDK 0.3.0 更新记录（2026-11-06）

本次以用户提供的 `ringo-touchpad-delivery-20261105.zip` 为准，将交付版完整纳入主项目，并按后续要求接入 Apple Silicon Mac 的触摸板业务和界面。新版并不是旧 `ring_python_sdk` 的同名替换包；它提供触控板、固件手势和设备连接接口，没有旧 SDK 的 MIC、PPG、健康记录等完整接口。

最新实机进展：2026-11-06 16:13–16:20 在用户的 Ringo8F56 上确认 dev9 的 Swipe / Raw2 启动顺序问题；增加限定版本的启动顺序兼容后，主项目接口连续两轮触摸板启停通过，详见文末。此前章节的“未真机验证”描述保留为当时的验证边界。

## 文件与来源

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
