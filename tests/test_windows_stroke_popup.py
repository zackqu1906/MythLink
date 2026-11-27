"""Windows popup overflow and continuous external stroke sessions."""
import math
import sys

import pytest
from PySide6.QtCore import QPointF, QRect
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from test_inline_ui import FakeBridge, inline_ui
from proximic_ring.ui.touchpad.stroke_session_controller import stroke_popup_geometry


@pytest.mark.parametrize("area", [QRect(0, 0, 1920, 1040), QRect(-1280, 0, 1280, 680),
                                  QRect(1920, -200, 960, 500), QRect(0, 0, 400, 300)])
def test_complete_popup_fits_work_area_at_every_edge(area):
    for x in (area.left(), area.center().x(), area.right()):
        for y in (area.top(), area.center().y(), area.bottom()):
            popup = stroke_popup_geometry([x, y, x + 2, y + 20], area)
            assert popup.width() > 76
            assert area.adjusted(8, 8, -8, -8).contains(popup)
            side = popup.width() - 76
            assert popup.height() >= side * 1.4 + 24


@pytest.mark.parametrize("area", [QRect(0, 0, 1920, 1040), QRect(-1280, -200, 1280, 680), QRect(0, 0, 400, 300)])
def test_stroke_sequence_remains_fully_visible_at_screen_edges(area):
    from types import SimpleNamespace
    from proximic_ring.ui.touchpad.stroke_session_controller import StrokeSessionController
    for x in (area.left(), area.right() - 2):
        for y in (area.top(), area.bottom() - 20):
            caret = [x, y, x + 2, y + 20]
            session = SimpleNamespace(_screen_caret=lambda _: (caret, area))
            popup = StrokeSessionController.strokeSequenceGeometry(session, caret, 480, 100)
            assert area.adjusted(4, 4, -4, -4).contains(popup)
            assert popup.width() == min(480, area.width() - 8)
            assert popup.height() == 100


