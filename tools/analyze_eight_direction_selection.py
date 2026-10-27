"""Offline descriptive features, with label-blind split proposals; not a classifier."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import statistics
import sys

from eight_direction_selection import validate_session
from selection_capture import integration_error


def angle(dx, dy):
    return math.degrees(math.atan2(dy, dx)) if math.hypot(dx, dy) > 1e-12 else None


def delta(a, b):
    return None if a is None or b is None else (a-b+180) % 360-180


def features(trace):
    """Never consume task labels to locate a turn or low-speed interval."""
    points = trace["points"]
    cumulative = [0.]
    speeds = []
    for a, b in zip(points, points[1:]):
        distance = math.hypot(b["x"]-a["x"], b["y"]-a["y"])
        cumulative.append(cumulative[-1]+distance)
        dt = b["t"]-a["t"]
        if dt > 0:
            speeds.append(distance/dt)
    length = cumulative[-1]
    total_ms = points[-1]["t"]-points[0]["t"]
    net = math.hypot(points[-1]["x"]-points[0]["x"], points[-1]["y"]-points[0]["y"])
    result = dict(pathLength=length, durationMs=total_ms,
                  netDistance=net, straightness=net/length if length else None,
                  netAngleDeg=angle(points[-1]["x"]-points[0]["x"], points[-1]["y"]-points[0]["y"]),
                  integrationError=integration_error(trace), geometrySplit=None, lowSpeedSplit=None,
                  pauseWindows={})
    if length <= 1e-12 or total_ms <= 0:
        return result

    def split(index):
        a, pivot, b = points[0], points[index], points[-1]
        first_length, second_length = cumulative[index], length-cumulative[index]
        first_net = math.hypot(pivot["x"]-a["x"], pivot["y"]-a["y"])
        second_net = math.hypot(b["x"]-pivot["x"], b["y"]-pivot["y"])
        first_angle = angle(pivot["x"]-a["x"], pivot["y"]-a["y"])
        second_angle = angle(b["x"]-pivot["x"], b["y"]-pivot["y"])
        return dict(index=index, timeMs=pivot["t"]-a["t"], firstLength=first_length,
                    secondLength=second_length, firstAngleDeg=first_angle, secondAngleDeg=second_angle,
                    firstUpErrorDeg=delta(first_angle, -90), turnDeg=delta(second_angle, first_angle),
                    firstStraightness=first_net/first_length if first_length else None,
                    secondStraightness=second_net/second_length if second_length else None,
                    chordGain=(first_net+second_net-net)/length)

    candidates = [i for i in range(1, len(points)-1) if .1*length <= cumulative[i] <= .9*length
                  and .1*total_ms <= points[i]["t"]-points[0]["t"] <= .9*total_ms]
    proposals = [split(i) for i in candidates]
    if proposals:
        # Tie-break near the temporal middle; a straight stroke still has a proposal,
        # so chordGain must be inspected before interpreting it as a real turn.
        result["geometrySplit"] = max(proposals, key=lambda s: (round(s["chordGain"], 12),
                                               -abs(s["timeMs"]-total_ms/2)))
    ordered = sorted(speeds)
    reference_speed = ordered[int(.75*(len(ordered)-1))] if ordered else 0.
    for window_ms in (30, 60, 100):
        windows = []
        # Exclude start/end slowing; measure cumulative path, not endpoint distance,
        # so a quick out-and-back is never counted as a pause.
        j = 0
        for i, p in enumerate(points[:-1]):
            if p["t"]-points[0]["t"] < .15*total_ms:
                continue
            j = max(j, i+1)
            while j < len(points) and points[j]["t"]-p["t"] < window_ms:
                j += 1
            if j >= len(points) or points[j]["t"]-points[0]["t"] > .85*total_ms:
                continue
            dt = points[j]["t"]-p["t"]
            windows.append(dict(startIndex=i, endIndex=j, startMs=p["t"]-points[0]["t"],
                                durationMs=dt, speed=(cumulative[j]-cumulative[i])/dt))
        if windows:
            best = min(windows, key=lambda w: (w["speed"], abs(w["startMs"]+w["durationMs"]/2-total_ms/2)))
            result["pauseWindows"][str(window_ms)] = dict(best,
                speedRatio=best["speed"]/reference_speed if reference_speed else None)
            if window_ms == 60:
                midpoint = (points[best["startIndex"]]["t"]+points[best["endIndex"]]["t"])/2
                index = min(range(best["startIndex"], best["endIndex"]+1), key=lambda i: abs(points[i]["t"]-midpoint))
                result["lowSpeedSplit"] = split(index)
    return result


def analyze(data):
    validate_session(data)
    rows = []
    for sample in data["samples"]:
        task = sample["task"]
        f = features(sample)
        # Labels are attached only after feature extraction.
        row = dict(taskIndex=sample["taskIndex"], label=task["label"], kind=task["kind"],
                   direction=task.get("direction"), variant=task.get("variant"), round=task["round"], **f)
        vector = task.get("targetVector")
        if vector:
            target = angle(*vector)
            row["targetAngleDeg"] = target
            for name in ("geometrySplit", "lowSpeedSplit"):
                s = row[name]
                row[name+"TargetErrorDeg"] = delta(s["secondAngleDeg"], target) if s else None
            row["netTargetErrorDeg"] = delta(row["netAngleDeg"], target)
        rows.append(row)
    return dict(schema="ring-eight-direction-features", version=1, sessionId=data["sessionId"],
                participant=data["participant"], phase=data["phase"], setupNotes=data["setupNotes"],
                confirmedCount=len(rows), plannedCount=len(data["tasks"]), retryCount=len(data["rejected"]),
                draftExcluded=data.get("draft") is not None, interruptions=data["interruptions"],
                method=dict(split="maximize two-chord gain; internal 10%-90% of time and path",
                            pause="minimum internal path speed over 30/60/100ms; compare to per-contact p75 speed",
                            lowSpeedSplit="midpoint of the slowest internal 60ms window",
                            classifier="none; no angle, length, pause or turn decision thresholds fitted"), rows=rows)


def describe(values):
    values = [v for v in values if v is not None]
    if not values:
        return "—"
    return f"{min(values):.3g} / {statistics.median(values):.3g} / {max(values):.3g}"


def markdown(report):
    lines = ["# 八向选字采集：长度、停顿与转折", "",
             f"已确认 {report['confirmedCount']} / {report['plannedCount']} 笔；重采 {report['retryCount']} 笔。",
             f"阶段：{report['phase']}；会话：{report['sessionId']}；佩戴／基准：{report['setupNotes']}", "",
             "这里只描述观测特征，不报告识别准确率。重采、未确认草稿不作为真值；缺帧等中断保留在 JSON。",
             "上—上若没有可观测的停顿或速度变化，可与一次长上滑相同；停顿对照也可能相同，意图标签不能创造轨迹证据。", "",
             "## 各动作的特征分布", "",
             "每格为最小 / 中位 / 最大。路径是 SDK 原始单位；停顿比值越低表示中间越慢，不等于已识别停顿。",
             "几何分段总会提出一个位置，即使直线也如此；chordGain 接近零时不要把该位置当成可靠转折。", "",
             "| 动作 | 笔数 | 路径 | 时长 ms | 内部 60ms 速度比 | chordGain | 转角绝对值 ° |",
             "| --- | ---: | --- | --- | --- | --- | --- |"]
    groups = defaultdict(list)
    for row in report["rows"]:
        groups[row["label"]].append(row)
    for label, rows in sorted(groups.items()):
        ratios = [r["pauseWindows"].get("60", {}).get("speedRatio") for r in rows]
        gains = [(r["geometrySplit"] or {}).get("chordGain") for r in rows]
        turns = [abs(r["geometrySplit"]["turnDeg"]) if r["geometrySplit"] and r["geometrySplit"]["turnDeg"] is not None else None for r in rows]
        lines.append(f"| {label} | {len(rows)} | {describe([r['pathLength'] for r in rows])} | "
                     f"{describe([r['durationMs'] for r in rows])} | {describe(ratios)} | {describe(gains)} | {describe(turns)} |")
    lines += ["", "## 八向第二段方向", "",
              "角度使用屏幕坐标：右 0°、下 90°、左 ±180°、上 -90°。"
              "先检查前段是否向上，再检查第二段；整个动作的起终点方向不能代替第二段方向。",
              "以下为几何提议分段后的角度，不是分类结果；上—上及弱折角应结合低速分段对照。",
              "| 方向 | 笔数 | 第一段偏离上 °（绝对值） | 第二段相对提示误差 °（绝对值） |",
              "| --- | ---: | --- | --- |"]
    directions = defaultdict(list)
    for row in report["rows"]:
        if row["kind"] == "selection":
            directions[row["direction"]].append(row)
    for direction, rows in sorted(directions.items()):
        first = [(r["geometrySplit"] or {}).get("firstUpErrorDeg") for r in rows]
        second = [r.get("geometrySplitTargetErrorDeg") for r in rows]
        lines.append(f"| {direction} | {len(rows)} | {describe([abs(v) if v is not None else None for v in first])} | "
                     f"{describe([abs(v) if v is not None else None for v in second])} |")
    lines += ["", "## 下一步检验", "",
              "1. 比较左、左上、左下的第二段角度分布与边界探测，不凭八等分扇区预设可靠率。",
              "2. 比较自然／短／长上—上与单次上滑，再比较轻停／明显停顿及停顿单次上滑对照。",
              "3. 比较转角、chordGain、前后段长度和30/60/100ms低速窗口；JSON/CSV保留每笔特征。",
              "4. 用探索会话选特征及阈值，冻结规则后在重新佩戴的独立验证会话报告八向混淆、完整手势正确率和普通笔画误触发率。",
              "5. 一轮是可行性探索，不能证明99%准确率；SDK长度并非实测厘米，不能从提示停顿时长当作实际测量。", ""]
    return "\n".join(lines)


def write_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / f"selection-eight-features-{report['sessionId']}"
    stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    stem.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    flattened = []
    for row in report["rows"]:
        flat = {k: v for k, v in row.items() if k not in ("geometrySplit", "lowSpeedSplit", "pauseWindows")}
        for name in ("geometrySplit", "lowSpeedSplit"):
            flat.update({name+"_"+k: v for k, v in (row[name] or {}).items()})
        for ms, window in row["pauseWindows"].items():
            flat.update({"pause"+ms+"_"+k: v for k, v in window.items()})
        flattened.append(flat)
    with stem.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = sorted({key for row in flattened for key in row}) or ["taskIndex", "label"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flattened)
    return stem.with_suffix(".md")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "diagnostics" / "selection-eight")
    args = parser.parse_args()
    report = analyze(json.loads(args.input.read_text(encoding="utf-8")))
    print(write_report(report, args.output_dir))
    print(f"已确认 {report['confirmedCount']} 笔；这里只提取特征，不计算识别准确率。")


if __name__ == "__main__":
    main()
