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
