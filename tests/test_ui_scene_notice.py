"""All mode notices render at upper-right without accepting focus or clicks."""
from pathlib import Path
from types import SimpleNamespace
import os
import pytest
from test_inline_ui import inline_ui


@pytest.mark.parametrize('scene,label',[('presentation','放映'),('pdf','PDF 阅读'),('video','视频播放'),('image','图片预览'),('music','音乐播放')])
def test_notice_is_upper_right_click_through_and_expires(inline_ui,monkeypatch,tmp_path,scene,label):
    from PySide6.QtCore import QObject,Qt
    from PySide6.QtTest import QTest
    from proximic_ring.ui.scene_notice_controller import SceneNoticeController
    import proximic_ring.ui.scene_notice_overlay as module
    c,*_=inline_ui
    catalog=c.appGestures.catalog
    monkeypatch.setattr(catalog,'configured_scenes',lambda:{'test.app':[scene]})
    catalog._apps['test.app']={'label':'Microsoft PowerPoint' if scene=='presentation' else '示例应用'}
    monkeypatch.setattr(module,'foreground_window_bounds',lambda:None)
    obj=SceneNoticeController(c.appGestures,channel=SimpleNamespace(close=lambda:None))
    overlay=module.SceneNoticeOverlay(obj)
    warnings=[]
    overlay.window.engine().warnings.connect(lambda items:warnings.extend(str(x) for x in items))
    before,messages=c.gestureBindings,list(c.bridge.messages) if hasattr(c,'bridge') else []
    try:
        front=dict(value='test.app',pid=12)
        value=dict(bundle='test.app',pid=12,scene=scene,blocked=False,input_context='nontext')
        obj._observe(value,front);obj._observe(value,front)
        QTest.qWait(100)
        window=overlay.window;root=window.rootObject()
        assert window.isVisible()
        assert window.flags() & Qt.WindowDoesNotAcceptFocus
        assert window.flags() & Qt.WindowTransparentForInput
        area=window.screen().availableGeometry()
        assert window.x()==area.x()+area.width()-window.width()-24
        assert window.y()==area.y()+24
        assert root.findChild(QObject,'sceneNoticeTitle').property('text')=='已进入'+label+'模式'
        shots=Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR',str(tmp_path)));shots.mkdir(parents=True,exist_ok=True)
        assert window.grabWindow().save(str(shots/(scene+'-entry.png')))
        obj._hide_timer.start(30);QTest.qWait(60)
        assert not obj.visible and not window.isVisible() and not warnings
        assert c.gestureBindings==before
    finally:overlay.close()
