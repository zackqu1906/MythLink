# Mythlink

Mythlink 将 Ringo 智能戒指的语音、触摸与手势能力整合到 Windows 和 macOS 桌面应用中。

- **语音输入与编辑**：近场语音检测、实时听写、跨应用编辑、本地与云端识别。
- **鼠标与笔画输入**：触摸板移动、点击、笔画识别和候选字。
- **手势与场景操作**：按应用配置快捷键、媒体控制与其他交互。
- **设备与记录管理**：连接管理、固件 OTA、语音详情、手势历史及筛选导出。
- **Agent 工作区**：macOS 可连接本机可用的 Codex，通过文字、语音和手势处理任务。

## 下载与使用

在 [GitHub Releases](https://github.com/zackqu1906/MythLink/releases) 下载 Windows x64 的 `.exe` 或 Apple Silicon macOS 的 `.dmg`。
源码 ZIP 需要自行构建。大型 ASR 和文本模型在使用相应功能时按需下载。

首次启动后连接戒指、按功能提示授予权限，再选择语音识别和输入模式。macOS 行内输入需要在设置中安装或更新输入法，并在目标文本框选中 Mythlink 输入源。

完整步骤见[安装与使用](documentation/GETTING_STARTED.md)。

## 从源码运行

macOS Apple Silicon：

```sh
bash scripts/setup-macos.sh
bash scripts/start-ui.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Compute cpu -SkipLocalLLM
.\scripts\start-ui.cmd
```

依赖、代码结构和测试入口见[开发说明](documentation/DEVELOPMENT.md)。

## 构建安装包

macOS 使用 `bash scripts/build-macos.sh`，Windows 使用 `scripts/build-windows-installer.ps1`。
构建环境、版本选择、签名与公证配置见[构建与发布](documentation/BUILDING.md)。

产品资源随源码提供；构建不依赖开发者的日志、录音、用户历史或原 `docs/` 目录。

## 项目说明

公开说明集中在 [documentation/](documentation/README.md)：

- [安装与使用](documentation/GETTING_STARTED.md)
- [构建与发布](documentation/BUILDING.md)
- [开发说明](documentation/DEVELOPMENT.md)
- [浏览器扩展](documentation/BROWSER_EXTENSION.md)
- [戒指固件更新](documentation/FIRMWARE.md)
- [仓库内容与本地数据](documentation/REPOSITORY_CONTENTS.md)

## 数据与贡献

用户记录保存在各自账户的数据目录，安装包不附带开发者记录。原 `docs/`、Agent 工作日志、实验报告、录音、截图、导出与缓存全部留在本地。
公开测试只使用明确允许的合成样例；固件、SDK、随包模型、词典和 UI 素材属于构建资源，继续纳入版本管理。

每个新克隆执行一次，启用提交前检查：

```sh
git config --local core.hooksPath .githooks
```

第三方来源与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。第一方代码的许可证与 SDK 再分发授权以项目所有者及提供方的授权为准。
