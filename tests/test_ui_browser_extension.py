"""Setup UI, permission states and real gesture action routing."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication
import pytest

from proximic_ring.ui.desktop.browser_extension_controller import BrowserExtensionController
from test_browser_bridge import snapshot
from test_app_gestures import route
from test_gesture_scenes import presentation
from test_application_menus import configure
from test_ring_gestures import request
from test_inline_ui import inline_ui


@pytest.fixture(autouse=True)
def no_proximity_helper(monkeypatch):
    from proximic_ring.ui.proximity.proximity_controller import ProximityController
    monkeypatch.setattr(ProximityController, 'refresh', lambda self: None)
    from proximic_ring.ui.shared.clipboard import QtClipboardBridge
    # Keep automated offscreen checks out of the user's system clipboard;
    # the signed app's real pasteboard path is verified in Chrome separately.
    monkeypatch.setattr(QtClipboardBridge, 'set_text', lambda self, value: QGuiApplication.clipboard().setText(value))


def pump_until(predicate, timeout=1):
    import time
    from PySide6.QtCore import QCoreApplication
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.001)
    assert predicate()


@pytest.fixture
def bridge(monkeypatch):
    import proximic_ring.ui.desktop.browser_extension_controller as module
    monkeypatch.setattr(module, "chrome_extension_state", lambda: "not_installed")
    monkeypatch.setattr(module, "safari_extension_state", lambda: "unknown")
    app=QApplication.instance() or QApplication([])
    controller=BrowserExtensionController()
    yield controller
    controller.close()


def test_settings_component_loads_and_distinguishes_permission_from_protected_page(bridge):
    engine=QQmlEngine()
    path=Path(__file__).parents[1]/'src/proximic_ring/ui/qml/BrowserExtensionSettings.qml'
    component=QQmlComponent(engine,QUrl.fromLocalFile(str(path)))
    item=component.createWithInitialProperties({'bridge':bridge})
    assert item is not None, [e.toString() for e in component.errors()]
    assert bridge.chromeStatus=='未连接 · 安装状态暂无法确认'
    bridge._states={'chrome':{'connected':True,'authorized':False,'reason':'browser_permission_required'}}
    assert bridge.chromeStatus=='已连接 · 待网站授权'
    bridge._states['chrome']['reason']='browser_page_unavailable'
    assert bridge.chromeStatus=='已连接 · 当前页不可访问'
    item.deleteLater()


@pytest.mark.parametrize('size',[(1200,780),(940,700)])
def test_connection_color_and_long_status_keep_browser_buttons_inside_settings(inline_ui,tmp_path,size):
    from PySide6.QtCore import QObject,QMetaObject,QPointF
    from PySide6.QtTest import QTest
    owner,_,_,root,_=inline_ui;bridge=owner.browserExtensions
    bridge.close()
    bridge._states={'chrome':{'connected':True,'authorized':False,'reason':''}}
    bridge._safari_state='unavailable';bridge.changed.emit()
    root.resize(*size);root.show()
    def item(name):
        value=root.findChild(QObject,name);assert value is not None,name
        return value
    dialog=item('runtimeSettingsDialog');QMetaObject.invokeMethod(dialog,'open');QTest.qWait(80)
    QMetaObject.invokeMethod(item('settingsAgentEntry'),'click');QTest.qWait(60)
    group=item('browserExtensionSettings')
    scroll=item('runtimeSettingsScroll').property('contentItem')
    scroll.setProperty('contentY',group.mapToItem(scroll.property('contentItem'),QPointF()).y()-16)
    QTest.qWait(50)
    chrome=item('chromeExtensionStatus');safari=item('safariExtensionStatus')
    # A connected browser may have no authorized active webpage. Connection
    # status must stay green without granting media-control permission.
    assert bridge.chromeConnected and not bridge.chromeReady
    assert chrome.property('text')=='已连接' and chrome.property('color').name()=='#16703f'
    assert safari.property('text')=='已安装 · 待加载' and safari.property('color').name()=='#76520b'
    for reason in ('browser_permission_required','browser_page_unavailable'):
        bridge._states['chrome']['reason']=reason;bridge.changed.emit()
        assert chrome.property('text')=='已连接' and not bridge.chromeReady
        assert chrome.parentItem().property('statusHint')==bridge.chromeStatus
    def assert_bounds():
        for name in ('connectChromeExtension','connectSafariExtension'):
            button=item(name);point=button.mapToItem(group,QPointF())
            assert group.property('leftPadding')<=point.x()
            assert point.x()+button.width()<=group.width()-group.property('rightPadding')+1
            assert button.mapToScene(QPointF(button.width(),0)).x()<=root.width()
        point=safari.mapToItem(item('connectSafariExtension'),QPointF())
        assert point.x()+safari.width()<0
    assert_bounds()
    import os
    shots=Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR',str(tmp_path)));shots.mkdir(parents=True,exist_ok=True)
    QTest.qWait(200)
    root.grabWindow().save(str(shots/f'browser-settings-{size[0]}.png'))
    # Preserve the original overflow case as a fallback-layout regression even
    # though Safari now uses a short summary and a detailed hover hint.
    safari.parentItem().setProperty('statusText','Safari 未加载 · 请打开扩展设置并检查启用状态及网站授权')
    QTest.qWait(50);assert_bounds()
    assert safari.height()>chrome.height()
    root.grabWindow().save(str(shots/f'browser-long-status-{size[0]}.png'))
    bridge._states={};bridge.changed.emit()
    assert chrome.property('text')=='待确认' and chrome.property('color').name()=='#76520b'
    QMetaObject.invokeMethod(dialog,'close')


def test_chrome_setup_prepares_host_before_opening_browser_and_does_not_claim_installed(bridge,tmp_path,monkeypatch):
    import proximic_ring.ui.desktop.browser_extension_controller as module
    calls=[]
    requested=[]
    bridge.setupRequested.connect(requested.append)
    monkeypatch.setattr(bridge,'start',lambda:None)
    monkeypatch.setattr(module,'install_chrome',lambda:calls.append('host') or tmp_path)
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kw:calls.append(args[0]))
    bridge.connectChrome()
    assert calls[0]=='host' and calls[1][-1]=='chrome://extensions/'
    assert bridge.chromePath==str(tmp_path) and '扩展目录已复制' in bridge.message
    assert bridge.chromeSummary=='未安装'
    assert QGuiApplication.clipboard().text()==str(tmp_path)
    assert requested==['com.google.Chrome']


def test_store_install_opens_store_without_developer_steps_or_clipboard_change(bridge,tmp_path,monkeypatch):
    import proximic_ring.ui.desktop.browser_extension_controller as module
    monkeypatch.setattr(bridge,'start',lambda:None)
    monkeypatch.setattr(module,'install_chrome',lambda:tmp_path)
    monkeypatch.setattr(module,'store_url',lambda:'https://chromewebstore.google.com/detail/'+'a'*32)
    calls=[]
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kw:calls.append(args[0]))
    QGuiApplication.clipboard().setText('keep clipboard')
    bridge.connectChrome()
    assert calls[0][-1].startswith('https://chromewebstore.google.com/detail/')
    assert '添加至 Chrome' in bridge.message and '开发者模式' not in bridge.message
    assert QGuiApplication.clipboard().text()=='keep clipboard'


def test_safari_setup_opens_settings_even_when_state_unavailable(bridge,monkeypatch):
    import proximic_ring.ui.desktop.browser_extension_controller as module
    monkeypatch.setattr(bridge,'start',lambda:None)
    calls=[]; requested=[]
    bridge.setupRequested.connect(requested.append)
    bridge._safari_state='unavailable'
    monkeypatch.setattr(module,'open_safari_settings',lambda:calls.append('opened'))
    bridge.connectSafari()
    pump_until(lambda:not bridge._settings_opening)
    assert calls==['opened'] and requested==['com.apple.Safari']
    assert '已打开 Safari 扩展设置' in bridge.message


@pytest.mark.parametrize('state,expected', [
    ('enabled', '已安装 · 已启用 · 等待 Safari 连接'), ('disabled', '已安装 · 请在 Safari 扩展设置中启用'),
    ('unavailable', '扩展组件已安装 · Safari 暂未加载，请打开扩展设置确认'), ('component_missing', '扩展组件缺失，请使用完整安装包')])
def test_safari_install_enable_and_connection_are_separate_states(bridge,state,expected):
    bridge._apply_safari_state(state)
    assert bridge.safariStatus == expected
    bridge._states = {'safari': {'connected': True, 'reason': 'browser_permission_required'}}
    assert bridge.safariStatus == '已连接 · 待网站授权'


def test_safari_settings_failure_does_not_claim_success(bridge,monkeypatch):
    import proximic_ring.ui.desktop.browser_extension_controller as module
    monkeypatch.setattr(bridge,'start',lambda:None)
    def fail(): raise OSError('missing component')
    monkeypatch.setattr(module,'open_safari_settings',fail)
    requested=[];bridge.setupRequested.connect(requested.append)
    bridge.connectSafari()
    pump_until(lambda:not bridge._settings_opening)
    assert '打开失败' in bridge.message and not requested


def test_install_in_another_profile_keeps_chrome_load_unpacked_entry(bridge,tmp_path,monkeypatch):
    import proximic_ring.ui.desktop.browser_extension_controller as module
    monkeypatch.setattr(bridge,'start',lambda:None)
    monkeypatch.setattr(module,'install_chrome',lambda:tmp_path)
    monkeypatch.setattr(module,'chrome_extension_state',lambda:'installed')
    calls=[]
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kw:calls.append(args[0]))
    QGuiApplication.clipboard().setText('keep clipboard')
    bridge.connectChrome()
    assert bridge.chromeInstalled and not bridge.chromeConnected
    assert bridge.chromeSummary=='已安装 · 待连接'
    assert calls[0][-1]=='chrome://extensions/'
    assert QGuiApplication.clipboard().text()==str(tmp_path)
    assert '第 2 步加载' in bridge.message and '启用或重新加载' in bridge.message


def test_chrome_installed_enabled_connected_and_authorized_are_independent(bridge):
    for state,label in [('installed','已安装 · 待连接'),('enabled','已安装 · 待连接'),
                        ('disabled','已安装 · 待启用'),('not_installed','未安装'),('unknown','待确认')]:
        bridge._apply_chrome_state(state)
        assert bridge.chromeSummary==label
        assert not bridge.chromeReady
    bridge._states={'chrome':{'connected':True,'authorized':False}}
    assert bridge.chromeInstalled and bridge.chromeSummary=='已连接' and not bridge.chromeReady


@pytest.mark.parametrize('bundle',['com.google.Chrome','com.apple.Safari'])
def test_browser_setup_creates_media_scenes_without_selecting_an_editor_or_reading_menus(presentation,bundle,monkeypatch):
    _,service,*_=presentation
    catalog=service.catalog
    selected=catalog._bundle,catalog._scene
    monkeypatch.setattr(catalog,'_request',lambda *a:pytest.fail('setup must not require menu discovery'))
    assert catalog.setupBrowser(bundle)
    assert set(catalog.configured_scenes()[bundle])=={'video','music'}
    assert catalog.for_scene(bundle,'video')['scene:tap'].action_id=='web-video:play'
    assert catalog.for_scene(bundle,'music')['scene:swipe-up'].action_id=='scene-default:music:volume-up'
    assert (catalog._bundle,catalog._scene)==selected


@pytest.mark.parametrize('change',['custom','disabled','cleared','removed'])
def test_browser_reconnect_preserves_user_choices(presentation,change,monkeypatch):
    _,service,*_=presentation
    catalog=service.catalog;bundle='com.google.Chrome'
    monkeypatch.setattr(catalog,'_request',lambda *a:None)
    assert catalog.setupBrowser(bundle)
    catalog.selectApplication(bundle);catalog.selectScene('video')
    if change=='custom':assert catalog.setCustomBinding(bundle,'tap','我的快捷键','Cmd+K')
    elif change=='disabled':assert catalog.disableScene('video')
    elif change=='cleared':assert catalog.clearApplicationBindings(bundle)
    else:assert catalog.removeApplication(bundle)
    before=json.dumps(catalog._apps,sort_keys=True)
    assert not catalog.setupBrowser(bundle)
    assert json.dumps(catalog._apps,sort_keys=True)==before


@pytest.mark.parametrize('scene',['video','music'])
def test_ring_builtin_media_uses_extension_ack_custom_key_stays_a_key(presentation,monkeypatch,scene):
    c,service,_,backend,_,sent,_=presentation
    bundle='com.apple.Safari'
    catalog=configure(service,monkeypatch,bundle)
    catalog.selectScene(scene)
    captured=snapshot(scene=scene)
    backend.target=replace(backend.target,bundle=bundle,scene=scene,input_context='nontext',page_key='opaque-page',browser_target=captured)
    actions=[]
    backend.browser_action=lambda target,action:actions.append((target.browser_target,action))
    assert not request(c,'tap')
    pump_until(lambda: actions and service._browser_actions._active is None)
    assert actions==[(captured,'play')] and sent==[]
    service._last_dispatch=None
    service._last_browser_submit=None
    assert catalog.setCustomBinding(bundle,'tap','自定义空格','Space')
    assert not request(c,'tap')
    pump_until(lambda: bool(sent))
    assert sent==[(bundle,'Space')] and len(actions)==1


@pytest.mark.parametrize('browser', ['chrome', 'safari'])
@pytest.mark.parametrize('size', [(1200, 780), (700, 700)])
def test_setup_guide_precedes_navigation_and_keeps_steps_and_actions_accessible(inline_ui, monkeypatch, tmp_path, browser, size):
    import os
    from PySide6.QtCore import QObject, QMetaObject, QPointF
    from PySide6.QtTest import QTest
    import proximic_ring.ui.desktop.browser_extension_controller as module
    owner, _, _, root, _ = inline_ui
    bridge = owner.browserExtensions
    monkeypatch.setattr(bridge, 'start', lambda: None)
    monkeypatch.setattr(module, 'chrome_extension_state', lambda: 'installed')
    monkeypatch.setattr(module, 'install_chrome', lambda: tmp_path)
    opened = []
    monkeypatch.setattr(module.subprocess, 'run', lambda argv, **kw: opened.append(argv))
    monkeypatch.setattr(module, 'open_safari_settings', lambda: opened.append('safari'))
    bridge._message = 'old message'
    bridge._safari_state = 'unavailable'
    root.resize(*size); root.show()
    def item(name):
        value = root.findChild(QObject, name)
        if value is None:
            # Repeater delegates belong to the visual tree, while the popup
            # itself remains in the QObject tree of the settings page.
            def visual(current):
                if current.objectName() == name:
                    return current
                for child in current.childItems():
                    match = visual(child)
                    if match is not None:
                        return match
            popup = root.findChild(QObject, 'browserExtensionSetup')
            if popup is not None:
                value = visual(popup.property('contentItem'))
        assert value is not None, name
        return value
    def click(name):
        assert QMetaObject.invokeMethod(item(name), 'click')
        QTest.qWait(30)
    QMetaObject.invokeMethod(item('runtimeSettingsDialog'), 'open')
    click('connectChromeExtension' if browser == 'chrome' else 'connectSafariExtension')
    guide = item('browserExtensionSetup')
    pump_until(lambda: guide.property('opened'))
    assert guide.property('opened') and guide.property('browser') == browser
    assert not opened and bridge.message == ''
    instructions = item('browserSetupStep2').property('text')
    assert ('加载已解压' in instructions and '⌘⇧G' in instructions) if browser == 'chrome' else ('勾选' in instructions and '无需选择文件夹' in instructions)
    button = item('browserSetupOpen')
    point = button.mapToScene(QPointF())
    assert 0 <= point.x() and point.x() + button.width() <= root.width()
    assert 0 <= point.y() and point.y() + button.height() <= root.height()
    click('browserSetupOpen')
    if browser == 'chrome':
        assert opened == [['/usr/bin/open', '-b', 'com.google.Chrome', 'chrome://extensions/']]
        assert QGuiApplication.clipboard().text() == str(tmp_path)
    else:
        pump_until(lambda: not bridge.safariSettingsOpening)
        assert opened == ['safari']
        click('browserSetupHelp')
        assert 'Allow unsigned extensions' in item('browserSetupHelpText').property('text')
        assert guide.property('showHelp')
        click('browserSetupHelp')
    # The guide remains available after returning from the browser. A successful
    # navigation alone must not turn the connection indicator green.
    assert guide.property('opened') and not guide.property('connected')
    shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    QTest.qWait(150)
    assert root.grabWindow().save(str(shots / f'browser-guide-{browser}-{size[0]}.png'))
    bridge._states = {browser: {'connected': True, 'authorized': False}}
    bridge.changed.emit()
    assert guide.property('connected') and guide.property('doneText') == '完成'
    click('browserSetupDone')
    assert not guide.property('opened')
    QMetaObject.invokeMethod(item('runtimeSettingsDialog'), 'close')


def test_safari_completion_does_not_replace_a_new_chrome_guide_message(bridge, monkeypatch):
    import threading
    import proximic_ring.ui.desktop.browser_extension_controller as module
    monkeypatch.setattr(bridge, 'start', lambda: None)
    release = threading.Event()
    monkeypatch.setattr(module, 'open_safari_settings', lambda: release.wait(1))
    bridge.connectSafari()
    bridge.beginSetup('chrome')
    bridge.copyChromeExtensionsAddress()
    message = bridge.message
    release.set()
    pump_until(lambda: not bridge.safariSettingsOpening)
    assert bridge.message == message
    assert QGuiApplication.clipboard().text() == 'chrome://extensions/'


def test_clipboard_failure_keeps_folder_fallback_without_claiming_copied(bridge, tmp_path, monkeypatch):
    import proximic_ring.ui.desktop.browser_extension_controller as module
    monkeypatch.setattr(bridge, 'start', lambda: None)
    monkeypatch.setattr(module, 'install_chrome', lambda: tmp_path)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: None)
    def fail(self, value): raise RuntimeError('pasteboard rejected write')
    monkeypatch.setattr(module.QtClipboardBridge, 'set_text', fail)
    bridge.connectChrome()
    assert bridge.chromePath == str(tmp_path)
    assert '复制失败' in bridge.message and '显示扩展文件夹' in bridge.message
    assert '已复制' not in bridge.message
    bridge.copyChromeExtensionsAddress()
    assert '复制失败' in bridge.message and 'chrome://extensions/' in bridge.message
