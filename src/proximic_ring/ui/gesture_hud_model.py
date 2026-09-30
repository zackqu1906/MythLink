"""Build a bounded hint view from effective mappings, without changing routing."""
from __future__ import annotations

from ..gesture_settings import GESTURE_LABELS, GlobalGestureBindings

MAX_ORBS = 6
GESTURE_ORDER = ("swipe-left", "swipe-right", "swipe-up", "swipe-down", "tap", "snap",
                 "circle-clockwise", "circle-counterclockwise", "clench", "index-pinch", "middle-pinch")
SYMBOLS = {"swipe-left": "left", "swipe-right": "right", "swipe-up": "up", "swipe-down": "down",
           "tap": "tap", "snap": "snap", "circle-clockwise": "clockwise",
           "circle-counterclockwise": "counterclockwise", "clench": "apps",
           "index-pinch": "pinch", "middle-pinch": "pinch"}


def gesture_label(key):
    return {"tap": "Tap", "snap": "响指", "circle-clockwise": "顺时针", "circle-counterclockwise": "逆时针"}.get(
        key, GESTURE_LABELS.get(key, key).split("（")[0])


def hint_view(mode, actions=None, global_bindings=None):
    """Scenes replace mode hints; regular application bindings precede them."""
    bindings = global_bindings or GlobalGestureBindings().as_dict()
    rows = actions or []
    context = rows[0] if rows else {}
    scene = context.get("scene", "")
    scene_active = bool(scene)
    voice_disabled = bool(context.get("voiceDisabled"))
    global_keys = set(bindings.values())
    by_label = {label: key for key, label in GESTURE_LABELS.items()}
    scoped = {}
    for row in rows:
        key = row.get("key") or by_label.get(row.get("gesture"), "")
        if (key in SYMBOLS and key not in global_keys and row.get("scope") not in {"global", "unbound"}
                and row.get("action") and row["action"] != "未绑定"):
            scoped[key] = row["action"]
    orbs = [dict(key=key, symbol=SYMBOLS[key], label=gesture_label(key), action=scoped[key],
                 scope="scene" if scene_active else "application", inputField=False)
            for key in GESTURE_ORDER if key in scoped]
    defaults = ([("swipe-up", "发送", "up"), ("tap", "语音开始 / 结束", "mic"),
                 ("swipe-right", "语音编辑", "edit"), ("swipe-down", "选择输入框", "down"),
                 ("swipe-left", "撤销", "undo")] if mode == "input" else
                [("swipe-up", "向上滚动", "up"), ("swipe-down", "向下滚动", "down")])
    if not scene_active and not voice_disabled:
        for key, label, symbol in defaults:
            if key not in scoped and key not in global_keys:
                orbs.append(dict(key=key, symbol=symbol,
                                 label="Tap + 右滑" if key == "swipe-right" else gesture_label(key), action=label,
                                 scope="mode", inputField=key == "swipe-down" and mode == "input"))
    # Also in the permanent footer, so capping the orbit never hides the entry.
    if not scene_active:
        orbs.append(dict(key=bindings["window_selector"], symbol="apps",
                         label=gesture_label(bindings["window_selector"]), action="窗口选择",
                         scope="global", inputField=False))
    return dict(orbs=orbs[:MAX_ORBS], overflow=sum(row["scope"] != "global" for row in orbs[MAX_ORBS:]),
                contextLabel=" · ".join(filter(None, (context.get("application"), context.get("sceneLabel")))),
                voiceDisabled=voice_disabled, sceneActive=scene_active,
                modeTitle=context.get("sceneLabel", "").split(" · ")[0] if scene_active else
                          ("输入模式" if mode == "input" else "操作模式"))
