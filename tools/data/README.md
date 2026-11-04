# Clef 测试样例

样例独立于测试程序。直接增删 JSON 数组中的对象即可；不用改 Python。

- `automatic_voice_routing_cases.json`：原有 32 条，含总体意图和任务集合。
- `clef_primary_validation_cases.json`：新增 24 条边界样例，已补充六类任务预期，和原样例均用于开发。
- `clef_primary_unseen_cases.json`：后加的 12 条措辞变化。首次盲测为 11/12，随后用失败例完善同义表达规则；因此它们也已参与调优，不能算最终版本的独立测试集。
- `clef_joint_holdout_cases.json`：原联合方案的 24 条合成留出样例；本次合并已复用，作为回归集，不再视作新独立测试集。
- `clef_hierarchical_baseline_v3.json`：旧两级提示词及原始 32 条标签冻结快照，仅供对照，不是可编辑样例。
- `clef_joint_eight_baseline_v2.json`：合并前八类 C 方案的提示词与样例快照，保留旧标签。
- `clef_organize_boundary_cases.json`：12 条内容整理边界样例，含纯格式不删信息、摘要、撤销精简、组合操作等。
- `clef_six_task_boundary_cases.json`：新六类边界的 26 条样例，含整句替换、量词纠错、原话插入、扩缩写、格式、创作及组合；19 条带预设生成约束。v10 首测后已查看错误并用于 v11/v12 修正，因此最终版中也属于开发回归集。
- `clef_cases.template.json`：复制这个文件建立自己的样例集。

每条必须显式包含以下字段，空输入也要写出：

| 字段 | 含义 |
|---|---|
| `id` | 唯一编号，任意非空字符串，不能重复 |
| `utterance` | 当前口述，即 ASR 后的文字，1–2000 字符 |
| `target` | 输入框中可编辑的原文；没有则为 `""` |
| `context.messages` | 已经过滤的 OCR 参考文字数组；没有则为 `[]` |
| `context.messages[].speaker` | `self` 自己、`other` 对方、`unknown` 无法确定 |
| `context.messages[].text` | 该条 OCR 参考文字 |
| `expected.task` | 总体意图：`dictation` 听写，或 `edit` 编辑 |
| `expected.tasks` | 联合评测时编辑样例必须填写；听写用 `[]` |

`context` 可以保留应用名、来源等额外元数据；分类器只读取 `messages`。
**`expected` 只给测试程序评分，从不发送给模型。** 联合分类使用口述、原文、OCR 上文；
后续文本模型接收完整口述、完整原文、`context`，以及分类器实际产出的 `edit_plan`。
不要把预期编辑计划写成模型输入，否则会绕过真实分类。

2026-11-04 用户确认 `joint-holdout-ordinary-answer`（原文“我带一份过去。”，口述“没问题，我带两份过去”）是对本人此前输入的重述纠错，预期已从听写修正为 `edit + modify`。历史报告与冻结快照仍保留当时标签；新对照须按同一修正标签重评分，不能把改标签计为提示词提升。`annotation_note` 仅记录标注依据，不发送给模型。

分类器按主项目规则只读取原文末 400 字符、最后 4 条 OCR 拼接后的末 360 字符。
这些文件模拟的是 OCR 后的文本，不会运行截图或语音识别；没有图片输入。
为了模拟真实采集，OCR 内容应是过滤后的可见文字（采集器最多 16 条／2400 字符）。

编辑任务：`modify` 修改、`insert` 插入、`polish` 润色（扩写／缩写／摘要）、
`format` 格式整理、`translate` 翻译、`compose` 按目标创作。
旧 `precise_edit` → `modify`，`verbatim_insert` → `insert`，`expand` → `polish`。
旧 `organize` 不能统一改成某一个字段，必须按口述区分：精简／摘要归 `polish`，重排／列表／表格归 `format`，两者同时要求时同时标记。
释字拼写和直接重述都标记 `modify`；用户已逐字给出整句替换正文也属于修改，不按改动字数判创作。
旧标签和 `hints` 在当前活动样例中会被拒绝；冻结快照仅供历史对照。
复现旧一级时，`expected` 可以只写 `task`；当前联合评测必须有任务预期。

在项目根目录运行：

```sh
cp tools/data/clef_cases.template.json tools/data/my_clef_cases.json
.runtime/venv/bin/python tools/check_clef_router.py --cases tools/data/my_clef_cases.json --validate-only
.runtime/venv/bin/python tools/check_clef_router.py --cases tools/data/my_clef_cases.json --case my-edit-01 --dump-request
bash scripts/clef-remote.sh check --cases tools/data/my_clef_cases.json --output .runtime/clef/my-joint-results.json
bash scripts/clef-remote.sh pipeline --cases tools/data/my_clef_cases.json --output .runtime/clef/my-pipeline-results.json
# 对照 A 旧两级、B 一级补定义、C 联合；--development 可重复，--holdout 指定留出样例
bash scripts/clef-remote.sh compare --output .runtime/clef/my-comparison.json
```

`check` 每条一次请求，HTML 和 JSON 展示总体意图两个概率及六个任务各自 apply/skip 概率，包括听写和矛盾结果。总体听写优先、不明确时回退听写；冲突记录标黄，仍展示原始 apply/skip 与全部概率。
`pipeline` 真实调用分类及已配置文本模型，不向其他应用写回或发送消息。
`primary` 保留为冻结旧 v3 一级的复现命令，不能用它评估当前应用。
`expected` 不传给模型。对照工具按总体意图和六个任务计算完整匹配。旧 precise_edit/verbatim_insert/expand/condense/structure 可按明确映射计分，原始概率不变；旧 organize 不能从一个分数拆出 polish 和 format。比较旧 v9 时，对涉及旧 organize 的样例不做自动完整任务重评分，其余可比样例才使用同一口径。旧 hint 仅展示、不计分；不能把不同分类口径的准确率直接相减。
分类程序退出码 0 表示最终处理全部匹配（仍可能包含标黄回退），2 表示最终处理不符，1 表示调用或接口校验失败；参数格式错误也为 2。原始模型分类正确率单独计分，不把回退效果算作模型提升。
概率未经校准；合成样例不能验证真实麦克风、OCR 和输入法写回。

可选的生成检查字段（只用于评分，绝不传给模型）：

- `expected.modified_text`：要求生成正文与此字符串完全一致。
- `expected.preserve_text`：要求保留的关键文字列表，逐项检查是否存在。

不填写这些字段时不评分生成文本，不能把“未检查”算成质量通过。完整流程命令会在 JSON/HTML 中报告检查结果，检查失败退出码为 2。
