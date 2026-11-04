# 昇腾 Clef 常驻服务与项目接入

2026-11-04 已部署并实测。当前主项目为单次联合 `ClefIntentRouter`：总体意图＋修改、插入、润色、格式整理、翻译、按目标创作六项任务。释字和重述并入修改，扩写及精简并入润色，格式单独保留。服务动态接收 schema，无须重载权重；最新结果见 [六类分类报告](reports/clef-six-tasks-2026-11-04.html)。

## 当前连接

- 服务器：`root@mythlink.cn:20030`（SSH）。
- 模型：`Cloudflare/clef-flash`，revision `41318bf2106d135c7a92da745fec4b255279b4de`。
- 硬件：Ascend 910B3，64 GiB HBM，逻辑设备 `npu:0`。
- 环境：Python 3.12.13、torch/torch_npu 2.10.0、Transformers 5.10.2；BF16，eager attention。
- 远端接口：`127.0.0.1:30000/v1/systemone`；仅监听服务器回环地址。
- 本机接口：`http://127.0.0.1:30000/v1/systemone`，通过 SSH 本地转发访问。
- 当前 HTTP 层未配置额外 Bearer token，远程访问使用 SSH 密钥认证。服务也支持可选的 `CLEF_API_KEY`。

模型只加载一次。加载和推理都在同一个专用线程执行，串行使用 NPU，最多容纳 4 个运行中或排队请求。`GET /health` 不等待推理队列。原部署用旧一级和二级请求预热后监听；当前评测另行预热联合请求。

接口接受 `model/state/questions`，支持文本或 JSON state 及 choice/score/noul；当前部署不接收图片、视频。请求体上限 128 KiB，最多 32 个问题，模型输入上限 8192 tokens。超长输入直接拒绝，避免上游编码器静默截断 state。日志只记录 token 数、问题数、耗时及错误类型。

## 从主项目使用

实际连接参数保存在本机未入库的 `.runtime/clef/connection.env`，脚本自动配置现有 `PROXIMIC_CLEF_*` 环境变量，并让回环请求绕过 HTTP 代理。

在项目根目录运行：

```bash
bash scripts/clef-remote.sh status
bash scripts/clef-remote.sh check --output .runtime/clef/routing-evaluation.json
bash scripts/clef-remote.sh pipeline --output .runtime/clef/pipeline-evaluation.json
bash scripts/clef-remote.sh ui
```

`check` 使用现有的 32 条分类样例。`pipeline` 读取本机主项目已有文本模型配置，只发送合成样例，通过真实 `TextProcessingWorker` 运行分类及后续生成；可加 `--case precise-replace` 只测一例。它不会修改桌面设置或写入其他应用。

`ui` 确保隧道存在，再带着连接配置启动源码版主项目。实际使用自动语音时，在“输入与控制”选择“自动判断”。本次保留了用户原有的手动模式，没有重启正在运行的应用、修改手势绑定、重新打包或更新已安装输入法。Finder 启动的旧应用不会自动继承此脚本的环境变量。

隧道控制命令：`bash scripts/clef-remote.sh start` / `stop`。已验证断开重连后服务器 PID 不变，并可继续完成真实分类。

## 服务器管理

部署文件位于 `/root/clef-test/`，包含 `serve_clef_npu.py`、`clef-service.sh`、`warmup.json`。

在服务器上运行：

```bash
bash /root/clef-test/clef-service.sh status
bash /root/clef-test/clef-service.sh start
tail -f /root/clef-test/service.log
# 需要停止时：
bash /root/clef-test/clef-service.sh stop
```

该容器 PID 1 是 sshd；当前使用 nohup 常驻，可跨 SSH 断线继续运行。它不是开机自启或崩溃自动重启的系统服务：容器重启或服务退出后需要重新执行 start。本机隧道断线后可通过 start/check/ui 重建。

## 2026-11-04 实测

