"""Summarize existing stroke-test logs without changing input decisions.

Temporal neighbors are investigation leads, not ground-truth tap labels.
Run with the same Python runtime used by the desktop application.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path


def analyze(path: Path, window_ms: float = 250):
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        row["_line"] = line_no
        row["_time"] = datetime.fromisoformat(row["time"]).timestamp()
        rows.append(row)
    strokes = [row for row in rows if row["kind"] == "stroke"]
    taps = [row for row in rows if row["kind"] == "gesture" and row.get("class_id") == 5]
    neighbors = []
    for tap in taps:
        if not strokes:
            continue
        stroke = min(strokes, key=lambda row: abs(row["_time"] - tap["_time"]))
        delta_ms = (stroke["_time"] - tap["_time"]) * 1000
        if abs(delta_ms) > window_ms:
            continue
        neighbors.append({
            "firmware_tap_time": tap["time"], "tap_line": tap["_line"],
            "stroke_time": stroke["time"], "stroke_line": stroke["_line"],
            "stroke_minus_tap_ms": round(delta_ms, 2),
            "category": stroke.get("result", {}).get("category"),
            "shape": stroke.get("result", {}).get("shape"),
            "points": len(stroke.get("points", [])),
        })
    return {
        "source": str(path.resolve()),
        "synthetic": next((row.get("synthetic") for row in rows if row["kind"] == "test_session"), None),
        "event_counts": dict(Counter(row["kind"] for row in rows)),
        "firmware_taps": len(taps),
        "candidate_window_ms": window_ms,
        "nearby_strokes": neighbors,
        "limitations": [
            "Log times reflect UI delivery, not synchronized device action times.",
            "Nearby firmware taps and strokes may be separate intentional actions.",
            "SDK click counts and firmware tap counts cannot be compared as accuracy rates.",
            "Existing contact_samples omit raw pre-gain velocities and click verdict reasons.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--window-ms", type=float, default=250,
                        help="Investigation window only; never a production arbitration threshold")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.window_ms < 0:
        parser.error("--window-ms must be non-negative")
    payload = {"reports": [analyze(path, args.window_ms) for path in args.logs]}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
