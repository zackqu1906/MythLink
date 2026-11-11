# 笔画模糊识别与独立测试

## 部件连笔与走之补采（2026-10-30）

连笔规则已统一到部件，反／斤的相同起笔及其合体字共享编码；走之保持第一点独立，只连第二、三笔。双击`scripts/collect-joined-strokes.cmd`新建v3记录，只补走之末两笔，默认2轮26条，可先设1轮13条。v1/v2记录仍按旧计划续采；原70条无需重采。详见[部件连笔规则](JOINED_COMPONENT_RULES.md)。

## 连笔适配专项采集（2026-10-19）

双击 `scripts/collect-joined-strokes.cmd`，采“反”的前两笔、“的”的前两笔、一笔写完的“了”、“子”的前两笔，以及衣字旁衤末尾的撇点连写。新计划v2默认训练2轮70条（可先设1轮35条），另建独立验证2轮70条，支持自动保存及续采；v1记录保持原计划。
原始轨迹及事件单独保存到 `data/recordings/joined-strokes`；采集和单笔基线记录不会启用主程序自动拆笔。实现方案、撇点模板复用分析及操作说明见 [连笔适配说明](JOINED_STROKES.md)。

## 横折弯钩／清空边界补采（2026-10-12）

双击 `scripts/collect-clear-fold.cmd`，新计划v2默认2轮168笔。重点采横折弯钩、横撇弯钩，同时覆盖全部25种折笔、3/4段清空、2段往返和普通笔画；可先设1轮84笔。旧v1记录保持原计划续采。
动作正确就保留，预测错误也保存；不执行清空，数据独立存入 `data/recordings/clear-fold`，详见 [专项采集说明](CLEAR_FOLD_COLLECTION.md)。

## 连续完整输入测试（2026-10-12）

双击 `scripts/test-complete-stroke-input.cmd`，连续试写当前DTW与模糊匹配、左滑删除、三段往返清空、七向选字与标点。
菜单常驻：下1、左下2、右下3、左4、右5，左上逗号、右上句号，正上留空。
有笔序时做标点先确认当前页第1候选，再插入标点。数据只写入本地测试窗口，详见 [完整测试说明](COMPLETE_STROKE_TEST.md)。

## 八向选字手势采集（2026-10-12）

双击 `scripts/collect-eight-direction-selection.cmd`，直接使用本项目的 Python 和 Ring SDK。
菜单常驻：正下第1候选、左下第2、右下第3、正左第4、正右第5，上方三处为标点位置。
默认探索一轮100笔，覆盖八向、连续上—上、不同长度和停顿、左侧方向边界、普通笔画及删除对照。
数据自动保存到本项目 `data/recordings/selection-eight`；分析输出到 `data/diagnostics/selection-eight`。
采集窗口不执行手势识别，完整轨迹抬手后保留，详情见 [八向采集说明](EIGHT_DIRECTION_SELECTION.md)。

## 左滑／往返独立识别测试（2026-10-12）

双击 `scripts/test-deletion-gestures.cmd`，使用 P01 的 231 笔训练记录生成的个人测试模型。
默认另建独立验证 77 笔，抬手显示预测；模型只读取轨迹，预测及模型来源随样本保存。
真实动作符合提示就保存，即使预测错误也保存。窗口只报告结果，不删除文字，不修改主程序映射。
详细说明及初步留出结果见相邻项目 `DELETION_COLLECTION.md` 与 `data/diagnostics/deletion`。

## 方向偏差诊断（2026-10-11）

指尖水平移动但轨迹倾斜时，双击 `scripts/diagnose-stroke-direction.cmd`。
新工具复用现有采集链路，默认 48 笔，对照八方向与日常写法／整手平移，记录佩戴朝向和纸面基准。
继续用 1 开始／保存下一笔、2 重写；数据单独存入相邻项目 `data/recordings/direction`。
详细说明见相邻项目 `DIRECTION_DIAGNOSTICS.md`。诊断只测量，不自动旋转坐标或调整模型。

## 左滑删除／左右往返清空专项采集（2026-10-11）

双击 `scripts/collect-deletion-gestures.cmd`，启动相邻 `ring-stroke-ime-demo` 项目的专项戒指采集窗口。
默认训练 3 轮共 231 笔，独立验证 1 轮 77 笔，覆盖左滑／浅撇边界、2/3/4/5/6/8/10 段往返、
速度与幅度变化、普通笔画对照。三段及以上标注清空，2/3 段为边界对照；本轮不采选字动作。
数据保存在相邻项目 `data/recordings/deletion`，原始轨迹和事件保留，支持自动保存及续采。
完整说明见相邻项目 `DELETION_COLLECTION.md`。采集工具不改变主程序目前的删除映射。

