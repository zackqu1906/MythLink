# 桌面安装包

产品和安装包名称统一为 **Mythlink**，定位为融合语音输入、鼠标控制、手势识别与场景联动的戒指交互平台。
构建入口为 `packaging/mythlink.spec`，Windows 安装器配置为 `packaging/windows/Mythlink.iss`。

为兼容已有安装，用户数据与模型缓存目录、`QSettings` 命名空间、macOS bundle / 输入源标识及
Windows 安装器 AppId 保持原值；Python 内部包名 `proximic_ring` 和 `PROXIMIC_*` 环境变量继续有效。
发行包名称为 `mythlink`，推荐命令为 `mythlink`、`mythlink-ui`，旧命令保留为兼容入口。
GitHub 仓库与下载链接沿用实际地址；历史构建记录中的产物路径保留原名。

## 输入法验收隔离

打包自检使用临时 `PROXIMIC_IME_SOCKET` 时，不能通过 `open` 预热系统已安装的输入法。
`open` 启动的常驻输入法可能继承测试环境，临时目录删除后仍不断连接旧地址，造成
正式主程序中 tap 已切换输入法、但始终不能开始听写。2026-09-21 已据进程环境和
真实日志确认此问题。现在 `warm_input_method()` 在设置了独立通信路径，或运行
`--self-check-package` 时直接跳过系统组件启动；正常运行的后台预热保持原样。
测试自己的输入法客户端须显式启动并自行清理，不能借系统常驻组件验收临时端点。

Mythlink 使用“轻量主安装包 + 模型按需下载”：安装包包含 UI、Ring SDK、
ASR/推理依赖和 16 kHz ADPCM 解码器，不包含 ASR 权重及约 2.5 GB 的本地 GGUF。
首次使用相应功能时，ASR 框架下载权重；本地文本模型由设置页的“下载本地模型”按钮下载。

推荐功能仅构建 Computer History 实现。`packaging/legacy_workflows.py` 排除旧 Workflow
提取、语义归组、参数生成、推荐与试验执行模块、BGE 安装器/编码器及其 XLM-RoBERTa
架构和模型清单。源码保留以便回查，历史任务和已保存 Workflow 的只读查看仍可用；
旧 worker 参数在安装包中返回停用状态。构建自检验证 Computer History 导入，并拒绝
仍包含旧推荐核心模块的包。

这项调整不会删除用户已下载的 BGE 模型或历史记录。约 2.3 GB 的 BGE 权重从未内嵌
安装包，因此移除旧推荐不能节省这部分安装包体积。Torch、Transformers、Qwen3 等
仍用于本地 ASR，继续保留。实际 DMG 大小以重新构建、压缩后的结果为准。

所有可变文件都放在用户目录，应用安装目录保持只读：

- Windows：`%LOCALAPPDATA%\ProxiMic Voice`
- macOS：`~/Library/Application Support/ProxiMic Voice`
- macOS 模型缓存：`~/Library/Caches/ProxiMic Voice`

本地模型下载会在镜像和官方地址之间自动重试、从 `.part` 断点续传，并在启用前校验
清单中的文件大小和 SHA-256。Windows 与 macOS 各自下载匹配平台的 llama.cpp，GGUF
模型在两个平台共用同一份版本。若两个地址最终都被当前网络拦截，失败信息会保留断点；
更换网络或启用能访问 Hugging Face 文件 CDN 的代理/VPN 后，再点一次即可继续。

Windows 的 GGUF 断点默认位于：

```text
%LOCALAPPDATA%\ProxiMic Voice\local-llm\models\qwen3-4b-instruct-2507-q4-k-m\Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf.part
```

不要手工删除这个文件，除非 UI 明确报告大小或 SHA-256 校验失败。

## Windows x64

