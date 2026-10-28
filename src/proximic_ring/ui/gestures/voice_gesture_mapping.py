"""Gesture-first presentation adapter for the existing voice binding settings.

Each gesture chooses one voice operation; an operation may use many gestures.
App/global profiles remain independent, and start/end may share a gesture.
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


def action_for(bindings, gesture):
    assigned = tuple(key for key, values in bindings.as_dict().items() if gesture and gesture in values)
    action = "start_end" if assigned == ("confirm", "end") else assigned[0] if assigned else ""
    return next((dict(id="voice:" + key, label=label, voiceAction=key, shortcut="", path="语音输入")
                 for key, label, _ in VOICE_ACTIONS if key == action), None)


def with_action(bindings, gesture, action):
    if gesture not in GESTURE_LABELS:
        raise ValueError("请选择有效的手势")
    targets = next((keys for key, _, keys in VOICE_ACTIONS if key == action), None)
    if action and targets is None:
        raise ValueError("请选择有效的语音功能")
    targets = targets or ()
    values = bindings.as_dict()
    for key, slots in values.items():
        if key not in targets:
            values[key] = ["" if value == gesture else value for value in slots]
    for key in targets:
        if gesture not in values[key]:
            if "" not in values[key]:
                values[key].append(gesture)
            else:
                values[key][values[key].index("")] = gesture
    return GestureBindings(**{key: tuple(slots) for key, slots in values.items()})
