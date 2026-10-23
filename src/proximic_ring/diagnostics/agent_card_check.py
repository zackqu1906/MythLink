"""Opt-in source/frozen UI acceptance using real Qt mouse events and Chromium.

No Codex task or device session is started. The command uses temporary settings,
data and an isolated IME socket, and writes its evidence to --output-dir.
"""
from pathlib import Path
import time


def until(predicate, timeout=15):
    from PySide6.QtTest import QTest
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('UI acceptance condition timed out')
        QTest.qWait(25)


def named(root, name):
    from PySide6.QtCore import QObject
    item = root.findChild(QObject, name)
    assert item is not None, name
    return item


def center(item):
    from PySide6.QtCore import QPointF
    return item.mapToScene(QPointF(item.width()/2, item.height()/2))


def javascript(web, expression):
    """Read the actual rendered document without exposing a production slot."""
    import json
    from PySide6.QtCore import QMetaObject, Q_ARG
    token = str(time.monotonic_ns())
    code = 'document.title=' + json.dumps(token) + '+JSON.stringify((' + expression + '))'
    assert QMetaObject.invokeMethod(web, 'runJavaScript', Q_ARG(str, code))
    until(lambda: web.property('title').startswith(token))
    return json.loads(web.property('title')[len(token):])


def drag(window, point, delta, state):
    from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    start = QPointF(window.mapToGlobal(point.toPoint()))
    def send(kind, local, global_pos, button, buttons):
        QCoreApplication.sendEvent(window, QMouseEvent(kind, local, global_pos,
                                                      button, buttons, Qt.NoModifier))
    send(QEvent.MouseButtonPress, point, start, Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseMove, point+delta, start+delta, Qt.NoButton, Qt.LeftButton)
    active = state.active
    send(QEvent.MouseButtonRelease, point, start+delta, Qt.LeftButton, Qt.NoButton)
    assert not state.active
    return active


def exercise_cards(overlay, *, cycles=12, output=None):
    from PySide6.QtCore import QObject, QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    agent = overlay.controller
    agent._memory_timer.stop()
    agent._visible = agent._focused = True
    overlay.focus('task')
    window = overlay.windows['task']
    root = window.rootObject()
    until(lambda: not root.property('compact'))
    for index in range(cycles):
        agent._apply_state({'turnId': f'ui-check-{index}', 'agent':
            f'# 回复 {index}\n\n**可选择的正文**\n\n' + '测试文本和滚动。\n\n' * 24})
        until(lambda: window.message_document.ready
              and root.findChild(QObject, 'agentRichMessageWeb') is not None)
        web = named(root, 'agentRichMessageWeb')
        until(lambda: web.property('loadProgress') == 100 and not web.property('loading')
              and window.message_document.ready)
        until(lambda: javascript(web, 'document.querySelector("h1")?.textContent') == f'回复 {index}')
        QTest.qWait(80)
        for name in ('agentLinkButton', 'agentNewTaskButton', 'agentSendButton',
                     'agentEditor', 'agentMessageViewport'):
            assert overlay.drag.hit_test(window, center(named(root, name))) == 0, name
        assert overlay.drag.hit_test(window, QPointF(8, 44)) == 1
        # Every press goes through the real installed window event filter.
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(120, 22))
        message = named(root, 'agentMessageViewport')
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center(message).toPoint())
        offset = QPoint(overlay.offset)
        assert not drag(window, center(message), QPointF(-30, 0), overlay.drag)
        assert overlay.offset == offset
        before = window.position()
        assert drag(window, QPointF(8, 44), QPointF(-24, -3), overlay.drag)
        assert window.position() != before
        agent.edit('task', '保留输入内容')
        editor = named(root, 'agentEditor')
        assert not drag(window, center(editor), QPointF(-30, 0), overlay.drag)
        assert agent.draft('task') == '保留输入内容'
        if output and index == 0:
            named(root, 'agentMessageViewport').property('contentItem').setProperty('contentY', 0)
            javascript(web, '(window.scrollTo(0,0),true)')
            QTest.qWait(300)
            assert window.grabWindow().save(str(output/'rich-card.png'))
        agent._apply_state({'agent': '纯文本回复'})
        until(lambda: root.findChild(QObject, 'agentRichMessageWeb') is None)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(120, 22))

    # Retain real Chromium while closing both expanded and compact cards.
    for compact in (False, True):
        agent._visible = agent._focused = True
        agent._apply_state({'agent': '# 已完成\n\n**仍在显示富文本**',
                            'taskOutcome': 'completed', 'status': '本轮结束'})
        overlay.focus('task')
        until(lambda: window.message_document.ready)
        if compact:
            overlay._release_focus()
            until(lambda: root.property('compact'))
        QTest.qWait(250)
        if compact:
            assert drag(window, QPointF(150, 35), QPointF(-24, -3), overlay.drag)
            assert root.property('compact'), 'drag must not expand the card on release'
        close = named(root, 'agentCompactCloseButton' if compact else 'agentCloseButton')
        point = center(close)
        assert overlay.drag.hit_test(window, point) == 2
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        until(lambda: not agent.visible and not window.isVisible())
        assert overlay.drag.press is None and not overlay.drag.active

    # A late press during retirement must neither traverse content nor drag it.
    agent._visible = agent._focused = True
    overlay.focus('task')
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(8, 44))
    overlay._retire('task')
    assert overlay.drag.press is None
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(8, 44))
    assert overlay.drag.press is None
    until(lambda: overlay.windows.get('task') is not window)
    return {'rich_load_unload_cycles': cycles, 'expanded_and_compact_close': True,
            'background_drag': True, 'content_and_editor_excluded': True,
            'retiring_card_ignored': True}


