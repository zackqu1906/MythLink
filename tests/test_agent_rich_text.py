"""Real local Chromium rendering, QML layout and message/URL boundaries."""
import json
import os
import time
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest
from test_inline_ui import inline_ui
from test_agent_ui import finish_fixture_voice


def until(predicate, timeout=15000):
    from PySide6.QtTest import QTest
    end = time.monotonic() + timeout / 1000
    while time.monotonic() < end:
        if predicate(): return
        QTest.qWait(25)
    assert predicate()


@pytest.fixture
def rich_window(inline_ui):
    from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
    owner = inline_ui[0]; agent = owner.agent
    finish_fixture_voice(owner); agent._visible = agent._focused = True
    overlay = AgentOverlay(agent, owner); overlay.reduce_motion = True
    overlay.focus('task')
    window = overlay.windows['task']
    yield agent, window
    overlay.close()


def js(web, expression):
    # QML WebEngine's JS callback is a QJSValue, so use a JSON title probe to
    # inspect the actual renderer from Python without adding production slots.
    from PySide6.QtCore import QMetaObject, Q_ARG
    token = str(time.monotonic_ns())
    code = 'document.title=' + json.dumps(token) + '+JSON.stringify((' + expression + '))'
    assert QMetaObject.invokeMethod(web, 'runJavaScript', Q_ARG(str, code))
    until(lambda: web.property('title').startswith(token))
    return json.loads(web.property('title')[len(token):])


def web_for(window):
    from PySide6.QtCore import QObject
    root = window.rootObject()
    until(lambda: root.findChild(QObject, 'agentRichMessageWeb') is not None)
    web = root.findChild(QObject, 'agentRichMessageWeb')
    until(lambda: window.message_document.ready)
    return web


def test_full_markdown_formula_diagram_image_and_copy(rich_window, tmp_path):
    from PySide6.QtGui import QImage, QColor, QGuiApplication
    from PySide6.QtCore import QObject
    agent, window = rich_window
    path = tmp_path / 'image with space.png'
    image = QImage(120, 50, QImage.Format_RGB32); image.fill(QColor('#5488cc')); image.save(str(path))
    text = ('# 任务结果\n\n**重点**、*强调*、~~取消~~和 `inline()`。\n\n'
        '> 引用\n\n- 一级\n  - 二级\n- [x] 完成\n\n'
        '| 名称 | 数值 |\n|---|---:|\n| 结果 | 42 |\n\n'
        '```python\nprint("hello")\n```\n\n'
        '行内 $x^2$，另一个 \\(y=2\\)。\n\n$$\\sum_{i=1}^n i=\\frac{n(n+1)}{2}$$\n\n'
        '```mermaid\nflowchart LR\n A[任务] --> B[完成]\n```\n\n'
        f'![测试图片](<{path}>)\n\n[文件](<{path}>) · [网页](https://example.com)')
    agent._apply_state({'status':'已完成','agent':text})
    web = web_for(window)
    until(lambda: js(web, '!!document.querySelector(".diagram svg")'))
    result = js(web, '''({headings:document.querySelectorAll('h1').length,
      strong:document.querySelector('strong').textContent,
      table:document.querySelectorAll('.table-scroll table').length,
      highlighted:document.querySelectorAll('code .hljs-built_in').length,
      math:document.querySelectorAll('.katex').length,
      image:[...document.images].some(i=>i.complete && i.naturalWidth===120),
      nested:!!document.querySelector('ul ul'), checked:!!document.querySelector('input:checked'),
      width:document.documentElement.scrollWidth, viewport:innerWidth})''')
    assert result['headings'] == result['table'] == 1
    assert result['strong'] == '重点' and result['highlighted'] > 0 and result['math'] == 3
    assert result['image'] and result['nested'] and result['checked']
    assert result['width'] <= result['viewport'] + 1
    before = QGuiApplication.clipboard().text()
    try:
        js(web, '(document.querySelector(".code-toolbar button").click(),true)')
        until(lambda: QGuiApplication.clipboard().text() == 'print("hello")')
    finally: QGuiApplication.clipboard().setText(before)
    viewport = window.rootObject().findChild(QObject, 'agentMessageViewport')
    assert window.height() == window.rootObject().property('maximumExpandedHeight')
    viewport.property('contentItem').setProperty('contentY', 150)
    until(lambda: abs(js(web, 'scrollY') - 150) < 2)
    js(web, '(window.scrollTo(0,250),true)')
    until(lambda: abs(viewport.property('contentItem').property('contentY') - 250) < 2)
    assert window.grabWindow().save(str(tmp_path/'rich-message.png'))
    if destination := os.environ.get('MYTHLINK_TEST_ARTIFACTS'):
        from PySide6.QtTest import QTest
        destination = Path(destination); destination.mkdir(parents=True,exist_ok=True)
        for name,selector in [('text','h1'),('table','.table-scroll'),('math','.math-source'),('diagram','.diagram'),('image','img')]:
            js(web, f'(document.querySelector({json.dumps(selector)}).scrollIntoView(),true)')
            QTest.qWait(100)
            assert window.grabWindow().save(str(destination/(name+'.png')))


