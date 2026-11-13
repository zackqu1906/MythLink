# 语音代码分区

三种模式共用录音、手势配置、OCR 采集和写回校验，各自拥有独立的处理策略。

邮件／富文本的原文读取、选区和写回失败排查见 [IMK 编辑诊断](IME_EDIT_DIAGNOSTICS.md)。该诊断只记录元数据，不改变写入和校验策略。

| 模式 | 谁决定听写／编辑 | 编辑处理 | UI 入口 | 文本处理 |
|---|---|---|---|---|
| 手动转换 `manual` | 用户手势 | 原有编辑候选流程 | `ui/voice/manual_voice.py` | `text_processing/manual_processing.py` |
| 自动判断 `auto` | Clef 联合分类 | 选中模块拼接后生成正文 | `ui/voice/automatic_voice.py` | `text_processing/automatic_processing.py` |
| 手动＋编辑路由 `manual_routed_edit` | 用户手势 | 一次 DeepSeek 请求；可选六类选择＋正文或无分类边界直编 | `ui/voice/routed_edit_voice.py` | `text_processing/routed_edit_processing.py` |

`ui/voice/voice_modes.py` 集中分派会话和原生编辑入口，`text_processing/processing_dispatch.py`
集中分派三种文本处理策略。Worker 只负责排队、取消、结果及 trace 回调。
原生 BEGIN 固定本句模式；设置变化作用于下一句，当前结果仍按原会话与 revision 校验。

## 自动识别专用

| 文件（相对 `src/proximic_ring/`） | 职责 |
|---|---|
| `ui/voice/automatic_voice.py` | 自动会话入口、复用公共截图采集、提交 Clef、接收计划并进入生成／原生编辑 |
| `text_processing/intent_router.py` | Clef 单次联合判断总体意图与六个任务，一致性拒绝与 trace |
| `text_processing/clef_connection.py` | 公网 HTTP(S) 连接、健康检查，默认 20031；不启动 SSH |
| `ui/voice/clef_settings_controller.py`、`ui/qml/ClefConnectionSettings.qml` | 公网地址/端口设置、旧地址迁移及合成分类连接测试 |
| `text_processing/task_routing.py` | 分类类型、原文决定的可用任务、编辑计划校验、路由输入裁剪 |
| `text_processing/router_prompts.py` | 联合分类提示词、按原文生成问题 schema、类别边界 |
| `text_processing/automatic_prompts.py` | 按六类任务组合生成提示词，修改包含释字及重述 |
| `text_processing/automatic_inputs.py` | 听写与编辑的独立输入结构，同时供实际请求和历史记录使用 |
| `text_processing/automatic_processing.py` | `process_task_with_trace` 分派到独立的听写整理／编辑生成接口；各调用一次模型，不走手动候选竞速 |
| `voice/automatic_history.py` | 自动分类及截图证据的历史展示数据 |

流程：配置的开始动作 → 录音并预取 OCR／DeepSeek 上文 → 配置的结束动作 → ASR 最终文本 →
上文开关开启时等待同一句整理结果，关闭时使用空上文 →
Clef 联合七字段 → 一致性校验 → 组合提示词 → 一次生成 → 共用校验与写回。
原文为空时的分类限制只在路由前处理；自动正文生成与原生编辑不再重复进行非空校验。
写回仍校验输入目标、光标和本次可操作范围。
“自动模式听写整理”关闭时，听写分类结果直接进入原有注入流程，跳过正文模型；
该设置不改变 Clef 分类或编辑生成，历史记录会说明跳过原因。

## 原有用户明确选择的处理

“手动”包含用户通过手势、快捷键或界面明确选择动作，不是手动输入文字。

| 文件 | 职责 |
|---|---|
| `ui/voice/manual_voice.py` | 手动模式入口、候选准备／接收／选择／结束及 `_AutoInteraction`；使用用户选定模式，不调用 Clef |
| `text_processing/manual_processing.py` | 原有听写、明确编辑、润色；保留片段／全文编辑竞速与候选记录 |
| `ui/voice/inline_controller.py` 中 `convert()` / `polish()` | 原生输入法的明确编辑、润色动作入口；会话、焦点等其余逻辑共用 |
| `text_processing/prompts.py` | 原有编辑／润色提示词，同时包含三种模式共用的听写和 OCR 边界规则 |

流程：配置手势 → 用户明确选择的操作 → 原有文本处理 → 共用校验与写回。
不经过Clef 联合分类，也不依赖自动模式的 `edit_plan`。

## 手动＋编辑路由专用

