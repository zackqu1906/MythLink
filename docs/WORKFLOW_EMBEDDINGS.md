# 本地 BGE-M3 Workflow 召回：入口、数据流与代码导读

当前入组已升级为“新 Workflow → 组的通用 Workflow”：本地 BGE-M3 召回，后台模型复核，实质新增才生成候选修订。完整最新管线、逐文件说明和启用规则见 [通用 Workflow 实现导读](WORKFLOW_OPERATIONS.md)。本文继续说明向量运行时和历史实测；旧 complete-linkage 已退出在线管线。

## 使用入口

### 安装版与新电脑（2026-10-20）

主界面 **设置 → Agent → 语义召回模型** 提供下载、取消、继续下载、校验修复。
未安装显示“未下载”，完整模型校验有效时显示绿色“已下载”。固定版本文件共 2,293,315,801 字节（约 2.3 GB），
下载源为 Hugging Face 官方仓库；文件大小及 SHA-256 固定在随包发布的 `bge-m3.json`。
模型不能下载时可从可联网的电脑取得该指定版本，把完整模型目录复制到下述固定位置，再点击“修复模型”校验。

DMG 包含编码运行时，用户不需要 Python、pip 或源码。后台通过自身可执行文件的
`--workflow-embedding-worker` 入口启动独立 CPU 进程。主界面和 Agent 对话进程不加载权重。
采用官方 BGE-M3 的 CLS pooling + L2 归一化路径，保留原 1024 维、2048 tokens 上限；
依赖改用安装包已有的 Transformers 5.15.0、Torch 2.11.0、NumPy 1.26.4。
编码器版本更新，因此旧向量缓存不会与新编码器混用；原 Workflow、分组和反馈不删除。

旧版独立 venv 不再是必需项，旧模型目录可直接校验复用，无需重新下载。
首次校验会读取完整权重；此工作在后台进行。未配置文本服务时提取任务保持排队，
未准备好召回模型时归并等待；配置或安装完成后后台继续，基础 Agent 与原始记录仍可用。

运行时实现：`embedding_install.py` 负责续传、校验和安装锁；`agent_setup.py` 提供非阻塞 UI；
`operation_embedding_worker.py` 负责离线计算；`AgentSetupRows.qml` 提供设置入口。
`PROXIMIC_DATA_HOME` 或 `PROXIMIC_EMBEDDING_RUNTIME` 可隔离冷启动验证数据。
新电脑其他必要准备与验收见 [Agent 安装准备](AGENT_SETUP.md)。

下文涉及独立 venv 和旧编码器的内容是历史实现与实测记录；当前安装入口以上述设置界面为准。

在 Downloads 项目终端运行：

```sh
cd /Users/admin/Downloads/ProximicVoice-main

# 一次性准备独立环境和固定版本模型；本次已经完成。
bash scripts/setup-workflow-embeddings.sh

# 导入已有自然语言 Workflow、准备入组队列；不调用云端模型。
bash scripts/run-workflow-recommendations.sh sync

# 后台复核新候选：复用现有文本模型，开启思考。
bash scripts/run-workflow-recommendations.sh review
# 明确重试失败/已中断超过10分钟的复核：
bash scripts/run-workflow-recommendations.sh review --retry

# 实测六条现有流程、同义改写与反例；不执行桌面任务、不调用 LLM。
bash scripts/run-workflow-recommendations.sh benchmark

# 查看报告；serve 提供本地推荐/反馈交互。
bash scripts/run-workflow-recommendations.sh report
bash scripts/run-workflow-recommendations.sh serve
```

结果入口：

- `agent-experience/operations/index.html`：操作库、新候选、复核说明、旧 TF-IDF 历史。
- `agent-experience/operations/embedding-benchmark/index.html`：可读实测报告。
- `agent-experience/operations/embedding-benchmark/report.json`：完整分数、标签、逐条耗时、测量定义。
- `agent-experience/operations/embedding-benchmark/runs/<id>/`：每次实测单独保存，不覆盖历史。
- `agent-experience/operations/embedding-status.json`：最近一次向量准备状态，含缓存命中/缺失。
- `agent-experience/operations/embedding-last-compute.json`：最近一次真正启动模型的耗时、峰值 RSS、各文本 token 数。

