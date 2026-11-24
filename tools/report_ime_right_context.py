"""List the latest measured caret distances without exposing any editor text."""

import argparse
import json
from datetime import datetime
from pathlib import Path


STATUS = {
    "aligned": "已在目标",
    "aligned_offsets": "光标快检通过",
    "before_boundary": "目标在右侧",
    "past_boundary": "已越过目标",
    "text_mismatch": "正文不匹配",
    "selection_active": "仍有选区",
    "invalid_boundary": "字符边界无效",
    "unavailable": "无法读取",
    "aligned_prefix_only": "仅左侧吻合",
    "right_range_unconfirmed": "右侧范围未确认",
    "paragraph_pattern": "符合段间空行规律",
}

MOVE_STATUS = {
    "requested": "已请求",
    "at_end": "已到末尾",
    "end_unconfirmed": "未确认到末尾",
    "ack_timeout": "发键回执超时",
    "delivery_failed": "发键失败",
}

def recent_samples(directory, app, limit):
    samples = []
    for path in directory.glob("build*-*.jsonl"):
        if "-read-" in path.name:
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if '"right_check"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # An active input method may still be appending.
                if (event.get("application") == app and event.get("event") in {"confirmed", "failed"}
                        and "right_check" in event):
                    samples.append(event)
    return sorted(samples, key=lambda event: event["timestamp"], reverse=True)[:limit]


def main():
    parser = argparse.ArgumentParser(description="查看近期输入法编辑的光标右侧测量")
    parser.add_argument("--trace-dir", type=Path, default=Path.home() / "Library/Logs/Mythlink/IMESelection")
    parser.add_argument("--app", default="com.openai.codex")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须为正数")
    samples = recent_samples(args.trace_dir, args.app, args.limit)
    if not samples:
        print("没有该应用的右侧实测记录。")
        return
    print("本地时间 | 提交长度 | 换行 | 段间空行 | AX光标 | 右侧总长 | 需右移(UTF-16) | 需右移(字符簇) | 右侧判断 | 最近一次光标取样(ms) | 请求右键次数 | 主程序回执 | 移动后右侧(UTF-16) | 光标结果 | 编辑校验")
    for event in samples:
        when = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).astimezone()
        values = [event.get(key, "—") for key in ("model_characters", "format_line_breaks",
                  "format_blank_lines", "right_ax_caret", "right_available_utf16",
                  "right_move_utf16", "right_move_graphemes")]
        move = event.get("paragraph_move_status")
        if move:
            values[-2] = event.get("paragraph_before_right_utf16", "—")
            values[-1] = event.get("postwrite_key_count", "—")
        ack = "已收到" if event.get("postwrite_key_acknowledged") else "未收到"
        elapsed = event.get("caret_sample_ms")
        print(when.strftime("%m-%d %H:%M:%S"), *values, STATUS.get(event["right_check"], "未知"),
              f"{elapsed:.2f}" if isinstance(elapsed, (int, float)) else "—",
              event.get("postwrite_key_count", "—") if move else "—", ack if move else "—",
              event.get("paragraph_after_right_utf16", "—"), MOVE_STATUS.get(move, "—"),
              "通过" if event["event"] == "confirmed" else event.get("failure_code", "未通过"), sep=" | ")


if __name__ == "__main__":
    main()