@pytest.fixture
def windows_ui(request, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows IME adapter")
    from proximic_ring.voice.ime import windows_bridge
    monkeypatch.setenv("MYTHLINK_WINDOWS_TSF", "1")
    monkeypatch.setattr(windows_bridge, "WindowsIMEBridge", FakeBridge)
    return request.getfixturevalue("inline_ui")


def test_real_windows_popup_contains_sliders_and_animated_palette(windows_ui, tmp_path):
    c, _, _, root, _ = windows_ui
    tp = c.touchpad
    popup = root.findChild(QQuickWindow, "strokeExternalPopup")
    assert popup is not None
    grid = popup.findChild(QQuickItem, "strokeExternalGrid")
    assert grid is not None
    tp.strokeSession._set_caret([100, 100, 102, 120])
    QTest.qWait(50)
    assert popup.isVisible()
    flags = popup.flags()
    from PySide6.QtCore import Qt
    assert flags & Qt.WindowDoesNotAcceptFocus and flags & Qt.WindowTransparentForInput

    states = [
        ("clear-partial", dict(phase="selecting", direction="left", clear_progress=.5),
         ["strokeClearPull"]),
        ("clear", dict(phase="selecting", direction="left", clear_progress=1, clear_armed=True),
         ["strokeClearPull"]),
        ("pull", dict(phase="selecting", direction="up-left",
                     punctuation_pull=dict(direction="up-left", progress=1, opened=False)),
         ["strokePunctuationPull"]),
        ("palette", dict(phase="selecting", direction="up-right",
                        punctuation_pull=dict(direction="up-right", progress=1, opened=True, symbol="！")),
         ["strokeMorePunctuation", "strokeCancelPunctuation"]),
    ]
    for side in (280, 176):
        popup.setWidth(side + 76)
        popup.setHeight(side + math.ceil(side * .4) + 24)
        for name, state, controls in states:
            tp._selection_state = state
            tp.selectionChanged.emit()
            # Include the OutBack overshoot, not only the final bounds.
            for frame in range(18):
                QTest.qWait(16)
                for control in controls:
                    item = grid.findChild(QQuickItem, control)
                    assert item is not None
                    if frame == 17:
                        assert item.isVisible()
                    for point in (QPointF(0, 0), QPointF(item.width(), item.height())):
                        p = item.mapToScene(point)
                        assert 0 <= p.x() <= popup.width(), (name, side, p)
                        assert 0 <= p.y() <= popup.height(), (name, side, p)
            image = popup.grabWindow()
            assert image.save(str(tmp_path / f"popup-{side}-{name}.png"))
            if name in {"clear", "clear-partial", "pull"}:
                # Bounds/visible alone miss unsupported shader effects which
                # leave an otherwise mounted item completely transparent.
                item = grid.findChild(QQuickItem, controls[0])
                center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
                ratio = image.width() / popup.width()
                assert image.pixelColor(round(center.x() * ratio), round(center.y() * ratio)).alpha() > 200
    tp.strokeSession._set_caret([])
    assert not popup.isVisible()


def test_windows_correction_negotiates_native_support_and_preserves_popup(windows_ui, monkeypatch):
    from proximic_ring.touchpad.stroke.stroke_menu import two_finger_selection
    from test_stroke_session import until

    controller, bridge, _, root, event = windows_ui
    tp, inline = controller.touchpad, controller._inline_input
    session = tp.strokeSession
    event(phase="idle", ready=True)
    session.phase = "native"
    session._token = "windows-correction"
    inline.stroke_target = (inline._epoch, inline._client_id, session._token)
    tp.handwritingCorrectionEnabled = True
    assert tp.handwritingCorrectionSupported and not tp.handwritingCorrectionActive

    caret = [100, 100, 102, 120]
    session._set_caret(caret)
    popup = root.findChild(QQuickWindow, "strokeExternalPopup")
    initial_width = popup.width()
    monkeypatch.setattr(session, "allow_input", lambda: True)
    monkeypatch.setattr(session, "_begin_output", lambda: setattr(session, "phase", "running"))
    event("stroke_ready", stroke_session=session._token, success=True, caret=caret, handwriting_correction=True)
    tp.changed.emit()
    assert tp.handwritingCorrectionActive and popup.width() == initial_width + 60

    event("stroke_correction_state", stroke_session=session._token, text="清华", busy=False)
    tp._selection_state = two_finger_selection([(0, 0), (30, 0)], correction_enabled=True)
    tp.selectionChanged.emit()
    QTest.qWait(160)
    slider = popup.findChild(QQuickItem, "strokeCorrectionPull")
    scope = popup.findChild(QQuickItem, "externalStrokeCorrectionScope")
    assert slider.isVisible() and scope.property("highlighted")
    assert slider.mapToScene(QPointF(slider.width(), slider.height())).x() < popup.width()
    assert scope.property("text") == "修正用词「清华」"

    tp._handwriting_correction.request()
    request = next(m for m in reversed(bridge.messages) if m["type"] == "stroke_correction_request")
    processor = controller._text_processing_worker._processor
    monkeypatch.setattr(processor, "locate_handwriting_correction", lambda *_: "青华", raising=False)
    event("stroke_correction_requested", stroke_session=session._token, request_id=request["request_id"],
          original="去青华。", handwriting="清华")
    until(lambda: any(m["type"] == "stroke_correction_result" for m in bridge.messages))
    result = next(m for m in reversed(bridge.messages) if m["type"] == "stroke_correction_result")
    assert result["original_text"] == "青华" and result["stroke_session"] == session._token

    tp._selection_state = {}
    tp.selectionChanged.emit()
    event("stroke_correction_state", stroke_session=session._token, can_undo=True, context="去清华。")
    assert tp.handwritingCorrectionCanUndo and tp._stroke_context == "去清华。"
    tp._handwriting_correction.undo()
    assert bridge.messages[-1]["type"] == "stroke_correction_undo"
    session.cancel()
    assert not tp.handwritingCorrectionActive


@pytest.mark.parametrize("local", [False, True])
def test_windows_interactive_qos_lasts_only_for_external_session(windows_ui, monkeypatch, local):
    from types import SimpleNamespace
    from proximic_ring.voice.ime import windows_bridge

    controller, _, _, _, _ = windows_ui
    tp = controller.touchpad
    session = tp.strokeSession
    calls = []

    def begin():
        calls.append("begin")
        return lambda: calls.append("restore")

    monkeypatch.setattr(windows_bridge, "begin_interactive_stroke", begin)
    tp._state = "running"
    tp._run = SimpleNamespace(output_ready=True)
    session.local = local
    session.phase = "starting"
    session._output_started = True
    try:
        session._output_changed()
        session._output_changed()
        assert session.phase == "running" and calls == ([] if local else ["begin"])
    finally:
        tp._run = None
        session.cancel(stop_output=False)
    assert calls == ([] if local else ["begin", "restore"])


def test_windows_anchor_survives_temporary_missing_layout(windows_ui):
    c, _, _, _, _ = windows_ui
    inline = c._inline_input
    session = c.touchpad.strokeSession
    session.phase = "running"
    session._token = "stroke-test"
    inline.stroke_target = (inline._epoch, inline._client_id, session._token)
    session._set_caret([100, 100, 102, 120])
    message = dict(type="state", epoch=inline._epoch, client_id=inline._client_id, ready=True)
    session._native_event({**message, "caret": [0, 0, 0, 0]})
    assert session.phase == "running" and session.caret == [100, 100, 102, 120]
    session._native_event({**message, "caret": [120, 100, 122, 120]})
    assert session.caret == [120, 100, 122, 120]
    session._native_event({**message, "client_id": "different-editor"})
    assert session.phase == "idle" and not session.caret


def test_windows_displays_stroke_updates_locally_and_only_sends_the_commit(windows_ui, monkeypatch):
    from proximic_ring.ui.touchpad import stroke_glyph_images

    controller, bridge, _, root, event = windows_ui
    touchpad = controller.touchpad
    inline = controller._inline_input
    session = touchpad.strokeSession
    event(phase="idle", ready=True)
    session.phase = "running"
    inline.stroke_target = (inline._epoch, inline._client_id, "stroke-test")
    touchpad._mode = "stroke"
    touchpad._code = "h"
    touchpad._candidates = ["一"]
    session._set_caret([100, 100, 102, 120])
    popup = root.findChild(QQuickWindow, "strokeExternalPopup")
    trail = popup.findChild(QQuickItem, "strokeTrailDisplay")
    bridge.messages.clear()

    def unexpected_png_conversion(_):
        pytest.fail("Windows already displays candidates in Qt")

    monkeypatch.setattr(stroke_glyph_images, "native_glyph_images", unexpected_png_conversion)
    touchpad._sync_external_composition()
    touchpad.changed.emit()
    touchpad._selection_state = {"phase": "selecting", "direction": "center"}
    touchpad._sync_external_selection()
    touchpad.selectionChanged.emit()
    touchpad.strokeTraceChanged.emit([[.2, .5], [.8, .5]], True)
    points = trail.property("points")
    if hasattr(points, "toVariant"):
        points = points.toVariant()
    assert len(points) == 2
    assert touchpad.strokeCandidates == ["一"]

    inline._stroke_request = ("old", "一", inline._client_id)
    touchpad._clear_external_composition()
    assert inline._stroke_request is None
    assert touchpad._commit_character("一") is None
    assert [message["type"] for message in bridge.messages] == ["stroke_commit"]


def test_windows_delete_waits_for_the_current_editor_result(windows_ui, monkeypatch):
    c, bridge, _, _, event = windows_ui
    tp = c.touchpad
    inline = c._inline_input
    session = tp.strokeSession
    origin = ("test.editor", 42)
    inline.stroke_mode = True
    session.phase = "running"
    session._token = "stroke-test"
    session._origin = origin
    inline.stroke_target = (inline._epoch, inline._client_id, session._token)
    monkeypatch.setattr(inline, "_foreground_identity", lambda: origin)
    monkeypatch.setattr(bridge, "focus_state", lambda **_: dict(
        epoch=inline._epoch, client_id=inline._client_id, application=origin[0],
        ready=True, selection=[1, 0]), raising=False)
    tp._stroke_context = "前文"

    for to_start, success in ((False, True), (True, False), (True, True)):
        tp._delete_editor(to_start=to_start)
        request = bridge.messages[-1]
        assert request["type"] == "stroke_delete" and request["to_start"] == to_start
        assert tp._stroke_context == "前文"
        result = dict(request_id=request["request_id"], to_start=to_start, success=success, changed=success)
        event("stroke_delete_result", stroke_session="previous-session", **result)
        assert tp._stroke_context == "前文"
        event("stroke_delete_result", stroke_session=session._token, **result)
        if success:
            assert tp.commitMessage.startswith("已") and not tp._stroke_context
        else:
            assert "失败" in tp.commitMessage and tp._stroke_context == "前文"
        assert session.phase == "running"
        tp._stroke_context = "前文"


def test_pending_strokes_follow_caret_without_moving_grid(windows_ui, tmp_path):
    from PySide6.QtCore import Qt
    c, _, _, root, _ = windows_ui
    tp = c.touchpad
    popup = root.findChild(QQuickWindow, "strokeExternalPopup")
    sequence = root.findChild(QQuickWindow, "strokeSequencePopup")
    label = sequence.findChild(QQuickItem, "externalStrokeSequenceLabel")
    tp.strokeSession._set_caret([100, 100, 102, 120])
    QTest.qWait(30)
    grid_geometry = popup.geometry()
    assert not sequence.isVisible()
    for code in ("h", "hspnz", "hspnz" * 8):
        tp._code = code
        tp.changed.emit()
        QTest.qWait(30)
        assert sequence.isVisible()
        assert label.property("text") == tp.strokeCode.replace("𠃍", "乛")
        assert popup.geometry() == grid_geometry
        bounds = tp.strokeSession.strokeSequenceBounds(tp.strokeSession.caret)
        assert bounds.contains(sequence.geometry())
        assert sequence.geometry() == tp.strokeSession.strokeSequenceGeometry(
            tp.strokeSession.caret, sequence.width(), sequence.height())
        assert sequence.x() >= 102 and sequence.y() == 100
    assert label.implicitHeight() > 30  # Long sequences wrap, preserving order.
    assert sequence.flags() & Qt.WindowDoesNotAcceptFocus
    assert sequence.flags() & Qt.WindowTransparentForInput
    assert sequence.grabWindow().save(str(tmp_path / "stroke-sequence-wrapped.png"))
    tp._code = "hspnz"
    tp.changed.emit()
    QTest.qWait(30)
    assert sequence.grabWindow().save(str(tmp_path / "stroke-sequence.png"))
    tp._code = ""
    tp.changed.emit()
    assert not sequence.isVisible() and popup.geometry() == grid_geometry
    tp._code = "h"
    tp.changed.emit()
    tp.strokeSession._set_caret([])
    assert not sequence.isVisible()


@pytest.mark.parametrize("scale", [1, 1.25, 1.5, 2])
def test_windows_anchor_converts_per_monitor_dpi(windows_ui, monkeypatch, scale):
    from proximic_ring.ui.shared import windows_caret as module
    from proximic_ring.voice.ime import windows_bridge
    from types import SimpleNamespace
    screen = SimpleNamespace(geometry=lambda: QRect(-1280, 0, 1280, 720),
                             availableGeometry=lambda: QRect(-1280, 0, 1280, 680),
                             devicePixelRatio=lambda: scale)
    monkeypatch.setattr(module, "QGuiApplication", SimpleNamespace(screens=lambda: [screen], primaryScreen=lambda: screen))
    monkeypatch.setattr(windows_bridge, "caret_monitor_origin", lambda _: (-1280, 0))
    native = [-1280 + 200 * scale, 100 * scale, -1280 + 202 * scale, 120 * scale]
    actual = windows_ui[0].touchpad.strokeSession.popupGeometry(native)
    expected = stroke_popup_geometry([-1080, 100, -1078, 120], screen.availableGeometry())
    assert actual == expected
