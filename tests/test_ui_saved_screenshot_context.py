"""The migrated screenshot hook, history display and native edit submission."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QFont, QImage, QPainter
from PySide6.QtTest import QTest

from proximic_ring.text_processing.model import ROUTED_EDIT_BOUNDARY
from test_inline_ui import inline_ui, _recording_test_begin_edit
from test_routed_edit import settings


def crop():
    image = QImage(800, 300, QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    painter.setPen(Qt.black)
    painter.setFont(QFont('Arial', 28))
    painter.drawText(40, 80, 'Saved screenshot context')
    painter.drawText(40, 155, 'Meeting Tuesday at 10:00.')
    painter.end()
    return image


@pytest.mark.parametrize('inline_ui', [{
    'input/routingMode': 'manual_routed_edit',
    'input/routedEditVariant': ROUTED_EDIT_BOUNDARY,
    'input/ocrContextEnabled': False,
}], indirect=True)
def test_saved_crop_display_and_request_are_independent_of_live_ocr(inline_ui, tmp_path, monkeypatch):
    owner, _, requests, root, event = inline_ui
    capture = owner.ringGestures.screenshot
    capture.setDirectory(str(tmp_path / 'screenshots'))
    capture.saved_context._ocr = lambda png: '项目资料\n周二 10:00 开会。\n下次的时间是'
    capture.saved_context._processor = SimpleNamespace(_request_chat=lambda *a, **kw: '项目资料\n周二 10:00 开会。')
    monkeypatch.setattr(owner, '_llm_settings', settings)
    try:
        capture.save_crop(crop())
        value = capture.saved_context.snapshot().result(3)
        QTest.qWait(100)
        assert value['text'] == '项目资料\n周二 10:00 开会。'
        assert capture.contextRecord['imageUrl'].endswith('.png')
        root.setProperty('currentPage', 4)
        root.show()
        for width, height in [(1200, 780), (940, 560)]:
            root.resize(width, height)
            QTest.qWait(120)
            card = root.findChild(QObject, 'savedScreenshotContextCard')
            text = root.findChild(QObject, 'savedScreenshotCleanedText')
            image = root.findChild(QObject, 'savedScreenshotImage')
            assert card.property('visible') and card.property('width') > 300
            assert text.property('text') == value['text'] and text.property('width') > 150
            assert image.property('source').toString() == capture.contextRecord['imageUrl']
            assert root.grabWindow().save(f'/private/tmp/saved-screenshot-context-{width}.png')
        owner._prefetch_voice_capture(None, inline=True, verified=True)
        # A copy made while speaking must be visible at edit submission time.
        capture.saveCopiedText('录音期间新复制的文本')
        first = _recording_test_begin_edit(owner, event, requests)
        assert first.saved_context.result()['text'] == '录音期间新复制的文本'
        assert first.screen_context == {}  # Live OCR toggle does not disable saved context.
        capture.saved_context._ocr = lambda png: '另一张截图'
        capture.saved_context._processor = SimpleNamespace(_request_chat=lambda *a, **kw: kw['user_content'])
        capture.save_crop(crop())
        assert capture.saved_context.snapshot().result(3)['text'] == '另一张截图'
        assert first.saved_context.result()['text'] == '录音期间新复制的文本'
    finally:
        capture.close()


def test_clipboard_updates_save_text_and_clear_image_in_history(inline_ui, tmp_path, monkeypatch):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import QMimeData
    from proximic_ring.ui.shared.clipboard import INTERNAL_CLIPBOARD_TYPE
    owner, _, _, root, _ = inline_ui
    capture = owner.ringGestures.screenshot
    capture.saved_context._ocr = lambda png: '截图文字'
    capture.saved_context._processor = SimpleNamespace(_request_chat=lambda *a, **kw: kw['user_content'])
    monkeypatch.setattr(owner, '_llm_settings', settings)
    capture.setDirectory(str(tmp_path / 'images'))
    capture.save_crop(crop())
    capture.saved_context.snapshot().result(3)
    clipboard = QGuiApplication.clipboard()
    # Copy buttons set clipboard text; no keyboard shortcut is involved.
    from proximic_ring.agent_workflow.ui.message_document import MessageDocument
    MessageDocument().copy('  Agent 回复\n第二行。\n')
    QTest.qWait(30)
    value = capture.saved_context.snapshot().result()
    assert value['source'] == 'copied_text' and value['text'] == '  Agent 回复\n第二行。\n'
    assert capture.contextRecord['imageUrl'] == ''
    root.setProperty('currentPage', 4)
    root.resize(940, 560); root.show(); QTest.qWait(100)
    assert not root.findChild(QObject, 'savedScreenshotImage').property('visible')
    assert root.findChild(QObject, 'savedScreenshotCleanedText').property('text') == value['text']
    assert root.grabWindow().save('/private/tmp/saved-context-copied-text.png')
    # Re-copying the same text is still a new explicit clipboard update.
    clipboard.setText(value['text'])
    assert capture.saved_context.snapshot().result()['capture_id'] != value['capture_id']
    before = capture.saved_context.snapshot()
    internal = QMimeData(); internal.setText('自动粘贴的编辑结果')
    internal.setData(INTERNAL_CLIPBOARD_TYPE, b'1')
    clipboard.setMimeData(internal)
    assert capture.saved_context.snapshot() is before
    clipboard.clear()
    clipboard.setImage(crop())
    assert capture.saved_context.snapshot() is before
    clipboard.clear()


def test_clipboard_watcher_polls_background_native_changes(inline_ui):
    NSPasteboardTypeString = pytest.importorskip('AppKit').NSPasteboardTypeString
    from proximic_ring.ui.shared.clipboard import INTERNAL_CLIPBOARD_TYPE
    owner, *_ = inline_ui
    watcher = owner.ringGestures.screenshot._clipboard_watcher
    count, content = [10], {NSPasteboardTypeString: '初始剪贴板'}
    watcher._pasteboard = SimpleNamespace(changeCount=lambda: count[0], stringForType_=content.get)
    watcher._revision = count[0]
    watcher.check()
    assert owner.ringGestures.screenshot.saved_context.snapshot().result() == {}
    count[0] += 1
    content[NSPasteboardTypeString] = '后台应用复制按钮的文本'
    watcher.check()
    first = owner.ringGestures.screenshot.saved_context.snapshot()
    assert first.result()['text'] == '后台应用复制按钮的文本'
    watcher.check()
    assert owner.ringGestures.screenshot.saved_context.snapshot() is first
    count[0] += 1
    content[INTERNAL_CLIPBOARD_TYPE] = '1'
    content[NSPasteboardTypeString] = '内部粘贴'
    watcher.check()
    assert owner.ringGestures.screenshot.saved_context.snapshot() is first