重启从源码运行的应用后，任务完成 → 原有后台提取 → 新召回 → 后台复核会自动衔接。已安装的 `.app` 没有被替换，需重新打包才能包含代码更新。手动 CLI 也会读取新版后台进程发布的忙碌状态；旧应用没有这个信号。

## 模型放在哪里

macOS 默认共享目录：

```text
~/Library/Application Support/ProxiMic Voice/workflow-embedding/
  venv/                  独立 Python 环境
  models/bge-m3/         固定版本的本地模型和分词器
  worker/                安装版可使用的后台编码脚本副本
```

工作区和 Downloads 共用这一份模型，不把 2.27 GB 权重复制进仓库。不修改 Agent/ASR 的 Python 依赖。可在启动应用和 CLI 前用 `PROXIMIC_EMBEDDING_RUNTIME` 指定其他共享目录；两边要一致。普通编码强制离线，只有显式 setup 命令下载文件。

固定模型：`BAAI/bge-m3`，revision `5617a9f61b028005a4858fdac845db406aefb181`。固定核心依赖见 `requirements-workflow-embeddings.txt`。默认 CPU、FP32、两个计算线程、每批一个文本；本次没有启用 MPS，目的是控制与前台应用的资源竞争。

## 一条 Workflow 如何经过代码

当前链路：`operation_ingest.py` → `operation_prototypes.py` → `operation_retrieval.py` → `operation_embeddings.py` / `operation_embedding_worker.py` → `operation_admission.py` / `operation_novelty.py` → 按需 `operation_update.py` → `operation_library.py` → `operation_recommendations.py`。逐阶段说明见 [当前管线](WORKFLOW_OPERATIONS.md)。

### 1. 调度和前台保护

`agent_controller.py` 新增忙碌状态变化检查。开始或结束 Agent 任务时立即排队通知后台；原有三秒定时通知仍保留。UI 代码不读模型、不启动推理、不等待进程退出。

`workflows.py` 的控制读取线程收到配置后立即设置 `computing_blocked`，并发布只含 PID、busy 和时间戳的心跳。主循环只有在 Agent、文本处理和实验界面空闲时同步操作库。原有提取任务优先于向量维护。

`operation_embeddings.py::agent_busy()` 检查本机各后台进程的心跳；过期或已经退出的进程不阻塞工作。不同项目目录的 CLI 也能让位。测试通过独立心跳目录隔离，避免测试影响真实应用。

`run_encoder()` 每100毫秒检查忙碌/关闭信号，收到后终止编码子进程，必要时强制结束；每批已写出的向量保留，下次只补缺失项。全机文件锁防止多份模型同时加载。worker 还会监测父进程是否退出，避免关闭应用后遗留孤儿推理进程。

进程隔离不是零资源占用保证：BGE-M3 仍消耗 CPU、内存和磁盘带宽。这次的保护是独立环境、低优先级、有限线程、空闲调度、及时结束和缓存复用；没有宣称已完成 Agent 对话耗时的 A/B 评测。

### 2. 从自然语言流程组织两个输入

`operation_embeddings.py::workflow_texts(flow)` 不调用 LLM，读取已有提取结果的自然语言字段：

- `goal` 文本：流程名称、完整目标、已有参数及其说明。
- `steps` 文本：全部步骤的动作、执行方法、完成条件及已有前提。

这不是重新生成一篇 Workflow，也不是提取公共子链。原始事件、截图和审计日志不拼进 Embedding 输入；原有流程文件仍完整保留。

### 3. 缓存与版本

`cache_key()` 对模型名、固定 revision、编码协议版本、文本组织版本和文本内容取哈希。`ensure_embeddings()` 先查 `operations/embeddings.sqlite3`：目标没变就复用目标向量，仅步骤变化就只重算步骤向量。

缓存保存1024维归一化向量、token数、创建时间；返回前检查维度、有限数值和单位范数。坏缓存重算，错误结果不写入操作归并。模型/编码协议/文本组织变化会失效相关缓存。升级依赖时需同时升级编码协议版本，不能混用不同向量空间。

向量计算期间不持有 `catalog.sqlite3` 的写锁，因此推荐读取、反馈写入和 Agent 记录不需要等待模型完成。计算结束后再次核对流程版本，期间有变化则推迟更新。

