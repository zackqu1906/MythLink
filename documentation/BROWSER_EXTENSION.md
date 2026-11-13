# 浏览器扩展安装与发布

扩展为 Mythlink 提供浏览器媒体场景和播放控制，Chrome 与 macOS Safari 共用网页识别逻辑。只处理已授权网页中可访问的标准音频、视频播放器；浏览器内部页面、内置 PDF、封闭 Shadow DOM 等页面可能无法识别。

## 安装与授权

启动 Mythlink，在设置中打开「浏览器媒体控制」。

- Chrome：打开安装指引，复制扩展文件夹路径并进入 `chrome://extensions/`。开启开发者模式，加载已解压的扩展。已安装时启用或重新加载，并允许目标网站访问、刷新页面。
- Safari：打开启用指引，进入 Safari 扩展设置，勾选 Mythlink 浏览器助手，再允许目标网站访问并刷新页面。Safari 助手随 macOS 应用提供。

安装、启用、连接和网站授权是不同状态。以 Mythlink 收到扩展心跳后显示的连接状态为准。多个播放器同时存在时，先点击希望控制的播放器。

未签名或未公证的 Safari 开发包可能需要用户在 Safari 开发者设置中允许开发扩展。正式外部分发需要完成相应签名及公证；程序不会替用户修改浏览器安全设置。

语音与笔画输入不依赖媒体扩展。扩展断开时媒体识别不可用，输入仍遵循应用自身的输入法与焦点规则。

## 开发构建

在已准备好依赖的项目根目录运行：

```sh
.runtime/venv/bin/python tools/build_browser_extension.py --chrome-only
```

macOS 构建 Safari 助手时去掉 `--chrome-only`，需要 macOS SDK 和 Swift 编译器。正式签名可指定 `--identity`；完整 `scripts/build-macos.sh` 会构建并嵌入该助手。

默认输出到 `.build/browser-extension/`，包括 Chrome 开发目录、商店提交 ZIP，以及 macOS 构建时的 `MythlinkBrowser.app`。构建工具把本说明复制到输出目录。商店 ID 由 `src/proximic_ring/browser_bridge/release.json` 配置；开发扩展 ID 不代表已发布的商店项目。

## 通信与数据

Chrome 使用 Native Messaging，Safari 使用内嵌原生桥。桌面桥只监听本机地址，并使用会话令牌。扩展提供临时窗口、标签、播放器身份及播放、焦点和授权状态，不向桌面发送网页正文、输入文本或媒体内容。

发布前在对应浏览器核对首次安装、拒绝与重新授权、多播放器选择、断连恢复、输入焦点和实际媒体控制。原始日志和测试报告留在本地。
