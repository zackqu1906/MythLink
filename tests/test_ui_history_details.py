"""The expanded production detail panel exposes classification and failure reasons."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl, QPointF
from PySide6.QtGui import QImage, QPainter, QColor, QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from proximic_ring.voice.history_details import history_details
from test_history_details_simple import record, results, routes
from test_ui_shell import visual_child


@pytest.mark.parametrize('width', [540, 940])
@pytest.mark.parametrize('state', ['success', 'invalid', 'timeout', 'writeback'])
def test_expanded_classification_and_failure_layout(tmp_path, width, state):
    saved = record()
    # A synthetic window demonstrates immediate image display without exposing
    # the desktop or requiring a second click inside the expanded details.
    screenshot = QImage(800, 280, QImage.Format_RGB32)
    screenshot.fill(QColor("#F6F7FD"))
    painter = QPainter(screenshot)
    painter.fillRect(0, 0, 180, 280, QColor("#EEF1F7"))
    painter.setPen(QColor("#11151C")); painter.setFont(QFont("PingFang SC", 20))
    painter.drawText(24, 48, "会议安排")
    painter.drawText(220, 92, "对方：周日开会。")
    painter.drawText(220, 225, "草稿：周六开会。")
    painter.end()
    assert screenshot.save(str(tmp_path / 'screen.png'))
    saved['screen_evidence'] = {'screenshot_file': 'screen.png'}
    request = saved['llm']['requests'][0]
    if state == 'success':
        request.update(edit_plan={'tasks': ['modify', 'insert', 'polish', 'format', 'translate', 'compose']})
    elif state == 'invalid':
        request.update(status='failed', error='编辑任务重复', edit_plan=None,
                       raw_output='分类：修改、修改\n正文：周日开会。')
    elif state == 'timeout':
        request.update(status='failed', error='大模型请求超时', edit_plan=None, raw_output='')
    else:
        saved['outcome'] = {'status': 'apply_failed', 'error': '编辑原文或光标已变化，未执行替换'}
    details = history_details(tmp_path, saved)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(str(message) for message in messages))
    engine.rootContext().setContextProperty('detailData', details)
    qml_dir = Path(__file__).resolve().parents[1] / 'src/proximic_ring/ui/qml'
    engine.loadData(b'''
        import QtQuick
        import QtQuick.Controls
        import QtQuick.Layouts
        import "."
        Window {
            width: 940; height: 900; visible: true; color: "white"
            ScrollView {
                id: scroll; anchors.fill: parent; anchors.margins: 24
                contentWidth: availableWidth
                ColumnLayout {
                    width: scroll.availableWidth
                    VoiceHistoryDetails { Layout.fillWidth: true; details: detailData }
                }
            }
        }
    ''', QUrl.fromLocalFile(str(qml_dir / 'HistoryDetailsHarness.qml')))
    assert engine.rootObjects(), warnings
    root = engine.rootObjects()[0]
    assert isinstance(root, QQuickWindow)
    surface = root.contentItem()
    root.setWidth(width)
    QTest.qWait(180)
    try:
        classification = visual_child(surface, 'voiceHistoryClassification_llm_0')
        assert classification.property('visible')
        assert classification.property('text') == '编辑分类：' + routes(details)[0]['classificationText']
        image = visual_child(surface, 'voiceHistoryScreenshot')
        assert image.property('visible') and image.property('paintedWidth') > 0
        point = classification.mapToScene(QPointF())
        assert 0 <= point.x() < point.x() + classification.width() <= width
        if state in ('invalid', 'timeout'):
            error = visual_child(surface, 'voiceHistoryDetail_llm_0_error')
            assert error.property('visible') and error.property('text') == results(details)[0]['errorText']
            assert visual_child(surface, 'voiceHistoryDetail_llm_0') is None
            assert error.mapToScene(QPointF()).y() > point.y() + classification.height()
        if state == 'writeback':
            error = visual_child(surface, 'voiceHistoryDetail_outcomeError')
            assert error.property('visible') and error.property('text') == saved['outcome']['error']
            assert visual_child(surface, 'voiceHistoryDetail_llm_0').property('visible')
        shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
        shots.mkdir(parents=True, exist_ok=True)
        assert root.grabWindow().save(str(shots / f'edit-details-{state}-{width}.png'))
        assert not warnings
    finally:
        root.close()
        engine.deleteLater()
        QTest.qWait(20)