先准备 CPU 运行环境（不下载本地 LLM），安装 Inno Setup 6，然后构建：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Compute cpu -SkipLocalLLM
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1
```

产物：`dist/installer/Mythlink-<版本号>-windows-x64-setup.exe`。安装器为当前用户
安装，不要求管理员权限，并创建开始菜单入口，可选桌面快捷方式。

公开分发前可通过证书存储中的代码签名证书为安装包签名：

```powershell
$env:WINDOWS_SIGNING_CERT_SHA1 = "证书指纹"
$env:WINDOWS_TIMESTAMP_URL = "http://timestamp.digicert.com" # 可省略
.\scripts\build-windows-installer.ps1
```

脚本会调用 Windows SDK 的 `signtool.exe`，完成 SHA-256 签名、时间戳和签名校验。
未配置证书时生成的安装包功能不受影响，但其他电脑可能显示 SmartScreen 来源警告。

## macOS Apple Silicon

当前产物支持 Apple Silicon（arm64）和 macOS 15 及以上。构建机需要 Xcode Command
Line Tools；脚本会把固定版本的 Python 3.11 和构建工具安装到项目目录，不要求
Homebrew，也不会修改系统 Python：

```bash
./scripts/build-macos.sh
```

每次本地 build（包括 `--app-only`）都会提示输入主应用版本号，默认把
`pyproject.toml` 中版本号的最后一段加 1，例如 `0.6.9` → `0.6.10`；回车采用默认值，
也可输入三段数字版本。选定后立即写回 `pyproject.toml`，作为下次默认值的基准；
即使后续打包失败也保留此次选择，重试同一版本可显式传入 `--version 0.6.10`。
macOS 的包信息、应用设置页与安装包文件名均读取这一版本；输入法版本独立管理。

无交互终端时须使用 `--version X.Y.Z` 或 `--auto-version`，也可设置
`MYTHLINK_BUILD_VERSION`。Windows 对应 `-Version X.Y.Z` / `-AutoVersion`。
GitHub tag 构建使用 tag 的数字版本（如 `v0.6.10`），其他 `CI=true` 构建使用默认递增值。

产物：`dist/Mythlink-<版本号>-macos-arm64.dmg`。构建脚本把 Python 运行时
复制进 `.app`，用户电脑不需要安装 Python、Homebrew 或其他运行库。生成 DMG 前，脚本会实际启动冻结后的
可执行文件执行 `--self-check-package`，验证内置 ADPCM 解码器、全部必需 QML 模块、QApplication、
控制器、ASR 导入、QML 根窗口和通常按需加载的 Agent 工作区（含 Shapes 图标插件）。
检查会创建隐藏的 Agent 窗口，不会发起任务；依赖缺失或窗口加载失败会直接终止构建，详细信息写入
`.build/macos-smoke-data/logs/diagnostic.log`。

Agent 卡片交互修改还须在真实 macOS 窗口上运行以下验收，先验证构建产物，再验证挂载后的 DMG 内程序：

```bash
QT_QPA_PLATFORM=cocoa dist/Mythlink.app/Contents/MacOS/Mythlink \
  --self-check-agent-ui --output-dir .runtime/agent-ui-acceptance --cycles 12
