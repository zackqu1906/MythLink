# Mythlink 后台 Agent 入口

## 当前生产流程（2026-10-22）

桌面 Agent 默认使用 `BackgroundFlow`。Mythlink 负责语音、文字、截图、附件、进度、授权及追问；每个应用进程共用一个无窗口的 Node 服务，由它持有安装版 Codex 的 App Server。新建任务与恢复旧对话均不执行 wake/open，不自动打开或激活 Codex。只有用户明确点击打开 Codex 的入口才导航桌面端。

服务通过固定版本 Remodex 的本机 Unix socket owner 协议同步状态，浮窗继续使用 follower 接口实现后续输入、steer、取消和授权。这里没有手机或网络中转。上游版本、许可证和本地适配列在 `src/proximic_ring/agent_workflow/agent/desktop_owner/README.md`；构建收集其中 JS/CJS 和许可证，复用已安装 Codex 的 Node，不额外捆绑 Node。

### 新建和链接

加号创建新卡片，首次提交时创建持久对话并设置标题。输入保留附件，所有文本项补齐 `text_elements: []`，避免当前 Codex 渲染器重新打开对话时访问缺失字段。首条输入不再包含要求 Agent 调用改标题工具的启动提示。

链接菜单保留紧凑浮层：按最后更新时间倒序显示最近 5 条对话，无搜索栏，不覆盖主界面。选择后先只读验证元数据，再发现现有 owner：

- 有 owner：订阅它的状态，后续操作交给原执行方。
- 明确没有 owner：由后台服务调用标准 `thread/resume`，Codex 的跨进程 writer lock 决定能否恢复，不覆盖锁或强行接管。
- 已有原生任务占有 writer，但未提供实时 owner：保留当前卡片和草稿，明确提示“请手动打开该对话一次，再重新连接”。用户已接受这个例外；程序不自动弹出 Codex。

第一份目标快照到达后才替换卡片，历史已完成事件不重放。替换保留当时的焦点状态：若原卡片仍聚焦，先把焦点交给目标卡片，再隐藏原卡片，避免触发返回原应用的延迟动作；若用户在等待连接期间已切走，则保持当前应用或其他卡片的焦点。steer 和取消绑定真实 turn ID；乐观临时轮次不能成为控制目标。所有回执校验原请求、owner 和方法。断线、版本缺口、超时或结果不确定时不自动重发提交或授权。

### 生命周期与交互

智能截图在提交时先锁定当前业务窗口并立即保存一帧，避免等待 Agent 判断期间用户切换页面。消息只携带本地图片路径和窗口信息，Agent 按语义决定是否读取；不需要界面的任务无需加载图片。首条、后续、排队和 steer 输入均保留各自发送时的截图，不能用后来桌面的画面替代。每次截图模式仍直接附图；关闭模式不截图。详见 `AGENT_CODEX_SYNC.md` 的截图说明。

语音发送在上滑时就捕获，和识别定稿同时进行；取消发送会删除未提交的截图。获取画面共用一秒时限，超时不采用迟到画面。2026-10-22 的两种合成窗口（1040×736 和 2360×1576 像素）各连续截取五次，含转换的耗时为 152–336 ms；这是本机测试值，不是所有设备的时延保证。

`.runtime/semantic-capture/report.json` 保存了生产后台 Agent 的语义验收：第一轮“请只回复数字12345”未读取图片；第二轮“看一下这些公式”实际产生 `imageView`，读取预先保存的合成图并正确指出 `7 × 8 = 54` 应为 `56`。验证仅使用生成的测试图片，未读取或操作用户应用；不代替 DMG 或实际戒指发送验收。

关闭单张浮窗只隐藏/断开，已提交任务继续执行，可以通过链接接回。**退出 Mythlink 会停止由本应用提交的任务并关闭后台执行进程。** 连接原生 owner 时只记录本应用实际启动成功的 turn ID，退出仅取消这些准确轮次，不取消仅查看的其他原生任务。后台服务也监视应用 PID，应用进程消失时结束自己的 App Server；它不安装登录项或常驻系统服务。