def exercise_main_window(window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    window.resize(1200, 780)
    window.show()
    QTest.qWait(200)
    area = named(window, 'mainWindowDragArea')
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(450, 10))
    assert area.property('pressed')
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(450, 10))
    assert not area.property('pressed')
    window.hide()
    QTest.qWait(150)
    return {'main_background_press_and_release': True}


def main(argv=None):
    import argparse
    import json
    import os
    import tempfile
    import traceback
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--cycles', default=12, type=int)
    args = parser.parse_args(argv)
    if not 1 <= args.cycles <= 100: parser.error('--cycles must be between 1 and 100')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mythlink-ui-check-') as isolated:
        os.environ['PROXIMIC_DATA_HOME'] = isolated
        os.environ['PROXIMIC_STARTUP_PROBE'] = '1'
        os.environ['PROXIMIC_IME_SOCKET'] = str(Path(isolated)/'bridge.sock')
        from proximic_ring.runtime_paths import configure_runtime_environment
        configure_runtime_environment()
        from .runtime_diagnostics import configure_diagnostics
        diagnostics = configure_diagnostics(tee=True)
        from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QUrl
        from PySide6.QtWidgets import QApplication
        from PySide6.QtQml import QQmlApplicationEngine
        from proximic_ring.agent_workflow.ui.message_document import initialize_web_engine
        initialize_web_engine()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, isolated)
        app = QApplication(['Mythlink UI acceptance'])
        app.setQuitOnLastWindowClosed(False)
        from proximic_ring.ui.controller import AppController
        from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
        controller = AppController()
        controller.agent._memory_timer.stop()
        controller._text_processing_worker.close(wait=True)
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('appController', controller)
        from proximic_ring.ui.desktop.application_icons import install_application_icons
        install_application_icons(engine)
        from proximic_ring.ui import main as ui_main
        engine.load(QUrl.fromLocalFile(str(Path(ui_main.__file__).parent/'qml/Main.qml')))
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        window.hide()
        overlay = None
        report = {'passed': False, 'platform': app.platformName()}
        try:
            report.update(exercise_main_window(window))
            overlay = AgentOverlay(controller.agent, controller)
            overlay.reduce_motion = True
            report.update(exercise_cards(overlay, cycles=args.cycles, output=output))
            report['passed'] = True
        except Exception:
            report['error'] = traceback.format_exc()
        finally:
            if overlay: overlay.close()
            controller.agent.close()
            controller.appGestures.close()
            controller.requestQuit()
            window.hide()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            diagnostics.flush()
            import shutil
            shutil.copyfile(diagnostics.writer.path, output/'diagnostic.log')
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
