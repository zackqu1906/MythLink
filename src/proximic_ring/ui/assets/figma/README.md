# Mythlink 原始 Figma 素材

来源：用户提供的 Mythlink 文件 `KHu0n7WydXkMMHC8pp2TFo`。
取得日期：2026-09-29。通过 Figma 官方连接读取并下载，素材文件保持原始导出内容，不重绘、不截取设计截图、不保留临时下载地址。

- [首页原稿](https://www.figma.com/design/KHu0n7WydXkMMHC8pp2TFo/MythLink?node-id=416-384)
- [场景与手势原稿](https://www.figma.com/design/KHu0n7WydXkMMHC8pp2TFo/MythLink?node-id=440-1223)

## 手势插画

均为原稿内对应圆形图片图层导出的 166 × 166 PNG；设计显示尺寸为 83 × 83。图片内已含圆形背景。

| 文件 | 手势 | 原稿图层 |
| --- | --- | --- |
| gesture-circle-clockwise.png | 顺时针旋转 | I440:1321;2129:1336 |
| gesture-clench.png | 握拳 | I440:1322;2129:1336 |
| gesture-circle-counterclockwise.png | 逆时针旋转 | I440:1323;2129:1336 |
| gesture-swipe-left.png | 左滑 | I440:1324;2129:1336 |
| gesture-snap.png | 响指 | I440:1325;2129:1336 |
| gesture-index-pinch.png | 食拇捏合 | I440:1326;2129:1336 |
| gesture-middle-pinch.png | 食中捏合 | I440:1327;2129:1336 |
| gesture-swipe-down.png | 下滑 | I440:1328;2129:1336 |
| gesture-tap.png | 点击 | I440:1329;2129:1336 |
| gesture-swipe-right.png | 右滑 | I440:1330;2129:1336 |
| gesture-swipe-up.png | 上滑 | I440:1331;2129:1336 |

`GestureIllustration.qml` 等比显示原图。依用户要求，Tap、左滑、右滑在灰色锁定卡中降低显示透明度，源文件不修改；未锁定手势保持原色。卡片文案继续显示项目实际绑定，不采用设计稿的示例动作。

## 品牌、产品与界面图标

| 文件 | 原稿图层 |
| --- | --- |
| mythlink-logo.svg | 440:1225，完整商标与字标 |
| ring-product.png | 416:506，原稿产品图，976 × 1088 |
| icon-home.svg | 440:1252 内的 Home |
| icon-voice.svg | 440:1253 内的 Message square |
| icon-gesture.svg | 440:1254 内的 Navigation |
| icon-mail.svg | 440:1256 |
| icon-settings.svg | 440:1263 |
| icon-help.svg | 440:1264 |
| icon-bluetooth.svg | 416:469 |
| icon-history.svg | 431:424 |
| icon-clock.svg | 416:1235 |
| icon-operation.svg | 192:880 |
| icon-edit.svg | 416:564 |
| icon-coffee.svg | 416:572 |
| icon-book.svg | 416:580 |
| icon-copy.svg | 416:600 |
| icon-more.svg | 416:604 |
| icon-search.svg | 440:1334 内的搜索图标 |
| icon-save.svg | 440:1396 内的保存图标 |
| icon-microphone.svg | 274:1187，设置页 Mic |
| icon-pointer.svg | 274:1264，设置页 Mouse pointer |
| icon-tool.svg | 274:1297，设置页 Tool |

主导航、底部工具区、首页统计及快捷入口通过 `UiIcon.qml` 复用对应 SVG；历史条目复用 Copy / More。SVG 默认保持原色，仅主按钮与禁用按钮按状态着色，完整字标不染色、不使用字体拼接。独立的语音状态麦克风、确认等小符号仍使用现有 UI 图元；实时手势 HUD 不在本次替换范围。

Figma 的 Code Connect 指向 React 设计库组件，当前项目采用 Qt Quick，因此直接使用相应图层的 SVG 导出。

设置页三个分组标题使用 [设置原稿](https://www.figma.com/design/KHu0n7WydXkMMHC8pp2TFo/MythLink?node-id=274-1181) 的原始 SVG，放入 20 × 20 图标区域等比显示，保留原色与源文件尺寸。

## 语音历史 v2（2026-10-12）

依据 [03 语音 · 历史记录 / v2](https://www.figma.com/design/KHu0n7WydXkMMHC8pp2TFo/MythLink?node-id=995-7924)。按用户确认，历史区仍在语音输入页下方；页面顶部状态和导航保持现有结构。保留原稿的白底、浅灰紫筛选底色 `#F6F7FD` 和细边框／分隔线 `#E3E7EF`。嵌入现有窗口后使用 18px 标题、14/22 正文、12/18 元信息及原文／改后说明，记录内边距为 12px；外层沿用现有卡片的 16px 圆角与 20px 留白。

`history/` 下的六个 SVG 均为该原稿导出的原文件，尺寸保持 16 × 16，不重绘、不着色、不截取截图。`history/manifest.json` 记录图层 ID、尺寸和 SHA-256。复制／删除／更多分别位于 24 × 24 的点击区域内；异常状态标识在时间左侧，类型标签位于应用名右侧。播放录音仍在“更多”菜单内。

原稿字体 Source Han Sans SC Regular／Medium 从 [Adobe 官方 2.005R](https://github.com/adobe-fonts/source-han-sans/tree/2.005R/OTF/SimplifiedChinese) 原样取得，连同 OFL 许可证保存在 `../fonts/`，由历史区的 FontLoader 加载，不安装系统字体。

搜索、记录类型、应用、时间和结果筛选全部可用；按用户最新确认，将原稿中的记录类型和处理模式合并为听写／润色（下滑）／指令，与列表标识一致。1200 × 780 下筛选区为一行五列，窄窗口改为三列两行；搜索框略宽，各控件底边对齐，下拉菜单沿用相同字号与细边框。应用来自现有记录，时间按本地日期筛选今天／近 7 天／近 30 天。按用户确认，本次不显示原稿中的“重试／再次输入”按钮；原有播放、复制、删除、查看详情继续可用。

## 听写与编辑布局核对（2026-10-23）

依据新文件的 [听写与编辑 / VoiceWorkbench](https://www.figma.com/design/lTJ3fi0mMULAjIsnF8ybwH/1022-MythLink-Ring?node-id=567-32820)。标准 1200 × 780 窗口：内容宽 932px、卡片间距 16px、卡片内边距 20px；语音手势卡 136px 高，输入模拟卡 500px 高。输入源和收音测试并排等宽、40px 高；文本框使用剩余高度，底部操作 40px 高。窄窗口将手势排列为三列，操作内容增加时允许页面滚动。

`redesign-567-32897-microphone.svg` 是收音测试槽位的原始 16 × 16 图标；`redesign-246-3060-navigation-active.svg` 是导航按钮 Hover / Pressed 的原始 16 × 16 蓝色箭头，沿用设计的旋转角度。其余图标复用已下载的同节点素材。新增文件的节点和 SHA-256 记录在 `redesign-manifest.json`，SVG 未修改。

按用户后续要求保留热词弹窗入口、真实收音波形和录音回放。手势文案读取实际配置；开始和定稿手势不同时，在同一槽位分两行显示。教学里的展开收音卡保持独立布局。
