# 场景代码结构

五类场景统一位于 `src/proximic_ring/scenes/`。本次整理保持既有行为，不调整快捷键、检测阈值、语音优先级、用户配置格式或自动启用策略。

```text
scenes/
├── models.py          场景 ID、SceneResult
├── registry.py        五类场景、默认手势含义、进入动作、兼容存储策略
├── capabilities.py    应用身份、类别、文件类型、能力与标题别名
├── policy.py          推荐场景和启用范围
├── adapters/
│   ├── presentation.py  各演示软件原有按键与动作目录
│   ├── native.py        PDF、图片、音乐、视频的原生按键
│   └── browser.py       浏览器共用转圈导航与播放器按键
├── menus.py           菜单动作的语义匹配
├── resolver.py        统一动作解析和 UI 动作目录入口
├── defaults.py        共用默认手势生成，普通应用与场景内配置组合
├── configuration.py   初始化、版本升级、待配置补全、未改动默认值刷新
├── migrations.py      启用状态、旧音乐音量、浏览器转圈、WPS 键位迁移
└── recognition/
    ├── engine.py        统一识别入口
    ├── presentation.py  放映证据
    ├── content.py       原生文档和媒体证据
    ├── browser.py       当前网页/播放器证据
    ├── playback.py      共用播放状态证据
    ├── documents.py     共用文档类型
    ├── focus.py         共用焦点边界
    └── diagnostics.py   识别原因
```

## 入口与边界

- `configuration.new_application(candidate)`：生成新应用记录，沿用推荐、默认启用和持久化格式。
- `defaults.default_mappings(...)`：普通应用配置及场景配置的对外组合入口。
- `defaults.scene_defaults(...)`：五类场景都使用的默认手势生成器。
- `defaults.entry_defaults(...)`：进入场景前的操作，仍写入普通应用 `bindings`。开始放映不能要求已处于放映模式。
- `resolver.resolve_action(...)`：动作含义转换成应用的具体快捷键。
- `resolver.catalog_actions(...)`：编辑器中的预设动作列表，保留原动作 ID、标签和顺序。
- `recognition.engine.detect_scene(...)`：接收最新窗口/焦点快照，仅返回场景判断。

依赖方向为：共用模型和场景定义 → 能力/适配 → 解析/默认配置 → 配置生命周期 → UI。
识别只依赖共用模型、元数据和自身识别模块，不加载快捷键解析、配置、Qt 或原生发送通道。
UI 继续负责异步请求、保存设置、发出信号和使旧请求失效；配置函数不发送按键。`app_shortcuts.py` 与手势分发器的执行逻辑不变。

## 本次保留的行为

- 放映继续优先使用已知应用预设，未知应用从菜单查开始放映命令，场景内保留原有方向键/Escape 兜底。
- 阅读/图片/媒体继续优先匹配菜单，再使用原有预设/适配；无法确认的动作保持待配置。未改动的内置默认值才允许被完整菜单结果更新。
- WPS 当前页为 Shift+F5，PowerPoint 当前页为 Cmd+Return；保留 Tap/响指等用户选定手势。
- 音乐上下滑调节音量；浏览器所有场景共用转圈切标签。语音忙碌、前台变化、窗口与焦点校验均沿用原实现。
- 不把 QuickTime 的快进速度操作改为固定秒数跳转，也不新增通用键位猜测。

`registry.py` 的 `resolution` 明确记录现有解析优先级。要改变菜单优先级或动作语义，需要独立的行为变更与回归，本次不顺带修改。

## 配置及导入兼容

设置键仍为 `gestures/applicationMenusV1`，保留 `bindings`、`scenes`、`enabledScenes`、`primaryScene`、`initializedScenes`、`pendingDefaults`、`pendingStart`、版本号和移除标记。主动清空、停用、删除和自定义绑定不会因重构重置。

放映原有的待配置字段 `pendingStart` 与其他场景的 `pendingDefaults` 保持原样；统一定义中的 `pending_format`、`storage_order` 只描述已有格式。`pending_scene_defaults` 是统一生成器的兼容投影，不包含第二套模板或解析算法。

旧顶层 `gesture_scenes.py`、`scene_capabilities.py`、`scene_defaults.py`、`application_defaults.py`、`application_scene_policy.py`、`browser_shortcuts.py` 仅提供兼容导入，不再保存独立业务实现。旧 `scene_recognition/` 目录已删除；生产代码和测试全部使用 `scenes/recognition/`，结果模型统一从 `scenes/models.py` 导入。打包无需包含旧识别目录。

日志会记录 `scenes/` 下实际实现的指纹，错误仍关联同一场景 trace。运行中的旧进程需要重启才会加载新模块。

## 修改位置与验证

新增/调整场景手势从 `registry.py` 开始；应用按键差异放入 `adapters/`，菜单名称匹配放入 `menus.py`，配置升级放入 `migrations.py`。识别问题在 `recognition/` 对应模块修复；不把应用键位塞进识别器或 QML。

验证覆盖旧接口兼容、新模块导入顺序、识别依赖隔离、五类场景共用生成器、普通作用域进入动作、用户配置生命周期、语音/手势路由及编辑器。重构前后还对已知/未知应用、场景组合、空/有效/歧义菜单的公共输出逐项比较。
