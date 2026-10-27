"""Eight-direction, release-time selection study; no recognition during capture."""
from __future__ import annotations

import math
import random
import uuid

from selection_capture import SHAPES, SAMPLE_PERIOD_MS, now, validate_trace

SCHEMA = "ring-eight-direction-selection"
PLAN_VERSION = 1
FILE_PREFIX = "selection-eight"
TITLE = "八向选字 · 长度、停顿与转折采集"
INTRO = "先向上，再向八个方向之一滑动；全程触桌，最后抬手。菜单常驻，高亮只表示任务提示。"
TRAIN_ROUNDS = 1
EVALUATION_ROUNDS = 3
PHASE_LABELS = ("探索采集", "独立验证")
SETUP_NOTES_LABEL = "佩戴／基准"
DEFAULT_SETUP_NOTES = "请记录手指、模块朝向、松紧、桌面／纸面方向及是否重新佩戴"
PLAN_HELP = ("36 个两段动作、19 个单段对照、8 个左侧边界／倾斜对照、32 种笔形、5 个删除／轻点对照。\n"
             "沿纸面方向自然完成，不跟屏幕纠正手势；短／长为个人舒适幅度，无固定单位。"
             "本窗口保留原始长度、逐帧时间和停顿，不判断是否识别正确。")
RADIAL_MENU = (
    ("up-left", 0, 0, "↖ 标点 A"), ("up", 0, 1, "↑ 标点 B"), ("up-right", 0, 2, "↗ 标点 C"),
    ("left", 1, 0, "← 候选 4"), ("right", 1, 2, "候选 5 →"),
    ("down-left", 2, 0, "↙ 候选 2"), ("down", 2, 1, "↓ 候选 1"),
    ("down-right", 2, 2, "↘ 候选 3"),
)
DIRECTIONS = (
    ("up", "↑ 上", (0, -1)), ("up-right", "↗ 右上", (1, -1)),
    ("right", "→ 右", (1, 0)), ("down-right", "↘ 右下", (1, 1)),
    ("down", "↓ 下", (0, 1)), ("down-left", "↙ 左下", (-1, 1)),
    ("left", "← 左", (-1, 0)), ("up-left", "↖ 左上", (-1, -1)),
)


def inventory(plan_version=1):
    if plan_version != PLAN_VERSION:
        raise ValueError("不是八向选字采集计划。")
    tasks = []

    def add(label, kind, title, note, **fields):
        tasks.append(dict(label=label, kind=kind, title=title,
                          note=note + " 全程触桌，结束才抬手；做对提示就保存，不按屏幕轨迹规整程度筛选。", **fields))

    for direction, name, vector in DIRECTIONS:
        for variant, variant_name, note in (
            ("natural", "自然连续", "两段用舒适幅度自然连做；不要刻意停顿。"),
            ("short", "短幅连续", "两段都缩短，但仍明确有两段；不要刻意停顿。"),
            ("long", "长幅连续", "两段都加长，在舒适范围内；不要刻意停顿。"),
            ("pause", "中间停顿", "第一段后短暂停住，再做第二段；约 0.2–0.4 秒，只是动作提示，不是识别阈值。"),
        ):
            add(f"select-{direction}-{variant}", "selection", f"上 → {name} · {variant_name}",
                "先竖直向上，再向提示方向移动。" + note, direction=direction,
                menuDirection=direction, targetVector=list(vector), variant=variant, expectedAction="selection")
    for variant, name, note in (
        ("brief-pause", "轻微停顿", "两段向上，中间只轻微停住约 0.05–0.1 秒。"),
        ("long-pause", "明显停顿", "两段向上，中间明显停住约 0.5–0.8 秒。"),
        ("first-short", "前短后长", "先短上滑，再长上滑，连续完成，不刻意停顿。"),
        ("first-long", "前长后短", "先长上滑，再短上滑，连续完成，不刻意停顿。"),
    ):
        add(f"select-up-{variant}", "selection", f"上 → 上 · {name}", note,
            direction="up", menuDirection="up", targetVector=[0, -1], variant=variant, expectedAction="selection")
    for direction, name, vector in DIRECTIONS:
        for variant, name2, note in (("natural", "自然", "只做一次直线滑动，不做前缀上滑。"),
                                     ("long", "长幅", "只做一次较长的直线滑动，不划分为两段。")):
            add(f"single-{direction}-{variant}", "control", f"单次 {name} · {name2}", note,
                direction=direction, targetVector=list(vector), variant=variant, expectedAction="non-selection")
    for variant, name, note in (
        ("brief-pause", "一次长上滑，中间轻停", "意图是一次长上滑，中间轻停约 0.05–0.1 秒再继续，始终向上。"),
        ("long-pause", "一次长上滑，中间明显停住", "意图是一次长上滑，中间停约 0.5–0.8 秒再继续，始终向上。"),
        ("slow", "一次慢上滑", "缓慢完成一次长上滑，允许自然速度波动，不刻意分成两段。"),
    ):
        add(f"single-up-{variant}", "control", name, note,
            direction="up", targetVector=[0, -1], variant=variant, expectedAction="non-selection")
    # Both sides of the left/diagonal boundaries, tagged as boundary probes rather than forced menu truth.
    for angle, name in ((150, "左上侧 30°"), (165, "左上侧 15°"), (195, "左下侧 15°"), (210, "左下侧 30°")):
        vector = [math.cos(math.radians(angle)), -math.sin(math.radians(angle))]
        for prefixed in (False, True):
            add(f"boundary-left-{angle}-{'prefixed' if prefixed else 'single'}", "boundary",
                ("先上滑 → " if prefixed else "单次滑动 · ") + name,
                ("先向上，再沿纸面标记的斜线滑动。" if prefixed else "只沿纸面标记的斜线滑动。") +
                "角度相对水平向左；不用在动作中精确量角，事先在纸上标线。",
                direction="left-boundary", targetVector=vector, variant=str(angle), hasUpPrefix=prefixed,
                expectedAction="boundary-probe")
    for category, shape, note in SHAPES:
        add(f"stroke-{shape}", "stroke", shape, "只写这一笔：" + note,
            category=category, expectedAction="stroke")
    for label, name, note in (
        ("left", "水平左滑删除对照", "自然向左滑一段，不先上滑。"),
        ("left-right", "左 → 右", "水平左滑后右滑，一笔连续完成，不先上滑。"),
        ("left-right-left", "左 → 右 → 左", "连续水平往返三段，不先上滑。"),
        ("right-left-right", "右 → 左 → 右", "连续水平往返三段，不先上滑。"),
        ("tap", "轻点", "轻触桌面后抬起，尽量不移动。"),
    ):
        add(f"other-{label}", "control", name, note, expectedAction="non-selection")
    return tasks


