from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QGuiApplication

from proximic_ring.ui.voice.windows_voice_panel import WindowsVoicePanel
from proximic_ring.ui.gestures.app_gesture_controller import AppGestureController
from proximic_ring.gestures.gesture_settings import GestureBindings


def test_panel_tracks_partial_final_and_undo_without_taking_focus():
    class Inline(QObject):
        changed = Signal()
        voicePanelPositioned = Signal(object)

    app = QGuiApplication.instance()
    inline = Inline()
    inline._gesture_hints = GestureBindings(end=("index-pinch", "")).as_dict()
    inline._hud_scale_percent = 90
    inline._utterance_id = "test-sentence"
    inline.error, inline.status, inline.cancelLabel = "", "正在听写", "取消"
    inline.canUndo, inline.canHistoryForward, inline.canRetryEdit = True, False, False
    inline.canConvert, inline.canPolish = True, False
    inline.session_automatic = inline.session_basic_dictation = inline.automatic_polish = False
    inline._view = {"phase": "listening", "raw": "这是一段听写", "can_cancel": True, "can_polish": False}
    panel = WindowsVoicePanel(inline, app)
    try:
        inline.changed.emit()
        root = panel.window.rootObject()
        assert panel.window.isVisible() and panel.window.flags() & Qt.WindowDoesNotAcceptFocus
        assert panel.window.flags() & Qt.WindowTransparentForInput
        def actions():
            items = root.property("actions")
            return items.toVariant() if hasattr(items, "toVariant") else items
        assert [item["label"] for item in actions()] == ["取消", "修改", "润色", "发送", "结束"]
        assert actions()[1]["gestures"] == ["swipe-right"] and actions()[3]["gestures"] == ["swipe-up"]
        assert "食指捏合" in actions()[-1]["description"]
        assert root.property("statusText") == inline.status and panel.window.height() == 36
        inline.canConvert = False
        inline.session_basic_dictation = True
        del inline._view["can_polish"]
        inline.changed.emit()
        assert "基础听写" in root.property("statusText")
        assert [item["label"] for item in actions()] == ["取消", "发送", "结束"]
        inline.session_basic_dictation = False
        inline._view.update(phase="editing", edit_requested=True)
        inline.changed.emit()
        assert root.property("processing") and [item["label"] for item in actions()] == ["取消"]
        inline._view.update(phase="dictated", revision=2)
        inline.status, inline.cancelLabel = "已听写", "撤销"
        inline.changed.emit()
        assert panel.dismiss.isActive() and actions()[-1]["label"] == "发送"
        panel.dismiss.timeout.emit()
        inline.changed.emit()
        assert not panel.window.isVisible()
        inline._view.update(voice_preview="整理后的候选句")
        inline.changed.emit()
        assert panel.window.height() == 72 and not panel.dismiss.isActive()
        assert root.property("suggestion") == "整理后的候选句" and actions()[1]["label"] == "采纳"
        inline._view["voice_preview"] = ""
        inline._view.update(phase="undone", raw="", revision=3)
        inline.status = "已撤销"
        inline.changed.emit()
        assert panel.window.isVisible() and root.property("statusText") == "已撤销"
        inline._utterance_id = ""
        inline.changed.emit()
        assert not panel.window.isVisible()
    finally:
        panel.window.close()


def test_pointer_yields_mapped_voice_actions_for_the_current_sentence():
    touchpad = SimpleNamespace(_windows_ime=True, inputMode="pointer", active=True, busy=False, blocksVoice=False)
    inline = SimpleNamespace(_utterance_id="sentence", canUndo=True, canHistoryForward=False,
                             canRequestConversion=False, active=True, canPolish=False)
    service = SimpleNamespace(owner=SimpleNamespace(_touchpad=touchpad, _inline_input=inline))
    bindings = GestureBindings(confirm=("index-pinch", ""), end=("index-pinch", ""))
    owns = AppGestureController.windows_pointer_owns_gesture
    assert not owns(service, "index-pinch", bindings)
    assert not owns(service, "swipe-left", bindings)
    assert owns(service, "swipe-right", bindings)
    assert not owns(service, "swipe-up", bindings)
    inline.canRequestConversion = True
    assert not owns(service, "swipe-right", bindings) and not owns(service, "swipe-down", bindings)
    inline._utterance_id = ""
    assert owns(service, "swipe-left", bindings)
    assert not owns(service, "swipe-up", bindings)
    touchpad.blocksVoice = True
    assert owns(service, "index-pinch", bindings)
