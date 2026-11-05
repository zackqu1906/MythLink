# 语音代码分区

这里有两个不同的判断：手势映射决定用户触发哪个动作，Clef 决定口述属于哪类文本任务。
自动模式也使用用户配置的开始、结束、取消手势，因此手势基础设施是两种模式共用的。

## 自动识别专用

| 文件（相对 `src/proximic_ring/`） | 职责 |
|---|---|
| `ui/voice/automatic_voice.py` | 自动会话入口、开始时预取 OCR、提交 Clef、接收计划并进入生成／原生编辑 |
| `text_processing/intent_router.py` | Clef 单次联合判断总体意图与六个任务，一致性拒绝与 trace |
| `text_processing/task_routing.py` | 分类类型、原文决定的可用任务、编辑计划校验、路由输入裁剪 |
| `text_processing/router_prompts.py` | 联合分类提示词、按原文生成问题 schema、类别边界 |
| `text_processing/automatic_prompts.py` | 按六类任务组合生成提示词，修改包含释字及重述 |
| `text_processing/automatic_processing.py` | `process_task_with_trace`：按计划一次生成完整结果，不走手动候选竞速 |
| `text_processing/screen_context.py` | 当次目标窗口截图、OCR、规则过滤及本地诊断证据 |
| `voice/automatic_history.py` | 自动分类及截图证据的历史展示数据 |
| `ui/qml/AutomaticVoiceDetails.qml` | 由记录展开区按钮打开的自动识别完整记录 |

流程：配置的开始动作 → 录音并预取 OCR → 配置的结束动作 → ASR 最终文本 →
Clef 联合七字段 → 一致性校验 → 组合提示词 → 一次生成 → 共用校验与写回。

## 原有用户明确选择的处理

“手动”包含用户通过手势、快捷键或界面明确选择动作，不是手动输入文字。

| 文件 | 职责 |
|---|---|
| `ui/voice/manual_voice.py` | 非原生桌面链路的手动模式入口 `_prepare_manual_candidates`；使用用户选定模式，不调用 Clef |
| `text_processing/manual_processing.py` | 原有听写、明确编辑、润色；保留片段／全文编辑竞速与候选记录 |
| `ui/voice/inline_controller.py` 中 `convert()` / `polish()` | 原生输入法的明确编辑、润色动作入口；会话、焦点等其余逻辑共用 |
| `text_processing/prompts.py` | 原有编辑／润色提示词，同时包含两种模式共用的听写和 OCR 边界规则 |

流程：配置手势 → 用户明确选择的操作 → 原有文本处理 → 共用校验与写回。
不经过Clef 联合分类，也不依赖自动模式的 `edit_plan`。

## 两种模式共用

| 文件 | 职责 |
|---|---|
| `ui/gestures/voice_gesture_mapping.py` | 用户绑定的手势 → 开始、结束、修改、润色、取消等动作 |
| `ui/gestures/ring_gesture_controller.py` | 手势事件、焦点、场景归属和优先级 |
| `voice/voice_actions.py` | 手势／快捷键转为业务动作，取消与撤销判断 |
| `ui/controller.py` | 应用总装配、ASR 和两种会话入口的分流、写回及状态协作 |
| `text_processing/worker.py` | 异步调度：有 `task_type` 走自动处理；否则走明确润色或原有处理 |
| `text_processing/llm.py` | 公共处理器 API、HTTP、模型服务、工具调用与编辑响应校验 |
| `text_processing/processing_common.py` | 输出预算和响应异常类型 |
| `voice/modification_dataset.py` | 听写、编辑、自动分类及截图的统一记录与删除 |
| `ui/voice/inline_controller.py`、`native/ProxiMicInput/Sources/` | 原生会话、目标与版本校验、文字应用、撤销 |

## 旧入口清理及仍在使用的内部命名

2026-11-05 移除了旧聊天模型二分类提示词、`legacy_input_routing.py`、
`classify_input_mode*` API 及其专用测试工具。`InputModeRoutingRequest` 默认使用 Clef，
Worker 仅接受 Clef；显式传入旧 `llm` 或 `gliner` 后端会报不支持，不会静默切换模型。
正文模型的本地预热不再发送旧分类提示词。历史记录读取保留，新的请求不再写入旧提示词。

`controller.py` 中 `_AutoInteraction`、`_prepare_auto_candidates` 等名称来自原有可切换候选流程，
目前手动桌面链路仍使用它们；它们不属于新的 Clef 自动流程。保留这些内部名称，避免连带改变候选、撤销和异步回调。

当前分类提示词为 `router_prompts.py` 的 v19，自动生成提示词为 `automatic_prompts.py` 的 v9。
本次清理未改这两套文本，仍在使用的手动编辑、润色、听写、OCR 与热词提示词保留。
旧两级／分支方案的冻结提示词仅供 `tools/` 历史评测及 `docs/reports/` 结果追溯，不会被应用加载。
