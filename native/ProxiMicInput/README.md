# ProxiMicInput

常规构建默认使用已验证的 B72 编辑、光标校验及撤销实现，无需测试版开关或其他 worktree。

```sh
/bin/zsh scripts/build-input-method.sh
```

组件输出到 `.build/input-method/ProxiMicInput.app`。`scripts/build-macos.sh` 调用同一入口，并将该组件及 `InputMethodAdmin` 放入主应用。构建号从 72 基线按源码和工具链指纹递增；相同输入沿用已有构建号。错误标签与选区日志文件名读取实际构建号，不固定为 B72。普通组件构建不会安装或切换输入法。

## 编辑与撤销约定

- 编辑只选取本次上文，先用 AX；确定不可用时退回 `⌘⇧↑`。通过选区校验后由 IMK 提交，光标后的内容不参与重写。
- 写后保留 B72 的光标流程：AX/IMK 不一致时按位置差发送一次左右方向键，回执后重新读取并比较。移动后仍不一致即终止，不循环追逐光标。
- 光标恢复后，两次稳定、完整的实际正文读回建立撤销基准。基准可能与模型输出不同；不以模型输出替代应用实际接收的正文。
- 撤销、重做以及历史恢复仍校验所属输入框、正文和光标。必需的精确正文读取先用 IMK；无返回、范围不完整或 UTF-16 长度不符时才尝试受目标及选区保护的 AX 读取。完整 IMK 正文不匹配不能通过 AX 绕过。
- 两种接口均不可读时停止操作；不伪造撤销基准。字符串比较沿用 Swift 的 Unicode 规范等价语义，范围仍以 UTF-16 计数。

单次选区诊断位于 `~/Library/Logs/Mythlink/IMESelection/build<构建号>-<操作编号>.jsonl`。主程序日志保留正文读取来源、撤销基准范围、光标修正前后读数、失败阶段及错误码；只记录元数据，不记录正文。

## 回归

```sh
/bin/zsh scripts/test-input-method.sh
.runtime/venv/bin/python -m pytest -o addopts= -q \
  tests/test_input_method_build.py tests/test_input_method_installer.py \
  tests/test_wechat_native_keys.py tests/test_inline_state.py
```

原生套件覆盖 B70/B71/B72 选区、光标、IMK/AX 正文回退和撤销基准；控制器与 Python IPC 集成验证连续撤销/重做、输入法切换后的历史恢复、中间光标与后文保留、撤销后重新编辑。IPC 使用本地输入框与注入的选区驱动，不操作其他应用。AppKit 测试需要可用的 macOS 图形会话。
