from test_inline_ui import inline_ui


def test_live_log_reads_all_producers_and_exports_one_file(inline_ui, monkeypatch, tmp_path):
    from PySide6.QtCore import QObject, QMetaObject, QPointF
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog
    controller, _, _, root, _ = inline_ui
    dialog = root.findChild(QObject, 'runtimeLogDialog')
    area = root.findChild(QObject, 'logArea')
    root.show()
    QTest.qWait(100)
    controller._diagnostic_log.record('startup evidence', source='startup')
    controller._append_background_diagnostic('background evidence')
    controller.appGestures._diagnostics.record('trace-ui', 'capture', 'recognition_timeout',
                                              native={'elapsed_ms': 304, 'scanned_nodes': 1084})
    QMetaObject.invokeMethod(dialog, 'open')
    QTest.qWait(250)
    shown = area.property('text')
    assert shown == controller.readDiagnosticLog()
    assert 'startup evidence' in shown and 'background evidence' in shown and '"scanned_nodes":1084' in shown
    # This producer deliberately emits no Qt signal; visible polling still
    # reads it from the same log without touching the hidden TextArea.
    controller._diagnostic_log.record('native error evidence', source='native.stderr', level='ERROR')
    QTest.qWait(600)
    assert 'native error evidence' in area.property('text')
    output = tmp_path / 'shared.log'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(output), ''))
    button = root.findChild(QQuickItem, 'exportDiagnosticLogButton')
    QMetaObject.invokeMethod(button, 'click')
    assert 'native error evidence' in output.read_text() and 'trace-ui' in output.read_text()
    assert root.grabWindow().save('/tmp/proximic-unified-log.png')
    for name in ['exportDiagnosticLogButton', 'openDiagnosticLogDirectoryButton', 'jumpToLatestLogButton']:
        item = root.findChild(QQuickItem, name)
        position = item.mapToScene(QPointF(0, 0))
        assert 0 <= position.x() and position.x() + item.width() <= root.width()
    QMetaObject.invokeMethod(dialog, 'close')
    QTest.qWait(250)
    before = area.property('text')
    controller._append_background_diagnostic('hidden evidence')
    QTest.qWait(600)
    assert area.property('text') == before
