# Workflow 提取与两组测试

用户入口和完整命令见 [Workflow 操作指南](../experiments/workflow_trials/README.md)。默认输出位于项目的 `agent-experience/`；安装版以应用显示的记录目录为准。

## 当前提取格式（2026-10-20）

新版 `agent-awm-7` 只要求目标和自然语言步骤，另保留 name、parameters 和独立审计来源。
必要条件、关键判断和有效替代方法融入步骤，不再单独生成 completion/check/conditions/fallbacks/task_profile。
步骤数量与表达可由模型决定，原始日志保持不变；旧格式兼容读取。
具体二分类与统一泛化规则见 [当前操作管线](WORKFLOW_OPERATIONS.md#当前管线二分类与统一泛化2026-10-20)。
下文有关旧字段结构与历史提取实验的内容保留用于追溯，不再作为新 prompt 的要求。

## 生成入口与结果

应用内：Agent「记录」→ 选择任务「提取 / 重新提取」→「查看 Workflow 草稿」。也可启用任务结束后的自动提取。

项目终端：

```sh
bash scripts/run-workflows.sh --list
bash scripts/run-workflows.sh --latest --run
open agent-experience/workflows/library.html
```

生成结果：`workflows/library.html` 是阅读入口；`<job>.flow.txt` 是可复制的自然语言 Workflow。`.flow.json` 是内部结构；`.audit.json` / `.audit.html` 保留引用和提取依据。原始会话保留在 `sessions/`，不混入 Workflow 正文。旧版本保留，手动重新提取后才升级，不会自动重提全部历史记录。

## 后台提取流程

1. 独立记录器保存真实调用、返回文本、已有截图和公开推理摘要。
2. 独立后台进程发现结束任务，等待结尾稳定后生成证据快照；没有明确观察就不补齐。
3. 本地规则精简轨迹，再去重模型输入：已核验的通用文档省略，明确配对的常规自动许可汇总，相同正文共享一份；所有动作、观察、澄清和错误保留。完整事件引用保留在原证据快照；按最终输入检查 480,000 字符本地上限（字符数不等于 token 数，服务端仍独立校验模型上下文容量）。归组/修订使用同一上限，完整传入结构化 Workflow。通过后按 session、episode、证据指纹和提示词版本持久入队。重复点击复用当前排队 / 执行中的任务，包括等待自动重试的任务。
4. 等前台 Agent 与文本编辑空闲，复用文本服务配置发独立请求。当前适配火山方舟 Responses，开启思考，180 秒超时、16,000 输出 token；不保存隐藏推理。
5. 当前 `agent-awm-6` 提取自然语言 `instruction`、可选关键 `check`、最终 `completion`，以及任务类型/输出类型。工具步骤和回答步骤分别标注内部 `kind`；一条可复用回答也可形成单步 Workflow。禁止复用历史 AX 数字编号、坐标、临时变量或生成答案；完成条件不是历史成功证据。
6. 校验结构、参数与引用；tool 步骤须关联真实调用，response 步骤须引用实际结束的 Agent 回答，不能只引用户目标或推理摘要。分类也保留证据引用。校验失败保存拒绝记录，不作为可执行草稿。
7. 程序直接排版模型生成的文字，输出 `.flow.txt` 和流程库；JSON 只留作内部校验与审计。没有第二次文字转换模型调用。

提取和本地校验失败默认自动重试 **3 次**，即首次请求加重试最多 **4 次模型请求**。网络超时、临时 HTTP 错误、响应不完整、JSON 格式或引用/参数校验失败均进入此策略；间隔为 10、30、60 秒，到期后仍等待 Agent 空闲。原始证据不变，重试携带上一次错误以指导模型修正，仍开启思考。每次尝试独立保存，`job_retries` 关联原始作业、前次作业、次数和最早执行时间；重启保留等待中的队列。成功立即停止重试，应用后台与 `--run` 继续导入、归组、文案及参数整理。

配置/认证错误、来源缺失、输入超限和进程中断导致远端结果未知时不自动重发。耗尽次数后保留最后错误，手动重新提取可开启一轮新预算；升级不会重新发送旧失败任务或重复提取已成功任务。此策略只适用于 Workflow 提取及其本地校验，归组/参数等后续阶段的失败仍通过各自入口重试。

校验是本地程序完成的，不调用另一个模型。它检查字段类型、参数声明与占位符一致性、来源是否在已提供资料中、步骤来源类型和历史 UI 数字索引等约束；不会证明文字与证据语义完全一致，也不实际执行 Workflow。通过后仍是未独立执行验证的草稿。

进程隔离让提取不阻塞 Agent 对话，不代表完全没有资源竞争。测试任务不会自动进入提取队列；应用内测试窗口固定任务后也会暂缓新后台提取。单任务提取后可进入完整操作归并与推荐；不启用公共链归纳或批量历史重提。

## 只测去重，不调用模型

```sh
PYTHONPATH=src .runtime/venv/bin/python -m proximic_ring.agent_workflow.reporting.dedup_benchmark \
  --root ./agent-experience
open ./agent-experience/workflow-dedup/report.html
```

默认测量该目录全部已结束任务；可以追加 `--session <session_id> --episode <episode_id>` 只测一个任务。`--repeats 7` 是本地去重次数，不是模型请求次数。输出到 `workflow-dedup/report.html` 和 `report.json`；字符数使用相同的紧凑 JSON 序列化，不能直接换算成 token 或推断提取加速。

生成报告后，总览下的“独立测试与历史报告”也会在下次导出总览时收录它。

实际新提取会保存 `.input.json`（原证据与完整来源）、`.trajectory.json`（精简轨迹）、`.trajectory-selection.json`（省略规则及来源）、`.model-input.json`（实际模型内容）、`.compaction.json`（字符数与规则统计）。不删除重复执行的动作，不合并不同调用的观察，不新增截断；旧版单项截断标记仍保留。模型输入超限仍停止并说明减少比例，本版尚不分段。

## 轨迹精简与提取入口测试（不调用模型）

餐厅任务可复现命令：

```sh
PYTHONPATH=src .runtime/venv/bin/python -m proximic_ring.agent_workflow.reporting.trajectory_benchmark \
  --root ./agent-experience \
  --session 9e5600e338ff4e919ef8b0a0eda0b456 \
  --episode 6298cdffd4454572af2ecc4d5fc2fb9c
open ./agent-experience/workflow-trajectory/report.html
```

报告比较原始证据、上一版仅去重、本版轨迹精简再去重的字符数和本地加工耗时。验证使用真实 `source_packet` 入口，入队验证在临时目录完成后清理；正式队列、分组和推荐数据不动。长度通过只代表满足项目保护值，不证明模型提供方会接受或已生成 Workflow。

规则细节在 `extraction/workflow_trajectory.py`、`workflow_trace.py`、`workflow_payloads.py` 的注释及 [全流程文档](AGENT_WORKFLOW_PIPELINE.md)。v2 不发送推理摘要或进度播报，同次结果和观察仅保留一份正文；省略空布局、已知能力装饰及符合规则的 SVG 源码。语义标签、数值、调用、错误和用户更正保留，未知格式原样保留。精简清单的 `trace.sources` 保存完整来源映射，`trace.changes` 保存规则、行号和原内容路径。源码应用重启后加载新逻辑。

## 通用任务与 v6 简洁产物

模型只生成具体自然语言步骤 `instruction`、可选关键节点 `check` 和一个最终 `completion`。普通点击或输入不强制检查；已有返回足够时不新增截图或重读。JSON 是内部校验及来源容器，`.flow.txt`、库页面和执行入口使用同一份自然语言。旧版历史不删除，归并和执行入口兼容旧版与 v5/v6。

实际发送内容在 `.model-input.json`；`.audit.json` 保留模型撰写的来源关联，E 编号不进入自然语言步骤。两条真实任务的本次生成结果在流程库各自任务下显示最新成功版本，旧版在“提取历史与旧版本”中保留。

## 问答、分析及非界面任务

- 原始记录覆盖所有可获得的 RPC；执行信息 `execution_profile` 根据已记录工具类型、调用状态、消息和来源事件生成，不额外调用模型，也不采集屏幕。未知类型和记录缺失明确保留。
- 同一次后台提取生成 `task_profile`（任务类型、输出类型、来源）。任务类型支持问答、检索研究、写作、分析、计算、代码开发、文件处理、应用操作、沟通发送和其他，可多选；工具执行语言不等于任务目的。类型只是模型推断，无法证明目标成功。
- 审计最外层分类代表整个任务；每个 Workflow 的分类描述自己的目标。参数化自然语言步骤仍是执行主体，分类、工具事实、来源不会变成额外执行指令。
- 纯问答也提取：例如“解释某个检索方法的原理与局限”，可提炼为一步“针对 {{method}} 解释原理与局限”。下次重新生成答案，不复用历史答案；不补造工具、思维链或虚构验证。没有可见回答/操作依据时可以保留空提取并说明缺失，不能凭空制造流程。
- `.flow.json` 的 `task_profile` 只含分类；审计保存分类来源、步骤 `kind` 和引用。原始记录页面新增“任务类型与执行方式”及 `.profile.json`，只读取已有分类，不在打开页面时请求模型。
- 新流程可以进入现有通用操作库、后台语义复核和本地推荐；不要求至少两步、不要求出现应用。同为问答不自动同组，完整目标、输入角色、输出要求及副作用仍决定归并边界。

旧任务打开记录即可派生工具事实；旧任务没有分类时在记录页显示“尚未后台分类”，手动重新提取才生成 v6，不付费批量迁移历史。重启源码应用后，新结束任务使用新版后台提取。

## 测试入口与结果

应用内「对比测试」：选择草稿、填写参数与共同起点、固定任务，然后分别执行「原任务」和「自然语言步骤」。旧快照可查看，不能重新执行。

项目终端：

```sh
bash scripts/run-workflow-trials.sh workflows
bash scripts/run-workflow-trials.sh prepare --workflow '流程标识' --task my-task
# 编辑 tasks/my-task/task.json 中的参数和共同起点
bash scripts/run-workflow-trials.sh preview --task my-task --mode steps
bash scripts/run-workflow-trials.sh run --task my-task --mode baseline
# 手动复位
bash scripts/run-workflow-trials.sh run --task my-task --mode steps
bash scripts/run-workflow-trials.sh report
open agent-experience/workflow-tests/comparison.html
```

两组均走现有 Flow 的新临时会话和 turn/start；baseline 只给目标，steps 只给步骤。没有额外 guidance、JSON Workflow、暂停 / resume，也不要求每次工具调用固定为“一次动作 + 一次观察”。沿用现有权限、取消、当前界面定位与结果核验规则。

任务输入单独在 `experiments/workflow_trials/tasks/<name>/` 的 `baseline.txt`、`steps.txt`、`task.json` 中。测试冻结为 schema v5；schema v1–v4 与旧 JSON 组只供历史回查。报告按不可变任务版本和执行设置隔离比较，实际投送文本逐轮保存为 `.prompt.txt`。计时从请求受理到 Agent 本轮结束，含线程准备和人工等待；完成不等于目标成功，需要核验。

提示词、协议与主要实现：`workflow_prompt.py`、`workflow_evidence.py`、`workflow_model.py`、`workflows.py`；自然语言输出在 `workflow_artifacts.py`，两组输入和计时在 `workflow_trials.py`，CLI 在 `workflow_trial_cli.py`。

## 完整操作归并与推荐

新增「操作归并与推荐」阅读入口和 `scripts/run-workflow-recommendations.sh`。基于完整自然语言 Workflow，后台模型复核同类操作；本地排序与反馈独立保存，测试数据不计入真实习惯，不要求公共子链。实现、算法依据、数据集、反馈和命令见 [操作推荐指南](../experiments/workflow_recommendations/README.md)。

当前通用操作的增量入组、候选版本与启用入口：见 [从经验到推荐的逐阶段代码导读](WORKFLOW_OPERATIONS.md)。
