import pytest

from proximic_ring.gestures.gesture_settings import GlobalGestureBindings
from proximic_ring.ui.gestures.gesture_hud_model import GESTURE_ORDER, hint_view


def row(key, action="播放 / 暂停", **context):
    return dict(key=key, action=action, scope="application", application="Safari", sceneLabel="常规", **context)


def test_input_hints_never_show_global_or_application_actions():
    globals = GlobalGestureBindings(window_selector="swipe-left").as_dict()
    view = hint_view("input", [row("swipe-left", "上一页")], globals)
    actions = {orb["key"]: orb["action"] for orb in view["orbs"]}
    assert actions == {"swipe-left": "撤销", "swipe-right": "语音编辑", "swipe-up": "发送",
                       "swipe-down": "润色本句", "double-tap": "开始语音", "tap": "结束语音"}
    assert all(orb["scope"] == "mode" for orb in view["orbs"])
    assert view["modeTitle"] == "输入中"


def test_outside_hints_follow_defaults_and_explicit_unbindings():
    view = hint_view("operation")
    keys = {orb["key"] for orb in view["orbs"]}
    assert keys == {"double-tap", "clench", "index-pinch", "swipe-up", "swipe-down", "double-index-pinch"}
    assert view["overflow"] == 1  # Agent remains configured beyond the six visible hints.
    assert view["modeTitle"] == "应用手势"
    bindings = GlobalGestureBindings(scroll_up="", scroll_down="").as_dict()
    assert {orb["key"] for orb in hint_view("operation", global_bindings=bindings)["orbs"]} == {
        "double-tap", "clench", "index-pinch", "double-index-pinch", "double-middle-pinch"}


def test_app_bindings_win_global_conflicts_without_duplicate_hints():
    bindings = GlobalGestureBindings(window_selector="circle-clockwise").as_dict()
    actions = [row("circle-clockwise", "下一个聊天"), row("circle-counterclockwise", "上一个聊天")]
    view = hint_view("operation", actions, bindings)
    assert [orb["action"] for orb in view["orbs"][:2]] == ["下一个聊天", "上一个聊天"]
    assert len({orb["key"] for orb in view["orbs"]}) == len(view["orbs"])
    assert not any(orb["action"] == "窗口选择" for orb in view["orbs"])


@pytest.mark.parametrize("scene,label", [("presentation", "放映"), ("pdf", "PDF 阅读"),
    ("video", "视频播放"), ("image", "图片预览"), ("music", "音乐播放")])
def test_scene_hints_only_apply_outside_text_focus(scene, label):
    actions = [dict(key="swipe-right", action="下一项", scope="scene", scene=scene,
                    sceneLabel=label, application="测试应用")]
    outside = hint_view("operation", actions)
    assert outside["sceneActive"] and outside["modeTitle"] == label
    assert outside["orbs"][0]["action"] == "下一项"
    focused = hint_view("input", actions)
    assert not focused["sceneActive"] and focused["modeTitle"] == "输入中"
    assert not any(orb["action"] == "下一项" for orb in focused["orbs"])


def test_capped_hints_preserve_all_saved_bindings_and_show_overflow():
    keys = GESTURE_ORDER[:8]
    actions = [row(key, "较长的动作名称", scene="video") for key in reversed(keys)]
    original = [dict(item) for item in actions]
    view = hint_view("operation", actions, {})
    assert [orb["key"] for orb in view["orbs"]] == list(keys[:6])
    assert view["overflow"] == 2 and actions == original
