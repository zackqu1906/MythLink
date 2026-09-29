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
var p = PresencePolicy()
p.thresholdsReady = false
p.sample(-90, now: 0)
check(p.step(now: 0, locked: false, connected: true, permitted: true, awake: true) == nil, "uncalibrated never locks")
print("Manual calibration: \(count) assertions passed; no system effects")
