# 统一语音测试主数据集

所有新增、纠正和标签维护都写入 `tools/data/voice_eval_master.json`。专项测试集是主数据的筛选结果，不单独维护答案。入口为 `python3 tools/voice_eval_dataset.py`，仅使用 Python 标准库，不发模型请求。

## 当前范围

初始版本合并 1,514 条来源记录，按完整输入去重为 675 个样例。修订版 3 新增 20 条保存上下文用例，修订版 4 再加入 4 条新闻摘要分享邮件，修订版 5 加入 24 条口述片段补全用例，修订版 6 收录 ASR 原意恢复专项，当前共 **748 个不同输入**：

| 用途 | 数量 | 口径 |
| --- | ---: | --- |
| 二分类 | 721 | 536 个编辑、185 个听写／无有效编辑 |
| 六类任务分类 | 536 | 只包含编辑；修改、插入、润色、格式整理、翻译、按目标创作，可以多选 |
| 单轮编辑正文 | 536 | 每条都有参考正文或检查条件，并有语义评分要求 |
| 多轮编辑正文 | 2 | 原消融实验的两步、五步序列；保留每一步要求 |
| 22 类细分类 | 162 | 沿用核对输入哈希后的历史细标签；其余样例不冒充已标注 |

截至 2026-11-17、主数据修订版 2，释字相关共 177 条：148 条执行正例（其中 1 条为多轮），24 条引用／撤销等反例，5 条目标已正确无需修改。默认单轮释字编辑集有 157 条，其中纯执行正例为 147 条；释字二分类集有 176 条。其余涉及释字的听写样例仅进入二分类等适用测试。

来源包括 346 条释字增强集、300 条二分类集、154 条联合分类集、216 条综合编辑集及其原始样例文件、60 条组合生成样例、8 条连接冒烟样例、28 个指令消融条件和 168 条细分类标注。重复输入合并来源和别名；语义相近但输入不同的样例保留。

新增 `saved-context-001` 至 `saved-context-020` 是直接在主数据编写的合成开发用例，标为 `development`，非独立留出集。覆盖 15 条使用保存资料（其中 1 条同时使用当前对话）、4 条使用当前屏幕、1 条忽略无关保存资料。润色、回复、翻译和表格各有一组输入完全相同、只改变口述指代的成对用例，验证“刚保存的”与“这段／他／上面／这里”的来源边界。所有预期在模型调用前冻结，模型实测与复核另存报告，不回填参考答案。

`saved-news-001` 至 `saved-news-004` 的口述均为“我写一封邮件，把刚才的摘要分享给团队”。每条提供不同的合成新闻摘要及干扰屏幕内容，覆盖三条／四条新闻、相同主题的冲突版本、空输入框／已有邮件草稿，以及研究范围、商业授权、计划时间等限制条件。新闻全部虚构，用于验证来源选择和完整成稿，不代表真实新闻事实。预设 `saved_news_email` 可单独导出这 4 条；`saved_context_editing` 包含全部 24 条。

`fragment-001` 至 `fragment-024` 为合成开发样例，可用预设 `fragment_completion` 导出。8 条同口述换屏配对、8 条日常聊天、4 条考察歧义和克制补全、4 条随手记录。用户直接口述内容，既有二分类口径为听写。补全模式的参考正文及语义要求存于 `special_annotations.fragment_completion`，均在模型调用前冻结。用 `.runtime/venv/bin/python tools/check_fragment_completion.py --output-dir <新目录>` 运行专项评测。

`asr_recovery_and_fragments` 从主集取两类标签的并集。V2 的 18 条错义修复、6 条正确原意保护和 2 条观察覆盖 25 个不同输入。两条观察共享可见输入，各自保留原判据。加上 24 条片段补全，联合评测有 50 个评测项，48 项计分。仅测前处理的主记录设置 `evaluation_scope: asr_final`，下游标签为 null。模型每次只接收本次口述、场景、屏幕上文和光标前文。运行 `.runtime/venv/bin/python tools/check_asr_recovery_fragments.py --output-dir <新目录>` 默认冻结纯文本候选提示并与单功能专项对照。候选按行输出，首行可直接采用。手动查询可重复 `--any-tag` 以选取任一标签。

原始结果报告、模型输出和旧数据文件不改动，也不作为新答案生成来源。旧八类／两级分类的冻结快照与模板不是本次活动主数据，保留供历史复现。未来维护只改主数据；历史实验脚本的旧默认路径仍用于复现旧报告，新评测从下述命令导出。

