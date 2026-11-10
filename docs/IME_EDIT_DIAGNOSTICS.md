# 邮件富文本编辑诊断

2026-11-10：本轮只增加可观测性，不修改编辑范围、原文比较、原生按键、写入、光标恢复或撤销策略。已复现的问题保留原行为，等待目标电脑上的实际证据后另行修复。

## 收集方式

主程序和原生输入法组件都需要包含本轮改动。新组件的 `INPUT_METHOD` 状态事件有 `edit_trace_version=1`、`native_pid`、`native_build`；没有版本字段时，不能认为新诊断已生效。源码修改不会自动更新其他电脑或已安装组件。

在目标电脑复现后，记下本地时间、浏览器名称与版本、邮件服务、操作前光标的位置，以及是否有签名、引用、表格或图片。通过应用现有的详细诊断导出功能导出包含故障时刻的日志。没有新增采集开关或上传行为。

诊断包中的 `diagnostic.log` 包含统一日志及保留的轮转记录；`runtime.json` 是导出时的环境，不保证等于故障时环境。用 `run + utterance_id + client_id` 关联本句；原生激活记录另外提供 `lease`、`native_read`、`front_application` 和生命周期事件。不要混用不同运行或输入连接的证据。

新增诊断只保存长度、范围、计时、布尔值与固定分类，不保存原文、指令、结果、文本哈希、HTML、剪贴板或屏幕内容。既有交互记录仍按原策略保留模型输入输出；需要查看实际正文差异时，应使用用户主动提供的诊断包中已有记录，不能根据字符类别猜测正文。

## 一句编辑的证据

日志中仍有原来的嵌套字典。字典可能被通用事件格式截断，因此本轮同时输出独立的 `read_*`、`edit_*`、`readback_*` 字段；排查时以这些未截断字段为准。

| 环节 | 关键字段 | 含义 |
|---|---|---|
| 初次 IMK 读取 | `read_context_origin`、`read_context_complete`、`read_document_length`、`read_selection`、`read_context_readable_range`、`read_context_queries`、`read_snapshot_ms` | 区分完整文档、有限上下文、已确认的历史提示与不可读取；有限上下文不等于全文 |
| 模型请求 | `edit_schema`、`edit_source_complete`、`edit_source_range`、`edit_original_selection`、`edit_model_source_characters`、`edit_instruction_characters`、`edit_retry` | 固定模型实际使用的范围与起句选区；后续成功替换或恢复不会覆盖这份元数据 |
| 结果到达 | `edit_candidate_characters`、`edit_model_wait_ms` | 结果长度，以及从原生发出编辑请求到准备写回的等待时间，包含主程序排队等时间，不能当成纯模型延迟 |
| 提交指令、全选 | `readback_operation_stage`、`readback_committed_caret`、`readback_key_command`、`readback_key_count`、`readback_key_acknowledged`、`readback_observed_selection`、`readback_observed_marked` | 按键回执只表示投递；真实选区仍来自原有 IMK 读取 |
| 比较原文 | `readback_source_expected_characters`、`readback_source_observed_characters`、`readback_source_match`、`readback_source_relation`、`readback_source_common_prefix`、`readback_source_common_suffix` | 区分精确一致、原有末尾段落标记兼容、局部窗口是全文前缀、真正不同；这些字段只解释原有比较，不放宽比较 |
| 首处差异 | `readback_source_first_expected_kind`、`readback_source_first_observed_kind` | 仅记录 `end/lf/cr/tab/space/nbsp/paragraph_separator/line_separator/object/zero_width/surrogate/other`，不记录字符本身 |
| 写入和定位 | `readback_write_sent`、`readback_replacement_text_matches`、`readback_target_selection`、`readback_tail_characters`、`readback_tail_utf16`、`readback_caret_restore_limit`、`readback_grapheme_boundary` | 区分尚未写入、已发出写入、写入已读回、定位失败；`write_sent=true` 本身不证明外部应用已接受文字 |
| 失败 | `readback_failure_code`、`error`、`readback_operation_stage`、生命周期事件 | 判断先失败的是文本、指令位置、按键投递、定位还是连接；`operation_timeout` 需结合最后阶段判断 |

以上范围、字符串长度和差异位置均为 UTF-16 单位；`tail_characters` 与按键移动次数为 Swift 扩展字形簇数量。两者不能混算。快照计时和比较统计仅使用现有读取结果，不添加 AX、IMK 读调用、选区移动或重试。

恢复听写可能覆盖一般 `readback_*` 字段。此时查 `edit_verification_*`：这是本次编辑验证最后捕获的同一组证据，保留到本句结束。它表示验证时的状态，不代表恢复完成后的当前选区。下一句重新建立证据。

## 已复现的两个边界

本地无窗口 NSTextView + 生产 IMK 适配器可重复复现，未使用真实 Outlook 邮箱，也没有证据证明用户报告的每次故障都由它们导致。

1. 光标后的后文超过有限上下文的 32,768 UTF-16 单位：初次读取是前缀窗口，全选后得到更长正文；原比较返回 `different`，`source_relation=snapshot_prefix`。后文又超过恢复距离上限时，失败为 `source_mismatch_restore_limit`，`write_sent=false`，可能留下全选状态。
2. 后文为 513 个简单字符、初次读取完整覆盖这些文字：原文比较通过，候选结果已经写入，随后光标恢复遇到 512 字形簇上限。失败为 `caret_restore_limit_or_boundary`，`write_sent=true`；不能将这个错误当成“模型结果未写入”。512 字符的对照用例正常成功。

测试 `native/ProxiMicInput/Tests/InputAdapter/main.swift` 中的 `Mail diagnostics distinguish bounded reads from post-write caret failure without changing behavior` 锁定上述行为与诊断数据。`Source disagreement diagnostics describe rich-text boundary classes without text` 检查换行、不可断空格、对象标记等分类；它不是实际浏览器富文本协议的验收。

## 排查顺序与证据限制

先确认新诊断版本、日志时间覆盖和本句 ID，再检查 `write_sent` 与最终阶段。随后比较初次范围、模型原文范围、全选范围和首处差异。若出现连接切换，关联同句 `activation_trace`，查看是否发生于 `snapshot.documentString`、`snapshot.contextString` 等原生读取期间。

`source_match=different` 只能说明两次读数不一致，不能单独证明用户改过原文；`snapshot_prefix` 也不能单独证明整个后文未变。`key_acknowledged=true` 不能证明全选或定位已生效。日志没有 DOM、格式树或完整焦点轨迹，证据不足时仍需脱敏复现材料，不能承诺一次日志必然确定全部根因。

相关代码：`InputClient.swift`（首次读取）、`CompositionSession.swift`（模型请求、验证证据保留）、`WeChatCompatibility.swift`（默认客户端的原生选区、比较与定位）、`voice/ime/edit_diagnostics.py`（元数据白名单）、`InlineInputController._diagnose` 和 `AppController._log_inline_diagnostic`（日志落盘）。
