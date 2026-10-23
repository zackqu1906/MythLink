# 浏览器扩展测试与发布

Chrome 与 macOS Safari 共享播放器识别代码。仅识别已授权网页中的标准 HTMLVideoElement / HTMLAudioElement，含可访问 iframe 和 open Shadow DOM；无需为网站标题、按钮文案写规则。暂停仍保留场景。默认播放、暂停、前后 10 秒、音量操作直接作用于选中的播放器；自定义快捷键与标签页切换继续沿用原有映射。

扩展不是对所有网页永远有效：closed Shadow DOM、纯 Web Audio、Canvas 自绘、浏览器内部页面、内置 PDF 阅读器、未授权 frame 不保证可见；网站可拒绝自动播放或覆写媒体状态。多个同等候选时先点击目标播放器。浏览器场景不再使用 AX 内容识别，未连接或不可访问时不报告媒体场景。浏览器原生窗口安全检查及其他输入功能仍使用系统辅助功能。

语音输入与笔画输入不依赖浏览器扩展或网站授权。共享输入手势通过系统辅助功能检查当前输入框及其窗口归属，地址栏、网页输入框和 contenteditable 编辑器均可直接使用。扩展的已授权焦点状态只补充网页根节点的信息；无法覆盖原生输入框、工具栏焦点或菜单、弹窗、失效焦点。扩展断开或网站未授权时，只停用媒体场景识别，不把已确认的输入焦点改成“未知”。

## 测试安装

