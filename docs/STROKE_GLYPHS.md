# 候选字笔画可视化

选字区将五个候选字显示为楷体卡片。每个字按自己的笔顺和匹配结果着色：

| 状态 | 显示 |
| --- | --- |
| 已输入 | 深色实心 |
| 尚未输入 | 灰色空心轮廓 |
| 有歧义或经过模糊纠错的已输入笔画 | 黄色实心 |
| 下一笔 | 半透明蓝色 |

输入、退笔、翻页和模糊程度调整都会更新字形。已写完的字不再显示下一笔；
联想候选显示完整实心字形。黄色来自当前候选自己的纠错位置，结合当前笔画
的识别歧义；其他候选的纠错不会污染这个字的显示。关闭模糊匹配后取消黄色。

字形来自 cnchar-draw 底层 Hanzi Writer 使用的 `hanzi-writer-data@2.0.1`。
直接复用每一笔的 SVG 封闭轮廓，在 Qt 中渲染；macOS 输入法浮窗通过现有 IPC
接收同一字形的透明 PNG。这样无需在原生界面嵌入浏览器或依赖在线 CDN。

本地包含 9,574 个字形，项目常用字表的 3,500 字均有字形和笔数一致的笔顺标签。
cnchar-order 3.2.6 提供五类笔顺标签。对于识别字库接受的不同笔顺，优先选用
与当前输入相符的字形笔顺，另支持已经核对过的忄三笔换序。
不能可靠对应时显示完整楷体并提示“笔顺版本不同”；没有轮廓数据的候选仍可选字，
提示“暂缺逐笔字形”。不会按笔画数量猜测对应关系。

重新生成：

```powershell
python tools/build_stroke_glyphs.py tmp/hanzi-writer-data-2.0.1.tgz tmp/left-dot-research/cnchar-order.tgz
```

来源 URL 与 SHA-256 在 `stroke-glyphs.json.gz` 的 `sources` 中，许可说明见
`THIRD_PARTY_NOTICES.md`。生成脚本仅读取指定归档；应用运行时不下载数据。

回归验证：

```powershell
$env:QT_QUICK_CONTROLS_STYLE='Basic'
.runtime/venv/Scripts/python.exe -m pytest tests/test_stroke_glyphs.py tests/test_stroke_fuzzy_ui.py tests/test_touchpad_page_layout.py tests/test_stroke_integration.py tests/test_stroke_session.py tests/test_stroke_input.py tests/test_stroke_ipc.py
```

包含实际 SVG/PNG 解码与像素颜色检查、候选逐字纠错、翻页、退笔、点击确认、
联想、左点纠错及两种窗口尺寸的界面渲染。Swift/AppKit 浮窗需要在 macOS
运行 `tests/test_stroke_ipc.py` 验证，Windows 会跳过原生测试。
