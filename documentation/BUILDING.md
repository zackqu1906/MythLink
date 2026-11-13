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

准备 Inno Setup 6，在仓库根目录运行 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Compute cpu -SkipLocalLLM
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1
```

产物为 `dist\installer\Mythlink-<版本>-windows-x64-setup.exe`。版本参数为 `-Version 0.6.28` 或 `-AutoVersion`；将示例版本替换成计划发布的版本。

只修改业务代码、未修改依赖时可以使用 `-SkipDependencyInstall`。修改依赖清单后先更新开发环境。签名使用 `WINDOWS_SIGNING_CERT_SHA1` 和可选的 `WINDOWS_TIMESTAMP_URL`。

## 构建资源

PyInstaller 配方为 `packaging/mythlink.spec`。固件 ZIP、SDK 动态库、随包检测模型、笔画词典和 UI 素材是产品资源，应保留在 Git 中。大型 ASR / 文本模型由应用按需下载，不随仓库和主安装包发布。

替换固件见[固件说明](FIRMWARE.md)。浏览器构建会复制[扩展安装说明](BROWSER_EXTENSION.md)，因此 `documentation/` 需要随源码分发。

GitHub 的安装包工作流由手动触发或 `v*` 标签触发；正式标签构建需要仓库配置相应签名与公证 secrets。打包命令、配置和许可证说明分别以仓库脚本及 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 为准。