### 首次部署基线：v2 的 32 条分类样例

| 项目 | 结果 |
| --- | --- |
| 网络、HTTP 或模型执行故障 | 0 |
| 一级听写/编辑正确 | 27/32（84.4%） |
| 完整任务及辅助标签精确匹配 | 15/32（46.9%） |
| 空编辑计划被客户端拒绝 | 1 |
| 8 条听写中的一级误判编辑 | 4，其中 1 条在二级被拦截；3 条返回了错误编辑计划 |
| 分类总耗时 median / P95 | 1.548 s / 1.790 s |

耗时包含本机客户端、SSH 转发及条件性的两个 HTTP 请求；这些是单客户端、串行、小样例的结果，不是并发容量指标。现有测试工具的 `errors=1` 是空编辑计划校验失败，不是服务连接失败；`false_edit_count=3` 只统计最终返回编辑计划的情况。

代表性问题：

- “他说把上一句删掉就可以了”被当作精确删除命令。
- “翻译成英文”在存在原文时仍被分到听写。
- 插入、扩写、润色、结构调整经常额外命中 `precise_edit`。
- “周四下午”能识别为编辑，但漏掉直接重述辅助标签。
- 带有 OCR 干扰指令的“我收到了”被一级判为编辑，随后因所有编辑任务为 skip 被客户端拦截。

这些现象表明服务及接口可用，但该轮分类效果尚不能作为自动编辑质量验收通过。该轮使用原有 v2 提示词；后续一级改进见下文。

### 真实 Worker → Clef → 已配置文本模型

7 条合成样例中，6 条完成实际文本模型调用并返回正文；1 条空编辑计划被拦截，未进入生成。生成服务使用主项目已有配置 `deepseek-v4-1-flash-260910`。6 条完成调用的总耗时约 1.54–2.66 秒。

- 精确修改：`我今天想喝咖啡。` + “把咖啡改成牛奶” → `我今天想喝牛奶。`
- 听写：“我明天下午到了给你发消息” → `我明天下午到了给你发消息。`
- 插入：返回 `请明天提交。谢谢配合`，但分类计划额外包含精确修改。
- 翻译：虽然文本模型返回了英文，Clef 实际路由为听写，不能据此判定自动编辑流程语义正确；应用的听写交付方式不同于编辑替换。
- 混合修改与润色：标签正确，但生成的 `麻烦请您让张玮明天把文件给我。` 引入了“让某人转告”的潜在语义变化，需继续评测生成质量。

这次验证的是源码客户端、HTTP 服务及文本处理 Worker 的软件链路。未采集真实麦克风/戒指输入、未做实时 OCR、未写入输入框或向聊天应用发送消息，因此不覆盖完整物理设备和输入法写回验收。

### 自动回归及证据

相关 137 项测试通过：HTTP 服务/客户端 57 项，自动语音、历史和文本处理 80 项。覆盖两级调用、无效请求、鉴权、超时、队列边界、取消及编辑计划校验。HTTP 测试曾因执行沙箱禁止绑定回环端口而失败，移到允许回环端口的执行环境后通过。

本机报告（未入库）：

- `.runtime/clef/routing-evaluation-2026-11-04.json`：32 条原始分类概率、预期/实际结果与耗时。
- `.runtime/clef/pipeline-evaluation-2026-11-04.json`：7 条真实软件链路输入输出。
- `.runtime/clef/reconnect-check-2026-11-04.json`：隧道重连后的真实分类。
- `.runtime/clef/http-regression.log`：57 项 HTTP 回归结果。