### 4. 真正计算向量的代码

`operation_embedding_worker.py::main()` 是唯一导入 Sentence Transformers/PyTorch 的新增生产模块：

1. 降低进程优先级，限制 Torch 线程，启动内存采样。
2. 检查依赖版本与模型 revision 标识。
3. 使用 `local_files_only=True`、`trust_remote_code=False` 加载本地 BGE-M3。
4. 先计算文本实际 token 数，再执行 `model.encode(..., normalize_embeddings=True)`。
5. 每批原子写入结果，记录编码耗时和 RSS。
6. 完成后退出，释放模型。

当前后台资源策略把单段输入限制为2048 tokens。超过时明确失败并保留原操作库，**不会静默截断**。模型本身支持8192 tokens，但更长文本的分段/资源策略不在本次实测范围；当前六条流程的输入均低于限制。

### 5. 当前候选召回

`operation_retrieval.py::retrieve()` 对新流程和各组当前通用流程使用目标、步骤两个向量，按 `0.75 × 目标余弦 + 0.25 × 步骤余弦` 排序取最多三个组。目标最低0.50、综合最低0.55，应用集合冲突时过滤。已取消旧版120个成员流程的限制；目前仍对组向量做精确检索，规模大时可另加近似索引。阈值尚未大规模校准。

### 6. 当前复核与修订

`operation_admission.py` 只比较候选组的当前通用 Workflow，不再运行组内两两补查。`operation_novelty.py` 验证实质新增的来源引用；有新增才由 `operation_update.py` 生成候选。当前通用版本保持稳定，候选经过精确版本 Trials 验证后显式启用。旧 pairwise 实现移至 `operation_legacy.py`，历史记录保留。

### 7. 查看与测试

`operation_view.py` 展示当前 BGE-M3 候选、状态和实测链接，旧 TF-IDF 记录单独折叠。`operation_cli.py` 新增 `benchmark` 命令，`review` 先准备当前候选；Agent 忙碌时明确提示等待。

`embedding_benchmark.py` 是保留的历史逐对召回与编码性能基准，只读六条已有测试 Workflow。当前通用流程入组回放使用 `prototype_replay.py`，入口见 [当前管线](WORKFLOW_OPERATIONS.md)。历史基准构建独立缓存，测量：

- 三组真实同类版本的候选召回、精确率和留一 Top-1。
- 六条人工同义改写是否召回正确操作。
- 六条人工不同操作反例是否进入候选，显式暴露复核负担。
- 无向量缓存的完整进程耗时、依赖导入、模型加载、编码、子进程 RSS。
- 缓存读取10次及纯召回20次的中位数。

标签和查询单独放在 `embedding_cases.json`，方便查看与更改。标签不会传给召回算法。原 TF-IDF 算法和旧阈值冻结在实验脚本内，生产模块不再提供回退。评测不调用后台复核模型，因此反例进入候选不能解读为最终错误归并；也不把手写查询当作真实用户使用。

## 首次接入 BGE-M3 的历史文件清单

