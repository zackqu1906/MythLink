# 开发说明

## 源码运行

在完整仓库根目录执行，脚本将运行环境放在项目内。

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

项目使用 Python 3.11。依赖和平台约束由 `pyproject.toml`、`requirements-macos.lock`、`requirements-windows.lock` 及 `requirements-packaging.txt` 定义。安装包构建见[构建与发布](BUILDING.md)。

每个克隆启用一次本地提交检查：

```sh
git config --local core.hooksPath .githooks
```

## 代码结构

| 位置 | 职责 |
| --- | --- |
| `src/proximic_ring/asr/` | 语音识别后端、请求与结果适配 |
| `src/proximic_ring/voice/` | 听写、编辑、输入法会话与用户历史 |
| `src/proximic_ring/text_processing/` | 模型调用、提示词及编辑结果校验 |
| `src/proximic_ring/touchpad/` | 鼠标控制、笔画识别与候选字 |
| `src/proximic_ring/gestures/` | 手势处理与历史 |
| `src/proximic_ring/scenes/` | 应用场景识别与映射 |
| `src/proximic_ring/agent_workflow/` | 产品 Agent、记录查看和 Computer History 功能 |
| `src/proximic_ring/firmware/` | 固件资源校验与升级流程 |
| `src/proximic_ring/ui/` | Qt 控制器、QML 和素材 |
| `src/ring_python_sdk/`、`src/ring_mac_sdk/`、`src/ring_windows_sdk/` | Ring 设备与平台 SDK |
| `native/` | 原生输入法、距离锁屏和浏览器助手 |
| `packaging/`、`scripts/`、`tools/` | 构建配方、启动与维护工具 |
| `tests/` | 自动化回归测试 |

保留的旧 Workflow 实验命令不是发行包中的活动推荐流程。产品发行使用 Computer History 路径；旧实验的输入和结果应留在本地。

## ASR 与设备接口

近场检测和 ASR 后端解耦，后端位于 `src/proximic_ring/asr/backends/`。后端公开 `backend_name`、`model_name` 和 `transcribe(audio_16k)`，通过 `create_backend(settings)` 创建。增加后端时同时检查依赖、工厂、用户设置和测试。

```sh
.runtime/venv/bin/python -m proximic_ring --help
.runtime/venv/bin/python -m proximic_ring asr-backends
.runtime/venv/bin/python -m proximic_ring ring --help
```

Windows 使用 `.runtime\venv\Scripts\python.exe`。CLI 的 `--asr` 选择后端，`--asr-model` 固定模型；云端服务使用当前用户配置的凭据。设备例子见 [ring_touchpad.py](../examples/ring_touchpad.py)。

## 测试

使用对应开发环境运行受影响模块的测试：

```sh
.runtime/venv/bin/python -m pytest tests/test_runtime_paths.py
python3 -m unittest discover -s tests -p test_repository_files.py
```

界面测试需要 Qt，平台原生测试需要对应系统。把单元测试、云服务请求、安装包启动和真实戒指验收分别记录；一种检查不能替代另一种。

真实录音、截图、用户历史和模型原始响应不作为公开测试样例。需要共享的测试输入应使用合成数据，明确列入 `tools/data/` 的允许列表。需要独立运行数据目录时设置 `PROXIMIC_DATA_HOME`；仓库内的日志、数据和缓存目录已被忽略。

## 文档维护

`documentation/` 只写可持续维护的安装、使用、开发和发布说明。实验结论、Agent 交接与个人调试记录放到本地 `docs/`、`.local/` 或 `.runtime/`。不要让构建脚本依赖这些本地目录。详细规则见[仓库内容与本地数据](REPOSITORY_CONTENTS.md)。