```

该入口使用临时数据、设置和独立 IME socket，不连接戒指、不向 Codex 提交任务。它加载真实 Chromium
富文本并反复切换到纯文本，检查正文、编辑器和按钮不会触发卡片拖动，背景可以拖动，展开/折叠卡片
可以关闭，退场中的卡片不再处理拖动，同时检查主窗口背景的鼠标事件。输出包含退出状态、
`report.json`、富文本截图和诊断日志。只有退出码为 0 且报告 `passed: true` 才通过；
`--self-check-package` 的隐藏窗口启动检查不能替代这一项。

要让下载后的应用在其他 Mac 上无警告打开，构建机必须配置 Developer ID：

```bash
export APPLE_SIGNING_IDENTITY="Developer ID Application: MythLink (Beijing) Technology Co., Ltd. (5H42RU3M38)"
export APPLE_NOTARY_PROFILE="proximic-notary"
./scripts/build-macos.sh
```

构建默认使用上述 MythLink 正式身份（Team ID `5H42RU3M38`），证书不可用时直接失败，
不会退回 ad-hoc。仅限开发验证可显式设置 `PROXIMIC_ALLOW_ADHOC_SIGNING=1`；此时若未
指定身份则生成临时签名包，不能作为正常升级包。应用的 `Info.plist` 已包含蓝牙和麦克风用途说明。

证书、私钥及密码不得提交到仓库。导入 `.p12` 后，若 `security find-identity -v -p codesigning`
没有有效身份，检查是否已从 [Apple PKI](https://www.apple.com/certificateauthority/) 导入
Developer ID G2 中间证书；不要设置“始终信任”来替代完整证书链。
可用 `APPLE_SIGNING_KEYCHAIN` 指定专用钥匙串；本机路径可放在被 Git 忽略的 `.env.macos-signing`，
脚本会自动加载，文件内不要保存密码。钥匙串锁定后需先解锁。Apple 公证另需
`APPLE_NOTARY_PROFILE` 对应的 notarytool 凭据；仅有 `.p12` 不能完成公证。

### 输入法随包安装（2026-09-18）

macOS 构建先编译 Swift 输入法和 `InputMethodAdmin`，再将两者完整放入 app 的
`Contents/Helpers` 并签名。组件最低版本与主程序统一为 macOS 15；默认与主程序使用同一正式身份。
`--self-check-package` 会核对组件身份、可执行文件及签名，缺失时构建失败。

输入法根据自身 Swift 源码、资源、Info.plist、构建脚本及编译器 / SDK / 目标配置的
内容指纹管理 `CFBundleVersion`。这些输入不变时，重复构建保留原版本；只有输入变化后
成功构建才递增，例如 `1.0.0 (2)` → `1.0.0 (3)`。主应用代码或主应用版本变化不会
触发输入法升级。`CFBundleShortVersionString` 是输入法正式发布版本，在发布时调整。
`scripts/build-input-method.sh` 和整包构建调用同一流程，从上一份构建和已安装组件的
最高构建号继续；清理构建目录后仍可从已安装组件恢复指纹和版本。旧组件没有指纹时，
首次迁移需要升级一次。编译、签名与校验在临时目录完成，成功后才替换可安装组件；
失败保留上一份产物和构建号，并发构建串行执行。此处构建号仅属于输入法组件。
冻结 app 从自身路径寻找安装资源，既不引用开发者源码目录，也不依赖用户安装 Python／Xcode。

主页会比较已安装输入法与当前 app 内附组件的版本：未安装或版本较旧时，显示提示和
安装／更新入口；启动、返回主页、主窗口重新激活及安装结束时重新检查，确认版本更新后
提示自动消失。已经安装更高版本时不提示降级，也不允许用较旧组件覆盖。
安装器复制到当前用户的 `~/Library/Input Methods/ProxiMicInput.app`，注册并启用。
更新时自动临时切换到系统键盘；旧组件如仍运行，会核对用户和完整路径后正常退出，
安装失败则回滚，更新完成后在目标仍有效时恢复输入源。
设置页“系统与权限 → 语音输入法”直接显示已安装版本与随应用提供的版本；
“通用 → 应用版本”显示当前主应用版本。同一设置窗口提供辅助功能和键盘设置入口。
仅当新 app 携带的输入法版本更高时，才需要从此入口更新系统中的输入法。
DMG 内附 `安装说明.txt`，说明连接戒指、服务配置、授权和手动切换步骤。

已有依赖环境且没有修改依赖时，可离线快速重建：

```bash
PROXIMIC_SKIP_DEPENDENCY_INSTALL=1 bash scripts/build-macos.sh
```

只更新 `.app` 并完成签名、便携性和启动检查，保留已有 DMG：

```bash
PROXIMIC_SKIP_DEPENDENCY_INSTALL=1 bash scripts/build-macos.sh --app-only
```

macOS 打包默认在最终签名前精简 10 个已验证运行库的调试和本地符号，当前依赖版本
约节省 84 MiB。工具 `tools/strip_macos_runtime.py` 对比处理前后的导出符号和动态链接
依赖，不一致即中止构建；体积明细保存在 `.build/macos-runtime-size.json`。
主可执行文件中的 Python 归档、ASR 依赖、ADPCM 解码器、输入法组件及其他模块均保留。
依赖升级导致库路径变化时会明确报错，需要重新检查名单，不能静默漏掉或扩大精简范围。

构建仍从当前 `src` 收集代码与资源，不复用已安装的旧项目包。打包启动检查使用独立
IME socket，避免占用正在运行的源码主程序连接。安装器诊断可使用打包可执行文件
`--input-method verify`／`status`；`--input-method install` 与界面共用同一安装实现。
临时签名包没有 Apple 公证，其他 Mac 下载后的 Gatekeeper 放行仍需用户按系统提示完成。

### 更新后的辅助功能权限

2026-10-10 起默认使用 MythLink 的 Developer ID Application 证书。主程序、输入法、
距离锁屏组件分别保留 `com.proximic.voice`、`com.proximic.inputmethod.ProxiMicVoice`、
`com.proximic.Presence` 标识。`InputMethodAdmin` 固定为 `com.proximic.InputMethodAdmin`。
正式构建在库精简后逐个从内到外签名所有 Mach-O、框架、辅助组件和主程序，开启
Hardened Runtime 和安全时间戳；主程序增加音频输入和 JIT entitlement。最终签名不使用
`codesign --deep`，避免漏签放在非标准目录中的 Python 扩展；`--deep` 仅用于校验。

`tools/sign_macos_bundle.py` 校验全部代码的 Team ID，并将四个组件的 designated requirement
写入 `.build/macos-signing-report.json`。后续构建须满足上次正式构建的身份要求，否则失败。
CI 的新工作区仍会检查固定 Team ID、组件标识和 Apple 正式签名要求。
这让正常覆盖更新通常可以保留辅助功能、蓝牙、麦克风等授权。首次从临时签名切换时，
旧的基于代码哈希的授权可能失效，需要重新授权一次，距离解锁的钥匙串访问也可能要重新确认。
屏幕录制的系统再次确认、用户撤销权限或系统策略变化仍可能产生提示，不能保证永久免授权。
证书续期应保持相同 Team ID、Developer ID 类型和组件标识，无需把证书序列号固定到要求中。
依据：[Apple 代码签名与更新身份](https://developer.apple.com/library/archive/technotes/tn2206/)。

输入法组件安装与主程序的辅助功能授权是两条独立流程。临时签名更新可能使旧授权不再匹配，
但无需每次都先删除权限：先完全退出旧进程、替换 `/Applications` 中的 app，再打开新版并
查看主界面的权限状态。重新授权后会自动检测，系统确认生效即可继续使用，无需例行重启。
若持续未生效，核对实际运行路径，必要时删除旧条目、添加新版 app。
不要从 DMG 或另一个同名副本继续运行。

主界面同时检测实际按键工作进程的 Accessibility 信任与按键发送权限，异常时持续显示警告。
按键通道通过同一个打包可执行文件的 `--native-access-worker` 入口运行，不启动第二个 UI/ASR 主机。
得到未授权结果就退出该工作进程，下次检查启动新进程，避免 CoreGraphics 在旧进程内缓存拒绝。
主程序、音频连接和输入法会话不因授权刷新而重启。源码运行使用同一 Python 解释器的模块入口。
检测在后台进行：启动和回到主界面时立即刷新，未授权时每 1 秒、已授权时每 10 秒刷新，
主窗口不在前台时仍持续检测。实际发键仍做即时检查，权限生效后下一次操作即可使用。
授权恢复不会自动重放之前失败的发送、编辑或撤销。系统授权状态的返回延迟不受程序控制；
检测恢复也不能修复临时签名变化造成的旧授权身份不匹配。
设置页提供授权入口、重新检测、显示当前 app 和复制诊断。诊断只含权限状态、进程与路径，
不含文本内容。程序不会自动清空 TCC 权限，也不会用 Accessibility 的成功结果绕过发键拒绝。
私有管道不开放网络或公开 socket；操作固定目标 PID，应用手势的 AX 对象留在工作进程内。
工作进程重建会使旧目标句柄失效，超时/断开不会自动重发按键。详细验证见 `docs/NATIVE_ACCESS.md`。

## 自动构建

`.github/workflows/build-installers.yml` 支持手工触发及 `v*` 标签构建两个平台。
GitHub Actions 手工构建默认也要求正式证书；只有明确勾选 `allow_ad_hoc` 才允许无证书测试包。`v*` 标签发布则会强制
校验 Developer ID 签名和 Apple 公证配置，缺少任一 Secret 都会终止发布，避免把不能在
新 Mac 上正常通过 Gatekeeper 的包上传到 Releases。仓库需要配置：

- `APPLE_CERTIFICATE_BASE64`：Developer ID Application 的 `.p12` 文件（Base64）。
- `APPLE_CERTIFICATE_PASSWORD`：导出 `.p12` 时设置的密码。
- `APPLE_SIGNING_IDENTITY`：完整的 `Developer ID Application: ... (TEAMID)` 名称。
- `APPLE_NOTARY_APPLE_ID`、`APPLE_NOTARY_TEAM_ID`、`APPLE_NOTARY_PASSWORD`：公证用
  Apple ID、Team ID 和 app-specific password。
- `APPLE_KEYCHAIN_PASSWORD`：可选的 CI 临时钥匙串密码。

- 手工触发：构建结果保存在 Actions Artifact，适合内部验证。
- 推送 `v*` 标签：两个平台构建成功后自动创建 GitHub Release，附带 `.exe`、`.dmg`
  和 `SHA256SUMS.txt`，用户可从 Releases 页面直接下载。

发布示例：

```bash
git tag v0.6.0
git push origin v0.6.0
```

公开下载页：<https://github.com/zackqu1906/ProximicVoice/releases>

## 发布前硬件验收

在干净的 Windows 10/11 x64 和 Apple Silicon macOS 15+ 上分别验证：

1. 安装/拖入 Applications 后正常启动，蓝牙权限提示文案正确。
2. 扫描、选择 Ringo、连接、ADPCM 连续音频至少 15 分钟、连续说 20 次，并验证单个 BLE
   音频块丢失后后续语音仍可继续；再测试断开后重连。
3. 首次 ASR 权重下载、重启后的缓存复用。
4. 本地模型下载中断后续传、校验完成、llama-server 启动和修改确认。
5. 数据、缓存和修改采集文件均落在用户目录，卸载不会误删用户模型/数据。
6. 清空 `ARK_API_KEY`、`VOLC_ASR_API_KEY` 环境变量后，分别在设置页填写线上大模型和
   线上语音模型 Key，重启应用确认配置仍在，并各完成一次真实请求。


## 2026-09-18 本地输入法整包验证

临时签名 DMG 已生成：`ProximicVoice-0.6.0-macos-arm64.dmg`（约 217.9 MiB）。
SHA-256：`6602c913cbf0524fbcbc5340c3a020947e15a68fd925b86b47e85650b6023d1e`。输入法二进制 SHA-256：`f358d0cb17edc0a15f709548981a4bf1696270708ff50a26f5f4b7c4553f10b5`。

- 79 项 Python 安装器／打包入口／输入法状态／快捷键／语音链路回归，以及 21 项 QML 界面回归通过。
- 整包临时签名、macOS 15 部署目标和动态库依赖检查通过；最新 QML 与源码一致，打包清单包含最新安装器、行内控制器、原生快捷键模块和戒指手势资源。
- DMG 校验和通过；从只读挂载的 DMG 运行冻结 app 的独立启动检查，Opus、QML、ASR 和内置输入法安装资源均通过。
- 从该 DMG 的冻结可执行文件调用同一安装器，在本机用户目录实际安装组件，系统确认注册及启用成功，未改变选中的输入源；安装后的二进制与 DMG 内组件一致。
- 以上不等于另一台干净 Mac 的验收。本包没有 Developer ID 公证；按用户要求保留本地临时签名。

## Public 固件音频与手势

ADPCM 使用纯 Python 解码，不需要 libopus。打包自检验证完整的 1600 样本
ADPCM 块可解码为 3200 字节 PCM16。生产安装脚本使用 `ring` extra。
手势只包含固件确认事件的解析和分发；电脑端手势模型、权重及参数不再打包。
Torch 仍用于近点检测与本地 ASR，不能作为手势模型残留删除。
