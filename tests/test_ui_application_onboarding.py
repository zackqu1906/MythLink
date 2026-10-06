"""The opt-in card renders without asking macOS to activate the app."""
from pathlib import Path
import os

import pytest
from test_inline_ui import inline_ui
from test_ui_shell import visual_child


@pytest.mark.parametrize('bundle,profiles', [
    ('com.apple.Music',{'music':'music'}),
    ('com.microsoft.Powerpoint',{'presentation':'powerpoint','pdf':'generic'}),
    ('com.apple.Safari',{'video':'generic','pdf':'generic','image':'generic','music':'generic'}),
    ('test.unknown',{}),
])
def test_opt_in_card_layout_and_confirm_preserves_voice(inline_ui,monkeypatch,tmp_path,bundle,profiles):
    from PySide6.QtCore import QMetaObject, QPointF, Qt
    from PySide6.QtTest import QTest
    from proximic_ring.ui.application_onboarding import ApplicationOnboarding
    import proximic_ring.ui.application_onboarding_overlay as module
    c,bridge,*_=inline_ui
    catalog=c.appGestures.catalog
    monkeypatch.setattr(catalog,'_request',lambda *args:None)
    monkeypatch.setattr(module,'foreground_window_bounds',lambda:None)
    target=dict(value=bundle,pid=123,path='/System/Applications/TextEdit.app')
    onboarding=ApplicationOnboarding(catalog,foreground=lambda:target)
    overlay=module.ApplicationOnboardingOverlay(onboarding)
    warnings=[]
    overlay.window.engine().warnings.connect(lambda items:warnings.extend(str(x) for x in items))
    before,messages=c.gestureBindings,list(bridge.messages)
    try:
        onboarding.poll()
        onboarding._ready(onboarding._epoch,target,dict(value=bundle,label='示例应用',path=target['path'],sceneProfiles=profiles))
        QTest.qWait(150)
        root=overlay.window.rootObject()
        assert overlay.window.isVisible() and overlay.window.flags() & Qt.WindowDoesNotAcceptFocus
        assert overlay.window.height()<650 and not catalog.apps
        confirm=visual_child(root,'acceptApplicationSuggestion')
        assert confirm is not None and confirm.property('visible')
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
