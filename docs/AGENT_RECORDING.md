# Agent 工作记录

每次连接本地 Agent 时自动启动记录器。Agent 卡片底部只保留文字居中的“记录”入口，生成并打开本机 HTML 回查页面。打开记录本身不提交 Agent 任务、不请求额外截图；Workflow 的查看、逐任务提取/重试、批量重试失败任务及自动提取开关全部放在记录页面中，详见 [Workflow 提取](AGENT_WORKFLOWS.md)。提取按钮提交到独立后台队列，使用配置的文本模型。

## 任务操作台

记录总览和任务列表新增“任务操作台”入口，任务详情与通用组成员也可以直接跳转到对应任务。左侧按任务发起时间从早到晚排列，可切换倒序、搜索目标、筛选处理状态；右侧显示选中任务的三个步骤。一次任务提取出多个 Workflow 时，可以分别选择处理。

1. **提取 / 重新提取 Workflow**：读取该任务的完整记录，生成新版本。已有结果和审计保留；当前尝试失败时明确显示失败，不将旧版冒充当前结果。
2. **合并 / 生成通用 Workflow**：针对所选 Workflow 召回通用组并判断同类，没有同类则建组。同类融合产生候选后，在同一页面对照来源、参数对应和新流程；逐来源填写核对说明，点击“采用此通用流程”使它生效。采用记录保存在原有 merge 审计中，不冒充实际执行验证。
3. **参数提取**：针对“这个任务 × 当前通用组版本”提取参数，规则优先、LLM 补充。留空、缺失和不确定分别展示；组版本改变后旧参数记录保留，新版需要重新提取。采用新契约不盲目迁移旧值。

归组和参数请求保存在 `workflows/queue.sqlite3` 的 `workbench_actions`，由现有独立后台进程消费。请求固定提取版本和通用版本；重复点击复用同一个排队请求，来源变化时拒绝过期操作。步骤会显示排队、处理中、待采用、完成或失败，历史可回查时间、版本、模型输入、输出和用量。进程中断会记录结果未知，不自动重发可能已经计费的手动请求。提取继续沿用原有有限重试策略。

操作台不会执行历史桌面操作，也不改变自动处理开关。HTTP 接口只接受本机能力 URL 和同源 JSON；模型仍由后台使用应用当前配置调用。直接打开 `workbench.html` 是静态快照，操作需从运行中的应用“记录”入口进入。更新源码后需重启源码应用加载新后台与页面接口。

## Codex 持久化会话

任务列表的“操作”列及任务详情顶部都有“在 Codex 中打开”。链接使用该任务实际收到的执行事件中的 thread ID，并用原始 `thread/start` 返回或 `thread/started` 事件确认 `ephemeral=false`。一个会话内多个任务会打开同一个会话，不保证滚动到指定 turn。静态 HTML 与应用内本机记录页均可点击，使用本机 Codex 的 `codex://threads/<ID>` 协议；链接本身不发送任务或恢复执行。当前活跃 Agent 会话在已支持版本中由实时同步层提供跟随和双向输入。浏览器可能要求确认打开 Codex。

旧临时会话、尚未开始的任务、缺失/非法 ID 或关联不确定时只显示不可用原因，不猜测目标。链接依据记录生成，不查询 Codex 当前是否仍保留会话；会话被删除或转移到另一台电脑后，Codex 可能无法打开。更新程序后重新打开“记录”，或重新导出 HTML，即可刷新已有任务页面。

正式 Agent 连接通过 `thread/start(ephemeral=false)` 建立持久化会话，普通任务和推荐执行都写入该会话。一个连接对应一个 Codex thread，多次任务是同一 thread 内的不同 turn；断开后历史保留，重新连接会新建 thread。已验证版本现已接入实时同步：Codex 可跟随本次 Agent 的任务并发送修改、回答追问。不会连接任意历史会话，具体兼容范围与操作说明见 [Agent 与 Codex 实时同步](AGENT_CODEX_SYNC.md)。

现有 `agent-experience` 继续独立记录完整公开 RPC、客户端事件和工具返回，并用原生 `thread_id` / `turn_id` 关联。Codex 历史用于原生会话回看，我们的记录用于步骤视图和 Workflow 提取。旧版临时会话不会自动补入 Codex。Workflow Trials 仍显式使用 `ephemeral=true`，每次独立建会话，测试结果仍保存于我们自己的记录库。

需重启 Downloads 源码程序，或使用新代码重新连接 Agent，才会创建持久化会话。使用同一 Codex 本地数据目录的桌面端可以读取这些原生历史；具体列表与打开能力以当前桌面版本为准。不要修改 Codex 内部数据库来导入或伪造历史。

