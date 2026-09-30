# 场景默认配置

添加应用时，按应用声明的能力创建所有支持的场景。场景的动作含义一致，实际快捷键按软件适配。

| 场景 | 默认动作 |
| --- | --- |
| 放映 | 常规响指开始；放映中左滑上一页、右滑下一页、响指结束 |
| 音乐 | tap 播放／暂停、左／右滑上／下一首、顺／逆时针提高／降低音量 |
| 视频 | tap 播放／暂停、左／右滑后退／快进、顺／逆时针提高／降低音量 |
| PDF 阅读 | 左／右滑上／下一页 |
| 图片预览 | 左／右滑上／下一张（按查看器自己的翻页行为） |

## 场景识别结构

所有场景识别实现统一放在 `src/proximic_ring/scene_recognition/`：

```text
scene_recognition/
├── engine.py        统一入口 detect_scene，负责调度和识别顺序
├── models.py        所有识别器共用 SceneResult 返回结构和场景名称
├── presentation.py  放映识别，适用于各个演示应用
├── content.py       PDF、图片、原生视频和音乐识别
├── browser.py       网页播放器识别
├── focus.py         共用焦点归属、输入状态、隐藏及弹窗检查
├── documents.py     共用文件类型判断
└── websites.py      共用网站身份解析
```

`app_shortcuts.py` 获取最新前台与 AX 快照后，只调用 `engine.detect_scene()`。
放映、内容和网页识别是同一识别模块的内部实现，均返回 `SceneResult`；返回中保留
场景、输入上下文，以及网页场景的页面／播放器身份，供发送按键前再次校验。

多用途应用先判断放映，再判断阅读／媒体。浏览器视频需真实页面及播放器证据；
未确认视频时继续判断 PDF 等内容，并保留当前页面身份。每次调用使用新快照，
没有跨手势沿用的场景缓存。保留浏览器 180 ms、原生识别 300 ms，以及多用途
应用放映识别 240 ms 的原有限制，总入口预算为 480 ms。

识别模块只返回判断结果，不保存绑定、不发送按键、不调用语音端点。
`website_defaults.py` 单独保存网站快捷键预设；应用能力、默认动作、配置管理与
手势分发仍由下述各层负责。QML 编辑器选择的模式不作为运行时识别依据。

原来的 `activity_scenes.py`、`presentation_detection.py`、`scene_focus.py`、
`scene_documents.py`、`browser_media.py` 已迁移并删除，生产代码没有旧识别入口。
已有行为测试通过测试辅助函数调用新统一入口；`tests/test_scene_recognition.py`
另覆盖统一返回结构、场景切换、网页身份、依赖隔离及导入顺序。

## 模块职责

- `scene_capabilities.py`：应用身份、文件扩展名和 UTI 声明决定支持哪些场景；这不代表当前场景已激活。
- `scene_defaults.py`：场景 → 手势 → 动作语义；软件快捷键适配与菜单语义匹配。无 Qt、配置读写、焦点判断、按键发送。
- `application_defaults.py`：组合放映、聊天、阅读和媒体的配置。各场景互不覆盖，多个应用不共享可变记录。
- `ApplicationMappingController`：添加、持久化、恢复、清空；菜单异步结果只补齐尚未完成且未被用户删除的预设。
- `scene_recognition/`：统一场景识别及共用焦点、文档判断。
- `app_shortcuts.py` 与手势控制器：读取已保存的实际映射，校验最新前台并执行。默认模板不作为运行时的隐藏兜底。

## 保存和升级

沿用 `gestures/applicationMenusV1`，保留既有 regular、scenes 和 websites 分区。

新增 `sceneDefaultsVersion`、`initializedScenes` 记录已经处理的场景；`pendingDefaults` 保存等待本机快捷键的手势和动作语义。`defaultsCleared` 保留主动清空意图。未知软件不会因猜测按键而获得错误操作；UI 展示“已保存预设／等待快捷键”，可刷新菜单、手动绑定或选择“无”后保存取消。

已有场景整体视为用户配置，包括空场景。缺少的场景初始化一次；重新启动、刷新、切换场景不会重复灌入默认。新版添加的预设支持逐项删除，清空应用后不会因扫描重新生成。用户移除的应用保持移除；只有明确重新添加或恢复默认时重建模板。

网站配置仍是独立覆盖：已有哔哩哔哩和用户网站映射保持不变；不会以通用视频默认值填充用户清空的网站配置。媒体默认配置只在原有运行时检测确认对应场景和可操作焦点后执行，输入框、未知焦点、其他前台应用仍按原有规则处理。

## 快捷键依据

优先沿用已实现的 Apple Music、Preview、QuickTime 适配。新增原生适配参考：

- [IINA 官方默认按键配置](https://github.com/iina/iina/blob/develop/iina/config/iina-default-input.conf)：区分快进后退与播放列表切换。
- [VLC 3.x 官方 macOS 默认按键](https://github.com/videolan/vlc/blob/3.0.x/src/libvlc-module.c)：使用 macOS 分支，不使用 Windows／Linux 的字母单键。
- [QuickTime 官方快捷键](https://support.apple.com/guide/quicktime-player/qtpa4808515d/mac)：音量上／下。
- [Spotify 官方快捷键](https://support.spotify.com/us/article/keyboard-shortcuts/)：仅预置已确认的 Space 播放／暂停；切歌和音量从该应用实际暴露的菜单补齐。

其它软件使用真实菜单的中／英文动作名称和已解析快捷键。同义动作出现不同快捷键、语义不明确、按键不受支持时保留待配置状态，不把切换标签页误当切歌、也不把切换幻灯片误当 PDF 翻页。未暴露菜单快捷键的软件仍需补充专用适配或用户录入，不能承诺所有软件、所有版本的按键完全相同。
