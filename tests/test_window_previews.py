import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage, QColor

from proximic_ring.ui.window_previews import WindowPreviews
from proximic_ring.window_capture import dimensions, MAX_STREAMS
from test_text_focus_controller import until


class Capture:
    def __init__(self): self.calls = []; self.frames = []; self.statuses = []
    def start(self, cards, frame, status):
        self.calls.append(("start", cards)); self.frames.append(frame); self.statuses.append(status)
    def update(self, cards): self.calls.append(("update", cards))
    def stop(self): self.calls.append(("stop", []))
    def close(self): self.calls.append(("close", []))


@pytest.fixture
def previews():
    app = QCoreApplication.instance() or QCoreApplication([])
    engine = Capture()
    service = WindowPreviews(enabled=True, factory=lambda: engine)
    until(lambda: service._engine is not None)
    yield service, engine
    service.close()


def cards(count=30):
    return [dict(id=str(i), number=i+1, pid=10, frame=(0, 0, 1440, 900)) for i in range(count)]


def image(color):
    result = QImage(20, 12, QImage.Format_ARGB32)
    result.fill(QColor(color))
    return result


def test_frames_coalesce_without_queueing_and_stop_discards_late_callbacks(previews):
    service, engine = previews
    rendered = []
    service.frameReady.connect(lambda *args: rendered.append(args))
    service.start(cards(), 0)
    for _ in range(20): engine.frames[-1]("0", image("red"))
    engine.frames[-1]("0", image("blue"))
    service.flush()
    assert len(rendered) == 1 and service.status == "live"
    assert service.provider.images["0"].pixelColor(0, 0) == QColor("blue")
    stale = engine.frames[-1]
    service.stop()
    assert not service.provider.images
    stale("0", image("green")); service.flush()
    assert len(rendered) == 1
    service.start(cards(), 0)
    stale("0", image("green")); service.flush()
    assert not service.provider.images  # Previous opening must not leak into a new one.


def test_only_visible_and_selected_windows_stream_with_a_fixed_resource_cap(previews):
    service, engine = previews
    service.setVisibleRange(0, 30)
    service.start(cards(), 29)
    started = engine.calls[-1][1]
    assert len(started) == MAX_STREAMS and started[0]["id"] == "29"
    service.setVisibleRange(16, 24)
    assert {c["id"] for c in engine.calls[-1][1]} == {"29", *(str(i) for i in range(16,24))}
    engine.frames[-1]("0", image("red")); service.flush()
    assert not service.provider.images
    service.select(20)
    assert {c["id"] for c in engine.calls[-1][1]} == {str(i) for i in range(16,24)}


def test_permission_message_is_explicit_and_does_not_prevent_selection(previews):
    service, engine = previews
    service.start(cards(), 0)
    engine.statuses[-1]("permission")
    until(lambda: service.status == "permission")
    assert "屏幕录制" in service.hint and not service.provider.images
    engine.frames[-1]("0", image("red")); service.flush()
    assert not service.provider.images  # Permission failure must discard an in-flight frame.
    service.stop()
    engine.statuses[-1]("live")
    QCoreApplication.processEvents()
    assert service.status == "permission"


@pytest.mark.parametrize("frame", [(0,0,3000,2000), (0,0,800,1800), (0,0,320,180)])
def test_stream_dimensions_keep_source_aspect_and_bound_memory(frame):
    w,h = dimensions(frame)
    assert 2 <= w <= 720 and 2 <= h <= 480
    assert abs(w / h - frame[2] / frame[3]) < .01
