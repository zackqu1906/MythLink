"""History resolves display identities without rewriting stored application keys."""
import pytest

from proximic_ring.ui.desktop import application_identity
from test_inline_ui import inline_ui


@pytest.fixture
def known_application(monkeypatch):
    calls = []

    def resolve(identity):
        calls.append(identity)
        if identity == 'com.example.editor':
            return dict(label='文本编辑', value=identity, path='/Applications/Editor.app')
        return None

    application_identity._resolved_application.cache_clear()
    monkeypatch.setattr(application_identity, '_native_application', resolve)
    yield calls
    application_identity._resolved_application.cache_clear()


def test_native_display_metadata_is_cached_and_legacy_names_hide_unresolved_ids(known_application):
    present = application_identity.application_presentation
    assert present('com.example.editor') == dict(
        name='文本编辑', bundle='com.example.editor', path='/Applications/Editor.app')
    present('com.example.editor')['name'] = 'must not mutate cached metadata'
    assert present('com.example.editor')['name'] == '文本编辑'
    assert known_application == ['com.example.editor']
    for identity, label in [('', '未记录应用'), ('unknown.missing.appid', '未识别应用'),
                            ('微信', '微信'), ('/Applications/Old Editor.app', 'Old Editor'),
                            (r'C:\Program Files\Editor\Editor.exe', 'Editor')]:
        assert present(identity) == dict(name=label, bundle='', path='')


def test_source_mythlink_uses_its_name_and_real_icon_without_installed_bundle(known_application):
    from proximic_ring.ui.desktop.application_icons import native_application_icon
    assert application_identity.application_presentation('com.proximic.voice') == dict(
        name='Mythlink', bundle='com.proximic.voice', path='')
    assert known_application == []
    icon = native_application_icon('com.proximic.voice', '')
    assert not icon.isNull() and icon.width() > 0


def test_history_uses_saved_context_only_when_target_owner_is_missing(tmp_path):
    from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
    (tmp_path / 'audio.wav').touch()
    record = dict(asr=dict(final_text='测试', context=dict(application='com.example.editor')))
    project = ModificationDatasetCollector._history_entry
    assert project(tmp_path, record)['application'] == 'com.example.editor'
    record['target'] = dict(application='com.proximic.voice')
    assert project(tmp_path, record)['application'] == 'com.proximic.voice'
    record['target'] = {}
    record['asr']['context'] = {}
    assert project(tmp_path, record)['application'] == ''
    record['asr']['context'] = None
    assert project(tmp_path, record)['application'] == ''


def test_filters_use_display_names_but_preserve_stored_ids(known_application):
    from proximic_ring.ui.controller import _VoiceHistoryListModel
    from proximic_ring.ui.voice.voice_history_filter import VoiceHistoryFilter
    rows = [dict(interactionId='one', application='com.example.editor', text='开会'),
            dict(interactionId='two', application='unknown.missing.appid', text='邮件')]
    source = _VoiceHistoryListModel(rows)
    proxy = VoiceHistoryFilter(source)
    assert {'value': 'com.example.editor', 'label': '文本编辑'} in proxy.applicationOptions
    assert {'value': 'unknown.missing.appid', 'label': '未识别应用'} in proxy.applicationOptions
    for term in ['文本编辑', 'com.example.editor']:
        proxy.searchText = term
        assert proxy.count == 1
        assert proxy.data(proxy.index(0, 0), proxy.ENTRY_ROLE) == rows[0]
    proxy.searchText = ''
    proxy.applicationFilter = 'com.example.editor'
    assert proxy.count == 1
    assert source.data(source.index(0, 0), source.ENTRY_ROLE)['application'] == 'com.example.editor'


@pytest.mark.parametrize('size', [(1200, 780), (940, 700)])
def test_home_and_history_show_application_icon_and_name_in_figma_order(request, monkeypatch, known_application, size):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from proximic_ring.ui.desktop import application_icons
    from test_ui_shell import visual_child
    from test_ui_voice_page import seed_history

    icon_image = QImage(128, 128, QImage.Format_ARGB32)
    icon_image.fill(0xff456789)
    monkeypatch.setattr(application_icons, 'native_application_icon', lambda *_: icon_image)
    controller, bridge, _, root, event = request.getfixturevalue('inline_ui')
    event(phase='idle', ready=True)
    root.resize(*size); root.show()
    rows = [dict(row, application='com.example.editor') for row in seed_history(controller)]
    controller._voice_history_entries = rows
    controller.voiceHistoryChanged.emit()
    before = list(bridge.messages)

    for page, list_name, row_name in [(0, 'homeVoiceHistoryList', 'homeVoiceHistoryEntry0'),
                                     (4, 'voiceHistoryList', 'voiceHistoryEntry')]:
        root.setProperty('currentPage', page); QTest.qWait(200)
        entry = visual_child(root.findChild(QObject, list_name), row_name)
        time = visual_child(entry, 'voiceHistoryMetadata')
        badge = visual_child(entry, 'voiceHistoryModeLabel')
        group = visual_child(entry, 'voiceHistoryApplicationGroup')
        icon = visual_child(entry, 'voiceHistoryApplicationIcon')
        name = visual_child(entry, 'voiceHistoryApplication')
        assert name.property('text') == '文本编辑'
        assert icon.property('ready') and icon.width() == icon.height() == 22
        assert badge.mapToItem(entry, QPointF()).x() == pytest.approx(time.mapToItem(entry, QPointF(time.width(), 0)).x() + 12)
        assert group.mapToItem(entry, QPointF()).x() == pytest.approx(badge.mapToItem(entry, QPointF(badge.width(), 0)).x() + 12)
        assert name.mapToItem(group, QPointF()).x() == pytest.approx(icon.width() + 8)
        assert entry.property('entry')['application'] == 'com.example.editor'
    assert bridge.messages == before