def test_streaming_rerenders_without_losing_input_or_stale_diagrams(rich_window):
    from PySide6.QtCore import QObject
    agent, window = rich_window
    agent.edit('task', '保留的输入')
    agent._apply_state({'turnId':'turn','agent':'## 处理中\n\n```mermaid\nflowchart LR\n A -->'})
    web = web_for(window)
    assert js(web, 'document.querySelector("pre code").textContent').endswith('A -->')
    agent._apply_state({'agent':'## 完成\n\n```python\nprint(42)\n```'})
    until(lambda: js(web, 'document.querySelector("h2")?.textContent') == '完成')
    assert js(web, 'document.querySelectorAll(".diagram").length') == 0
    assert agent.draft('task') == '保留的输入'
    assert window.rootObject().findChild(QObject,'agentEditor').property('text') == '保留的输入'
    agent._apply_state({'agent':'简短结果'})
    until(lambda: window.rootObject().findChild(QObject,'agentRichMessageWeb') is None)
    assert window.height() < window.rootObject().property('maximumExpandedHeight')


def test_raw_html_and_dangerous_links_cannot_execute(rich_window):
    agent, window = rich_window
    agent._apply_state({'agent':'# 安全\n\n<script>window.compromised=1</script>\n'
        '<img src="file:///etc/passwd" onerror="window.compromised=1">\n'
        '[危险](javascript:alert%281%29)\n\n[终端](terminal://run)\n\n'
        '```js\n<script>alert(1)</script>\n```'})
    web = web_for(window)
    assert js(web, '({bad:!!window.compromised,images:document.querySelectorAll("img[src]").length,links:document.querySelectorAll("a[href]").length,code:document.querySelector("pre code").textContent})') == {
        'bad':False, 'images':0, 'links':0, 'code':'<script>alert(1)</script>'}


def test_link_policy_and_stale_measurements(tmp_path):
    from proximic_ring.agent_workflow.ui.message_document import message_link, MessageDocument
    file = tmp_path/'a b.py'; file.write_text('print(1)')
    assert message_link(str(file)+':12').toLocalFile() == str(file)
    assert message_link('a b.py:3',tmp_path).toLocalFile() == str(file)
    assert message_link('https://example.com').scheme() == 'https'
    for value in ('javascript:alert(1)','data:text/html,hi','terminal://run','file:///nonexistent','x\nhttps://example.com'):
        assert message_link(value).isEmpty()
    doc = MessageDocument(); doc.setSource('first'); old = doc.revision
    doc.setSource('second'); doc.measure(old,100)
    assert not doc.ready
    doc.measure(doc.revision,55); assert doc.documentHeight == 55


def test_links_open_only_on_click_and_invalid_diagrams_keep_source(rich_window, monkeypatch):
    from proximic_ring.agent_workflow.ui import message_document
    agent, window = rich_window; opened=[]
    monkeypatch.setattr(message_document.QDesktopServices,'openUrl',lambda url: opened.append(url.toString()) or True)
    agent._apply_state({'agent':'1) 第一步\n2) 第二步\n\n---\n\nhttps://example.com\n\n'
        '\\[x+1\\]\n\n```mermaid\nnot-a-supported-diagram!!!\n```'})
    web=web_for(window)
    until(lambda: js(web, '!!document.querySelector(".render-note")'))
    assert js(web, 'document.querySelectorAll("ol li").length') == 2
    assert js(web, '!!document.querySelector("hr") && !!document.querySelector(".katex-display")')
    assert js(web, 'document.querySelector("pre code").textContent') == 'not-a-supported-diagram!!!'
    assert opened==[]
    js(web,'(document.querySelector("a").click(),true)')
    until(lambda: opened==['https://example.com/'])


def test_input_gesture_after_reading_returns_to_composer_without_main_focus(rich_window):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import Qt
    agent,window=rich_window
    agent._apply_state({'agent':'## 可选择的回复\n\n**这是一段说明。**'})
    web=web_for(window)
    agent.edit('task','保留草稿')
    web.forceActiveFocus(Qt.MouseFocusReason)
    until(lambda: not agent.owns_input_focus())
    agent.owner._disconnect_event=threading.Event()
    gesture=agent.owner._gesture_bindings.confirm[0]
    assert not agent.filter_gesture(SimpleNamespace(name=gesture),agent.owner._disconnect_event)
    until(agent.owns_input_focus)
    assert QGuiApplication.focusWindow() is window
    assert agent.draft('task')=='保留草稿'
