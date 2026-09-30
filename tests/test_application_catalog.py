"""Installed application discovery must include unopened apps without launching them."""
from pathlib import Path
import plistlib
import subprocess

from proximic_ring.application_catalog import application_metadata, installed_applications, spotlight_application_paths


def application(path, bundle, **extra):
    contents = path / 'Contents'
    contents.mkdir(parents=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps(dict(
        CFBundleIdentifier=bundle, CFBundleName=path.stem, CFBundlePackageType='APPL', **extra)))
    return path


def test_unopened_apps_nested_folders_and_indexed_locations_are_searchable(tmp_path):
    root = tmp_path / 'Applications'
    editor = application(root / 'Editor.app', 'test.editor')
    terminal = application(root / 'Utilities/Terminal.app', 'test.terminal')
    elsewhere = application(tmp_path / 'Elsewhere/Extra.app', 'test.extra')
    helper = application(editor / 'Contents/Helpers/Helper.app', 'test.helper')
    background = application(root / 'Background.app', 'test.background', LSBackgroundOnly=True)
    duplicate = application(tmp_path / 'Old Editor.app', 'test.editor')
    result = installed_applications([dict(value='test.editor', label='Running Editor', path=str(editor))],
                                    roots=[root], indexed=[elsewhere, helper, background, duplicate])
    apps = result['candidates']
    assert {app['value'] for app in apps} == {'test.editor', 'test.terminal', 'test.extra'}
    assert apps[0]['value'] == 'test.editor' and apps[0]['running']
    assert apps[0]['path'] == str(editor)
    assert all(not app['running'] for app in apps[1:])
    assert not result['partial']
    assert any('test.terminal' in app['search'] for app in apps)
    assert application_metadata(helper) is None


def test_broken_app_is_skipped_and_spotlight_failure_keeps_folder_results(tmp_path, monkeypatch):
    import proximic_ring.application_catalog as module
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(module.subprocess, 'run', timeout)
    assert spotlight_application_paths() == ([], True)
    app = application(tmp_path / 'Good.app', 'test.good')
    (tmp_path / 'Broken.app').mkdir()
    result = installed_applications([], roots=[tmp_path])
    assert result['partial'] and [item['value'] for item in result['candidates']] == ['test.good']
    assert result['candidates'][0]['path'] == str(app)


def test_presentation_capability_is_discovered_for_editors_not_attachment_viewers(tmp_path):
    app = application(tmp_path / 'Slides.app', 'test.slides', CFBundleDocumentTypes=[{
        'CFBundleTypeRole': 'Editor', 'CFBundleTypeExtensions': ['PPTX']}])
    chat = application(tmp_path / 'Chat.app', 'test.chat', CFBundleDocumentTypes=[{
        'CFBundleTypeRole': 'Viewer', 'CFBundleTypeExtensions': ['pptx']}])
    proprietary = application(tmp_path / 'Deck.app', 'test.deck', CFBundleDocumentTypes=[{
        'CFBundleTypeRole': 'Editor', 'CFBundleTypeName': 'Deck Presentation', 'CFBundleTypeExtensions': ['deck']}])
    result = installed_applications([dict(value='test.slides', label='Slides', path=str(app))], roots=[tmp_path], indexed=[])
    found = {item['value']: item for item in result['candidates']}
    assert found['test.slides']['presentationProfile'] == 'generic' and found['test.slides']['running']
    assert found['test.deck']['presentationProfile'] == 'generic'
    assert found['test.chat']['presentationProfile'] == ''
    assert application_metadata(chat)['presentationProfile'] == ''
    assert application_metadata(proprietary)['presentationProfile'] == 'generic'


def test_known_presentation_apps_have_capability_without_document_type_metadata(tmp_path):
    for bundle, name, profile in [('com.kingsoft.wpsoffice.mac', 'WPS', 'wps'),
                                  ('com.apple.iWork.Keynote', 'Keynote', 'keynote'),
                                  ('org.libreoffice.script', 'LibreOffice', 'libreoffice')]:
        path = application(tmp_path / (name + '.app'), bundle)
        assert application_metadata(path)['presentationProfile'] == profile


def test_running_only_app_preserves_presentation_capability(tmp_path):
    app = application(tmp_path / 'External/Slides.app', 'test.external', CFBundleDocumentTypes=[{
        'CFBundleTypeRole': 'Editor', 'LSItemContentTypes': ['org.oasis-open.opendocument.presentation']}])
    result = installed_applications([dict(value='test.external', label='Slides', path=str(app))], roots=[], indexed=[])
    assert result['candidates'][0]['presentationProfile'] == 'generic'


def test_application_icon_provider_uses_native_path_and_caches_original(monkeypatch):
    from urllib.parse import quote
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage
    from proximic_ring.ui import application_icons as module
    calls = []
    source = QImage(128, 128, QImage.Format_ARGB32)
    source.fill(0xff123456)
    monkeypatch.setattr(module, 'native_application_icon', lambda bundle, path: calls.append((bundle, path)) or source)
    provider = module.ApplicationIcons()
    path = '/Applications/My Editor.app'
    size = QSize()
    first = provider.requestImage('test.editor/' + quote(path, safe=''), size, QSize(44, 44))
    second = provider.requestImage('test.editor/' + quote(path, safe=''), size, QSize(46, 46))
    assert first == source and second == source and size == QSize(128, 128)
    assert calls == [('test.editor', path)]
