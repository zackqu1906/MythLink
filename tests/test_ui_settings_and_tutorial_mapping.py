"""Current settings navigation and teaching graphics follow saved user mappings."""
import pytest
from PySide6.QtCore import QObject, QPointF, Qt, QMetaObject, Q_ARG
from PySide6.QtQml import QQmlExpression, QQmlEngine
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_ui_service_settings import open_page, item
from test_ui_shell import visual_child
from test_ui_gesture_hand import find
from test_voice_tutorial import start_lesson, begin_sentence

GESTURES = ["tap", "double-tap", "index-pinch", "double-index-pinch",
            "middle-pinch", "double-middle-pinch", "swipe-up", "swipe-down",
            "swipe-left", "swipe-right", "circle-clockwise",
            "circle-counterclockwise", "clench", "snap"]


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_advanced_settings_matches_preferences_and_sidebar_commits_current_edit(inline_ui, size):
    controller, _, _, root, _ = inline_ui
    root.resize(*size)
    controller.asrBackend = "volcengine"
    open_page(root, 4)
    advanced, preferences = item(root, "advancedSettingsDialog"), item(root, "runtimeSettingsDialog")
    for prop in ("width", "height", "leftPadding", "rightPadding", "topPadding", "bottomPadding"):
        assert advanced.property(prop) == preferences.property(prop)
    field = item(root, "asrApiKeyField")
    field.forceActiveFocus(Qt.OtherFocusReason)
    field.setProperty("text", "test-only-sidebar-save")
    account = visual_child(advanced.property("background"), "preferencesNav0")
    position = account.mapToScene(QPointF(account.width() / 2, account.height() / 2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, position)
    QTest.qWait(220)
    assert not advanced.property("visible")
    assert preferences.property("visible") and preferences.property("section") == 0
    assert controller.asrApiKey == "test-only-sidebar-save"
    QMetaObject.invokeMethod(preferences, "close")


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_tutorial_renders_all_current_gestures_and_updates_when_they_are_removed(inline_ui, size):
    controller, _, _, root, _ = inline_ui
    root.resize(*size)
    dialog = item(root, "tutorialPreviewDialog")
    root.show()
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(160)
    QMetaObject.invokeMethod(dialog, "begin", Q_ARG("QVariant", True))
    dialog.setProperty("step", 3)
    QTest.qWait(200)
    preview = find(root, "tutorialCurrentGesture")
    for gesture in GESTURES:
        assert controller.setVoiceGestureAction(gesture, "polish")
        QTest.qWait(20)
        assert [g for g in preview.property("gestures").toVariant() if g] == [gesture]
        graphic = visual_child(preview, "voicePreviewGesture_" + gesture)
        ready = QQmlExpression(QQmlEngine.contextForObject(graphic), graphic, "status === Image.Ready")
        assert ready.evaluate()[0], gesture
        point = graphic.mapToItem(preview, QPointF())
        assert 0 <= point.x() <= preview.width() - graphic.width()
        assert 0 <= point.y() <= preview.height() - graphic.height()
        count = visual_child(preview, "voicePreviewCount_" + gesture)
        assert count.isVisible() == gesture.startswith("double-")
    content = find(root, "tutorialHintContent")
    assert preview.width() <= content.width()
    assert controller.setVoiceGestureAction("snap", "send")
    QTest.qWait(40)
    assert "snap" not in preview.property("gestures").toVariant()
    QTest.qWait(200)
    assert not preview.property("visible")
    assert "尚未绑定手势" in find(root, "tutorialHintTitle").property("text")
    assert "全局默认" in find(root, "tutorialLiveHint").property("text")
    QMetaObject.invokeMethod(dialog, "close")


def test_live_tutorial_does_not_suggest_an_unbound_start_or_polish_gesture(inline_ui):
    controller, _, _, _, event = inline_ui
    assert controller.setVoiceGestureAction("double-tap", "")
    tutorial = start_lesson(controller, event)
    assert tutorial.hint["id"] == "unbound-confirm" and not tutorial.hint["gestures"]
    assert controller.setVoiceGestureAction("double-middle-pinch", "confirm")
    assert tutorial.hint["gestures"] == ["double-middle-pinch"]
    assert controller.setVoiceGestureAction("swipe-down", "send")
    tutorial._stage = 1
    begin_sentence(controller, event)
    event(phase="listening", ready=True, raw="本句已有识别结果")
    assert tutorial.hint["id"] == "unbound-polish" and not tutorial.hint["gestures"]
    assert "下滑" not in tutorial.hint["text"]


def test_gesture_page_has_no_redundant_explanation_section(inline_ui):
    _, _, _, root, _ = inline_ui
    root.setProperty("currentPage", 2)
    for name in ("savedMenuBinding", "savedMenuBindingStatus", "globalBindingEffect",
                 "gestureDetailsButton", "gestureSceneHint", "openWorkflowRecommendations"):
        assert root.findChild(QObject, name) is None