1. 启动新版 Mythlink，进入设置 → 浏览器媒体控制。
2. **Chrome**：点击「安装指引」，先显示三步说明。点击「复制路径并打开扩展页」，程序准备扩展、复制文件夹路径，并打开 `chrome://extensions/`，无需另外下载 ZIP。开启右上角「开发者模式」，点击「加载已解压的扩展程序」，在文件选择器按 ⌘⇧G、⌘V，回车后选择文件夹。指引保留「复制扩展路径」「显示扩展文件夹」，以及打开失败时复制扩展页地址的备用入口。列表已有 Mythlink 时直接启用或重新加载；最后允许网站访问并刷新页面。此流程遵循 [Chrome 的本地扩展加载步骤](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world#load-unpacked)。
3. **Safari**：点击「启用指引」，按「打开 Safari 扩展页 → 勾选 Mythlink 浏览器助手 → 允许网站访问并刷新页面」操作。按钮通过 SafariServices 打开 Safari 设置中的扩展页。Safari 扩展随应用提供，不使用 Chrome 的选择文件夹流程；空列表时可展开「扩展列表里没有 Mythlink？」查看测试版开发许可步骤，见 [Apple 的运行说明](https://developer.apple.com/documentation/safariservices/running-your-safari-web-extension)。

安装、启用、连接和网站授权独立判断。Chrome 在后台只读取标准个人资料中本扩展 ID 的安装元数据，不读取浏览历史或其他扩展信息，也不修改浏览器偏好。已安装但没有心跳时显示「已安装 · 待连接」；安装记录可能来自另一个个人资料，因此开发版始终打开扩展管理页，保留加载入口，不再按全局安装记录跳到可能不存在的详情页。未知状态显示「待确认」，不当成未安装。连接成功显示绿色「已连接」，即使当前页面未授权或是浏览器设置页。安装指引保持打开并自动更新连接状态，打开浏览器页面本身不会被当作安装成功。

Safari 的只读 `--extension-status` 调用公共状态 API，但 API 可能保留曾启用的记录，并不证明当前扩展已加载。只有收到真实心跳才显示绿色「已连接」。`--open-settings` 是独立、一次性的命令，直接请求打开 Safari 扩展设置；失败会反馈到桌面，不会误报成功。旧助手保留兼容入口。

### 后台查询与 Dock

Safari 未连接时，桌面每隔至少 5 秒调用一次助手查询启用状态。助手的 `Info.plist` 必须在签名前声明 `LSUIElement=true`：仅在 Swift 入口中切换为 `.prohibited` 太晚，`NSApplication.shared` 初始化期间已可能以普通应用身份进入 Dock，随后退出，造成闲置时 Dock 反复伸缩。新助手从启动时即为后台代理；只有用户主动打开助手界面才切换为 `.regular`。不使用会禁止界面的 `LSBackgroundOnly`。参见 [Apple 的 LSUIElement 定义](https://developer.apple.com/documentation/bundleresources/information-property-list/lsuielement)。

源码启动若仍找到缺少这个标记的旧编译助手，跳过自动查询并显示「启用状态暂无法确认」，等待重新构建；不影响已建立的扩展连接或手动打开设置。安装包验证同时检查内嵌助手标记，防止后续打包遗漏。

这一边界也参考了浏览器扩展开源项目：[KeePassXC 的代理入口](https://github.com/keepassxreboot/keepassxc/blob/develop/src/proxy/keepassxc-proxy.cpp)使用无界面的 `QCoreApplication`，[消息代理](https://github.com/keepassxreboot/keepassxc/blob/develop/src/proxy/NativeMessagingProxy.cpp)在标准输入输出和本机 socket 之间转发，连接断开便退出；[Browserpass 的请求处理](https://github.com/browserpass/browserpass-native/blob/master/request/process.go)由独立命令行 host 读取 Native Messaging 请求。借鉴的是通信与界面生命周期分离：后台探测不创建普通桌面应用身份。Mythlink 的 Safari 助手仍需调用 SafariServices，因此采用 macOS 的 agent 启动声明，保留独立的用户界面入口。

当前用户选择暂缓公证，测试版仍可能需要在 Safari 设置 → 开发者手动开启「Allow unsigned extensions」，由用户完成系统身份验证；Safari 重启后该许可可能复位。安装记录还在而列表为空时应检查这项条件，不应重复安装扩展。程序不会自行改变 Safari 的安全设置。正式外部分发须按 [Apple 的发布要求](https://developer.apple.com/documentation/safariservices/distributing-your-safari-web-extension) 完成签名及公证。

## 构建

```sh
.build/packaging-venv/bin/python tools/build_browser_extension.py
# 输出 .build/browser-extension/Chrome、Mythlink-Chrome-0.1.0.zip、MythlinkBrowser.app
```

macOS arm64 SDK / Swift 编译器即可构建。默认 ad hoc 签名用于本机开发；`--identity 'Developer ID Application: …'` 使用正式签名。完整 `scripts/build-macos.sh` 自动嵌入 Safari 助手，按配置签名；只有同时配置签名身份与 `APPLE_NOTARY_PROFILE` 才会提交公证。开发包与正式发行状态写入 build-report.json。

## Chrome Web Store 待提交包

`Mythlink-Chrome-0.1.0.zip` 是商店上传源码包，移除了仅用于本地固定 ID 的开发 key。没有现成商店项目，所以目前没有可用商店安装链接。创建商店项目后，把商店生成的 ID 填入 `src/proximic_ring/browser_bridge/release.json` 的 `chrome_store_id`，重新打包桌面端；它会注册该 ID 的 native host 并打开对应商店链接。不要把开发 ID 当成已发布商店 ID。

提交材料：单一用途为本机戒指媒体控制。`nativeMessaging` 用于与桌面端双向通信；`tabs`、`webNavigation` 用于当前活动标签和 frame 身份核对；`scripting` 用于首次安装后注入已打开的授权页面；网站权限用于读取播放器和输入焦点。无远程代码、分析 SDK、账号、云端上传。发布前提供实际隐私政策 URL、支持联系地址、商店截图，并完成浏览器实测。

## 通信与隐私

Chrome 使用注册的 Native Messaging host；Safari 使用内嵌 NSExtensionRequestHandling 原生桥。原生桥只访问 127.0.0.1:47837，不开放 CORS，拒绝 Origin / Sec-Fetch 请求；交换使用随机会话令牌。桌面与原生进程属于同一用户信任边界，不宣称防御同用户恶意本地程序。

传给桌面的数据只有临时会话/窗口/标签/frame/文档/播放器/源版本 ID、场景、播放状态、焦点和授权状态。网址仅在扩展内部用于授权检查及导航失效，不传给桌面；不读取页面正文、输入内容或媒体字节。命令有过期时间，重新核对活动窗口、标签、文档、源及输入焦点后执行，返回结果，不重放失联期间的命令。

识别提示在场景查询之前提交；浏览器播放器动作和用户自定义快捷键通过有界队列在后台执行，结果回到界面线程显示。排队动作在设置变化、断连或等待超过 1 秒后取消。Native Messaging 使用可被命令唤醒的交换请求，空闲时维持 300 毫秒心跳，命令到来立即返回，不再让每次查询、校验、执行分别等下一轮定时器。仍然实时获取网页快照并等待操作确认，不使用缓存场景绕过焦点或播放器身份校验。新扩展连接旧桌面版本时自动保留旧轮询节奏。

2026-10-19 延迟排查：开发版日志中的 24 次 Chrome 视频动作，从收到已识别事件到完成的中位数为 787.5 毫秒，其中执行段为 612 毫秒；旧实现把浏览器等待放在 Qt 主线程，连带延迟了手势提示。修复后通过慢操作期间 Qt 定时器/手势提示响应、排队动作失效、浏览器通信及输入焦点回归测试。隔离 Chromium/WebKit 的 40 项播放器与扩展检查通过。通信基准只测 broker 调度，不能作为真实戒指端到端延迟；真机复测需要重启桌面端并更新/重新加载扩展。

## 发布验收

在 Chrome 和 Safari 各检查：首次安装与拒绝/撤销权限、播放/暂停/seek/volume、同 URL 两个标签页、跳转与 SPA 换源、跨域 iframe、多个播放器、输入框与地址栏、切换窗口/应用、浏览器和桌面进程重启。第三方网站至少覆盖 YouTube、Bilibili、一个标准 audio 页面。受保护播放器按真实能力显示不可用，不添加 AX 兜底。

可自动验证协议、无 AX 内容读取、DOM 识别与动作路由，以及 Safari 编译/签名。商店审核、签名公证及真实浏览器授权不能由自动测试替代。

## 本次验证记录

- 浏览器桥接、场景/手势映射、安装 UI 和打包相关的聚焦测试：372 项通过（340 项应用测试 + 32 项本机桥接测试）。
- 独立 Chromium / WebKit 中的真实 HTML 媒体测试，加上实际加载 Chrome MV3 扩展的标签切换与命令过期测试：40 项运行检查通过。包含 Safari 拒绝带端口的网站权限匹配规则，以及首次播放被浏览器策略拒绝时的明确诊断。
- Safari 原生助手和 appex 编译通过，sandbox / network.client entitlement 与嵌套代码签名完整性已检查。完整 Mythlink.app 的 macOS 兼容性校验、离屏启动自检和冻结版 Native Messaging 握手均通过。
- 2026-10-15 已安装到本机 Chrome 与 Safari。用户已亲手启用 Safari 扩展并授予网站访问权限，实际 Safari 已经通过原生桥返回已授权的音频场景。修复了 Safari 对带端口权限规则报错的问题；Chrome 接受该格式，所以此前隔离 Chrome 测试未暴露它。
- 本机 Chrome 已记录真实戒指固件事件 → 视频场景 → 扩展确认执行 → 网页播放/暂停、seek 事件（8 次 Tap、2 次右滑）。Safari 已记录音频场景下 10 次 Tap、2 次上滑、2 次下滑确认执行，网页音量按 50% → 60% → 50% 变化。Safari 刷新后的首次播放曾被策略拒绝，先在网页播放一次后成功；未把这类拒绝记作执行成功。
- 实际安装的 Chrome 音频、Safari 视频通过本机通道验证了播放、暂停、进度及适用的音量操作；两浏览器的输入框焦点测试均阻止了媒体命令。这些程序命令验证与上述真实戒指记录分开保存。
- 本机安装包与导出包已使用 Developer ID（5H42RU3M38）签名，尚未公证；Safari 开发授权仍需按上述步骤处理。最新安装流程、跨用户路径、默认场景与提示的聚焦测试共 85 项通过，与此前测试存在重叠，不累计相加。完整 DMG 的校验和与签名核验通过。第三方网站验收与第二台实体 Mac 安装验收仍未完成。
- 更宽范围回归还存在旧断言不匹配：输入时标签切换是否允许、捏指保留手势、已迁移的旧模块导入、旧界面文案；完整套件未宣称通过。原生工作进程在受限沙箱内的启动检查也失败，需在正常应用环境检查。

2026-10-19 Safari 安装排查：本机未允许开发扩展时，Safari 列表为空，但组件与 Developer ID 签名有效。允许后发现开发目录与已安装应用各注册了一份；本次保留当前开发版注册，取消旧副本注册（保留旧应用文件）。更新并签名本机助手后，Safari 列表只有一项且已启用；公共状态 API 返回 `enabled`，实际音频与视频页均返回 `authorized=true`、`reason=recognized`，音频暂停通过与戒指相同的桥接接口成功执行。73 项安装状态、连接与手势回归测试通过。本次未进行新的真实戒指动作测试，未重建完整 DMG，也未提交公证；不能据此宣称发布包已免除 Safari 开发许可。
