"""Native references for a spatial field-selection session; no text content."""
from dataclasses import dataclass
import math
import uuid

from .text_focus import FocusError, intersection


def spatial_target(rectangles, selected, direction):
    current = rectangles[selected]
    cx, cy = current[0] + current[2]/2, current[1] + current[3]/2
    best, score = None, float("inf")
    for index, rect in enumerate(rectangles):
        if index == selected:
            continue
        x, y = rect[0] + rect[2]/2, rect[1] + rect[3]/2
        overlap_x = min(current[0]+current[2], rect[0]+rect[2]) - max(current[0], rect[0])
        overlap_y = min(current[1]+current[3], rect[1]+rect[3]) - max(current[1], rect[1])
        if direction in {"up", "down"} and overlap_x > 10 and (y < cy if direction == "up" else y > cy):
            cost = abs(y-cy) + 2.2*abs(x-cx)
        elif direction in {"left", "right"} and overlap_y > 0 and (x < cx if direction == "left" else x > cx):
            cost = abs(x-cx) + 2.2*abs(y-cy)
        else:
            continue
        if cost < score:
            best, score = index, cost
    return best


def valid_rect(rect):
    return rect is not None and len(rect) == 4 and all(math.isfinite(v) for v in rect) and rect[2] > 0 and rect[3] > 0


@dataclass
class Candidate:
    node: object
    rect: tuple
    address: bool
    control_rect: tuple = ()
    bounds_source: str = "control"


@dataclass
class Selection:
    token: str
    scope: object
    fields: list
    index: int
    expected_focus: object
    partial: bool
    expires: float
    version: int = 0