2026-10-19 实测：通过正式 `Flow` 路径运行固定文字回复，退出 App Server 后，另一个 App Server 成功通过 `thread/read` 和 `thread/turns/list` 读回用户输入和最终回复，`ephemeral=false`。桌面端 `list_threads` 找到该 thread，`read_thread` 读回完整一轮，`navigate_to_codex_page` 返回 `navigated=true`。本次未验证在 Codex 中继续执行任务，也未做截图核验。

验证会话 ID：`01a152f2-6362-78a2-8ce1-f7fad005ad7f`。在 Codex 的任务列表查找开头为“Mythlink 持久化会话验证”的会话即可打开。验证报告位于 `.runtime/persistence-checks/20261019-145617/report.json`，本次独立记录入口为同目录的 `agent-experience/index.html`。验证数据与正式经验库分开，不进入 Workflow 推荐数据集。

## 保存位置

数据保存在 `app_data_root()/agent-experience/`，不在临时缓存中。源码运行时位于项目的 `agent-experience/`，已加入 Git 忽略；打包后的 macOS 版本位于 `~/Library/Application Support/ProxiMic Voice/agent-experience/`。`PROXIMIC_DATA_HOME` 覆盖根目录。

每次连接建立独立 Session，目录结构为：

```text
agent-experience/
  index.html                         # 由“记录”按钮或 CLI 生成
  sessions/<session-id>/
    session.json                     # 环境、格式版本、记录健康状态
    events.jsonl                     # 按顺序追加的双向 RPC 与本地事件
    index.sqlite3                    # 任务、条目、返回观察、事件索引
    attachments/<sha256>.png          # 工具返回的内嵌图片，按内容去重
    episode-<episode-id>.html         # 每次任务的回查页面
    episode-<episode-id>.steps.json   # 可重新生成的步骤视图，含事件引用
    session.html                     # 初始化和未能关联的事件
```

## 记录范围

* 用户实际提交的目标、补充要求、答案、来源与触发上下文。主界面统一标为 `agent_editor`，不猜测混合编辑框的文字究竟来自语音还是键盘；手势提交单独记录 trigger。
* 一个正常新任务一个 Episode；排队时即创建，执行时保持同一标识。steer 归入原任务，清空队列的取消逐一记录。线程和 Turn ID 作为关联，不把整场会话当成一个任务。
* 发出的 RPC 请求和回答，以及收到的完整任务事件。记录 outgoing 表示客户端尝试写入；服务端响应才是接受证据。写入失败、连接断开另外记录。
* 工具名称、原始参数（包括 CUA JavaScript）、状态、结果、错误、报告的耗时和可用元数据。兼容未知通知，不要求只使用 MCP 工具。
* Agent 进度及最终文字、计划更新、实际返回的推理摘要及分段。保留增量事件，完成条目作为聚合视图的最终版本，不把增量重复拼入最终文本。不保存非摘要推理内容，不补写没有返回的推理摘要。
* 追问、用户回答、权限请求与决定、修改/取消，以及原有客户端控制事件（旧版暂停/恢复事件仍可回查）。
* 工具实际返回的文本、结构化内容和内嵌 PNG/JPEG/WebP/GIF 图片。图片移到附件目录，JSON 留下哈希、类型、大小和引用。保留图片其他元数据。未主动读取屏幕、下载远程图片或任意读取工具返回的本地文件路径；只有路径/URL 的资源仍是引用。
* 用户切入 Agent 前，已有焦点捕获提供的应用/窗口元数据及采集时间。此信息不是实时完整桌面状态；未取得时明确标记。执行开始的界面证据来自后续 Agent 工具结果，不另开观察工具。
* 时间、请求/线程/Turn/Item ID、token 用量的原始通知、错误及记录质量。token 累计值不累加为总用量；不把其他时间全部算作推理时间。

## 事实与解释

记录器本身不调用模型。任务结束后的独立后台提取会生成自然语言 Workflow，详见 [Workflow 提取](AGENT_WORKFLOWS.md)。数据库中沿用名称的 `observations` 表是工具返回索引（`kind=tool_result`），不是已经识别出的观察，更不是动作后的界面。步骤视图单独标注本次调用实际返回、且格式可明确识别的观察；普通文字、文件正文、执行回执和任意图片不提升为观察。一个 `js` 调用可能包含多个操作，源码也不能证明它们全部执行成功。

