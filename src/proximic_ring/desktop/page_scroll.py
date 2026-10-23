"""Pinned, metadata-only discovery of a front window's main scrolling region."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import time
import uuid

from .text_focus import FocusError, MacAX, TEXT_ROLES, TextFocusSession, intersection


MESSAGES = {
    "no_region": "当前窗口未提供可用的页面滚动区域",
    "unavailable": "暂时无法读取页面滚动区域",
    "limited": "页面区域读取未完成，请重试",
    "permission": "页面滚动需要辅助功能和控制权限",
    "stale": "窗口已变化，请重新滑动",
    "no_window": "当前没有可用窗口",
    "own_app": "请切到目标应用",
    "checking": "应用正在准备辅助功能，请再次滑动",
    "unsupported": "当前系统不支持页面滚动",
}
SCROLL_ROLES = {"AXScrollArea", "AXWebArea"}
SKIP_ROLES = TEXT_ROLES | {"AXToolbar", "AXMenu", "AXMenuBar", "AXScrollBar"}
SCROLL_DURATION = .36
# Each step crosses the process boundary and validates the AX target. Fewer,
# larger wheel increments leave time for this work without uneven catch-up.
SCROLL_FRAMES = 8
SCROLL_MAX_DURATION = 1.2


def animate_scroll(post, active, *, clock=time.monotonic, sleep=time.sleep):
    """Pace wheel increments; never catch up by sending the remaining distance.

    Runs in the host's background thread. Between every native call the host
    can cancel for disconnection, speech or shutdown. The worker separately
    validates the pinned AX target before each incremental wheel event.
    """
    started = clock()
    next_frame = started
    result = {"status": "cancelled", "pixels": 0}
    for frame in range(1, SCROLL_FRAMES + 1):
        delay = next_frame - clock()
        if delay > 0:
            sleep(delay)
        if not active() or clock() - started > SCROLL_MAX_DURATION:
            return {"status": "cancelled", "pixels": result.get("pixels", 0)}
        next_frame = clock() + SCROLL_DURATION / (SCROLL_FRAMES - 1)
        t = frame / SCROLL_FRAMES
        result = post(t * t * (3 - 2 * t))  # Smoothstep: gentle start and finish.
        if result.get("status") != "scrolling":
            return result
    return result


def usable(rect):
    return (rect is not None and len(rect) == 4 and all(math.isfinite(v) for v in rect)
            and rect[2] >= 120 and rect[3] >= 100)


def area(rect):
    return rect[2] * rect[3]


class MacScrollAX(MacAX):
    def _window_location_setter(self):
        # macOS has no public setter for a *foreign window's* local event
        # coordinates. Isolate this optional SPI; absence disables the action
        # instead of falling back to a global event that moves the cursor.
        if not hasattr(self, "_set_window_location"):
            import Foundation
            import Quartz as CG
            import objc
            functions = {}
            bundle = Foundation.NSBundle.bundleWithPath_("/System/Library/Frameworks/CoreGraphics.framework")
            objc.loadBundleFunctions(bundle, functions, [("CGEventSetWindowLocation",
                                     CG.CGEventSetLocation.__metadata__()["full_signature"])])
            self._set_window_location = functions.get("CGEventSetWindowLocation")
        if self._set_window_location is None:
            raise FocusError("unsupported")
        return self._set_window_location

    def hit(self, app, point):
        error, node = self.ax.AXUIElementCopyElementAtPosition(app, *point, None)
        self._check(error)
        return node if not error else None

    def window_number(self, scope):
        # A Safari suggestion popup can precede the actual AX window in the
        # WindowServer list. Route to the AX window, not that transient popup.
        import Quartz as CG
        frames = [self.rect(scope.root)]
        if scope.root != scope.window and self.metadata(scope.root).get("AXRole") not in {"AXSheet", "AXPopover"}:
            # Web dialogs share the host window; native sheets/popovers have
            # their own window and must never send wheels to the parent.
            frames.append(self.rect(scope.window))
        windows = CG.CGWindowListCopyWindowInfo(
            CG.kCGWindowListOptionOnScreenOnly | CG.kCGWindowListExcludeDesktopElements, 0) or []
        for frame in frames:
            for window in windows:
                if window.get(CG.kCGWindowOwnerPID) != scope.stamp["pid"]:
                    continue
                bounds = window.get(CG.kCGWindowBounds, {})
                rect = tuple(bounds.get(k, 0) for k in ("X", "Y", "Width", "Height"))
                if frame and all(abs(a - b) <= 2 for a, b in zip(frame, rect)):
                    return int(window[CG.kCGWindowNumber])
        raise FocusError("stale")

    def scroll(self, pid, window, point, pixels):
        import AppKit
        import Quartz as CG
        set_window_location = self._window_location_setter()
        source = CG.CGEventSourceCreate(CG.kCGEventSourceStatePrivate)
        wheel = CG.CGEventCreateScrollWheelEvent(source, CG.kCGScrollEventUnitPixel, 1, pixels)
        info = CG.CGWindowListCopyWindowInfo(CG.kCGWindowListOptionIncludingWindow, window) or []
        bounds = next((w[CG.kCGWindowBounds] for w in info
                       if w.get(CG.kCGWindowNumber) == window and w.get(CG.kCGWindowOwnerPID) == pid), None)
        if bounds is None:
            raise FocusError("stale")
        local = (point[0] - bounds["X"], point[1] - bounds["Y"])
        # PostToPid bypasses WindowServer annotation: a fresh wheel's window
        # number is zero and AppKit drops it. Seed the public NSEvent window
        # number, then convert the event to a pixel wheel BEFORE posting.
        carrier = AppKit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
            AppKit.NSEventTypeMouseMoved, (0, 0), 0, time.monotonic(), window, None, 0, 0, 0)
        event = CG.CGEventCreateCopy(carrier.CGEvent()) if carrier is not None else None
        if event is None or wheel is None:
            raise FocusError("unavailable")
        CG.CGEventSetType(event, CG.kCGEventScrollWheel)
        CG.CGEventSetSource(event, source)
        for field in (CG.kCGScrollWheelEventDeltaAxis1, CG.kCGScrollWheelEventFixedPtDeltaAxis1,
                      CG.kCGScrollWheelEventPointDeltaAxis1, CG.kCGScrollWheelEventIsContinuous):
            CG.CGEventSetIntegerValueField(event, field, CG.CGEventGetIntegerValueField(wheel, field))
        CG.CGEventSetFlags(event, 0)  # Held modifiers must not turn scrolling into zoom.
        CG.CGEventSetLocation(event, point)
        set_window_location(event, local)
        CG.CGEventSetIntegerValueField(event, CG.kCGMouseEventWindowUnderMousePointer, window)
        CG.CGEventSetIntegerValueField(event, CG.kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent, window)
        # No mouse move, click, keyboard command, activation or focus write.
        if self.front()["pid"] != pid:
            raise FocusError("stale")
        CG.CGEventPostToPid(pid, event)


@dataclass
class ScrollPlan:
    scope: object
    focus: object
    target: object
    rect: tuple
    point: tuple
    window: int
    pixels: int
    expires: float
    progress: float = 0.0
    applied: int = 0


class PageScrollSession:
    def __init__(self, ax=None, *, clock=time.monotonic):
        self.ax = ax or MacScrollAX()
        self.clock = clock
        self.context = TextFocusSession(self.ax, clock=clock)
        self.plan = None  # A new gesture supersedes an unconsumed plan.

    def _candidates(self, scope):
        ctx = self.context
        clip = self.ax.rect(scope.root)
        if not usable(clip):
            return []
        queue, seen, candidates = deque([(scope.root, 0)]), set(), []
        while queue:
            ctx._check_budget()
            node, depth = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            if len(seen) > ctx.MAX_NODES or depth > ctx.MAX_DEPTH:
                raise FocusError("limited")
            meta = self.ax.metadata(node)
            role = meta.get("AXRole")
            if meta.get("AXHidden") or meta.get("AXEnabled") is False or role in SKIP_ROLES:
                continue
            if node != scope.root and role in {"AXWindow", "AXSheet", "AXDialog", "AXPopover"}:
                continue
            if role in SCROLL_ROLES:
                frame = self.ax.rect(node)
                rect = intersection(clip, frame) if usable(frame) else None
                if usable(rect):
                    candidates.append((node, rect))
                # Do not enumerate thousands of document nodes. Hit testing
                # below resolves inner scrolling containers at a few points.
                continue
            queue.extend((child, depth + 1) for child in self.ax.children(node, ctx.MAX_NODES - len(seen)))
        return sorted(candidates, key=lambda item: area(item[1]), reverse=True)

    def _receiver(self, scope, outer, point):
        ctx = self.context
        path = ctx._ancestry(self.ax.hit(scope.app, point), scope.root)
        if not path or outer not in path:
            return None
        clip = self.ax.rect(scope.root)
        receiver = None
        for node in reversed(path):
            ctx._check_budget()
            meta = self.ax.metadata(node)
            role = meta.get("AXRole")
            if meta.get("AXHidden") or meta.get("AXEnabled") is False or role in SKIP_ROLES:
                return None
            if role in SCROLL_ROLES:
                frame = self.ax.rect(node)
                if not usable(frame):
                    return None
                clip = intersection(clip, frame)
                if not usable(clip):
                    return None
                receiver = (node, clip)
        return receiver

    def _target(self, scope):
        candidates = self._candidates(scope)
        if not candidates:
            return None
        # Never fall back from an unreadable main region to a much smaller
        # sidebar. Ambiguous equal panes use the exposed control-tree order.
        largest = area(candidates[0][1])
        for outer, rect in candidates:
            if area(rect) < largest * .6:
                break
            for fx, fy in ((.5, .5), (.65, .35), (.35, .35), (.65, .65), (.35, .65)):
                self.context._check_budget()
                point = (rect[0] + rect[2] * fx, rect[1] + rect[3] * fy)
                receiver = self._receiver(scope, outer, point)
                if receiver and area(receiver[1]) >= largest * .6:
                    return (*receiver, point)
        return None

    def handle(self, operation, *, ignored_pid=0, expected=None, direction="", plan="", progress=1.0):
        ctx = self.context
        ctx.deadline = self.clock() + ctx.BUDGET_SECONDS
        try:
            if operation == "scroll_apply":
                return self._apply(plan, ignored_pid, progress)
            if operation != "scroll_plan" or direction not in {"up", "down"}:
                raise ValueError("未知页面滚动操作")
            self.plan = None
            scope, focus = ctx._capture(ignored_pid, expected)
            target = self._target(scope)
            if target is None:
                return {"status": "no_region"}
            node, rect, point = target
            window = self.ax.window_number(scope)
            current, current_focus = ctx._capture(ignored_pid, scope.stamp)
            if current.token != scope.token or current_focus != focus:
                raise FocusError("stale")
            pixels = max(1, round(rect[3] * .5)) * (1 if direction == "up" else -1)
            token = uuid.uuid4().hex
            self.plan = (token, ScrollPlan(scope, focus, node, rect, point, window, pixels, self.clock() + .6))
            return {"status": "ready", "plan": token, "pixels": pixels}
        except FocusError as exc:
            return {"status": exc.status}

    def _apply(self, token, ignored_pid, progress):
        pending, self.plan = self.plan, None
        if pending is None or pending[0] != token or self.clock() > pending[1].expires:
            raise FocusError("stale")
        plan = pending[1]
        if not math.isfinite(progress) or not plan.progress < progress <= 1.0:
            raise FocusError("stale")
        scope, focus = self.context._capture(ignored_pid, plan.scope.stamp)
        if scope.token != plan.scope.token or focus != plan.focus:
            raise FocusError("stale")
        receiver = self._receiver(scope, plan.target, plan.point)
        if receiver != (plan.target, plan.rect) or self.ax.window_number(scope) != plan.window:
            raise FocusError("stale")
        self.context._check_budget()
        if self.ax.front() != scope.stamp:
            raise FocusError("stale")
        cumulative = round(plan.pixels * progress)
        delta = cumulative - plan.applied
        if delta:
            self.ax.scroll(scope.stamp["pid"], plan.window, plan.point, delta)
        if plan.progress == 0:
            # One bounded animation, not a lease renewed indefinitely per step.
            plan.expires = self.clock() + SCROLL_MAX_DURATION
        plan.progress, plan.applied = progress, cumulative
        if progress < 1.0:
            self.plan = pending
        # Posting has no delivery acknowledgement. Do not claim the page moved
        # or retry when an app ignores wheel events / is already at an edge.
        return {"status": "posted" if progress == 1.0 else "scrolling", "pixels": cumulative}
