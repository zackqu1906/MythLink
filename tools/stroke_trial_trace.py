"""Menu-relative selection feedback; handwriting retains its fixed display scale."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from collect_stroke_samples import TraceView
from proximic_ring.touchpad.stroke.stroke_contact_intent import CONTACT_INTENT
from proximic_ring.touchpad.stroke.stroke_hold_selection import TARGETS


SELECTION_DISPLAY = dict(mode='menu-relative', origin='hold-arming-frame',
    projection='bounded-direction-cells-v1', opacity=.42)


def selection_point(point, origin, cells, direction=None):
    """Project one raw sample independently, with no path-dependent recentering.

    The selection policy is categorical: crossing a sector changes its target.
    The display follows those same boundaries and compresses travel inside that
    cell, so even large displacement cannot suggest a different menu action.
    Rectangles come from the actual layout, including its margins and DPI.
    """
    dx, dy = point[0] - origin[0], point[1] - origin[1]
    distance = math.hypot(dx, dy)
    center = cells['center'].center()
    threshold = CONTACT_INTENT['selectDistance']
    if distance < threshold:
        radius = min(cells['center'].width(), cells['center'].height()) * .36
        return center + QPointF(dx / threshold * radius, dy / threshold * radius)
    if direction is None:
        sector = int((math.degrees(math.atan2(dy, dx)) + 22.5) % 360 // 45)
        direction = TARGETS[sector][0]
    target = cells[direction]
    vector = target.center() - center
    # Move into the target at the recognition threshold, then gently approach
    # its center. Gain depends on raw distance, never the trace's bounding box.
    gain = .72 + .24 * (1 - math.exp(-(distance / threshold - 1) / 4))
    length = math.hypot(vector.x(), vector.y())
    lateral = min(target.width(), target.height()) * .22
    result = center + vector * gain + QPointF(
        (dx / distance - vector.x() / length) * lateral,
        (dy / distance - vector.y() / length) * lateral)
    inset = min(10., target.width() * .15, target.height() * .15)
    inside = target.adjusted(inset, inset, -inset, -inset)
    return QPointF(max(inside.left(), min(inside.right(), result.x())),
                   max(inside.top(), min(inside.bottom(), result.y())))


class TrialTraceView(TraceView):
    def __init__(self):
        super().__init__()
        self.selection_state = {}
        self.direction_buttons = {}

    def set_selection_state(self, state):
        self.selection_state = state
        self.update()

    def selection_geometry(self):
        if self.selection_state.get('phase') != 'selecting':
            return [], None
        display = self.selection_state.get('display')
        if not display or not self.direction_buttons:
            return [], None
        cells = {name: QRectF(self.mapFromGlobal(button.mapToGlobal(button.rect().topLeft())), button.size())
                 for name, button in self.direction_buttons.items()}
        origin = display['origin']
        path = [selection_point(point, origin, cells) for point in display['points']]
        # Both the endpoint and the highlighted button use this state packet,
        # independent of the separate (potentially downsampled) handwriting ink.
        if path:
            path[-1] = selection_point(display['points'][-1], origin, cells,
                                       self.selection_state.get('direction'))
        return path, cells['center'].center()

    def paintEvent(self, event):
        if self.selection_state.get('phase') != 'selecting':
            return super().paintEvent(event)
        points, origin = self.selection_geometry()
        if not points:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath(origin)
        for point in points:
            path.lineTo(point)
        painter.setOpacity(.42)
        painter.setPen(QPen(QColor('#234cdb'), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(path)
        # The stable origin and live endpoint stay distinct over the glyphs.
        painter.setOpacity(.9)
        painter.setBrush(QColor('#ffffff'))
        painter.setPen(QPen(QColor('#21a675'), 2))
        painter.drawEllipse(origin, 4, 4)
        painter.setBrush(QColor('#345ee5'))
        painter.setPen(QPen(QColor('#ffffff'), 2))
        painter.drawEllipse(points[-1], 6, 6)