## 先筛选，再评测

在项目根目录执行：

```sh
# 校验主数据及统计可用数量
python3 tools/voice_eval_dataset.py validate
python3 tools/voice_eval_dataset.py stats

# 二分类准确率：含听写与编辑
python3 tools/voice_eval_dataset.py export --task binary --output /tmp/binary.json

# 六类多标签分类准确率：只含编辑正例
python3 tools/voice_eval_dataset.py export --task six_class --output /tmp/six-class.json

# 编辑正文：只含单轮编辑，听写自动排除
python3 tools/voice_eval_dataset.py export --task editing --kind single --output /tmp/editing.json

# 释字准确率：只检查确实需要释字的单轮编辑正例
python3 tools/voice_eval_dataset.py export --task editing --kind single --tag 释字 \
  --clarification-role positive --output /tmp/clarification-positive.json

# 释字边界：同时看执行纠正与引用／撤销等反例，仍然只含编辑样例
python3 tools/voice_eval_dataset.py export --task editing --kind single --tag 释字 \
  --output /tmp/clarification-editing.json

# 释字相关二分类：保留姓名介绍、转述和已正确等听写侧反例
python3 tools/voice_eval_dataset.py export --task binary --tag 释字 \
  --output /tmp/clarification-binary.json

# 一次生成／更新预设专项集
python3 tools/voice_eval_dataset.py refresh --output-dir /tmp/voice-eval

# 保存资料与当前屏幕的来源选择、润色与创作依赖
python3 tools/voice_eval_dataset.py export --task editing --tag 保存上下文 \
  --kind single --output /tmp/saved-context.json
.runtime/venv/bin/python tools/check_saved_context_edit.py \
  --output-dir docs/reports/<新建评测目录>

# 优先复测固定口述的 4 条新闻分享邮件
.runtime/venv/bin/python tools/check_saved_context_edit.py \
  --output-dir docs/reports/<新建新闻评测目录> \
  --case saved-news-001 --case saved-news-002 \
  --case saved-news-003 --case saved-news-004
```

预设名称和筛选条件放在 `tools/data/voice_eval_subsets.json`，只保存查询，不复制样例。`refresh` 会按当前主数据重建文件。`export` 同时支持 `--exclude-tag`、`--intent`、`--split`、`--source`（完整来源路径）、`--id`（主 ID 或旧别名）。重复 `--task` 或 `--tag` 表示同时满足。未知条件、空结果和非法标签都会明确报错。

导出的 JSON 包含 `master_revision`、`master_sha256`、`filters`、`count` 与完整样例，可以固定评测输入并追溯本轮到底测了哪些题。同一轮评测使用导出快照，不在运行途中重新筛选变化中的主数据。数据并非类别均衡集；二分类应另报两类 precision／recall 和混淆矩阵，不能只报告总准确率。

## 字段约定

主文件包含版本、类别与标签目录、统一评分规则、来源文件哈希和 `cases` 数组。逐条字段如下：

| 字段 | 含义与约束 |
| --- | --- |
| `id`、`aliases` | 稳定主编号及所有旧编号；别名不能指向不同样例 |
| `kind` | `single` 或 `sequence`；连续多轮不得合并成一次口述 |
| `input.target` | 可编辑原文，空原文显式写 `""` |
| `input.utterances` | 按执行顺序排列的口述数组；单轮恰好一项 |
| `input.context` | 只读上下文；无 OCR 时仍写 `{"messages": []}`，其他来源元数据保留 |
| `input.saved_context` | 可选的独立保存文字，必须是字符串；不放进 OCR 的 `context`，也不含图片或本地路径。参与完整输入哈希、去重、筛选快照及导出；旧样例不添加空字段，以保留历史输入哈希 |
| `input_sha256` | 完整输入的规范化哈希；同输入不得重复录入，应该合并来源 |
| `labels.intent` | `dictation` / `edit`；仅测前处理或多轮时为 `null` |
| `labels.edit_tasks` | 六类多标签集合，按目录排序；听写为 `[]`，仅测前处理或多轮时为 `null` |
| `labels.fine_edit_tasks` | 已标注的 22 类细标签；未标注为 `null`，不得用空列表表示完成标注 |
| `applicability` | `binary`、`six_class`、`editing`、`fine_22` 四个显式布尔字段 |
| `exclusion_reasons` | 每个不适用任务的原因；例如听写不用于编辑正文 |
| `special_tags` | 可多选专项标签。目录同时提供英文键与中文名，命令支持两者 |
| `special_annotations.character_clarification` | `present`、`role`、`mechanism`，区分是否涉及释字、其作用和机制 |
| `editing_expectations` | 每轮一个评分对象；听写必须为 `[]` |
| `split` | `development` / `holdout` / `regression`；这次已用过的样例统一为 `regression` |
| `annotation` | 标注者、日期、理由、独立人工复核标记及输入修正记录 |
| `annotation.semantic_review` | 可选的逐条复核记录：复核者、日期、范围、结论、原因和 `semantic_sha256`；哈希绑定该条输入、标签、用途、专项标注与评分预期 |
| `provenance` | 每个来源的路径、原编号、记录哈希、旧标签、旧划分、旧检查、实验候选计划等 |
| `relations` | 二分类对照对 ID、关联样例 ID，保留原有成对和消融关系 |