接口依据：[固定版本的 Clef 官方代码](https://huggingface.co/Cloudflare/clef-flash/blob/41318bf2106d135c7a92da745fec4b255279b4de/joint_schema_model.py)。

### 一级提示词 v3 与可编辑样例

`voice-intent-hierarchical-v3-primary` 只修改一级 instructions 和两个类别定义；二级提示词、权重及概率选择规则未改。
原有 32 条从 27/32 提高到 32/32，补充 24 条和后加 12 条也通过，最终共 68/68。
这 68 条均已用于调优。后加 12 条首次未参与调优时为 11/12；失败的照录翻译指令变体随后用于完善同义表达规则。
因此这些结果不能作为独立测试准确率，也不能保证真实新输入全部正确。

新一级客户端耗时 median 1.835 s、P95 2.032 s，包含网络和服务端处理；旧版一级中位数约 0.643 s。
更长的提示词带来明显耗时增加；前述 1.548 s 是旧版两级混合总耗时，不能当成旧版一级时间。
本轮 98 项相关回归通过，未重新验收二级计划、文本生成或实际设备写回。

- [完整报告：68 条输入、每类概率、旧版对照、prompt 前后对比](reports/clef-primary-2026-11-04.html)
- [原始 JSON 与各轮统计](reports/clef-primary-2026-11-04.json)
- [提示词逐行 diff](reports/clef-primary-prompt-2026-11-04.diff)
- [独立样例文件、模板及使用方法](../tools/data/README.md)

```sh
bash scripts/clef-remote.sh primary --cases tools/data/automatic_voice_routing_cases.json --output .runtime/clef/primary-results.json
```

一级测试会同时生成同名 HTML。一级、两级和完整流程程序都支持 `--cases`、可重复的 `--case` 和 `--validate-only`。
修改源码提示词后，重新启动源码主项目即可使用；模型服务接收每次请求中的提示词，无须重新加载权重。

### v3 一级接现有二级的完整复测

2026-11-04 使用当前 v3 一级和未修改的二级提示词，重新跑原有 32 条：一级 32/32，
24 条编辑样例中二级计划完整匹配 12/24；包含 8 条正确听写的完整路由结果为 20/32。
两级请求均无调用错误。总分类耗时 median 2.663 s、P95 2.866 s（含网络）。
其余 12 条仍有多选精确修改、漏选直接重述或插入等问题，尚未完成二级质量验收。

```sh
bash scripts/clef-remote.sh check --cases tools/data/automatic_voice_routing_cases.json --output .runtime/clef/my-routing-results.json
```

该历史报告按一级结果决定是否请求二级；当前 `check` 命令已改为一次联合请求，并同时生成 JSON 和 HTML。
[查看逐条两级概率报告](reports/clef-routing-2026-11-04.html) · [原始 JSON](reports/clef-routing-2026-11-04.json)。
每条编辑样例展示全部 10 个问题的 apply/skip 概率；听写样例标记未调用二级。

### 当前联合结构命令

```sh
bash scripts/clef-remote.sh check --output .runtime/clef/joint-results.json
bash scripts/clef-remote.sh compare --output .runtime/clef/joint-comparison.json
bash scripts/clef-remote.sh pipeline --case character-explanation --case redictation-fragment --output .runtime/clef/joint-pipeline.json
```

`primary` 仅复现冻结旧 v3 一级，当前应用应使用 `check` 评测。两个 hint 从主动分类及生成计划删除，旧历史仍可读。
分类矛盾、概率并列拒绝计划，不以任一细类分数覆盖总体意图；UI 沿用有错误提示的原始听写保留流程，不修改已有原文。

### 联合结构最终对照（2026-11-04）

主项目只运行 C；A/B 是离线对照入口，不在一次业务请求中并列运行。总体意图是各方案共同的字段，不是第四种模型方案。

| 方案 | 开发集总体意图 / 完整计划 | 新合成留出集总体意图 / 完整计划 | 留出集听写 P50 | 留出集编辑 P50 |
|---|---|---|---|---|
| A 冻结旧两级 | 68/68 · 50/68 | 23/24 · 15/24 | 1.712 s | 2.586 s |
| B 一级补定义，仍两级 | 67/68 · 49/68 | 23/24 · 15/24 | 1.943 s | 2.842 s |
| C 一次联合九字段 | 68/68 · 64/68 | 23/24 · 20/24 | 2.793 s | 2.807 s |

延迟均含 HTTP/SSH 网络及服务器处理。联合方案提升了这批样例的完整任务匹配，但没有比旧两级更快。
留出集 C 有两条矛盾被拒绝，另两条任务多选；没有依据这些留出结果继续调优。
完整匹配统一按总体意图和八个任务评分，旧 hint 不计分；因此不能直接与旧十字段完整匹配报告比较。

[三方案完整报告及所有概率](reports/clef-joint-comparison-2026-11-04.html) ·
[32 条真实 Worker 分类与生成结果](reports/clef-joint-pipeline-2026-11-04.html) ·
[提示词前后对比](reports/clef-joint-prompts-2026-11-04.diff)。

32 条软件流程调用全部完成，分类计划29/32完整匹配；报告标注了两例创作语义问题，不能据调用完成断言生成质量全部通过。
分类、历史、共享生成回归通过；扩展 UI 回归中的日志刷新测试两次未通过，后台权限日志在显示快照与断言之间插入。
本轮没有修复该无关日志逻辑，也未重新打包、重启用户应用或验收真实设备和输入法写回。

### 内容整理合并（v5 基线）

`router_prompts.py` 的 `voice-intent-joint-v5-organize` 一次输出总体意图和七类任务；`organize` 合并原精简摘要与结构格式调整。
生成规则位于 `automatic_prompts.py`：只重排、分点或换格式时保留全部信息，明确压缩时才摘要。
旧八类报告与历史记录保留原始标签和概率。新报告见 [合并后全部概率](reports/clef-organize-2026-11-04.html)。

| 样例组 | 旧 C 输出按合并口径离线重评分 | 本轮七类 C 完整匹配 |
| --- | --- | --- |
| 已参与调优的开发样例 | 65/68 | 68/68 |
| 复用的旧留出样例 | 20/24 | 18/24 |
| 本轮新增边界样例 | 未测试 | 11/12 |

合计总体意图 102/104、完整计划 97/104；4 条联合矛盾被拒绝。旧结果未重新推理，不能由此宣称泛化准确率提高；合并后旧留出回归反而减少 2 条匹配。全部 104 条均保留总体意图及七个任务的每一选项概率。
分类总延迟（含网络）听写 P50/P95 为 2.717/2.874 秒，编辑为 2.757/3.005 秒；不含 ASR、OCR、生成及写回。

[12 条完整软件流程报告](reports/clef-organize-pipeline-2026-11-04.html)：调用全部完成，分类 11/12 完整匹配，7 条预设文本约束通过；仍存在转述误判、创作补造信息、听写输出错误，不能视为全部生成质量通过。报告逐条标注语义问题；尚未验收真实麦克风、OCR 或 IME 写回。相关代码测试 210 项通过。
[提示词改动](reports/clef-organize-prompts-2026-11-04.diff) 记录了旧八类到七类的调整。`routing_schema()` 直接返回包含八项问题的完整字典，没有分阶段请求。

当前保存的 `input/routingMode` 在本轮核对时为 `manual`。上述接入是源码路径，尚未重打包安装版；启动源码并切换“自动判断”才会使用 Clef。配置入口仍是 `bash scripts/clef-remote.sh ui`。

### 听写选项定义调整（旧 v9 基线）

`voice-intent-joint-v9-dictation` 仅修改 `CLEF_ROUTING_CRITERIA["dictation"]` 的文字，另更新版本号：明确转述建议、评价和个人未来计划中提及的操作不自动成为编辑委托；原文适合处理也不足以改变这个判断。保留实际委托及更新本人已有输入的边界。总体 edit 选项、共享规则、七类任务定义、判定阈值与生成提示词均未改动。

用户确认“没问题，我带两份过去”是在更新本人之前输入的“我带一份过去。”，当前活动样例改标为编辑＋精确修改。旧 v5 输出也按同一修正标签离线计分：总体意图 103/104，完整计划 97/104，联合矛盾 4 条。当前 v9 为总体意图 103/104、完整计划 97/104、联合矛盾 2 条。

三条转述／计划错误全部修复；三条新退步是“下周二上午”漏选精确修改，以及法语、葡萄牙语翻译请求额外启用精确修改。“一份改两份”本轮仍误判听写，标签修正不代表模型已修复。不能将本轮称为整体准确率提升。

[当前全部概率与新旧对照数据](reports/clef-dictation-criteria-v9-2026-11-04.html)，[JSON 对照](reports/clef-dictation-criteria-v9-2026-11-04.json)，[精确提示词 diff](reports/clef-dictation-criteria-prompts-2026-11-04.diff)。104 条均为已用于开发的回归样例，不能视为独立泛化测试；本轮未调用文本生成或执行 IME 写回。52 项相关接口／报告测试通过。v6、v7、v8 是未采用候选，此轮记录的源码为 v9，后续已改为下述六类 v12。


### 六类 v12 与听写优先校验（2026-11-04 当前源码）

分类字段为修改、插入、润色、格式整理、翻译、按目标创作。整句替换仍属于修改；插入要求已给出的原话；扩写、缩写、摘要并入润色；格式整理单独保留。标签、分类提示词、生成模块和活动样例均已更新。

- 分类提示词：`voice-intent-joint-v12-six-tasks`；生成提示词：`automatic-composed-v5-six-tasks`。
- 校验策略：`dictation-priority-v1`。总体听写优先于任何任务 apply；总体或任务概率并列、编辑全 skip 时回退听写。保留原始选择与概率，记录被忽略任务。冲突／不明确回退在历史列表、详情及 HTML 报告标黄。
- 130 条开发回归：原始总体意图 129/130，原始完整分类 112/130；对同一批实际响应重放新校验后，最终处理匹配 124/130，其中 12 条标黄听写冲突。后者是校验策略的效果，不是模型原始准确率提高。
- 最终剩余 6 条：4 条组合任务漏选、1 条创作多选润色、1 条转述误判编辑。
- 26 条再次实际运行 Worker → Clef → 文本模型：26/26 返回正文，原始完整分类 22/26、最终处理匹配 24/26；预设文本约束 18/19 通过。语义审阅有 3 条明确问题（归还时点变化、转述误编辑、照录带入外层指令），另有扩写条件需复核、故事偏长两条意见，详见报告。
- 212 项相关回归测试、3 项历史记录 UI 测试通过，已检查标黄截图。常驻服务状态 ready，模型 clef-flash，设备 npu:0。

[汇总报告](reports/clef-six-tasks-summary-2026-11-04.html) 包含历次候选、旧 v9 可比子集、全部错误分类和复现命令；[130 条概率明细](reports/clef-six-tasks-2026-11-04.html) 包含每个结果的全部 14 个概率；[26 条实际生成](reports/clef-six-tasks-pipeline-2026-11-04.html) 保留实际生成请求、正文和语义意见；[校验层 diff](reports/clef-dictation-priority-2026-11-04.diff) 记录本次听写优先变更。

本轮 130 条均已用于开发，不能作为独立测试准确率。分类耗时 P50/P95 为 2.992/3.284 秒，包含 HTTP/SSH 和服务端处理，不含 ASR、OCR、生成或写回；校验重放保留原始测得时延。未重打包、重启用户应用或进行真实录音、OCR 采集、输入法写回验收。

```sh
bash scripts/clef-remote.sh check --cases docs/reports/clef-six-tasks-cases-2026-11-04.json --output .runtime/clef/my-six-routing.json
bash scripts/clef-remote.sh pipeline --cases tools/data/clef_six_task_boundary_cases.json --output .runtime/clef/my-six-pipeline.json
```
