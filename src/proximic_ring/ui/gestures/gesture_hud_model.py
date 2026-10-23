"""Build a bounded hint view from effective mappings, without changing routing."""
from __future__ import annotations

from ...gestures.gesture_settings import GESTURE_LABELS, GlobalGestureBindings

MAX_ORBS = 6
GESTURE_ORDER = ("swipe-left", "swipe-right", "swipe-up", "swipe-down", "double-tap", "tap", "snap",
                 "circle-clockwise", "circle-counterclockwise", "clench", "index-pinch", "middle-pinch",
                 "double-index-pinch", "double-middle-pinch")
SYMBOLS = {"swipe-left": "left", "swipe-right": "right", "swipe-up": "up", "swipe-down": "down",
           "double-tap": "tap", "tap": "tap", "snap": "snap", "circle-clockwise": "clockwise",
           "circle-counterclockwise": "counterclockwise", "clench": "apps",
           "index-pinch": "pinch", "middle-pinch": "pinch",
           "double-index-pinch": "pinch", "double-middle-pinch": "pinch"}


def gesture_label(key):
    return {"double-tap": "双击 Tap", "tap": "Tap", "snap": "响指", "circle-clockwise": "顺时针", "circle-counterclockwise": "逆时针"}.get(
        key, GESTURE_LABELS.get(key, key).split("（")[0])


def hint_view(mode, actions=None, global_bindings=None, global_labels=None):
    """Show the effective focus route; application bindings override global defaults."""
    from ...gestures.gesture_settings import GLOBAL_ACTION_LABELS
    labels = GLOBAL_ACTION_LABELS if global_labels is None else global_labels
    bindings = GlobalGestureBindings().as_dict() if global_bindings is None else global_bindings
    rows = actions or []
    context = rows[0] if rows and mode != "input" else {}
    scene = context.get("scene", "")
    effective = {}
    if mode == "input":
        for key, label, symbol in [("swipe-up", "发送", "up"), ("swipe-down", "润色本句", "down"),
                ("double-tap", "开始语音", "mic"), ("tap", "结束语音", "mic"),
                ("swipe-right", "语音编辑", "edit"), ("swipe-left", "撤销", "undo")]:
            effective[key] = (label, "mode", symbol)
    else:
        effective["double-tap"] = ("开始语音", "mode", "mic")
        for action, key in bindings.items():
            if key in SYMBOLS and action in labels:
                effective[key] = (labels[action], "global", SYMBOLS[key])
        by_label = {label: key for key, label in GESTURE_LABELS.items()}
        for row in sorted(rows, key=lambda row: row.get("scope") != "global"):
            key = row.get("key") or by_label.get(row.get("gesture"), "")
            if key in SYMBOLS and row.get("action") and row["action"] != "未绑定":
                effective[key] = (row["action"], row.get("scope", "application"), SYMBOLS[key])
    orbs = [dict(key=key, symbol=effective[key][2], label=gesture_label(key),
                 action=effective[key][0], scope=effective[key][1], inputField=False)
            for key in GESTURE_ORDER if key in effective]
    if mode != "input":
        orbs.sort(key=lambda row: row["scope"] not in {"application", "scene"})
    return dict(orbs=orbs[:MAX_ORBS], overflow=len(orbs[MAX_ORBS:]),
                contextLabel=" · ".join(filter(None, (context.get("application"), context.get("sceneLabel")))),
                voiceDisabled=False, sceneActive=bool(scene),
                modeTitle=context.get("sceneLabel", "").split(" · ")[0] if scene else
                          ("输入中" if mode == "input" else "应用手势"))