| 文件 | 主要功能 |
|---|---|
| 新增 `requirements-workflow-embeddings.txt` | 固定独立向量环境核心依赖 |
| 新增 `scripts/setup-workflow-embeddings.sh` | 创建独立环境、下载固定模型、安装 worker 副本 |
| 新增 `agent_workflow/operations/operation_embeddings.py` | 文本组织、内容缓存、忙碌心跳、进程锁、启动/终止子进程 |
| 新增 `agent_workflow/operations/operation_embedding_worker.py` | 本地模型编码、长度检查、逐批结果、时间和内存采样 |
| 修改 `agent_workflow/operations/operation_matching.py` | 双通道语义召回；保留 complete-linkage，新增组内补查 |
| 修改 `agent_workflow/operations/operation_catalog.py` | 缓存与归并衔接、无长事务、复核迁移、显式失败恢复 |
| 修改 `agent_workflow/operations/operation_service.py` | 传递忙碌取消回调并保存等待/失败状态 |
| 修改 `agent_workflow/extraction/workflows.py` | 空闲调度、控制线程及时发出忙碌信号 |
| 修改 `ui/agent/agent_controller.py` | 任务忙碌变化时立即排队通知后台 |
| 修改 `agent_workflow/operations/operation_cli.py` | benchmark 入口、review 前准备与忙碌保护 |
| 修改 `agent_workflow/operations/operation_view.py` | 当前召回解释、历史记录、缓存与实测报告链接 |
| 新增 `experiments/workflow_recommendations/embedding_benchmark.py` | 冻结 TF-IDF 基线、真实 Workflow 与诊断查询评测、HTML/JSON 报告 |
| 新增 `experiments/workflow_recommendations/embedding_cases.json` | 可编辑的人工评测标签、同义改写、不同操作反例 |
| 新增 `tests/test_workflow_embeddings.py` | 缓存失效、取消、迁移、无长锁、无主进程模型导入等边界测试 |
| 修改 `tests/test_workflow_recommendations.py` | 原推荐/复核测试改用注入向量，避免测试下载模型 |
| 修改 `tests/test_agent_recording_ui.py` | 手动配置队列测试停用周期心跳，避免异步导出时额外心跳造成时序误报 |
| 修改 `conftest.py` | 测试心跳与真实应用隔离 |
| 修改 `experiments/workflow_recommendations/README.md` | 更新入口与算法说明 |
| 新增本文档 | 按运行顺序解释每个代码块与数据文件 |

以上 `agent_workflow/`、`ui/agent/` 位于 `src/proximic_ring/` 下。其余记录、LLM 请求适配器、完整流程执行器、本地推荐打分器没有因本次召回替换而重写。

## 2026-10-16 首次接入 BGE-M3 的历史验证结果

设备为 M1 Pro、16 GB 内存，使用上述 CPU/FP32/两线程配置。Downloads 入口直接运行的最新报告见 [召回实测](../agent-experience/operations/embedding-benchmark/index.html)。

- 六条实际测试 Workflow 中有三对同类；TF-IDF 与 BGE-M3 均召回三对，均没有额外候选。
- 六条人工同义改写：两者 Top-1 均为 6/6；经过各自当前阈值后，TF-IDF 召回 0/6，BGE-M3 为 6/6。这说明当前召回策略的覆盖差异，不能单凭此结果声称 Embedding 排名全面胜出。
- 六条人工不同操作反例：BGE-M3 六条都进入候选，TF-IDF 没有；这些只用于检验召回边界，不代表已错误归并，后台语义复核仍必要。
- 无向量缓存时，12 个向量共 10.63 秒：依赖导入 3.21 秒、模型加载 0.79 秒、编码 5.63 秒；采样峰值 RSS 约 2.00 GiB。缓存读取 3.75 毫秒、纯召回 19.23 毫秒（分别10次、20次中位数）；TF-IDF 拟合加召回 11.78 毫秒。另一次首次安装后探测共31.31秒，其中导入库23.40秒，单独保留，不混入正式运行。
- Downloads 的七条当前 Workflow 新增召回两对“图片附件笔记／文字笔记”，相似度约0.747、0.763。使用现有文本模型一次复核，两对均为 `different`；服务端确认 thinking enabled，约23.02秒。此耗时单列，不包含在本地 Embedding 计时里。
- 迁移后四类操作：随机播放音乐（2版本）、新建文字笔记（2版本）、Safari 打开网址（2版本）、笔记附加图片（1版本）。七条样本记录及 operation_id、历史候选和反馈完整保留。
- Downloads 运行相关自动化测试：186项通过。覆盖取消与缓存恢复、无数据库长写锁、历史复核迁移、稳定ID、后台队列、记录、推荐、Trials 和 Agent UI；未做 Agent 对话延迟 A/B 实测。

本次同步前备份：Downloads 项目 `.runtime/backups/workflow-bge-m3-20261016-143453/`，含原代码、原操作库及同步文件哈希清单。

## 参考实现

- [Sentence Transformers 语义检索](https://sbert.net/examples/sentence_transformer/applications/semantic-search/README.html)
- [BGE-M3 模型及 Sentence Transformers 用法](https://huggingface.co/BAAI/bge-m3)
- [SentenceTransformer 本地加载参数](https://sbert.net/docs/package_reference/sentence_transformer/model.html)