`applicability` 必须与标签一致，校验器不接受“听写＋可用于编辑”或“无六类真值＋可用于六类评测”。多轮只用于编辑序列测试。二分类中的全撤销、保持原样、无旧文可修改等仍沿用产品的听写回退标签，这不表示允许把取消指令写入正文。

单个 `editing_expectations` 对象包含：

| 字段 | 含义 |
| --- | --- |
| `output_scope` | 固定 `full_text`，期望模型返回完整处理后正文 |
| `reference_text` | 可接受的参考正文；开放生成可为 `null`；清空全文的答案是空字符串，不是缺失 |
| `checks` | `contains`、`contains_ci`、`excludes`、`max_chars` 等机械检查 |
| `requirements` | 逐例语义要求：目标内容、保护项、事实、范围、操作顺序等，必填 |
| `grading` | `semantic_with_checks`；字符串命中只是辅助，不能替代语义复核 |
| `input_source` | 首轮 `initial_target`，后续 `previous_actual_output` |

参考正文是可接受示例，只有用户授权的改写范围允许等义表达；定向修改、插入不能顺带润色。排版可添加必要表头、编号或样式标记，也可用表格／列表结构代替连接词，但不增加事实。完整保留、指定字形、固定字符串等仍按逐例要求严格核对。`max_chars` 仅用于口述明确给定的数值上限，“简短一点”不自动附加人为阈值。空检查不能计成通过；模型请求成功也不能计成编辑正确。源文件的原始机械检查保留在 `provenance.original_edit_checks`，其中历史 `modified_text` 迁为参考正文，避免把所有开放表达当成逐字匹配任务。

释字 `role` 分为：

- `positive`：需要按释字依据得到、纠正并保留目标写法。
- `negative`：引用释字内容、撤销释字要求、只处理说明本身，或最终替换覆盖了中间释字结果。不能混入纯释字执行准确率分母。
- `no_change`：目标已经正确，只需确认；当前路由口径归无有效编辑，因此不进入编辑集。
- `none`：不涉及释字。

其他专项标签包括组合任务、字母拼读、偏旁部首、字形拆解、字位取字、ASR 错字、局部范围、撤销、照录、只读上下文、长原文、续写边界、多轮和消融等。它们是离线样例标注，不参与模型分类或运行时纠错。

`saved_context`（保存上下文）标签与 `special_annotations.saved_context.expected_source` 记录来源选择验收：`saved`、`screen`、`both`、`ignore`。导出时该文字保留为独立 `saved_context` 字段；实际请求必须包含它，不能用只支持旧三个输入字段的执行器评估此专项。`check_saved_context_edit.py` 复用生产无分类边界编辑入口，核对实际请求、冻结提示词和输入；分类标签只保留在主数据，不发送给编辑模型。

## 新增和修改

从主数据复制一个结构相近的完整对象，填写新输入、分类真值、用途、专项标签、参考／评分要求和标注依据。单条对象、对象数组或带 `cases` 的对象均可导入。修改保留原 `id`，新增使用新的 `id` 和别名。

```sh
# 将填写完整的新样例加入唯一主文件；重复输入、重复 ID 和不完整标注会被拒绝
python3 tools/voice_eval_dataset.py add --input /tmp/new-cases.json --note "新增量词纠错与续写对照"

# 修改既有样例；不会往旧数据文件回写
python3 tools/voice_eval_dataset.py update --input /tmp/corrected-cases.json --note "纠正释字目标与评分要求"

# 修改后刷新下游集
python3 tools/voice_eval_dataset.py refresh --output-dir /tmp/voice-eval
```