协议的 completed、Agent 的“已完成”陈述、计划状态和用户目标达成是不同信息。Episode 的 `goal_status` 当前始终为 `unknown`，等待后续有证据的验证逻辑。中断/失败/未正常结束会保留，不改写成成功路径。队列取消标为 `cancelled_before_start`；正常关闭时仍开放的任务标为 `unfinished`。

优先按 Turn ID、Item ID、双向各自的 Request ID 关联。只有唯一待开始的请求才能连接先到的 turn/started；没有足够信息的事件保留在会话中并标记 unresolved，不随意挂到下一个任务。所有证据保留原始消息、时间与序号。

## 步骤视图

打开“记录”后，任务页面按实际调用顺序展示：调用前 Agent 文字、工具调用、执行结果，以及本次调用中的观察。没有识别到观察时明确标记，不补齐“观察 → 动作 → 观察”模板。原有消息、工具条目和完整事件时间线仍可展开查看。点击“事件 #编号”跳到原始事件；每个引用同时包含事件 UUID、序号、时间、任务/线程/轮次/条目标识和方向。

观察保留 `source_call_id`、`source_item_id`、`result_path` 和 `event_refs`，只归入实际返回它的调用。页面按调用标识与结果事件精确匹配，显示可点击的来源步骤及调用标识；不把前一调用、后一调用或任务提交上下文自动绑定为当前步骤的观察。Agent 文字展示来源消息标识。

一条事件是一次收到/发出的 RPC 消息或客户端本地记录，不等于一次任务或桌面操作。一次调用通常对应开始、返回、权限处理和状态更新等多条事件；流式文字的每个增量也分别保留。原始协议事件和客户端派生的状态事件可以描述同一次执行的不同环节，步骤视图依据调用标识合并展示。

步骤视图在读取时由 `step_view.py` 确定性生成，不修改原始 JSONL 或 SQLite，也不需要迁移旧记录。HTML 导出时同时生成 `episode-<id>.steps.json`；CLI 的 `--episode` 输出增加 `step_view`。视图当前为 `version=2`，步骤标识由会话和调用标识稳定生成。v2 删除 v1 的 `before_observation`、`after_observation` 候选关联，改为 `recorded_observations`；`action_explanation` 改为 `preceding_agent_messages`，不认定消息就是行动理由。两者使用 `entries` 保存原文和证据，再次导出会重建旧的派生 JSON/HTML。

当前关联规则：

* 一次工具调用一个步骤。保留参数/代码原文，不执行代码，不把 JavaScript 内部的多次操作推断为独立成功动作；失败、重复和重试不合并。支持 MCP、动态工具、命令执行、文件变更等已知条目类型，未知或缺少标识的条目明确列为“未整理成步骤”。
* 调用和结果按同一任务、线程、轮次与 Item ID 关联。客户端动态工具使用实际 `callId` 和有方向的 Request ID 关联；同一调用的请求/回复与条目不重复计数。只有客户端回复时标明“回复尝试”，不宣称服务端已收到或条目已完成。
* 调用前 Agent 文字：展示同轮次中前一次已结束调用之后、本次开始之前收到的推理摘要或 Agent 文字，只表示时间顺序。不使用动作开始后才收到的最终文本；流式内容只保留当时已收到的部分并标为不完整；已明确标为最终答复的消息不放入调用前文字。原有完整消息仍在下方保留。
* 本次调用中的观察：只识别实际返回证据，不因返回非空、工具只读提示、调用标题或代码中出现观察方法名而生成观察。观察保留原始内容块及其在本次结果中的路径，不重新总结。
* 当前 CUA 适配：`cua_repl.js` 返回携带 `codex/toolSurface.kind=computerUse` 时，按已知输出格式识别无障碍界面树、差异及“未变化”返回。初次选取应用自动返回的界面树也属于该调用实际提供的观察，随附 API 文档不算。仅直接 `await app.getScreenshot()` 或 `await app.getAXStateAndScreenshot()` 且实际返回已保存图片时标注截图。注释、字符串、条件分支里的方法名不作为截图执行证据。
* 未覆盖的工具/格式、复杂脚本里的截图或只有图片路径、图片保存失败时，不自动识别为观察，原始参数、结果和质量标记照常保留。`not_identified` 不是断言 Agent 没有观察，而是当前记录不足以由现有规则明确识别；后续可针对真实工具协议补充适配。
* 观察只标为 `within_call_unlocated`，不推断为调用内部某个操作的前状态或后状态；没有逐动作执行日志时，不拆分混合脚本。并行/重叠调用、工具失败、记录缺口和来源质量标记继续保留。没有返回的观察不借用其他调用来补齐。