任务卡片支持三个授权模式：逐次授权在浮窗显示请求；自动授权默认只处理应用使用权限；全部授权额外处理可识别的命令、文件及网络权限。授权范围以服务端实际提供的选项为准，不额外安装命令规则。追问可以在浮窗选择或输入回答，也可在 Codex 处理。每项决定只提交一次，回执不等于后续工具成功。普通输入排队，steer 修改当前轮次；尚未提交的本地队列不承诺持久化。

本地适配保留 `itemsView: summary` 之前已收到的完整命令/工具记录；正确清除 ID 为 0 的请求，并在轮次结束时清除同步残留请求。使用 Computer Use 的轮次结束后，执行 `turn_ended` 和 `js_reset`，释放会话后才允许下一轮开始。释放失败时明确阻止继续使用该会话，要求重新启动 Mythlink。

独立后台执行可用普通应用和浏览器的 Computer Use 接口；不依赖 Codex 内嵌浏览器。受保护的 `codex_app` 工具不作为该流程依赖，不修改签名身份、Codex 安装包或数据库。

### 已验证结果及边界

生产 `BackgroundFlow` 实测记录保存在 `.runtime/background-migration/`：

- `acceptance.json`：新建、活动任务重连、steer、关闭卡片后继续运行、浮窗追问、单次命令授权、完成后命令记录保留；退出后持久化轮次为 interrupted，服务及 App Server 均退出。
- `browser-acceptance.json`：实际操作 Chrome，新建并关闭本轮空白标签页，保留原有 6 个标签；待处理请求归零；电脑控制释放后下一轮正常完成。浏览器专用标签列表接口在独立进程不可用，本轮使用文档化的原生应用接口完成操作。
- `final-display.json`：最终适配再次创建隔离任务，接收运行中正文与最终答案；关闭测试服务后，用独立只读连接确认完成历史仍可读取。用户已确认「Mythlink · 后台迁移最终验收 1022」从左侧打开、切回本聊天再打开，两次都能正常看到正文；本次 Codex 正文显示人工验收通过。该结果不替代 DMG 验收，也不证明侧栏能即时刷新。
- 两个独立 App Server 的 writer-lock 验证确认：第二个恢复被 `already has an active writer` 拒绝；代码将此转换为用户已接受的手动打开提示。

**Codex 左侧列表即时刷新仍未保证。** 本地 IPC 能同步任务状态，但不等同于桌面侧栏目录通知；浮窗明确提示列表可能稍后刷新。下面历史记录中的原生工具改标题方案不再是生产路径。

单元与界面回归使用 `QT_QPA_PLATFORM=offscreen QT_QUICK_CONTROLS_STYLE=Basic`。涉及真实 Unix socket、App Server、QtWebEngine 的验收使用正常主机权限，不能把沙箱中的 QtWebEngine SIGTRAP 当成应用崩溃结果。源码验证不替代冻结 DMG 验收；本轮按用户要求不构建 DMG。

最终回归：202 项通过、1 项跳过，日志为 `final-regression.log`。生产打包过滤后仍包含全部 9 个 owner 资源，见 `package-assets.json`。已正常重启源码 UI；自动化应用选择器只能识别安装版，故没有把旧安装版的界面观察计入源码 UI 验收。

## 浮窗焦点动画与提示（2026-10-22）

移除“按需截图”模式按钮，任务输入区提示为“自动截图，上滑发送”；截图的执行策略仍由原有控制器负责。

