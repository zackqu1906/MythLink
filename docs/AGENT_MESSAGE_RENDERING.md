# Agent 任务回复样式

任务浮窗会直接渲染 Agent 返回的 Markdown，不追加模型调用，不修改原始回复或 Workflow 记录。
纯文本使用原生可选择文本；富文本按需加载本地 Qt WebEngine 区域，与原输入框共存。

支持标题、粗体/斜体/删除线、引用、嵌套列表、任务列表、分隔线、行内代码、带语言标识的代码高亮与复制、
表格横向滚动、网页和本地文件链接，以及以下扩展：

- 公式：`$…$`、`$$…$$`、`\(…\)`、`\[…\]`，通过 KaTeX 本地渲染。
- 图表：`mermaid` 围栏代码块；可放大、查看/复制源码。流式未闭合或语法不支持时保留源码。
- 图片：Markdown 图片中的绝对文件路径、相对于项目根的路径、HTTP(S) 地址及常见 raster data URL。
  点击可在当前回复区域预览。文件不存在、网络不可达或附件地址无法识别时显示提示，不伪造内容。
- 复制全文保留原始 Markdown；选中文本可使用系统复制快捷键。

链接仅在用户点击时打开。文件的 `:行号` 后缀会解析成实际文件再交给默认应用，默认应用未必支持跳行。
Codex 专属工具卡片、内嵌应用和 `sandbox:`/内部资源引用需要对应服务的数据与权限，
不能仅凭一段回复文本复刻；未知标记保留文本或显示不可预览提示。

渲染库、字体、版本及校验值位于 `agent_workflow/ui/rich_text/vendor/manifest.json`。
启动和显示无需访问 CDN；HTTP(S) 图片本身仍需要网络。页面使用无持久化 profile、内容清理和请求过滤，
不暴露 Agent 命令、设置、模型密钥，不执行回复中的 HTML 脚本。

代码入口：`ui/message_document.py`、`ui/qml/AgentMessage.qml`、`ui/qml/AgentMessageWeb.qml`、`ui/rich_text/`，
均相对于 `src/proximic_ring/agent_workflow/`。生产入口在创建 QApplication 前初始化 WebEngine。
打包收集 WebEngine/WebChannel 及 helper 的资源；macOS 签名保留 Qt 自带的 helper entitlements。

验证：`PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_CONTROLS_STYLE=Basic .runtime/venv/bin/python -m pytest -q tests/test_agent_rich_text.py`。
测试使用固定文本、临时图片和隔离记录，不发送真实 Agent 任务。
