import Foundation
var count = 0
func check(_ value: @autoclosure () -> Bool, _ label: String) {
    count += 1; if !value() { fatalError(label) }
}
var c = ProximityCalibration()
for v in [-60.0, -70, -80] { c.observe(v, now: 0) }
check(c.samples == 0 && !c.qualified, "never learn automatically")
c.begin(now: 100)
for v in [Double.nan, .infinity, 127, -120] { c.observe(v, now: 100) }
check(c.samples == 0, "invalid samples do not count")
for i in 0..<20 { c.observe(i == 0 ? -95 : -60, now: 100 + Double(i) * 0.5) }
check(!c.finish(now: 109.9) && !c.qualified, "full ten seconds required")
check(c.finish(now: 110), "complete ten-second capture")
check(c.baseline == -60, "median rejects isolated low reading")
check(c.thresholds()?.lockRSSI == -68 && c.thresholds()?.unlockRSSI == -64, "saved margins")
for i in 0..<100 { c.observe(-90, now: 111 + Double(i)) }
check(c.baseline == -60 && c.samples == 20, "frozen until explicit recalibration")
var restored = ProximityCalibration(baseline: c.baseline)
restored.observe(-90, now: 0)
check(restored.baseline == -60 && restored.samples == 0, "reconnect/relaunch restore only")
restored.begin(now: 0)
restored.observe(-90, now: 0)
check(!restored.finish(now: 10) && restored.baseline == -60, "failed recalibration retains baseline")
restored.begin(now: 20)
for i in 0..<20 { restored.observe(i % 2 == 0 ? -70 : -71, now: 20 + Double(i) * 0.5) }
check(restored.finish(now: 30) && restored.baseline == -70.5, "explicit recalibration replaces baseline")
for level in [-90.0, -55, -56] {
    restored.begin(now: 40)
    for i in 0..<20 { restored.observe(level, now: 40 + Double(i) * 0.5) }
    check(restored.finish(now: 50) && restored.baseline == level,
          "manual recalibration has no limit on the baseline change")
    check(restored.thresholds()?.lockRSSI == level - 8 && restored.thresholds()?.unlockRSSI == level - 4,
          "both manual thresholds follow large and small baseline changes")
}
var sparse = ProximityCalibration()
sparse.begin(now: 0)
for i in 0..<10 { sparse.observe(-60, now: Double(i) * 0.1) }
check(!sparse.finish(now: 10) && !sparse.qualified, "burst at beginning is insufficient")
sparse.begin(now: 0)
for i in 0..<10 { sparse.observe(-60, now: 8 + Double(i) * 0.1) }
check(!sparse.finish(now: 10), "burst at end is insufficient")
for invalid in [Double.nan, .infinity, -120, 127] {
    check(!ProximityCalibration(baseline: invalid).qualified, "invalid saved baseline rejected")
}
var p = PresencePolicy(lockCountdownDuration: 0)
p.thresholdsReady = false
p.sample(-90, now: 0)
check(p.step(now: 0, locked: false, connected: true, permitted: true, awake: true) == nil, "uncalibrated never locks")

// In-place capture uses the exact same raw samples and median as a standalone
// calibration. Neither boundary reinitializes the live EMA.
var live = LiveCalibrationSession()
var reference = ProximityCalibration(baseline: -60)
var continuous = PresencePolicy(lockRSSI: -68, unlockRSSI: -64)
continuous.sample(-60, now: -1)
let initialEMA = continuous.smoothedRSSI
live.observe(-30, now: -0.5)
live.begin(identifier: "capture-a", now: -0.1)
continuous.cancelLockRequest()
check(continuous.smoothedRSSI == initialEMA && live.capture.samples == 0, "begin preserves EMA and excludes old readings")
reference.begin(now: 0)
for i in 0..<20 {
    let time = Double(i) * 0.5, raw = i == 7 ? -95.0 : -80.0
    continuous.sample(raw, now: time)
    reference.observe(raw, now: time)
    live.observe(raw, now: time)
}
check(live.finishIfDue(now: 9.9) == nil && live.result(for: "capture-a") == nil, "cannot commit an incomplete capture")
check(reference.finish(now: 10) && live.finishIfDue(now: 10) == true, "both captures complete at ten seconds")
let result = live.result(for: "capture-a")!
check(result.readings == reference.readings && result.baseline == reference.baseline, "identical samples produce identical calibration")
live.observe(-20, now: 10.1)
check(live.capture.samples == 20 && live.finishIfDue(now: 11) == nil, "completion emits once and freezes only the capture")
check(live.result(for: "wrong-capture") == nil, "stale acceptance cannot commit another capture")
let finalEMA = continuous.smoothedRSSI
continuous.setThresholds(lock: result.thresholds()!.lockRSSI, unlock: result.thresholds()!.unlockRSSI)
continuous.cancelLockRequest(); live.cancel()
check(continuous.smoothedRSSI == finalEMA && !live.active, "commit preserves EMA and clears only calibration/departure evidence")
live.begin(identifier: "capture-b", now: 20)
live.observe(-90, now: 20); live.cancel()
check(live.result(for: "capture-b") == nil && continuous.smoothedRSSI == finalEMA, "cancellation cannot change the baseline or EMA")
live.begin(identifier: "capture-c", now: 30)
for i in 0..<9 { live.observe(-70, now: 30 + Double(i)) }
check(live.finishIfDue(now: 40) == false && live.result(for: "capture-c") == nil, "insufficient capture never offers a result")
print("Manual calibration: \(count) assertions passed; no system effects")
