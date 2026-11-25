"""Gesture-to-action configuration shared by automatic and manual voice modes.

This maps user bindings, not spoken intent. Automatic mode still uses configured
start/end/cancel actions; Clef decides the text task after the utterance finishes.
App/global profiles remain independent.
"""
from ...gestures.gesture_settings import GestureBindings, GESTURE_LABELS


VOICE_ACTIONS = (
    ("start_end", "开始 / 定稿", ("confirm", "end")),
    ("confirm", "开始语音", ("confirm",)),
    ("end", "结束语音", ("end",)),
    ("polish", "润色当前句", ("polish",)),
    ("switch_mode", "语音修改", ("switch_mode",)),
    ("undo", "取消 / 撤回", ("undo",)),
    ("send", "发送文本", ("send",)),
)

VOICE_GESTURES = (
    ("tap", "点击"), ("double-tap", "双击"),
    ("swipe-up", "上滑"), ("swipe-down", "下滑"),
    ("swipe-left", "左滑"), ("swipe-right", "右滑"),
    ("index-pinch", "食拇捏合"), ("double-index-pinch", "双次食拇捏合"),
    ("middle-pinch", "中拇捏合"), ("double-middle-pinch", "双次中拇捏合"),
    ("clench", "握拳"), ("snap", "响指"),
    ("circle-clockwise", "顺时针"), ("circle-counterclockwise", "逆时针"),
)

RINGO_START_END = ("tap", "index-pinch")


def normalize_bindings(bindings):
    """Keep the first legacy choice and preserve the Ringo start/end pair."""
    return GestureBindings(**{
        key: RINGO_START_END if key in {"confirm", "end"} and tuple(values) == RINGO_START_END
        else (next((value for value in values if value), ""), "")
        for key, values in bindings.as_dict().items()
    })


def action_for(bindings, gesture):
    assigned = tuple(key for key, values in bindings.as_dict().items() if gesture and gesture in values)
    action = "start_end" if assigned == ("confirm", "end") else assigned[0] if assigned else ""
    return next((dict(id="voice:" + key, label=label, voiceAction=key, shortcut="", path="语音输入")
                 for key, label, _ in VOICE_ACTIONS if key == action), None)


def with_action(bindings, gesture, action):
    if gesture not in GESTURE_LABELS and (gesture or not action):
        raise ValueError("请选择有效的手势")
    targets = next((keys for key, _, keys in VOICE_ACTIONS if key == action), None)
    if action and targets is None:
        raise ValueError("请选择有效的语音功能")
    targets = targets or ()
    values = bindings.as_dict()
    if gesture:
        for key, slots in values.items():
            values[key] = [gesture, ""] if key in targets else [value for value in slots if value != gesture]
    else:
        for key in targets:
            values[key] = [value for value in values[key]
                           if (action_for(bindings, value) or {}).get("voiceAction") != action]
    return normalize_bindings(GestureBindings(**{key: tuple(slots) for key, slots in values.items()}))