默认沿用手动手势：双击开始，点击结束听写，右滑结束并确认编辑；已有手势绑定不被重置。
本模式没有听写／编辑模型判断，不调用 Clef，不预先计算另一种候选。

设置 → 听写偏好 → 输入与控制，选择“手动＋编辑路由”后显示“手动编辑版本”：
“六分类 v5”（`six_class_v5`，默认，旧设置继续使用）与“无分类边界 v1”（`boundary_only_v1`）。
偏好保存为 `input/routedEditVariant`，未知值回到六分类；录音开始固定本句版本，下次录音应用设置变化。
历史重做保留原请求的版本、模型设置和上文，不套用当前选择。

- `ui/voice/routed_edit_voice.py`：原生编辑附带本句 OCR；桌面回退路径读取目标快照并提交。
- `text_processing/routed_edit_prompts.py`：复用当前六类定义、共用编辑要求、组合规则和 OCR 边界；
  使用第三模式专用执行模块，不继承 Clef 的听写／编辑排除条件或自动模式的歧义跳过规则。
  用户手势已确认编辑，模型必须选择至少一个类别；所有类别都允许结合上下文自主推断对象、范围、
  措辞、语言和格式等省略信息并完成结果。空原文排除“修改”；正文可按目标创作。
- `text_processing/routed_edit_processing.py`：一次火山 DeepSeek 请求，无重试、无候选竞速；
  六分类版返回中文 `分类：…` 与 `正文：…`，不要求模型输出 JSON。兼容正文同一行或下一行，拒绝未知／重复类别、
  空原文的修改类别、未选择编辑类别、截断响应，以及共用正文校验不接受的输出。
- `text_processing/boundary_edit_prompt.py`：无分类边界 v1 的完整固定提示词，逐字对应 2026-11-11 的
  216 条实测版本。保留六类原有能力和执行边界，移除选类别、条件模块与类别输出要求。
  此版本直接返回正文、`edit_plan=None`；不生成假分类或再发一次分类请求。
  只有已完成的 Responses 显式文本允许空字符串（清空正文），缺失内容、拒绝、工具参数和截断不能作为清空。
- `ui/voice/inline_controller.py`、原生 `CompositionSession.swift`：保留手动点击／右滑语义；
  `routed_edit_mode` 只允许已确认空原文进入创作，不把不可读取的上下文当作空框。
  原生组件通过 `routed_edit_version=1` 确认支持；旧组件会提示更新，不静默降级为旧编辑。

流程：录音并采集同一帧 OCR，同时按开关预取 DeepSeek 上文 → 手势确认编辑 → 读取已绑定原文 →
上文开关开启时等待同一句整理结果，关闭时使用空上文 →
一次 DeepSeek（按版本返回分类＋正文或直接正文）→ 解析校验 → 原生 revision／光标／范围校验 → 写回及撤销。
点击确认的听写保持手动行为。第三模式编辑不额外启动热词学习请求。

实际请求只有 `utterance`、`editable_text`、`screen_context`。无预期标签、无上游 `edit_plan`。
六分类版完整返回的类别保存为 `result.edit_plan`；它是返回结果，不是在第二次请求中发送的计划。
六分类版不提示或接受“无可执行编辑”作为成功结果；未知类别不被“修复”为成功。

历史记录保留模式、单次总耗时、实际模型输入、原始分类与正文、最终计划、失败原因和 OCR 过滤证据。
展开编辑详情后依次显示路由判断、编辑结果与失败信息、OCR 截图、LLM 整理的 OCR。正文校验失败或输出截断时，仍展示已返回的分类并注明
结果未通过校验；未收到类别时明确显示“未取得分类”，不借用同一记录里其他请求的类别。
写回失败的原因与模型生成结果分开展示；失败回退原文不作为模型正文显示。旧记录的无任务结果仍可读。
没有独立分类耗时，也不编造类别概率。源代码接入验证见 `tests/test_routed_edit.py`、
`tests/test_ui_routed_edit.py` 和原生 `Tests/main.swift` 的 routed 模式事务测试。
新请求还保存 `routed_edit_variant`、对应 `prompt_version` 与完整 system；无分类版标记
`classification_type=not_applicable`，详情显示“无分类边界 v1 · 直接生成正文”，不显示分类失败。
该版本的传输、设置持久化、会话切换和重做测试见 `tests/test_boundary_edit.py`、`tests/test_ui_boundary_edit.py`。

## 三种模式共用