## main 迁移（2026-10-10）

当前开发基线为 `main` 的 `3bd3d04`。模糊识别已适配新的笔画会话控制器，后续在 main 上继续修改。
合并时 main 的主程序触摸板仅支持 macOS；后续已补充 Windows 鼠标和程序内试写，见 `docs/TOUCHPAD_SDK.md`。
Windows 离线界面测试使用模拟输出，不能代替真实戒指验证。

切换输入框、外部光标接管、退出会话、切换输入模式及停止连接时，模糊记录随笔序清空。
确认外部选字期间冻结笔序及模糊开关；输入法确认成功后清空，失败时保留原笔序和候选。
主程序保留 main 的双击进入／退出笔画会话及统一轨迹显示。
独立窗口保留原有采集和固件手势测试流程，方便单独观察识别结果。

## 模糊识别

保留现有五类编码、字典和固定 DTW 模板，不引入 cnchar，也不需要重新采集。
DTW 使用 32 点、对齐窗口 6 点、方向权重 0.15；新增五类匹配距离输出，原来的两个不同编码备选保持不变。
距离不是校准概率。

默认开启“模糊匹配”，关闭后可对同一段笔序比较原来的严格匹配结果。
接近的类别按轨迹距离参与排序；对于不够接近的竖、撇、点／捺解释，每个候选最多允许一笔带惩罚的替换。
点／捺与斜钩也可带惩罚互相补救：斜钩属于折类，但补救使用斜钩自身的模板距离，
即使另一种折类笔形更接近，也不会丢掉斜钩分数。不会因此放开点与所有折类的任意替换。
手动按钮输入保持确定，不参与替换。每次落笔重新检查完整已输入前缀，后续笔画可以排除之前的解释；
同一个字的不同合法笔顺按最低代价去重。长于 12 笔的字保留完整笔序。

候选排序结合轨迹代价、Conway 常用字排名、离线字符频率和前文上下文，上下文加分有上限。
试写框及独立窗口使用光标前文字；外部会话使用输入法提供的前文，无法读取时使用轨迹和全局频率，
随后结合已经确认输入的文字更新上下文。

试写框和独立窗口中，轨迹有接近类别或前五候选采用了该笔的替代解释时，笔画右下角显示小问号。
符号仍显示原识别类别，问号随候选更新；关闭模糊匹配后隐藏。
`STROKE_RECOGNIZED` 日志包含五类距离、开关状态和前五候选的代价、语言加分、最终分数及替代位置；
独立窗口日志额外保留原始轨迹。

## 独立测试入口

Windows 本地环境双击 `scripts/test-stroke-dtw.cmd`，开始新一轮时添加 `--new`。
Mac 使用已安装项目依赖的 Python：

```sh
python tools/collect_stroke_samples.py --recognizer dtw --new
```

先断开主程序戒指连接，确认佩戴方向，再连接测试窗口。
按提示书写，抬手后按 **1 正确、2 错误、3 模糊**。评价针对首选具体笔形，备选正确但首选错仍按 2。
未评价的轨迹可以重写。每轮共 190 笔，标注立即保存；不加 `--new` 时继续最近一轮同版本的未完成采集。
结果位于 `data/stroke-collection`，不会自动进入模板库。

`scripts/collect-stroke-samples.cmd --recognizer structure --new` 可运行旧结构版对照。
点击诊断入口仍为 `scripts/test-click-stroke.cmd`／`tools/collect_tap_diagnostics.py`，诊断文件可用
`analyze_tap_diagnostics.py`、`replay_tap_diagnostics.py` 和 `diagnose_stroke_taps.py` 分析。
旧采集文档和修改前文件保存在本地迁移备份中。

## 验证

```sh
python -m pytest tests/test_stroke_fuzzy.py tests/test_stroke_fuzzy_ui.py tests/test_stroke_fuzzy_window.py tests/test_stroke_dtw.py tests/test_stroke_context.py
QT_QPA_PLATFORM=offscreen python tools/collect_stroke_samples.py --recognizer dtw --self-test /tmp/stroke-test.png
```

界面测试使用假戒指和假输入法；Windows 上仅模拟控制器的平台开关，不模拟真实 Mac 系统能力。
独立自检通过原 SDK 后处理和实际笔画处理线程产生轨迹，验证标注及选字流程，无需设备连接。
异步自检需要 Python 事件循环的本机回环连接。

阈值和排序权重仍是初始启发式参数。真实戒指测试应观察前五候选命中率、正确输入的排序变化和每笔耗时，
再决定是否调参或引入 cnchar。
