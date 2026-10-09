"""Immutable, validated gesture assignments shared by the GUI and audio worker."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json


GESTURE_LABELS = {
    "tap": "轻点（tap）",
    "snap": "弹指（snap）",
    "swipe-left": "左滑",
    "swipe-down": "下滑",
    "swipe-right": "右滑",
    "swipe-up": "上滑",
    "clench": "握拳",
    "index-pinch": "食指捏合",
    "middle-pinch": "中指捏合",
    "circle-clockwise": "顺时针画圈",
    "circle-counterclockwise": "逆时针画圈",
}
GESTURE_ACTION_LABELS = {"confirm": "确认", "undo": "撤销", "switch_mode": "类型转换"}
RING_RESERVED_GESTURES = frozenset({"index-pinch", "middle-pinch", "clench", "swipe-down", "swipe-up"})
VOICE_GESTURE_GROUP = frozenset({"tap", "swipe-left", "swipe-right", "swipe-up", "swipe-down"})
GLOBAL_ACTION_LABELS = {"show_menu": "手势提示", "switch_mode": "切换交互模式", "window_selector": "窗口选择"}
GLOBAL_BINDINGS_KEY = "gestures/globalBindingsV1"


@dataclass(frozen=True)
class GlobalGestureBindings:
    show_menu: str = "index-pinch"
    switch_mode: str = "middle-pinch"
    window_selector: str = "clench"

    def __post_init__(self):
        values = list(self.as_dict().values())
        if any(not isinstance(value, str) for value in values):
            raise ValueError("请选择有效手势或无")
        values = [value for value in values if value]
        if (any(value not in GESTURE_LABELS or value in VOICE_GESTURE_GROUP for value in values)
                or len(set(values)) != len(values)):
            raise ValueError("每个全局功能需使用不同的有效手势")

    def as_dict(self):
        return {action: getattr(self, action) for action in GLOBAL_ACTION_LABELS}

    @property
    def reserved(self):
        return frozenset(value for value in self.as_dict().values() if value)

    def action_for(self, gesture):
        return next((action for action, value in self.as_dict().items() if value and value == gesture), "")

    def to_json(self):
        return json.dumps(self.as_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, value):
        data = json.loads(str(value))
        if not isinstance(data, dict) or set(data) != set(GLOBAL_ACTION_LABELS):
            raise ValueError("全局手势设置格式无效")
        return cls(**data)


def reserve_ring_gestures(bindings: GestureBindings, reserved=RING_RESERVED_GESTURES) -> GestureBindings:
    """Retire only conflicting slots; leave all other deliberate choices intact."""
    values = {action: tuple("" if name in reserved else name for name in names)
              for action, names in bindings.as_dict().items()}
    if not any(values["confirm"]):
        # There must always be a way to start/end the sentence. If tap was
        # previously reused for undo/edit, release that slot for confirmation.
        values = {action: tuple("" if name == "tap" else name for name in names)
                  for action, names in values.items()}
        values["confirm"] = ("tap", "")
    return GestureBindings(**values)


@dataclass(frozen=True)
class GestureBindings:
    confirm: tuple[str, str] = ("tap", "")
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
                if value in used:
                    raise ValueError(f"{GESTURE_LABELS[value]}已用于{GESTURE_ACTION_LABELS[used[value]]}，不能重复分配")
                used[value] = action
        if not any(self.confirm):
            raise ValueError("确认至少保留一个手势，用于结束本句语音")

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
        if not isinstance(data, dict) or set(data) != set(GESTURE_ACTION_LABELS):
            raise ValueError("手势设置格式无效")
        if any(not isinstance(values, list) for values in data.values()):
            raise ValueError("手势设置格式无效")
        return cls(**{key: tuple(values) for key, values in data.items()})

    @property
    def confirm_hint(self) -> str:
        return " / ".join(
            "tap" if name == "tap" else GESTURE_LABELS[name]
            for name in self.confirm if name
        )


@dataclass(frozen=True)
class BoundGestureEvent:
    """Keep queued GUI actions tied to the assignments used at recognition."""

    event: object
    bindings: GestureBindings