JSON 中 `availability=recorded` 表示对应证据已记录；`missing` 表示所需消息或调用事件缺失；观察中的 `not_identified` 表示未识别到明确观察证据。`observation_policy=explicit_returns_in_own_call_only` 明确禁止相邻候选关联。`reasons` 是可供程序判断的原因代码，页面显示中文解释。步骤视图仍然不验证用户目标，也不生成自然语言 workflow；所有步骤的 `goal_verification` 保持 `unknown`。

## 存储与异常

一个后台工作线程负责磁盘和图片处理。RPC 回调只冻结并排队消息，不执行额外工具或模型，也不改变授权/取消逻辑。单事件上限 48 MiB，待写入队列预算 96 MiB；超限事件计入丢失数量，后续日志标记 recording_gap，界面显示记录不完整。异常不会被当成完整成功记录。

原始日志先落盘，随后提交 SQLite 索引；索引失败时日志仍可用于诊断。正常关闭会排空队列（最多等待 5 秒）；进程被强制终止仍可能丢失尾部尚未落盘的数据。每条事件 flush，未承诺断电级事务或跨文件原子性。

账号/认证消息体与明确标为 secret 的问题回答不进入经验库。凭据字段和可识别的 Bearer/API key 文本会脱敏；任意正文和图片里的秘密无法靠该过滤器完全识别。任务工具已经返回的正文和图片会按用户选择保存在本地。会话目录采用私有目录权限。

## 命令行回查

在项目 Python 环境中执行：

```sh
PYTHONPATH=src .runtime/venv/bin/python -m proximic_ring.agent_workflow.recording.records
PYTHONPATH=src .runtime/venv/bin/python -m proximic_ring.agent_workflow.recording.records --episode <episode-id>
PYTHONPATH=src .runtime/venv/bin/python -m proximic_ring.agent_workflow.recording.records --html
```

`--root <目录>` 可查看指定记录库。无参数列出任务；`--episode` 输出任务、消息、调用、观察和事件 JSON；`--html` 生成静态回查页面。导出的 HTML 中所有文本转义、不加载远程资源、不执行脚本。直接打开文件时提取按钮禁用；从应用“记录”入口打开时，本机服务附加交互脚本，支持逐任务提取和状态更新。应用退出后该连接失效，静态文件仍可回查。

历史的精简 `events.jsonl` 缺少调用参数和返回内容，不能据此补造旧轨迹。完整记录从更新后的 Agent 新连接开始生效。

## 验证

项目根目录的 `conftest.py` 将 pytest 中记录器和回查器的默认目录统一重定向到每项测试的临时目录，避免测试连接污染真实记录库。独立手工验证脚本应显式传入临时 `recording_dir` / `root`。

记录器测试覆盖截图提取、调用参数/结果、失败和缺失信息、最终摘要与增量去重、跨任务迟到事件、双向请求 ID 冲突、启动失败恢复、凭据过滤、未知事件、容量限制、写入故障、关闭以及本地 HTML 转义。

Qt 测试使用项目的 Material 样式：

```sh
PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_CONTROLS_STYLE=Material .runtime/venv/bin/python -m pytest tests/test_agent_recording.py tests/test_agent_step_view.py tests/test_agent_recording_ui.py tests/test_agent_connection.py tests/test_agent_workspace.py tests/test_agent_ui.py tests/test_agent_input_activation.py
```

## 多类型任务执行信息

任务目的与执行工具分开存储。`records.episode_detail` 新增 `execution_profile`，从当前记录快照派生：执行模式（tools / response_only / unknown）、工具条目类型及次数、实际工具标识、返回状态、结束事件是否收到、回答数量、未知类型和质量提示；每个调用保留步骤与原始事件引用。没有工具的任务仍保留用户输入、实际回复、用量和原始事件，不编造 UI 步骤。

`commandExecution`、`fileChange`、`webSearch`、图像、代理委派、MCP/客户端工具均可计入；这仅代表执行能力，不自动把命令任务分类为“代码开发”，也不把提交时前台应用当作实际目标。原始事件和 SQLite 表不做迁移或重写。

导出页面另存 `episode-<id>.profile.json`：`execution` 是上述事实，`classification` 是最近一次有效提取已有的 `task_profile` 及提取时间/提示词版本/审计路径；未提取为 null。分类由后台模型推断，来源 E 编号通过 `.audit.json` 对应原始事件。页面打开和导出不调用模型。