def new_session(participant, phase="train", rounds=TRAIN_ROUNDS, device=""):
    if not participant.strip() or phase not in {"train", "evaluation"} or not 1 <= rounds <= 30:
        raise ValueError("请填写编号，并选择 1–30 轮。")
    session_id = str(uuid.uuid4())
    rng = random.Random(session_id)
    tasks = []
    for rnd in range(1, rounds + 1):
        block = inventory()
        rng.shuffle(block)
        tasks.extend({**task, "round": rnd} for task in block)
    return dict(schema=SCHEMA, version=1, planVersion=1, sessionId=session_id,
                participant=participant.strip(), phase=phase, createdAt=now(), device=device,
                source="ring-sdk-touchpad", setupNotes=DEFAULT_SETUP_NOTES,
                coordinates=dict(xPositive="right", yPositive="down", unit="sdk-displacement",
                                 samplePeriodMs=SAMPLE_PERIOD_MS, normalization="none"),
                menu=[dict(direction=d, row=r, column=c, label=label) for d, r, c, label in RADIAL_MENU],
                purpose="Explore eight directions, continuous up-up, pauses, lengths and turns; recognition only after release.",
                tasks=tasks, samples=[], rejected=[], interruptions=[], draft=None)


def validate_session(data):
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("version") != 1
            or data.get("planVersion") != 1 or data.get("phase") not in {"train", "evaluation"}
            or not isinstance(data.get("participant"), str) or not data["participant"].strip()
            or not isinstance(data.get("sessionId"), str) or not isinstance(data.get("setupNotes"), str)):
        raise ValueError("不是八向选字记录；旧横移采集文件请用原采集器续采。")
    known = {t["label"]: t for t in inventory()}
    tasks, samples = data.get("tasks"), data.get("samples")
    size = len(known)
    if not isinstance(tasks, list) or not tasks or len(tasks) % size or len(tasks) > 30*size:
        raise ValueError("八向采集计划不完整。")
    for i, task in enumerate(tasks):
        if not isinstance(task, dict) or task.get("label") not in known or task != {**known[task["label"]], "round": i//size+1}:
            raise ValueError("任务标签与计划不一致。")
    for start in range(0, len(tasks), size):
        if {t["label"] for t in tasks[start:start+size]} != set(known):
            raise ValueError("轮次内任务重复或缺失。")
    if not isinstance(samples, list) or len(samples) > len(tasks):
        raise ValueError("样本数量错误。")
    for i, sample in enumerate(samples):
        if not isinstance(sample, dict) or sample.get("taskIndex") != i or sample.get("task") != tasks[i]:
            raise ValueError("保存样本与任务不一致。")
        validate_trace(sample)
    if data.get("draft") is not None:
        if len(samples) == len(tasks):
            raise ValueError("已完成记录仍有草稿。")
        validate_trace(data["draft"])
    return data