命令自动重算输入哈希、增加主数据版本、记录变更说明，并在完整校验通过后原子写回。新样例的来源填采集／编写说明路径和编号；自主编写时也须说明作者及依据。没有人工复核就保持 `independent_human_review: false`。新专项标签先加入主文件的 `tag_catalog`，再标到样例上。输入或答案有实质修正时，在 `annotation.input_amendments` 和变更说明中记录旧值、新值、原因；派生文件不要手工改。

复制已有样例再修改时，`add`／`update` 会移除与新内容不匹配的旧 `semantic_review`，并写明需要重新复核；不能把原记录的“已复核”继承给新题或新答案。直接编辑主文件留下失效复核哈希时，`validate` 会拒绝通过。后续复核必须基于实际内容重做，不能仅刷新哈希。

## 接入评测程序

新程序直接使用：

```python
from tools.voice_eval_dataset import load_master, select_cases, model_input

master = load_master()
cases = select_cases(master, tasks=['editing'], tags=['释字'], kind='single')
for case in cases:
    payload = model_input(case)  # 仅 utterance / editable_text / screen_context
    # 调用模型后，用 case['editing_expectations'][0] 离线评分。
```

标签、参考答案、评分规则和来源一律不能进入生成／分类模型的请求。多轮调用 `model_input(case, step=i, previous_output=actual_previous_output)`，禁止用参考答案代替真实上一步输出。

现有脚本支持的扁平格式也可导出，并附独立 `.manifest.json`：

```sh
python3 tools/voice_eval_dataset.py export --task binary --format routing --output /tmp/router-cases.json
python3 tools/voice_eval_dataset.py export --task editing --kind single --format editing --output /tmp/generation-cases.json

.runtime/venv/bin/python tools/check_clef_router.py --cases /tmp/router-cases.json --validate-only
.runtime/venv/bin/python tools/check_automatic_edit_generation.py --cases /tmp/generation-cases.json \
  --validate-only --output /tmp/edit-check.json
```

`routing` 格式供总体意图／六类分类；`editing` 格式在 `edit_plan` 提供真值，只用于隔离正文生成，不能据此报告分类准确率。它保留 `semantic_review_required`、`reference`、`review_points`；旧工具的机械检查结果不是最终语义分数。连续多轮必须使用标准格式，不静默展平成旧单轮格式。

## 数据修正与使用边界

2026-11-17 逐条核对全部 675 条的原文、口述、只读上下文、听写／编辑边界、六类多标签、释字正反例及编辑预期，完成主数据修订版 2。二分类和六类任务真值均保持不变，未改动题目输入。410 条有专项标注或评分预期调整，其中 11 条修正释字标注、406 条调整编辑预期，两者有重叠；多数是统一限定评分授权范围，并非 410 条分类错误。

- 补上“我姓陈，耳东陈”的释字反例标签，并修正 10 条拼读、偏旁部首或字形拆解机制。
- 删除 16 条题目中未由用户指定的数值长度上限，补上“二十字以内”“三十字以内”两条明确约束。
- 纠正未指定目标语言却要求英文词命中的检查，厘清“周五前”和 `by Friday` 的时间边界。
- 纯分行参考保留原字词；先分两行再追加提醒允许随后增加第三行；拼读大小写和 ASR 噪声解释明确写入要求。

完整逐条结论和修改前后值见 [`voice_eval_annotation_audit_20261117.json`](voice_eval_annotation_audit_20261117.json)。每条主数据也保存绑定内容哈希的复核记录。这是 Codex 语义复核及数据一致性验证，不是独立人工验收或模型准确率测量。

本次纠正了 `clarify-hol-003`、`clarify-hol-044` 中“时间的时、实际的实”与“实时”参考答案的顺序矛盾，主数据改为“实际的实、时间的时”，旧值和既有勘误路径完整保留。历史报告里的原题及成绩没有重写。

原有 `holdout` 已经在历史评测使用，不再宣称是当前未见测试集；其原划分保存在来源记录。本主数据是合成／既有回归样例与 Codex 标注，未新增独立人工标注或真实用户分布证据。后续需要真正留出集时，新增未参与开发的样例，并按关联对／同源家族一起划分，避免相近改写跨集合泄漏。