| 文件 | 职责 |
|---|---|
| `ui/gestures/voice_gesture_mapping.py` | 用户绑定的手势 → 开始、结束、修改、润色、取消等动作 |
| `ui/gestures/ring_gesture_controller.py` | 手势事件、焦点、场景归属和优先级 |
| `voice/voice_actions.py` | 手势／快捷键转为业务动作，取消与撤销判断 |
| `ui/controller.py` | 应用总装配、ASR 和三种会话入口的分流、写回及状态协作 |
| `ui/voice/voice_capture.py` | 三种模式录音开始时共用截图／原始 OCR；固定本句上文开关，并分别持有原始采集和模型上文 future |
| `text_processing/screen_context.py` | 整张目标窗口截图、原始 OCR；提供聊天区域、气泡归属等本地整理规则 |
| `text_processing/context_preparation.py` | 共用上文服务：区域整理、一次 DeepSeek 文本请求、输出校验和可取消等待；原始 OCR 不被摘要覆盖 |
| `text_processing/worker.py` | 异步调度与取消；委托 `processing_dispatch.py` 按模式分派 |
| `text_processing/llm.py` | 公共处理器 API、HTTP、模型服务、工具调用与编辑响应校验 |
| `text_processing/processing_common.py` | 输出预算和响应异常类型 |
| `voice/modification_dataset.py` | 听写、编辑、自动分类及截图的统一记录与删除 |
| `voice/history_details.py`、`ui/qml/VoiceHistoryDetails.qml` | 三种模式共用的易读详情：编辑记录按路由、结果与失败、截图、整理后的 OCR 排列，全文自然增高，无输入展开或内部滚动框 |
| `voice/history_export.py`、`ui/voice/history_export_controller.py` | 设置首页与历史页整体菜单导出完整原始记录、附件、CSV/JSONL 索引和 context 覆盖清单；后台压缩，成功后自动打开导出文件夹，不受历史筛选或展示条数限制 |
| `ui/voice/inline_controller.py`、`native/ProxiMicInput/Sources/` | 原生会话、目标与版本校验、文字应用、撤销 |

记录 schema v5 将 `screen_capture`（采集状态／目标／时序）、`screen_evidence.ocr_lines`
（整窗原始文字、位置、置信度）和 PNG 独立于分类保存。原有手动模式不做自动文字过滤，也不把公共 OCR 传入原有手动正文模型。
自动模式与手动编辑路由使用同一帧派生的 `processing=llm_compact` 上文。旧记录的规则过滤证据
`screen_evidence.ocr_filter_lines` 继续可读；新整理过程独立保存到 `context_preparation`，不覆盖原始 OCR。
截图回调绑定固定记录 ID；取消判断不会取消记录采集，删除／清空／重连后的迟到回调不会重建或串写记录。
输入法 BEGIN 已返回的原文、选择范围及版本等另外保存在 `input_context`；未取得的数据不补造。
三种模式都通过记录的“…”菜单选择“查看记录详情”。编辑指令固定为四块：路由判断、编辑结果与失败信息、
OCR 截图、LLM 整理的 OCR。路由区保留各次请求实际返回的分类，结果区保留正文与各自的失败原因；
不会用后一次成功的分类填补前一次失败。没有路由、截图或整理结果时明确说明，不补造数据。
截图随详情直接显示；取消所有“查看输入”按钮，详情内不再进行第二次展开。
详情文本按可用宽度换行并完整增高，不限制文本高度，也不设内部滚动框；鼠标滚轮和触控板在文本、截图、标题及留白处
都滚动外层历史列表。历史列表提供统一滚动条。
详情不展示请求输入、请求 JSON、系统提示词、概率、坐标、内部标识、事件或传感器日志；底层文件继续完整保存，
已有“打开记录文件夹”入口和完整记录导出保留。

旧记录仍可读，未采集的截图、上下文或传感器字段明确显示未记录。

设置首页“记录与导出 → 语音记录 → 导出”和历史页整体“⋯ → 筛选应用并导出语音记录”
共用后台任务，每次选择应用、时间范围及保存文件夹（默认 Downloads），导出符合范围的完整交互；
两个入口均显示进度并防止重复导出，
成功后自动打开导出文件夹；打开失败时显示文件路径，设置首页仍可手动重新打开。
文件名为 `Mythlink-voice-history-日期时间-唯一后缀.zip`。包内保留 `interactions/` 原始记录与附件，
另有 `records.jsonl`、`summary.csv`、`associations.jsonl`、`manifest.json` 和字段说明 `README.txt`。
导出固定起始记录列表，每条记录在锁内打开同一版本文件，锁外流式压缩；不更改记录或暂停语音输入。
缺失/损坏文件在清单中列出；磁盘写入失败不发布不完整 ZIP。CSV 区分已保存空原文与未记录，
JSON 保留完整模型输入、标签来源、取消/撤销事件等原始证据，不把成功应用当作用户满意度。

