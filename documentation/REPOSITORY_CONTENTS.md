# 仓库内容与本地数据

GitHub 保存应用源码、原生组件、产品资源、依赖清单、构建脚本、`documentation/` 中的项目说明和可复用测试。
原 `docs/` 整体只留本地，包含的 Agent 记忆、交接与工作记录不对外发布。
固件 ZIP、SDK 动态库、随应用发布的模型和词典都是构建输入，继续纳入版本管理。
测试中明确列出的合成样例可以提交；真实语音、屏幕内容和用户历史不能作为样例上传。

本地工作产物统一存到以下已忽略的位置：

| 内容 | 本地位置 |
| --- | --- |
| Agent 工作日志、交接、临时分析 | `.local/` 或 `.runtime/` |
| 旧文档、Agent 记忆、评测报告与原始响应 | `docs/`、`reports/`、`tools/results/` |
| 一次性实验及录音 | `experiments/<实验名>/`、`audio_tests/` |
| 用户记录、诊断及导出 | `data/`、`logs/`、`agent-experience/`、`records/`、`exports/` |
| 手动保存的图片、录音 | `captures/`、`screenshots/`、`recordings/` |

`experiments/` 只保留 `.gitignore` 明确允许的复用代码和合成测试目录。
`tools/data/` 同样使用具体文件白名单；新增真实记录不会因放进这个目录就变成可提交样例。
其他位置的 `*.log`、SQLite 数据库和旁路文件也会被忽略。
不要用 `git add -f` 上传本地数据，也不要把用户历史复制到 `src/` 的资源目录。

## 提交检查

每个新克隆执行一次，启用本仓库的提交钩子：

```sh
git config --local core.hooksPath .githooks
```

提交前会运行 `python3 tools/check_repository_files.py`。它检查整个暂存区，
包括强制添加的文件；发现违反仓库忽略规则的文件就拒绝提交。
GitHub Actions 在 push 和 PR 时执行相同检查。CI 会报告失败，但它不是服务端接收钩子，
不能撤回已经推送的内容；新克隆仍应启用本地钩子。
这些是按路径的防误提交规则，不会自动判断任意源码文件里是否混入了个人内容。

已经被跟踪的文件不会因为新增 `.gitignore` 自动停止跟踪。可以用
`git rm --cached -- <文件>`（目录加 `-r`）取消跟踪，保留本地副本。
这会从之后的版本中移除文件；旧提交中的副本仍在 Git 历史里。

## 构建与用户数据

安装包从 `src/`、`native/`、`packaging/`、`scripts/` 和指定的第三方依赖构建，
不依赖本机的报告、实验录音或用户历史。`.runtime/` 中的构建工具由安装脚本准备。
具体构建步骤见 [桌面安装包](BUILDING.md)。

已打包的应用把用户记录保存在各自账户的数据目录：macOS 为
`~/Library/Application Support/ProxiMic Voice`，Windows 为
`%LOCALAPPDATA%\ProxiMic Voice`。源码运行时仍使用项目内已忽略的数据目录；
需要独立测试数据目录时可以设置 `PROXIMIC_DATA_HOME`。
