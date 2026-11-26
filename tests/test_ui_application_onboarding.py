"""The opt-in card renders without asking macOS to activate the app."""
from pathlib import Path
import os

import pytest
from test_inline_ui import inline_ui
from test_ui_shell import visual_child


@pytest.mark.parametrize('bundle,label,profiles', [
    ('com.apple.Music','音乐',{'music':'music'}),
    ('com.microsoft.Powerpoint','Microsoft PowerPoint',{'presentation':'powerpoint','pdf':'generic'}),
    ('com.apple.Safari','Safari',{'video':'generic','pdf':'generic','image':'generic','music':'generic'}),
    ('test.unknown','示例应用的长名称' * 8,{}),
])
def test_opt_in_card_layout_and_confirm_preserves_voice(inline_ui,monkeypatch,tmp_path,bundle,label,profiles):
    from PySide6.QtCore import QMetaObject, QPointF, Qt
    from PySide6.QtGui import QWindow
    from PySide6.QtTest import QTest
    from proximic_ring.ui.desktop.application_onboarding import ApplicationOnboarding
    import proximic_ring.ui.desktop.application_onboarding_overlay as module
    c,bridge,*_=inline_ui
    catalog=c.appGestures.catalog
    monkeypatch.setattr(catalog,'_request',lambda *args:None)
    notice = inline_ui[3].findChild(QWindow, 'appGestureNotice')
    target=dict(value=bundle,pid=123,path='/System/Applications/TextEdit.app')
    onboarding=ApplicationOnboarding(catalog,foreground=lambda:target)
    overlay=module.ApplicationOnboardingOverlay(onboarding)
    warnings=[]
    overlay.window.engine().warnings.connect(lambda items:warnings.extend(str(x) for x in items))
    before,messages=c.gestureBindings,list(bridge.messages)
    try:
        onboarding.poll()
        onboarding._ready(onboarding._epoch,target,dict(value=bundle,label=label,path=target['path'],sceneProfiles=profiles))
        QTest.qWait(150)
        root=overlay.window.rootObject()
        assert overlay.window.isVisible() and overlay.window.flags() & Qt.WindowDoesNotAcceptFocus
        area = overlay.window.screen().availableGeometry()
        assert overlay.window.x() + overlay.window.width() == area.x() + area.width() - 24
        assert overlay.window.y() == area.y() + 24
        assert overlay.window.height() <= 60 and not catalog.apps
        question = visual_child(root, 'applicationSuggestionQuestion')
        assert question.property('text') == f'为「{label}」配置手势？'
        assert visual_child(root, 'dismissApplicationSuggestion').property('text') == '暂不'
        c.appGestures.notify('当前应用未配置手势', duration=100)
        assert notice.isVisible() and notice.flags() & Qt.WindowTransparentForInput
        assert not overlay.window.isVisible() and onboarding.visible
        assert notice.x() + notice.width() == overlay.window.x() + overlay.window.width()
        assert notice.y() == overlay.window.y()
        QTest.qWait(160)
        assert not notice.isVisible() and overlay.window.isVisible()
        confirm=visual_child(root,'acceptApplicationSuggestion')
        assert confirm is not None and confirm.property('visible')
        assert confirm.width() >= confirm.implicitWidth()
        assert 0 < confirm.mapToScene(QPointF()).x() + confirm.width() <= overlay.window.width()
        assert confirm.mapToScene(QPointF()).y()+confirm.height() <= overlay.window.height()
        shots=Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR',str(tmp_path)))
        shots.mkdir(parents=True,exist_ok=True)
        assert overlay.window.grabWindow().save(str(shots/(bundle+'-suggestion.png')))
        QMetaObject.invokeMethod(confirm,'click')
        QTest.qWait(50)
        assert not onboarding.visible and not overlay.window.isVisible() and catalog._is_added(bundle)
        assert not warnings
        assert c.gestureBindings==before and bridge.messages==messages
    finally:
        overlay.close()