### OCR 上文开关与等待

设置中的“OCR 截图上文传入”默认开启，在每句开始采集时固定，修改作用于下一句。

- 无论开关状态如何，都采集原始截图及整窗 OCR，并沿用固定记录 ID、删除及重连隔离逻辑。
- 开启：原始 OCR 完成即在独立线程池进行区域整理 → 火山 DeepSeek 文本 LLM。
  聊天应用沿用已有区域和发言人信息，草稿单列；其他窗口按横向区域分栏。
  输出软件类型、主题、短摘要、独立草稿和不确定标记。只发送 OCR 文字和坐标，不发送图片。
- 自动 Clef 分类和第三模式编辑等待这一个 `PreparedContext`。自动分类后的听写整理／编辑
  继续复用同一个结果，不重新调用摘要模型。第三模式的分类与正文仍为一次请求。
- 关闭：不发起上文整理请求；消费者收到 `{}`，不等待截图、OCR 或上文模型。
  原有手动处理仍不消费 OCR 上文。
- 上文整理请求上限 10 秒，使用现有火山 DeepSeek 服务配置、关闭 thinking，无重试。
  失败、截断或格式不合法返回不可用上文及原因；不回退到原始整窗 OCR。
  请求取消后停止等待和后续路由／编辑；截图记录、已发起的上文整理仍可归档到原记录。
- “OCR 上文整理”底层记录包含开关、状态、实际输入、提示词版本、原始返回、context 和耗时；
  界面只显示易读的上文结果和简短状态，实际输入可展开查看。
  原有处理总耗时包含尚未完成的上文等待；整理耗时单列保存，不能把两者相加计算录音后的延迟。

验证：`tests/test_context_preparation.py`、`tests/test_ui_context_preparation.py`；
真实接口合成输入结果：`docs/reports/ocr-context-app-integration-2026-11-09.json`。

## 旧入口清理及仍在使用的内部命名

2026-11-05 移除了旧聊天模型二分类提示词、`legacy_input_routing.py`、
`classify_input_mode*` API 及其专用测试工具。`InputModeRoutingRequest` 默认使用 Clef，
Worker 仅接受 Clef；显式传入旧 `llm` 或 `gliner` 后端会报不支持，不会静默切换模型。
正文模型的本地预热不再发送旧分类提示词。历史记录读取保留，新的请求不再写入旧提示词。

`manual_voice.py` 中 `_AutoInteraction`、`_prepare_auto_candidates` 等名称来自原有可切换候选流程，
目前手动桌面链路仍使用它们；它们不属于新的 Clef 自动流程。保留这些内部名称，避免连带改变候选、撤销和异步回调。2026-11-09 将 5 个候选方法共 317 行移出总控制器，迁移前后 AST 一致；总控制器保留类型导入兼容旧调用者。

当前分类提示词为 `router_prompts.py` 的 v20，沿用 v19 的联合分类及空原文 schema；
v20 将直接重述／释义纠错推广到任意字词、片段或整句，不限定姓名等词类或解释形式。
自动生成版本为 `automatic_prompts.py` 的 v13。
v10 将听写输入限为 `utterance` 和 `screen_context`，使用独立的只读 OCR 规则；编辑输入继续包含
`editable_text` 和 `edit_plan`，六类编辑模块及组合提示词沿用 v9。手动提示词和 Clef 路由未变。
v11 将共用提示词收敛为整体目标、信息忠实与交付；类别边界只放在路由选中的模块内，顺序继续使用通用组合说明。
无法确定替换／删除对象时的保留规则限定到修改模块的对应操作；创作明确允许空原文，
结合有效口述及相关 OCR 生成新正文，缺少非必要细节不再作为返回空原文的理由。
v12 明确 compact context、草稿及不确定性的只读边界；六类编辑规则不变。
v13 在修改模块中加入共用的 `prompts.py:CHARACTER_CLARIFICATION_RULES`：
“某词的某字”中指称字被 ASR 识别错时，以释字词中明确对应的字为准，
例如“怪异的亿腾飞的藤”提取为“异腾”；同音候选无法消歧时不猜，逐字照录时不执行释字。
手动编辑的片段／全文提示词也引用同一规则；第三模式 v4 复用释字依据并将歧义处理适配为自主选择，
Clef 分类提示词保持不变。
旧两级／分支方案的冻结提示词仅供 `tools/` 历史评测及 `docs/reports/` 结果追溯，不会被应用加载。