class TextSelectionSession:
    def __init__(self, owner):
        self.owner = owner
        self.current = None

    def valid_plan(self, identity):
        return self.current is not None and (self.current.token, self.current.version) == identity

    def _geometry(self, node, scope):
        owner = self.owner
        ancestors = owner._ancestry(node, scope.root)
        if not ancestors:
            raise FocusError("stale")
        rect = owner.ax.rect(node)
        if not valid_rect(rect):
            raise FocusError("no_geometry")
        for parent in ancestors:
            meta = owner.ax.metadata(parent)
            if meta.get("AXHidden") or meta.get("AXEnabled") is False:
                raise FocusError("stale")
            if parent == scope.root or meta.get("AXRole") == "AXScrollArea":
                rect = intersection(rect, owner.ax.rect(parent))
        if not valid_rect(rect):
            raise FocusError("stale")
        return tuple(rect), ancestors

    def _outline(self, field, ancestors, scope, fields):
        """Use a tight, single-field wrapper when AX exposes one.

        AXTextArea can describe only the line-editing portion of a composer.
        Keep focusing that node, but outline its enclosing input container.
        Never inflate a missing wrapper into the web page/window/whole toolbar.
        """
        raw = field.control_rect
        if field.address:
            return raw, "control"
        for parent in ancestors[1:7]:
            meta = self.owner.ax.metadata(parent)
            role = meta.get("AXRole")
            if parent == scope.root or role in {"AXWebArea", "AXToolbar", "AXWindow", "AXSheet", "AXDialog", "AXPopover"}:
                break
            if role not in {"AXGroup", "AXScrollArea"}:
                continue
            rect = self.owner.ax.rect(parent)
            if not valid_rect(rect):
                continue
            x, y, w, h = rect
            rx, ry, rw, rh = raw
            gaps = (rx-x, ry-y, x+w-rx-rw, y+h-ry-rh)
            if any(gap < -1 for gap in gaps):
                continue
            if (max(gaps[0], gaps[2]) > 32 or gaps[1] > 32 or gaps[3] > 96
                    or w > max(rw*1.35, rw+48) or h > max(rh*4, rh+80)):
                break
            if w <= rw+2 and h <= rh+2:
                continue
            if any(other is not field and (overlap := intersection(rect, other.control_rect))
                   and overlap[2] > 1 and overlap[3] > 1 for other in fields):
                break
            # Respect the same clipping boundaries as the actual editable node.
            for ancestor in ancestors[1:]:
                if ancestor == scope.root or self.owner.ax.metadata(ancestor).get("AXRole") == "AXScrollArea":
                    rect = intersection(rect, self.owner.ax.rect(ancestor))
            return tuple(rect), "container"
        return raw, "control"

    def _refresh_geometry(self, state):
        paths = []
        for field in state.fields:
            field.control_rect, ancestors = self._geometry(field.node, state.scope)
            paths.append(ancestors)
        for field, ancestors in zip(state.fields, paths):
            try:
                field.rect, field.bounds_source = self._outline(field, ancestors, state.scope, state.fields)
            except FocusError as exc:
                if exc.status == "limited":
                    raise
                field.rect, field.bounds_source = field.control_rect, "control"

    def _capture(self, token, ignored_pid):
        owner, state = self.owner, self.current
        if state is None or state.token != token or owner.clock() > state.expires:
            raise FocusError("stale")
        # Safari's suggestion panel can change WindowServer ordering while the
        # actual AX window and selected field stay the same. Pin the logical
        # window and focus here, then stamp a fresh plan before any mutation.
        scope, focus = owner._capture(ignored_pid)
        if scope.token != state.scope.token or focus != state.expected_focus:
            raise FocusError("stale")
        state.scope = scope
        field = state.fields[state.index]
        if not owner._editable(field.node):
            raise FocusError("stale")
        # Keep references pinned; a tab change/removal must never silently
        # replace the selection with a different page's controls.
        self._refresh_geometry(state)
        state.expires = owner.clock() + 15
        return state, focus

    def snapshot(self, state, *, bounce=False):
        frame = self.owner.ax.rect(state.scope.root)
        if not valid_rect(frame):
            raise FocusError("no_geometry")
        return {"status": "available", "scope": state.scope.token, "stamp": state.scope.stamp,
                "selection": state.token, "frame": list(frame), "index": state.index+1,
                "count": len(state.fields), "partial": state.partial, "bounce": bounce,
                "deferred": state.fields[state.index].address,
                "fields": [{"rect": list(field.rect), "control_rect": list(field.control_rect),
                            "bounds_source": field.bounds_source, "address": field.address}
                           for field in state.fields]}

    def handle(self, action, *, ignored_pid, expected, token, direction):
        owner = self.owner
        if action == "cancel":
            if self.current is not None and self.current.token == token:
                self.current = None
            return {"status": "cancelled"}
        if action == "enter":
            self.current = None
            scope, focus = owner._capture(ignored_pid, expected)
            nodes = owner._fields(scope)
            partial = owner.scan_incomplete
            fields = []
            for node in nodes:
                try:
                    rect, ancestors = self._geometry(node, scope)
                    fields.append(Candidate(node, rect, owner.is_address_bar(node, scope, ancestors)))
                except FocusError as exc:
                    if exc.status == "limited":
                        raise
                    partial = True
            if not fields:
                raise FocusError("no_geometry" if nodes else "unavailable" if partial else "no_fields")
            current, after = owner._capture(ignored_pid, scope.stamp)
            if current.token != scope.token or after != focus:
                raise FocusError("stale")
            remembered = focus if focus in [f.node for f in fields] else scope.last
            index = next((i for i, f in enumerate(fields) if f.node == remembered), 0)
            state = Selection(uuid.uuid4().hex, scope, fields, index, focus, partial, owner.clock()+15)
            self._refresh_geometry(state)
            self.current = state
        else:
            state, focus = self._capture(token, ignored_pid)
        bounce = False
        if action == "move":
            if direction not in {"up", "down", "left", "right"}:
                raise ValueError("未知输入框方向")
            target = spatial_target([f.rect for f in state.fields], state.index, direction)
            bounce = target is None
            if target is not None:
                state.index = target
        elif action not in {"enter", "inspect", "confirm"}:
            raise ValueError("未知输入框选择操作")
        result = self.snapshot(state, bounce=bounce)
        if action == "inspect":
            return result
        state.version += 1
        selected = state.fields[state.index]
        if action == "confirm" or not selected.address:
            result["plan"] = owner.make_plan(state.scope, selected.node, focus, len(state.fields),
                                              state.index+1, state.partial, (state.token, state.version))
        return result

    def did_focus(self, plan, scope):
        state = self.current
        state.scope = scope
        state.expected_focus = plan.target
        self._refresh_geometry(state)
        return {**self.snapshot(state), "status": "focused"}
