"""Fresh defaults and saved user choices through the real settings and UI."""
import pytest

from test_inline_ui import inline_ui


@pytest.mark.parametrize("inline_ui,routing,history", [
    ({"input/routingMode": None}, "manual_routed_edit", "retry"),
    ({"input/routingMode": None, "ui/voiceHudScale": 100,
      "input/multiUndoEnabled": False}, "manual_routed_edit", "retry"),
    ({"input/routingMode": "manual", "input/historyActionMode": "next"}, "manual", "next"),
    ({"input/routingMode": "auto", "input/historyActionMode": "undo"}, "auto", "undo"),
    ({"input/routingMode": "manual_routed_edit", "input/historyActionMode": "retry"}, "manual_routed_edit", "retry"),
    ({"input/routingMode": None, "input/redoEnabled": True}, "manual_routed_edit", "retry"),
    ({"input/routingMode": None, "input/redoEnabled": False}, "manual_routed_edit", "undo"),
], indirect=["inline_ui"])
def test_new_and_unmodified_profiles_get_defaults_without_overwriting_choices(inline_ui, routing, history):
    from PySide6.QtCore import QObject

    controller, bridge, _, root, _ = inline_ui
    assert controller.inputRoutingMode == routing
    assert controller.historyActionMode == history
    assert root.findChild(QObject, "inputRoutingModeCombo").property("currentIndex") == [
        "auto", "manual", "manual_routed_edit"].index(routing)
    assert root.findChild(QObject, "historyActionModeCombo").property("currentIndex") == [
        "undo", "next", "retry"].index(history)
    begin = next(message for message in reversed(bridge.messages) if message["type"] == "begin")
    assert begin["routed_edit_mode"] == (routing == "manual_routed_edit")
    assert begin["automatic_mode"] == (routing == "auto")
    configure = next(message for message in reversed(bridge.messages) if message["type"] == "configure")
    assert configure["history_action_mode"] == history


@pytest.mark.parametrize("inline_ui", [{"input/routingMode": None}], indirect=True)
def test_user_selection_survives_restart_with_new_defaults(inline_ui):
    from PySide6.QtCore import QObject, QMetaObject, Q_ARG
    from proximic_ring.ui.controller import AppController

    controller, _, _, root, _ = inline_ui
    assert controller.inputRoutingMode == "manual_routed_edit"
    assert controller.historyActionMode == "retry"
    # Reading a default must not turn it into an explicit routing preference.
    assert not controller._settings.contains("input/routingMode")
    for name in ("inputRoutingModeCombo", "historyActionModeCombo"):
        combo = root.findChild(QObject, name)
        combo.setProperty("currentIndex", 1)
        assert QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, 1))
    controller._settings.sync()
    restarted = AppController(inline_input_enabled=True)
    try:
        assert restarted.inputRoutingMode == "manual"
        assert restarted.historyActionMode == "next"
        assert not restarted.inlineInput.routed_edit_mode
    finally:
        restarted._text_processing_worker.close(wait=True)
        restarted.voicePractice.cancel()
        restarted.proximity.close()
        restarted.appGestures.close()
        restarted.inlineInput.close()
        restarted._close_voice_history()
