# 完整 Workflow 操作库与本地推荐

当前版本以**完整 Workflow** 为单位，同一用户意图的不同实现归到稳定操作 ID 下。保留每个版本的自然语言步骤、参数和前提；不依赖公共子链。源码应用支持默认双食指捏合唤起推荐，用户选择后交给现有 Agent 执行；不会自动执行推荐。

现已用 **Sentence Transformers + 本地 BGE-M3** 替换生产 TF-IDF 召回。逐文件、逐函数的数据流说明见 [本地 Embedding 实现导读](../../docs/WORKFLOW_EMBEDDINGS.md)。历史测试与归并依据继续保留。

**当前入组机制已改为与组的通用 Workflow 比较。完整逐阶段代码说明、实质新增规则、候选版本验证和数据路径见 [通用 Workflow 实现导读](../../docs/WORKFLOW_OPERATIONS.md)。**

## 项目内使用

```sh
cd /Users/admin/Downloads/ProximicVoice-main
# 一次性安装独立向量环境和固定版本模型（本次已经完成）
bash scripts/setup-workflow-embeddings.sh
# 当前自然语言 Workflow → 操作库与增量队列；向量在处理入组时准备
bash scripts/run-workflow-recommendations.sh sync
# 导入这次已准备的历史测试数据集（可重复，不增加使用次数）
bash scripts/run-workflow-recommendations.sh import-dataset
# 对新候选进行后台语义复核；复用文本模型，开启思考
bash scripts/run-workflow-recommendations.sh review
# 已完成的候选不重复调用；失败时明确手动重试一批
bash scripts/run-workflow-recommendations.sh review --retry
# 召回、耗时和内存实测；不执行桌面任务，不调用云端模型
bash scripts/run-workflow-recommendations.sh benchmark
open agent-experience/operations/embedding-benchmark/index.html
# 只刷新静态结果，不调用模型
bash scripts/run-workflow-recommendations.sh report
open agent-experience/operations/index.html
# 开启本机交互页；复制终端输出的网址，可获取推荐并提交反馈
bash scripts/run-workflow-recommendations.sh serve
```