参考 [Caelestia Launcher Wrapper](https://github.com/caelestia-dots/shell/blob/main/modules/launcher/Wrapper.qml) 的统一动画进度与关闭期间稳定内容尺寸，以及 [KDE Popup](https://github.com/KDE/qqc2-desktop-style/blob/master/org.kde.desktop/Popup.qml) 的淡入淡出。仅借鉴思路，未引入项目代码或依赖。

Mythlink 的窗口尺寸仍由原有单个 QRect 动画管理，过渡收敛为 240 ms。正文和简要信息分别使用固定的排版宽度，避免窗口每次缩放都触发文字换行、高度重算和动画目标重置；以当前窗口宽度推导统一进度，驱动内容交叉淡入淡出、圆角和底色。反向切换从当前尺寸继续，系统减少动态效果设置仍生效。原生 Cocoa 验证覆盖稳定排版、底部锚定、快速反向切换、边界约束和焦点保持，记录位于 `.runtime/agent-motion-check/`。这些是源码验证，不替代 DMG 验收。

## 历史调查与旧方案验收

以下为切换前的阶段记录，保留原始限制和验证边界；其中“正式流程尚未切换”只描述当时状态，当前以本文开头为准。旧 `--self-check-codex-native` 入口验证原生交接方案，不作为当前后台方案验收。

### 后台入口验证（2026-10-22，尚未启用）

在 build 21161 中，单独调用 `open -g codex://threads/wake/<id>` 能在 Codex 隐藏时消费队列并完成原生任务；隔离前台窗口的实测没有发现 Codex 显示或抢焦点。但新建或尚未加载的对话没有原生流 owner，Mythlink 收不到快照、进度，也无法发送 follower steer／取消。`thread-owner-discovery` 只发现已有 owner，不加载对话；只读 `thread/read` 同样不会建立 owner。不能把任务实际执行成功等同于浮窗控制链路已接通。

因此没有将 wake-only 试验替换正式的 wake/open 流程。仅删除打开调用会导致任务在 Codex 成功执行、浮窗却连接超时；不能作为完成修复交付。加载过的对话能够后台订阅，也不能证明新建对话具备同样能力。实测脚本、状态报告和原生界面测试留在 `.runtime/agent-background-check/`，不随应用打包。

### 开源实现对照（2026-10-22）

- [Notifai 的后台唤醒适配器](https://github.com/Raidiant-io/notifai/blob/73197cbe7ab51449a5885feb2468806f5e9d9146/apps/cli/src/reopen-codex-app.ts) 使用队列和 `threads/wake`，优先向指定运行进程发送 Apple Event，备用 `open -g -b`。这是原生后台执行的参考，不代表新对话已建立 follower 实时控制。
- [TemPad 的 loadConversation/loadOwner](https://github.com/ecomfe/tempad-dev/blob/2ad385b9f2c3fc3455c16dbfa98b748dddfc2413/packages/mcp-server/src/agent-clients/codex-feedback.ts) 在 owner 发现失败后执行 `open -g codex://threads/<id>`，再限时重试发现；[Agents Anywhere 的交接模块](https://github.com/anywhere-labs/Agents-Anywhere/blob/a8b38aca70296f994ce18f1ec2d287b485259251/connector/connector/runtimes/codex/ipc/deeplink.py) 也采用该命令。当前 build 21161 的隔离原生测试确认：空白已有对话接上 owner，但 Codex 同时显示窗口并变为前台，因此 `-g` 本身不满足不抬窗要求。证据为 `.runtime/agent-background-check/open-g-report.json`，脚本为 `open-g-research.py`；此次没有发送模型任务，结束后恢复原聊天。
- [Remodex 的双向实时同步](https://github.com/Emanuele-web04/remodex/blob/f67b1499dbd8ace7a23902fad3242cc88f7e9a9e/README.md#codex-desktop-app-integration) 让自家 app-server 持有后台任务，并提供 [owner 端控制适配](https://github.com/Emanuele-web04/remodex/blob/f67b1499dbd8ace7a23902fad3242cc88f7e9a9e/phodex-bridge/src/desktop-ipc-live-owner.js)，处理 steer、interrupt、审批和状态广播。手机到电脑是独立的网络通道；owner 到 Codex Desktop 是同机 Unix socket IPC，可由 Mythlink 本地入口复用。它默认不自动打开桌面端；桌面端打开该对话后才订阅外部流。该路线改变 Mythlink 的执行权架构，不能视为原生桌面 owner 的后台加载接口。以下隔离实测已接通实时控制，但没有替换正式执行流程。

### Remodex 本地互通实测（2026-10-22，build 21161）

固定上游版本 `f67b1499dbd8ace7a23902fad3242cc88f7e9a9e`，在 `.runtime/remodex-interop/` 保存六个未修改的上游模块及 Apache-2.0 许可。测试由安装版 Codex CLI 启动独立 App Server，没有手机、网络中转、后台服务安装或全局配置修改。Mythlink 现有 `NativeCodex` 作为 follower 连接上游 owner，证据汇总在该目录的 `report.json`。

- 首条测试 `01a1623e-4447-7062-b652-40de7385909e` 未打开时，follower 已收到实际轮次、运行状态和 `REMODEX_RUNNING` 正文。通过 Mythlink 的 steer 请求修改最终结果，释放测试等待工具后得到 `REMODEX_STEER_OK` 和 completed。
- 关闭测试桥接及其 App Server 后，独立读取仍得到 completed 和最终回答。用户确认切走再从左侧进入，两次都能正常显示正文。此结果证明本次会话完成后的历史可读，不等同于重启 Codex 或 DMG 验收。
- 最后一次后台复核 `01a1625c-6e20-70b2-9a82-df5f68dbd3ea` 通过现有 follower 接口启动、接收正文和取消，App Server 实际返回 interrupted。该运行区间的桌面日志没有导航或对话激活记录，只有广播引起的元数据读取；证据在 `background-recheck/`。
- 接线须与上游 `bridge.js` 一致：owner 内部的 `sendCodexRequest` 及其响应不能再次进入普通入站/出站观察器。最初测试接线重复观察内部请求，产生重复乐观轮次并覆盖 canonical items；只修正测试接线后，后台启动及取消复核通过。不能将此测试接线问题当作上游缺陷。

**侧栏及时更新仍未通过。** 用户观察到对话出现很慢。Remodex 的 `thread-unarchived` 广播已引起桌面在约 1.2 秒内读取目标线程，说明本地广播到达；但本机版本的该处理器更新内存对话集合，持久侧栏目录的协调器订阅的是桌面自身 App Server 的内部通知。三条验收对话的 `local_thread_catalog` 记录未出现。目录缺项不能单独证明界面不可见，因为本次用户已看到内存中的对话并成功重新打开；也不能据此承诺下一次启动后立即出现在侧栏。

另用安装版公开 schema 中的 `mcpServerStatus/list` 检查外部 App Server 是否能正常调用 Codex 自带改标题工具。在仅对隔离线程启用该 MCP 的条件下，初始化返回 `Codex app tools pipe closed`，桌面日志同时记录 `missing-code-signing-identity`，因此未进入 `mcpServer/tool/call` 阶段。该测试甚至继承了 Codex 启动诊断进程的环境，不能视为独立 Mythlink/DMG 获得调用能力。普通 Remodex IPC 与这个受保护的工具管道是不同接口；没有证据表明 Remodex 获得了特殊签名信任。

本轮没有修改安装版 Codex、写入其数据库、伪造受信任身份，也没有构建 DMG。实时桥接可作为移植依据；侧栏目录登记、已有原生 owner 的安全交接、授权和完整任务能力仍需独立完成验收，不能直接把本实验切换为生产默认路径。

### 扩展能力验收（2026-10-22，仍为隔离实验）

后续将原版 Remodex owner 接到 Mythlink 现有 `NativeCodex` follower，实际执行以下检查。测试注入了禁止打开界面的 opener，恢复和连接流程没有调用 wake/open。用户在其他应用中观察后确认“没有自动跳出，是我手动点开的”；15:07 的桌面对话激活来自用户手动操作。记录保存在 `.runtime/remodex-interop/extended-20261022/extended-report.json`。

| 项目 | 实测结果与边界 |
| --- | --- |
| 恢复已结束、未加载的旧对话 | owner 发现明确返回 `no-client-found` 后，在隔离 App Server 中恢复此前的取消验收历史，后续模型正确复述旧标记 `REMODEX_CANCEL_WAIT`。目标是已知结束的自建测试对话，不证明能安全接管正在原生后台执行的任意对话。 |
| 连接正在执行的任务 | 先启动任务并等待工具，再接入 follower，收到真实活动轮次及 `ATTACH_RUNNING` 正文。 |
| 断开重连、steer、附件 | 重连同一 owner/turn 后发送 steer；最终准确返回 `REATTACH_STEER_OK，红色，蓝色`。 |
| 关闭浮窗连接后继续执行 | 关闭所有测试 follower，再释放等待工具，任务完成；重连仍能读取结果。持有执行权的桥接进程始终运行。 |
| 取消 | 前一轮未打开桌面对话的测试已通过实际 `turn/interrupt` 得到 interrupted，见 `background-recheck/`。 |
| 命令授权 | 通过现有 `NativeApprovals` 回复单次 accept，仅允许固定无副作用命令 `printf IPC_APPROVAL_OK`；真实命令返回退出码 0 和预期输出。未安装持久命令授权规则。 |
| 追问 | 真实任务发出 `request_user_input`，经 `thread-follower-submit-user-input` 回传 `MARKER_B`，模型返回 `QUESTION_OK MARKER_B`。这是协议验证；生产 `NativeFlow.answer_text()` 仍提示用户去 Codex 回答。 |
| Computer Use | 真实模型轮次调用 `cua_repl.js` 的 `cua.getState()` 成功，能读取应用和浏览器清单。只做了只读清单检查，不代表所有控制动作或应用权限流程已验证。 |
| 桥接进程退出 | 仅让自建测试 owner 退出；follower 检测到断开，子 App Server 也退出，磁盘上的测试轮次为 interrupted，未生成完成答案。任务不会自动由 Codex 接管。 |

发现了两个与当前版本适配相关的状态问题：

- App Server 的 `turn/completed` 携带 `itemsView: summary`，上游 adapter 将已收集的完整 items 替换成摘要，导致完成后的 follower 快照缺少命令记录。`thread/items/list` 仍可读到持久化的命令及结果；调用 `thread-follower-load-complete-history` 后记录恢复。因此是状态适配缺口，不是磁盘历史丢失。原始授权综合检查因这一断言失败，后续独立核验确认授权和实际执行均成功。
- 测试等待工具结束后，请求 ID 0 仍留在 owner 的 `requests` 中；该请求没有对应的 `serverRequest/resolved` 通知。Mythlink 当前投影会将未清理的请求显示为需要处理，即使轮次已经完成。追问和命令授权本身已实际完成，不能把这个状态残留描述成授权接口不可用。

另在 `standalone-20261022/` 移除继承的 `CODEX_*` 会话环境后启动独立进程（保留可能显式指定的 `CODEX_HOME`）。后台 follower 启动和模型回答仍通过；真实 CUA 调用能够读取应用清单，但返回 `browsers: []`。继承 Codex 环境的同类轮次可发现 IAB/MCP Apps 两个浏览器。这里没有验证独立启动下的浏览器控制，不能宣称与 Codex 原生工具能力完全一致。独立进程中的 `codex_app` 默认禁用；前述显式启用测试仍受签名校验阻止。

**结论：后台核心控制链路可行，但并非只剩侧栏刷新。** 移植仍需处理完整历史/请求状态适配、桥接执行进程的生命周期、已有原生执行任务的安全连接或交接，以及独立启动时的桌面工具和浏览器能力。两组测试进程均已清理；正式 `NativeFlow` 未切换，没有构建或验收 DMG。

## 验收

单元与界面回归使用 `QT_QPA_PLATFORM=offscreen QT_QUICK_CONTROLS_STYLE=Basic`。`tests/test_agent_native_codex.py` 覆盖执行权交接、图片输入保留、原生流状态、过期控制拒绝、所有者变化和断开时不误取消。

签名应用带一个必须显式调用的验收入口：

```sh
/path/to/Mythlink.app/Contents/MacOS/Mythlink --self-check-codex-native \
  --runtime-dir /absolute/path/to/fresh-acceptance-directory \
  --codex /Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex
```

它创建一个隔离对话并依次检查：运行中进入侧栏目录；图片颜色及 steer 改变最终答案；取消得到 interrupted；关闭输入 Flow 后原生任务继续完成。`acceptance.json` 保存时间和任务 ID。此入口只读目录数据库作证据，生产提交路径没有数据库访问。目录记录和原生 active 状态不能替代对左侧实际渲染结果的人工观察。验收会使用用户当前 Codex 模型和额度，不在启动或构建自检时自动执行。

源码实测记录：2026-10-21，build 21161 下对话 `01a15de1-0879-7d43-a251-058588c1ac01` 在提交后 12.168 秒进入侧栏目录，原生任务仍为 active；steer、图片、取消、断开继续执行和历史输入字段检查均通过。用户随后确认从侧栏两次打开均正常。该记录不替代冻结 DMG 的验收。
