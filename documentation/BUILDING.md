# 构建与发布

从完整源码构建。保留 `src/`、`native/`、`third_party/`、`packaging/`、`scripts/`、`tools/`、`documentation/` 和根目录的依赖清单。原 `docs/`、个人记录及实验报告不属于构建输入。

## macOS Apple Silicon

构建机需要 Apple Silicon、网络连接和 Xcode Command Line Tools。最低部署目标由 `packaging/macos-target.txt` 定义，目前为 macOS 13.3。

```sh
xcode-select --install
bash scripts/build-macos.sh
```

脚本准备 Python 3.11 和构建环境，构建输入法、距离锁屏和浏览器助手，再通过 PyInstaller 生成应用。产物为 `dist/Mythlink.app` 和 `dist/Mythlink-<版本>-macos-arm64.dmg`。

构建会要求选择版本号，并更新 `pyproject.toml`。自动化运行需要指定版本或使用自动递增：

```sh
bash scripts/build-macos.sh --auto-version
# 只生成应用、不制作 DMG
bash scripts/build-macos.sh --app-only --version 0.6.28
```

将示例版本替换成计划发布的版本。开发机器没有正式证书时，可明确选择仅供开发使用的 ad-hoc 签名：

```sh
PROXIMIC_ALLOW_ADHOC_SIGNING=1 APPLE_SIGNING_IDENTITY=- bash scripts/build-macos.sh --app-only
```

正式发布使用可用的 Developer ID 身份和 Apple 公证配置。可在本地 `.env.macos-signing` 或环境变量中设置 `APPLE_SIGNING_IDENTITY`、`APPLE_SIGNING_KEYCHAIN`、`APPLE_NOTARY_PROFILE`；证书、密码和该配置文件不得提交。

脚本验证签名、最低系统版本、原生依赖和冻结应用启动，并在配置后提交公证、装订结果。源代码测试通过不能代替最终安装包在目标系统上的验收。

## Windows x64

准备 Inno Setup 6 后，在仓库根目录用 PowerShell 运行构建。首次重编译启动器需要 Visual Studio Build Tools 的「使用 C++ 的桌面开发」工作负载和 Windows SDK。[PyInstaller Windows 启动器构建说明](https://pyinstaller.org/en/v6.22.2/bootloader-building.html#building-for-windows)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -Version 0.6.41
```

脚本准备项目内的 Python 和 CPU 依赖，使用 `--no-cfg` 重编译 Mythlink 的 Windows 启动器以兼容 R1 SDK 使用的 Unicorn。后续构建复用已重编译的启动器。Ringo 和 R1 的触摸板模型自检通过后，生成 `dist\installer\Mythlink-<版本>-windows-x64-setup.exe`。可用 `-AutoVersion` 自动递增版本。

已有完整打包环境和兼容启动器时可传 `-SkipDependencyInstall`，构建仍会安装项目内的 R1 wheel。签名使用 `WINDOWS_SIGNING_CERT_SHA1` 和可选的 `WINDOWS_TIMESTAMP_URL`。

### Windows TSF 输入法原型

安装项目开发依赖后，使用 CMake 和 Visual Studio C++ 工具链构建 x64 DLL。MinGW-w64 可用 `-Compiler <g++.exe 的路径>`。

```powershell
.\scripts\windows-ime-prototype.ps1 -Action Build
```

在管理员 PowerShell 中执行 `-Action Install` 注册输入法。回到普通 PowerShell 执行 `-Action Run` 启用原型。若切换列表里没有 Mythlink，在 Windows 的简体中文语言选项中添加 Mythlink 键盘。

更新时再次执行 `-Action Install`。安装脚本按版本保存 DLL，已加载旧 DLL 的应用在重开后使用新版。执行 `-Action Status` 可查看注册路径和当前能查询到的加载进程。

在目标文本框按 Win+空格选中 Mythlink。将 Mythlink 的「语音启停方式」设为「手势启停」，开启语音识别和「输入到当前光标」。原型预留单指双击切换语音和触摸板。原本配置为双击开始听写时，原型改用上滑，结束通常为单击。触摸板默认控制鼠标，点入文本框后双指双击开启笔画九宫格。

原型目标为 Windows x64 桌面文本框，支持听写和笔画。安装包和代码签名另行配置。卸载前切换到其他输入法，再在管理员 PowerShell 中执行 `-Action Uninstall`。

## 构建资源

R1-PC wheel 随源码放在 [third_party/r1-sdk](../third_party/r1-sdk)。Windows 和 macOS 的安装、构建脚本都从这个目录安装 SDK。更新时移走旧 wheel，放入新版并保留一个 `ring_python_sdk_r1-*.whl`，然后重新运行安装或构建脚本。

Ringo 的 Windows 和 macOS SDK 代码、平台库和原始 wheel 也已随源码提供。首次在新电脑上运行安装脚本时需要下载公共 Python 依赖和构建工具。

PyInstaller 配方为 `packaging/mythlink.spec`。固件 ZIP、SDK 动态库、随包检测模型、笔画词典和 UI 素材是产品资源，应保留在 Git 中。大型 ASR / 文本模型由应用按需下载，不随仓库和主安装包发布。

替换固件见[固件说明](FIRMWARE.md)。浏览器构建会复制[扩展安装说明](BROWSER_EXTENSION.md)，因此 `documentation/` 需要随源码分发。

GitHub 的安装包工作流由手动触发或 `v*` 标签触发；正式标签构建需要仓库配置相应签名与公证 secrets。打包命令、配置和许可证说明分别以仓库脚本及 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 为准。