应用执行入口：重启源码应用 → Agent「推荐」，或连续两次食指捏合；有参数的流程先填写再执行。完整回查入口：Agent「记录」→「操作归并与推荐」。采集本次前台应用/时间后再显示面板，打开期间冻结四项；后台入组后下次打开或刷新才使用新排序。实现及真实测试见 [原生推荐完整链路](../../docs/WORKFLOW_OPERATIONS.md#原生手势到-agent-的完整链路2026-10-16)。安装版 `.app` 需重新构建；这里没有替换已安装应用。记录服务也可读取安装版目录：脚本最后加 `--root "$HOME/Library/Application Support/ProxiMic Voice/agent-experience"`。所有命令必须使用同一个 root。

交互页由用户主动点击获取，默认展示前四个，并冻结本次位置和版本；后续四个 swipe 方向可映射到这些位置。HTML 页不接手势；原生面板已接通 `double-index-pinch`，无自动弹窗。可选择真实使用或测试演示，手动模拟场景自动切到测试范围。摘要在前台可见 500ms 后才记录曝光并开放「选择此操作 / 现在不需要 / 暂不判断」按钮；选择只返回冻结的流程及缺失参数，不操作电脑。离线 HTML 仅查看静态未知场景分数；独立 `serve` 没有应用内唤起场景回调，不会主动观测桌面或启动模型消费者。

终端测试场景：

```sh
bash scripts/run-workflow-recommendations.sh recommend --scope test \
  --context experiments/workflow_recommendations/contexts/music.json --record
```

输出包含 `request_id`、前四项的位置和通用版本、分数/依据及缺失场景/参数。`--record` 只保存请求快照，不算曝光；不加该参数为纯预览。CLI 无法检测你是否看到了结果，查看后需明确确认（method=cli_confirmed），再用返回的 exposure_id 反馈：

```sh
bash scripts/run-workflow-recommendations.sh expose \
  --request '请求ID' --operation '操作ID'
bash scripts/run-workflow-recommendations.sh feedback \
  --exposure '曝光ID' --operation '操作ID' --kind accepted
# 导出未来 LinUCB 可用的显式偏好样本；测试和用户仍严格分开
bash scripts/run-workflow-recommendations.sh feedback-data --scope test
```

反馈类型：`accepted` 需要、`rejected` 不需要、`ignored` 暂不判断、`succeeded` 明确核验执行成功、`failed` 明确核验执行失败。后两项当前只支持显式记录，没有把 Agent 本轮结束误当成功；一条展示的偏好与执行结果各只能提交一次，重复同值幂等。需要改变评价时重新获取推荐。不能给未展示的候选伪造反馈。

## 数据与后台流程

```text
原始任务记录 / 已有工具结果
  → 现有独立进程提取自然语言 Workflow（desktop-awm-4）
  → 登记样本：任务来源、发生时间、已有场景、完整流程版本哈希
  → 本地 BGE-M3 召回最多三个组的当前通用 Workflow
  → 空闲时文本模型复核完整目标、参数映射及实质新增（开启思考）
  → same 入组；有实质新增才生成候选修订，执行核验后显式启用
  → 稳定 operation_id + 当前通用 Workflow + 来源和版本历史
  → 用户主动请求：本地平滑场景统计排序所有组 → 默认前四个
  → 请求快照 → 客户端确认可见曝光 → 显式反馈（暂不影响分数）
```

应用运行时，操作库同步与复核放在已有 Workflow 后台进程。先处理提取队列，再处理归并候选；Agent 与文本编辑空闲时才开始模型请求。BGE-M3 另开低优先级子进程，CPU 两线程、每批一个文本；收到忙碌信号会结束子进程，已完成向量缓存后续复用。模型计算不持有操作库写锁。已发出的云端复核请求独立完成，不在 Agent 请求链中等待；进程隔离不代表零资源竞争。不主动截图或读取桌面。复核失败保存错误，不自动重试计费；进程中断的复核超过 10 分钟后可 `review --retry` 恢复。

- `agent-experience/operations/catalog.sqlite3`：稳定 ID、版本、任务样本、归并结论、展示与反馈。
- `operations/index.html`：阅读入口，展开可看各版本原文、来源、场景、模型归并理由和参数差异。
- `operations/<version>.txt`：完整自然语言流程，内容来自已有提取，不另生成公共链。
- `operations/catalog.json`：可检查的内部导出，不是投送 Agent 的提示词。
- `operations/definitions/<operation>/<revision>.txt/.json`：当前、候选、历史通用 Workflow 与来源。
- `operations/*.operation-input.json` / `*.operation-result.json`：本版入组与修订请求、结果、耗时和用量。
- `operations/*.review-input.json` / `*.review-result.json`：历史逐对复核输入与模型最终输出、模型/提示词/用量元数据；无隐藏推理或凭证。
- `operations/status.json`：同步状态与旧版流程被跳过等原因。
- `operations/embeddings.sqlite3`：按模型版本和文本内容寻址的目标/步骤向量缓存。
- `operations/embedding-status.json` / `embedding-last-compute.json`：缓存状态、最近模型计算的时间和内存数据。
- `operations/embedding-benchmark/`：实测 HTML/JSON 及每次运行的独立存档。
- `experiments/workflow_recommendations/dataset/`：测试原件、重新提取、清单和样本。导入时为 HTTP 回查复制到 `operations/dataset/`。
- `experiments/workflow_recommendations/contexts/`：可编辑的测试场景，不冒充真实用户场景。

任务样本按 session / episode / 流程索引标识；重提保留历史，不额外计次。当前版保持旧操作 ID，新流程只与组的通用 Workflow 比较，不做全局重分组。旧 complete-linkage、逐对复核与历史结果继续保留供对照，不参与当前在线入组。

## 当前入组算法

1. `operation_retrieval.py` 使用 BGE-M3 目标/步骤加权余弦召回最多三个组；组内成员数不影响 LLM 输入大小。
2. `operation_admission.py` 用 `operation_admission_prompt.py` 判断 same/different/uncertain，保留参数映射和来源。
3. `operation_novelty.py` 检查实质新增的原文证据；重复经验只积累统计。
4. `operation_update.py` 只在确有新增时生成候选通用流程，保留历史。候选不会自动启用；使用 `prepare-trial` / 现有 Trials / `activate` 验证和启用。
5. 当前推荐返回稳定操作 ID 及其当前通用 Workflow；不再从任意历史成员挑一个名字代替概括。

完整规则、阈值和限制见 [当前实现](../../docs/WORKFLOW_OPERATIONS.md)。单段编码仍限2048 tokens，超限明确失败，不静默截断。模型判断不等于语义或执行正确性证明。

## 本地推荐如何学习

当前策略 `scene-statistics-1` 只使用场景条件统计，不调用模型，不使用行为序列，也不让反馈参与分数：

1. 按 session/episode 去重，每个任务总质量为一；多组均分，按 30 天半衰期衰减。仅使用同范围的有效历史时间记录。
2. 总体先验 `P0(g)=(该组质量+0.5)/(全部质量+0.5×组数)`。
3. 按已知条件逐层增加同应用、同四小时时段、同周内/周末，计算 `P(g)=(本层该组质量+3×上层P)/(本层全部质量+3)`。缺失维度跳过，无匹配时回退。
4. 每组输出分数、每层计数、平滑先验、回退理由、使用及反馈次数；分数未校准为真实意图概率。
5. 所有组排序后取默认前四个。完全无证据时均匀冷启动并明确标记；缺失参数会显示，但不偷偷执行或过滤该组。

当前应用取唤起 Agent 时原有焦点捕获得到的元数据，超过120秒作为未知；历史统计按当次唤起时判断新鲜度。没有已记录时间的旧任务不补造小时/星期。窗口标题不计分，目标应用不额外加分，未记录的前序操作不推断。

`user` 和 `test` 的使用计数、场景统计、请求、曝光和反馈严格分开；来源不明不算真实使用。测试流程可成为冷启动候选，测试次数不冒充常用程度。旧曝光记录保留但不追认为新标准曝光。`accepted/rejected`、展示时场景和特征、位置、版本会完整保存，留作 LinUCB 输入；忽略、未展示和执行结果不转换为偏好标签。

实现文件、HTTP接口和四张新增表详见 [主动请求推荐](../../docs/WORKFLOW_OPERATIONS.md#主动请求推荐默认前四个)。

## 已有测试数据与前版归并记录

- 原封保留 26 条历史运行（11 baseline、8 自然语言、7 历史 JSON）及任务输入。
- 使用 3 份已有最新版种子流程，并从每类一条独立 baseline 的实际记录重新提取，得到 6 个完整 Workflow 样本；没有重放桌面操作。
- 后台模型复核三对候选，得到 **3 类操作，每类 2 个完整版本**：备忘录新建并填写笔记；Apple Music 随机播放并报告歌名；Safari 打开目标网址并确认页面。
- 归并没有使用测试 case 的分类标签；case 只用于选择需要重新提取的来源。规则先对完整内容计算候选，再做语义复核。
- `index.html` 的三个固定场景演示只是检查排序是否符合输入，不是推荐准确率评测；这批任务不足以证明真实使用频率、推荐效果或长期收益。

原 Workflow 生成及 baseline 对比入口保持不变，见 [两组执行测试指南](../workflow_trials/README.md)。

## 先前 TF-IDF 版本的验证记录

工作区和 Downloads 各通过 234 项相关回归测试，包括版本/ID、完整链归并、参数条件、测试隔离、场景缺失、反馈幂等、后台请求去重、HTTP 同源校验，以及既有 Agent/Workflow/Trials 行为。浏览器中验证了“模拟：音乐 → 获取本地推荐 → 暂不判断”，仅保存一条 `test / ignored` 反馈，真实反馈仍为空；报告的本地链接全部存在。

Downloads 还同步登记了已有真实任务“将本地图片附加到 macOS 备忘录”，作为第四个独立操作保留。它不属于测试数据集，也没有与文字笔记操作归并。目前总计 4 类操作、7 个版本、1 条真实任务和 6 条测试样本；真实样本不足以衡量个性化推荐准确率。
