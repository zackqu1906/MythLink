import pytest

from proximic_ring.gesture_settings import GlobalGestureBindings
from proximic_ring.ui.gesture_hud_model import GESTURE_ORDER, hint_view


def row(key, action="播放 / 暂停", **context):
    return dict(key=key, action=action, scope="application", application="Safari", sceneLabel="常规", **context)


def test_application_bindings_lead_both_modes_without_duplicates():
    actions = [row("circle-clockwise", "下一个聊天"), row("circle-counterclockwise", "上一个聊天")]
    for mode in ("input", "operation"):
        view = hint_view(mode, actions)
        assert [orb["key"] for orb in view["orbs"][:2]] == ["circle-clockwise", "circle-counterclockwise"]
        assert len({orb["key"] for orb in view["orbs"]}) == len(view["orbs"])
        assert not view["sceneActive"]
        assert view["modeTitle"] == ("输入模式" if mode == "input" else "操作模式")
        assert all(orb["action"] for orb in view["orbs"])
    assert hint_view("input", actions)["overflow"] == 1


@pytest.mark.parametrize("scene,label", [("presentation", "放映"), ("pdf", "PDF 阅读"),
    ("video", "视频播放"), ("image", "图片预览"), ("music", "音乐播放")])
def test_scene_has_only_its_own_actions_and_is_identical_in_both_modes(scene, label):
    actions = [dict(key="swipe-right", action="下一项", scope="scene", scene=scene, sceneLabel=label,
                    application="测试应用", voiceDisabled=True),
               dict(key="swipe-left", action="未绑定", scope="unbound"),
               dict(key="clench", action="窗口选择", scope="global")]
    view = hint_view("input", actions)
    assert view == hint_view("operation", actions)
    assert view["sceneActive"] and view["modeTitle"] == label
    assert [orb["key"] for orb in view["orbs"]] == ["swipe-right"]


def test_capped_scene_hints_do_not_shrink_or_change_saved_actions():
    keys = GESTURE_ORDER[:8]
    actions = [row(key, "较长的动作名称", scene="video", voiceDisabled=True) for key in reversed(keys)]
    original = [dict(item) for item in actions]
    view = hint_view("input", actions)
    assert [orb["key"] for orb in view["orbs"]] == list(keys[:6])
    assert view["overflow"] == 2 and actions == original


def test_voice_override_suppresses_inactive_defaults_in_both_modes():
    for mode in ("input", "operation"):
        view = hint_view(mode, [row("swipe-up", "放大", voiceDisabled=True)])
        assert [orb["key"] for orb in view["orbs"]] == ["swipe-up", "clench"]
        assert view["voiceDisabled"] and not view["sceneActive"]


def test_remapped_globals_never_show_as_application_actions():
    globals = GlobalGestureBindings(window_selector="circle-clockwise").as_dict()
    view = hint_view("operation", [row("circle-clockwise", "旧应用动作")], globals)
    assert all(orb["action"] != "旧应用动作" for orb in view["orbs"])
    assert view["orbs"][-1]["key"] == "circle-clockwise"


def test_empty_scene_does_not_fall_back_to_mode_actions():
    view = hint_view("input", [dict(scene="video", sceneLabel="视频播放", application="Safari",
                                   key="tap", action="未绑定", scope="unbound", voiceDisabled=True)])
    assert view["sceneActive"] and view["orbs"] == []
