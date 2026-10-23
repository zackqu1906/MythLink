#!/usr/bin/env python3
"""Recompute a saved manual or activity calibration through production Swift code."""
from __future__ import annotations
import argparse
import json
import math
import statistics
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = '''import Foundation
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let record = try JSONSerialization.jsonObject(with: data) as! [String: Any]
let rows = record["samples"] as! [Double]
let baseline: Double
if record["version"] as? Int == 2 {
 baseline = ActivityCalibration.Capture(readings: rows, duration: record["duration_seconds"] as! Double,
                                       sources: record["sources"] as! [String]).baseline
} else {
 var c = ProximityCalibration()
 c.begin(now: 0)
 for (index, rssi) in rows.enumerated() {
  c.observe(rssi, now: Double(index) * 9.5 / Double(rows.count - 1))
 }
 guard c.finish(now: 10), let saved = c.baseline else { exit(2) }
 baseline = saved
}
guard let result = ProximityCalibration(baseline: baseline).thresholds() else { exit(2) }
print("baseline \\(result.baseline), lock \\(result.lockRSSI), near boundary \\(result.unlockRSSI)")
print("calibration samples: \\(rows.count)")
'''

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('calibration', type=Path, help='导出的 proximity/calibrations/<UUID> JSON')
    args = parser.parse_args()
    record = json.loads(args.calibration.read_text())
    rows = record.get('samples', []) if isinstance(record, dict) else []
    duration = record.get('duration_seconds') if isinstance(record, dict) else None
    manual = isinstance(record, dict) and record.get('version') == 1 and duration == 10
    activity = (isinstance(record, dict) and record.get('version') == 2 and record.get('method') == 'activity'
                and type(duration) in (int, float) and math.isfinite(duration) and 2 <= duration <= 120
                and isinstance(record.get('sources'), list) and bool(record['sources'])
                and all(s in ('keyboardMouse', 'gestures', 'voice') for s in record['sources']))
    minimum = 5 if activity else 10
    if (not isinstance(rows, list) or not minimum <= len(rows) <= 100
            or any(type(v) not in (float, int) or not math.isfinite(v) or not -100 <= v <= -20 for v in rows)
            or not (manual or activity)
            or record.get('baseline') != statistics.median(rows)):
        parser.error('不是有效的手动或操作采样校准记录；普通信号日志不能用来建立桌前基线。')
    with tempfile.TemporaryDirectory(prefix='proximity-replay-') as temporary:
        root = Path(temporary)
        (root / 'main.swift').write_text(HARNESS)
        subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-module-cache-path', str(root / 'module-cache'),
                        str(ROOT / 'native/ProxiMicPresence/Calibration.swift'), str(root / 'main.swift'),
                        '-o', str(root / 'replay')], check=True)
        subprocess.run([str(root / 'replay'), str(args.calibration.resolve())], check=True)

if __name__ == '__main__': main()
