"""Immutable, validated gesture assignments shared by the GUI and audio worker."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json


GESTURE_LABELS = {
    "tap": "轻点（tap）",
    "double-tap": "双击（tap）",
    "snap": "弹指（snap）",
    "swipe-left": "左滑",
    "swipe-down": "下滑",
    "swipe-right": "右滑",
    "swipe-up": "上滑",
    "clench": "握拳",
    "index-pinch": "食指捏合",
    "double-index-pinch": "双食指捏合",
    "middle-pinch": "中指捏合",
    "double-middle-pinch": "双中指捏合",
    "circle-clockwise": "顺时针画圈",
    "circle-counterclockwise": "逆时针画圈",
}
GESTURE_ACTION_LABELS = {"confirm": "开始", "end": "结束", "undo": "撤销", "switch_mode": "类型转换"}
RING_RESERVED_GESTURES = frozenset({"index-pinch", "middle-pinch", "clench", "swipe-down", "swipe-up"})
VOICE_GESTURE_GROUP = frozenset({"tap", "double-tap", "swipe-left", "swipe-right", "swipe-up", "swipe-down"})
GLOBAL_ACTION_LABELS = {"show_menu": "手势提示", "window_selector": "窗口选择",
                        "scroll_up": "向上滚动", "scroll_down": "向下滚动",
                        "workflow_recommendations": "下一步推荐", "agent": "唤起 / 返回 Agent"}
GLOBAL_BINDINGS_KEY = "gestures/globalBindingsV1"


@dataclass(frozen=True)
class GlobalShortcut:
    gesture: str
    label: str
    shortcut: str

    def __post_init__(self):
        from .app_gestures import normalize_shortcut
        if self.gesture not in GESTURE_LABELS:
            raise ValueError("请选择有效手势")
        if not isinstance(self.label, str) or not self.label.strip() or len(self.label.strip()) > 120:
            raise ValueError("请填写动作名称（最多 120 字）")
        object.__setattr__(self, 'label', self.label.strip())
        object.__setattr__(self, 'shortcut', normalize_shortcut(self.shortcut))

    @property
    def action_id(self):
        return 'custom:' + self.gesture


@dataclass(frozen=True)
class GlobalGestureBindings:
    show_menu: str = "index-pinch"
    switch_mode: str = ""  # Retired; accepted only for old callers/settings.
    window_selector: str = "clench"
    scroll_up: str = "swipe-up"
    scroll_down: str = "swipe-down"
    workflow_recommendations: str = "double-index-pinch"
    agent: str = "double-middle-pinch"
    custom_shortcuts: tuple[GlobalShortcut, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.custom_shortcuts, tuple)
                or any(not isinstance(item, GlobalShortcut) for item in self.custom_shortcuts)):
            raise ValueError("全局自定义快捷键格式无效")
        values = list(self.as_dict().values()) + [item.gesture for item in self.custom_shortcuts]
        if any(not isinstance(value, str) for value in values):
            raise ValueError("请选择有效手势或无")
        values = [value for value in values if value]
        if (any(value not in GESTURE_LABELS for value in values)
                or len(set(values)) != len(values)):
            raise ValueError("每个全局功能需使用不同的有效手势")

    def as_dict(self):
        return {action: getattr(self, action) for action in GLOBAL_ACTION_LABELS}

    @property
    def reserved(self):
        return frozenset(value for value in self.assignments().values() if value)

    def assignments(self):
        return {**self.as_dict(), **{item.action_id: item.gesture for item in self.custom_shortcuts}}

    def labels(self):
        return {**GLOBAL_ACTION_LABELS, **{item.action_id: item.label for item in self.custom_shortcuts}}

    def custom_for(self, gesture):
        return next((item for item in self.custom_shortcuts if item.gesture == gesture), None)

    def action_for(self, gesture):
        return next((action for action, value in self.assignments().items() if value and value == gesture), "")

    def with_action(self, gesture, action):
        if gesture not in GESTURE_LABELS or (action and action not in self.assignments()):
            raise ValueError("请选择有效的全局功能和手势")
        previous = self.action_for(gesture)
        old_gesture = self.assignments().get(action, '')
        if previous == action:
            return self
        assignments = self.assignments()
        if previous:
            assignments[previous] = old_gesture if action else ''
        if action:
            assignments[action] = gesture
        return replace(self, **{key: assignments[key] for key in GLOBAL_ACTION_LABELS},
                       custom_shortcuts=tuple(replace(item, gesture=assignments[item.action_id])
                           for item in self.custom_shortcuts if assignments[item.action_id]))

    def with_custom(self, gesture, label, shortcut):
        custom = GlobalShortcut(gesture, label, shortcut)
        cleared = self.with_action(gesture, '')
        return replace(cleared, custom_shortcuts=(*cleared.custom_shortcuts, custom))

    def to_json(self):
        return json.dumps({**self.as_dict(), 'custom_shortcuts': [
            dict(gesture=item.gesture, label=item.label, shortcut=item.shortcut)
            for item in self.custom_shortcuts]}, ensure_ascii=False)

    @classmethod
    def from_json(cls, value):
        data = json.loads(str(value))
        if (not isinstance(data, dict) or set(data) - (set(GLOBAL_ACTION_LABELS) | {"switch_mode", "custom_shortcuts"})
                or not {"show_menu", "window_selector"} <= set(data)):
            raise ValueError("全局手势设置格式无效")
        data.pop("switch_mode", None)
        customs = data.pop('custom_shortcuts', [])
        if not isinstance(customs, list):
            raise ValueError("全局自定义快捷键格式无效")
        try:
            data['custom_shortcuts'] = tuple(GlobalShortcut(**item) for item in customs)
        except (TypeError, AttributeError) as exc:
            raise ValueError("全局自定义快捷键格式无效") from exc
        # Upgrade missing functions without replacing explicit unbindings or
        # taking a gesture already assigned by the user.
        occupied = {value for key, value in data.items() if key != 'custom_shortcuts' and isinstance(value, str)}
        occupied.update(item.gesture for item in data['custom_shortcuts'])
        for action, gesture in cls().as_dict().items():
            if action not in data:
                data[action] = gesture if gesture not in occupied else ''
                occupied.add(data[action])
        return cls(**data)


def reserve_ring_gestures(bindings: GestureBindings, reserved=RING_RESERVED_GESTURES) -> GestureBindings:
    """Retire only conflicting slots; leave all other deliberate choices intact."""
    values = {action: tuple("" if name in reserved else name for name in names)
              for action, names in bindings.as_dict().items()}
    if not any(values["confirm"]):
        # Keep a confirmation gesture after removing global reservations.
        for action in ("undo", "switch_mode"):
            values[action] = tuple("" if name == "double-tap" else name for name in values[action])
        values["confirm"] = ("double-tap", "")
    if not any(values["end"]):
        # Prefer the default single tap, unless an existing voice action owns it.
        tap_busy = any("tap" in values[action] for action in ("undo", "switch_mode"))
        values["end"] = (next(name for name in values["confirm"] if name), "") if tap_busy else ("tap", "")
    return GestureBindings(**values)


@dataclass(frozen=True)
class GestureBindings:
    confirm: tuple[str, str] = ("double-tap", "")
    end: tuple[str, str] = ("tap", "")
    undo: tuple[str, str] = ("swipe-left", "")
    switch_mode: tuple[str, str] = ("swipe-right", "")

    def __post_init__(self):
        used = {}
        for action in GESTURE_ACTION_LABELS:
            values = getattr(self, action)
            if not isinstance(values, tuple) or len(values) != 2:
                raise ValueError("每项操作只能设置两个手势位置")
            for value in values:
                if not isinstance(value, str) or value and value not in GESTURE_LABELS:
                    raise ValueError("请选择 SDK 支持的手势")
                if not value:
                    continue
                if value in used and {action, used[value]} != {"confirm", "end"}:
                    raise ValueError(f"{GESTURE_LABELS[value]}已用于{GESTURE_ACTION_LABELS[used[value]]}，不能重复分配")
                used[value] = action
        if not any(self.confirm):
            raise ValueError("开始至少保留一个手势")
        if not any(self.end):
            raise ValueError("结束至少保留一个手势")

    def as_dict(self) -> dict[str, list[str]]:
        return {action: list(getattr(self, action)) for action in GESTURE_ACTION_LABELS}

    def with_slot(self, action: str, slot: int, value: str) -> GestureBindings:
        if action not in GESTURE_ACTION_LABELS or slot not in (0, 1):
            raise ValueError("无效的手势设置位置")
        values = list(getattr(self, action))
        values[slot] = value
        return replace(self, **{action: tuple(values)})

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, value: object) -> GestureBindings:
        data = json.loads(str(value))
        if not isinstance(data, dict) or set(data) not in (set(GESTURE_ACTION_LABELS), set(GESTURE_ACTION_LABELS) - {"end"}):
            raise ValueError("手势设置格式无效")
        if any(not isinstance(values, list) for values in data.values()):
            raise ValueError("手势设置格式无效")
        if "end" not in data:
            # Legacy confirmations toggled both directions. Migrate the shipped
            # single/double tap defaults to separate start and end gestures.
            data["end"] = (["tap", ""] if data["confirm"] in (["tap", ""], ["double-tap", ""])
                           else list(data["confirm"]))
        return cls(**{key: tuple(values) for key, values in data.items()})

    @property
    def confirm_hint(self) -> str:
        return " / ".join(
            "双击 tap" if name == "double-tap" else
            "tap" if name == "tap" else GESTURE_LABELS[name]
            for name in self.confirm if name
        )

    @property
    def end_hint(self) -> str:
        return " / ".join(
            "单击 tap" if name == "tap" else
            "双击 tap" if name == "double-tap" else GESTURE_LABELS[name]
            for name in self.end if name
        )


@dataclass(frozen=True)
class BoundGestureEvent:
    """Keep queued GUI actions tied to the assignments used at recognition."""

    event: object
    bindings: GestureBindings
