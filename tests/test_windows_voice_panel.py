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
    inline._gesture_hints = {"end": ["index-pinch"], "undo": ["swipe-left"]}
    inline._hud_scale_percent = 90
    inline._utterance_id = "test-sentence"
    inline.error, inline.status, inline.cancelLabel = "", "正在听写", "撤销"
    inline.canUndo, inline.canRedo = True, False
    inline._view = {"phase": "listening", "raw": "这是一段听写", "can_cancel": True}
    panel = WindowsVoicePanel(inline, app)
    try:
        inline.changed.emit()
        root = panel.window.rootObject()
        assert panel.window.isVisible() and panel.window.flags() & Qt.WindowDoesNotAcceptFocus
        assert panel.window.flags() & Qt.WindowTransparentForInput
        assert root.property("partialText") == "这是一段听写" and "食指捏合" in root.property("hintText")
        assert root.property("statusText") == inline.status and panel.window.height() > 20
        inline._view.update(phase="dictated", revision=2)
        inline.status = "已听写"
        inline.changed.emit()
        assert panel.dismiss.isActive() and "撤销" in root.property("hintText")
        panel.dismiss.timeout.emit()
        inline.changed.emit()
        assert not panel.window.isVisible()
        inline._view.update(phase="undone", raw="", revision=3)
        inline.status = "已撤销"
        inline.changed.emit()
        assert panel.window.isVisible() and root.property("statusText") == "已撤销"
        inline._utterance_id = ""
        inline.changed.emit()
        assert not panel.window.isVisible()
    finally:
        panel.window.close()


def test_pointer_allows_only_history_actions_owned_by_current_dictation():
    touchpad = SimpleNamespace(_windows_ime=True, inputMode="pointer", active=True, busy=False, blocksVoice=False)
    inline = SimpleNamespace(_utterance_id="sentence", canUndo=True, canHistoryForward=False)
    service = SimpleNamespace(owner=SimpleNamespace(_touchpad=touchpad, _inline_input=inline))
    bindings = GestureBindings(confirm=("index-pinch", ""), end=("index-pinch", ""))
    owns = AppGestureController.windows_pointer_owns_gesture
    assert not owns(service, "index-pinch", bindings)
    assert not owns(service, "swipe-left", bindings)
    assert owns(service, "swipe-right", bindings)
    inline._utterance_id = ""
    assert owns(service, "swipe-left", bindings)
    touchpad.blocksVoice = True
    assert owns(service, "index-pinch", bindings)
