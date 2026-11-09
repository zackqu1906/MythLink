# macOS 兼容性验收

更新：2026-11-06。当前最低部署目标为 **Apple Silicon、macOS 13.3**，不支持 Intel Mac。
新版戒指 SDK 固定使用 ONNX Runtime 1.21.0；其 wheel 虽标记 `macosx_13_0_universal2`，
内含 `libonnxruntime.1.21.0.dylib` 和 `onnxruntime_pybind11_state.so` 的 arm64 Mach-O
最低版本实际为 13.3。因此统一提高声明、原生组件构建目标及整包校验标准，保留完整兼容性检查。
构建机为 macOS 15.7.5；尚未在 macOS 13.3 真机运行，不能将静态检查当作所有系统版本的完整功能验收。

## 已处理的阻塞项

| 原问题 | 当前处理 |
| --- | --- |
| 主应用、输入法、距离组件、Safari 助手要求 15.0 | 统一读取 `packaging/macos-target.txt`，当前默认 13.3；Swift 组件按此目标重新编译 |
| PySide6 6.11.1 及 6.10.2 的绑定库实际要求 15.0 | macOS 固定为 6.9.3；检查 wheel 内真实 Mach-O，不以 wheel 文件名判断兼容性 |
| 截图、窗口预览使用 14/14.2/15 才有的 API | 新系统使用 ScreenCaptureKit 截图；13 使用 CoreGraphics；预览的可选属性按可用性设置 |
| Agent 借用外部应用中最低 13.5 的 Node | 随包提供固定版本 Node 22.23.4，保留 JIT 签名权限；运行前核对架构和最低系统 |
| 本地 LLM 官方 b10516 归档曾超过原 13.0 目标 | 继续从同一版本源码按配置目标编译运行库，保留 CPU/Metal，维持原有可选 BLAS 关闭配置 |
| 新版戒指 SDK 的 ONNX Runtime 1.21.0 要求 13.3 | 最低部署目标提高到 13.3，不篡改依赖二进制的最低版本声明 |
| 后续依赖升级可能再次提高最低系统 | 构建时检查全部原生二进制和内嵌 bundle，超过声明目标直接阻止构建 |

内置 llama.cpp 会替代已有默认运行库路径，继续使用用户原有模型权重；自定义运行库路径
保留。关闭可选 BLAS 可能影响 CPU 性能，当前未做正式模型的性能对比。

## 2026-11-06：当前 13.3 构建

产物：`dist/Mythlink-0.6.10-macos-arm64.dmg`，约 454.2 MiB。主程序与三个内嵌助手的
最低系统声明均为 13.3，全部原生库通过 arm64、最低系统版本和依赖可移植性检查。
36 项打包相关回归通过；历史记录服务的集成测试需要本机监听权限，沙箱外复验通过。
Developer ID 签名、冻结应用启动自检、DMG 完整性校验和只读挂载后的签名及兼容性检查通过。
从冻结程序归档核对，已包含生成提示词 `automatic-composed-v11-modular-rules` 和路由提示词
`voice-intent-joint-v20-general-modify`。本次未提交 Apple 公证，未进行 macOS 13.3 真机验收。

距离助手改为在临时目录编译和签名，验证后再替换构建产物，避免源码主程序在构建途中启动未签名助手，
导致就地重签出现 `Operation not permitted`。没有安装或替换 `/Applications` 中的主程序。

SHA-256：`a69c50940262314bf1759d89e9d9bebdbdf472dfb63c89ed05308710bb985299`。
证据：`.build/macos-13.3-0.6.10-build.log`、`.build/macos-13.3-0.6.10-dmg-check.log`、
`.build/macos-13.3-0.6.10-artifact.json`。

## 2026-11-02：原 13.0 测试包的历史证据

以下数量和结果对应旧版 0.6.7，保留用于追溯，不作为当前版本的验收结果。

- 成品 199 个原生二进制均含 arm64；arm64 最低版本分别为 11.0（80 个）、12.0
  （96 个）、12.1（1 个）、13.0（22 个），没有超过 13.0 的项。
- 218 项相关自动回归通过，Windows 安装器测试在 macOS 跳过。
- 合成窗口的现代截图和强制旧系统截图共 4 个案例通过；旧路径同时验证了排除遮挡窗口。
  这证明旧路径可运行，不等于已在旧系统验收。
- 真实合成窗口的动态预览、遮挡后更新、停止清理通过。
- 冻结应用的主 QML、Agent 工作区、输入法资源、权限后端和 ASR 导入启动检查通过。
- 冻结应用的 Chromium 富文本加载/卸载 12 轮、输入区与拖动区隔离、展开/收起和关闭通过。
- 签名后的 Node 执行检查通过。签名后的 llama.cpp 用官方 stories15M 测试模型完成 CPU
  和 Metal 各 16 token 推理，日志分别确认 GPU 卸载 0/7 和 7/7 层。
- 整包 Developer ID 签名、内嵌组件签名与更新身份检查通过。
- 最终 DMG 校验和与签名通过；只读挂载后再次通过最低版本、Node/LLM 启动、冻结应用
  启动和 Agent 界面 12 轮交互检查。

本机证据保存在 `.build/macos13-binary-report.json`、`.build/macos13-regression.xml`、
`.build/macos13-packaging-tests.log`、`.build/macos13-history-packaging.log`、
`.build/macos13-native-preview.log`、`.build/macos13-agent-ui/report.json`、
`.build/macos13-llm-smoke.json` 和 `.build/macos13-build.log`。
最终映像的挂载检查见 `.build/macos13-dmg-check.log`。
历史记录 HTTP 服务测试需要正常本机监听权限：沙箱内失败，正常权限下复验通过。

## 测试包与后续实机验收

历史测试包：`dist/Mythlink-0.6.7-macos13-arm64.dmg`。原有同版本常规 DMG 保留。
大小约 429 MiB，SHA-256：
`c1a1a75ca788a3a906506a016cddbaaa049ada34c4676af1f9c7603728c7d07c`。
已有 Developer ID 签名；本机未配置 Apple 公证凭据，本次没有提交公证。
本次未安装到 `/Applications`，未替换正在使用的输入法或改变设备设置。

当前版本待测试系统：macOS 13.3、13 最新小版本、14、15、26。每台机器记录系统小版本、芯片、
权限状态、外部 Codex/ChatGPT 版本和本地模型版本。15.7.5 已完成上面的软件检查，
实体戒指和首次授权流程仍需单独验收。

1. 从 DMG 拖入应用并首次启动，完成蓝牙、辅助功能、屏幕录制和所需麦克风权限。
2. 安装/更新输入法，在多个应用中切换输入源、听写、编辑、取消，再重启主程序验证。
3. 实体 Ring 扫描、连接、断开重连；按当前配置验证手势、焦点路由和演示操作。
4. 验证窗口预览、被遮挡窗口截图、整屏/区域截图、多屏不同缩放和取消截图。
5. 完成一轮 Agent 实际任务、追加输入、取消、富文本展示及电脑操作；确认外部
   Codex/ChatGPT 自身支持该系统。本应用内置 Node 不替代外部客户端的兼容性要求。
6. 用正式 ASR/LLM 模型测试电脑麦克风、Ring 语音、云端识别、本地识别和本地文本处理。
   小测试模型推理通过不代表正式模型性能或识别准确率已验收。
7. 验证浏览器扩展、权限引导、距离功能、睡眠唤醒及应用升级后的权限保持。

收到实机结果后，应按具体失败日志补适配，再将对应系统标为已验收。当前包可用于兼容性
测试，面向普通用户分发前还需完成实机验收和 Apple 公证。
