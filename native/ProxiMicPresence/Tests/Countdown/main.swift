import Foundation

var count = 0
func check(_ condition: @autoclosure () -> Bool, _ label: String) {
    count += 1
    if !condition() { fatalError("FAIL: \(label)") }
}
func step(_ p: inout PresencePolicy, _ now: Double, locked: Bool? = false,
          connected: Bool = true, permitted: Bool = true, awake: Bool = true,
          locallyActive: Bool = false) -> PresencePolicy.Action? {
    p.step(now: now, locked: locked, connected: connected, permitted: permitted,
           awake: awake, locallyActive: locallyActive)
}
func countdown() -> PresencePolicy {
    var p = PresencePolicy()
    p.sample(-90, now: 0)
    check(step(&p, 0) == nil, "one far callback cannot start a countdown")
    p.sample(-90, now: 1)
    check(step(&p, 1) == nil && !p.lockPending, "confirmed departure warns before the host handshake")
    check(p.lockCountdownRemaining(now: 1) == 5, "production countdown starts at five")
    return p
}

var p = countdown()
for tick in 1..<20 {
    let t = 1 + Double(tick) * 0.25
    p.sample(-90, now: t)
    check(step(&p, t) == nil, "never lock before the five-second deadline")
    check(p.lockCountdownRemaining(now: t) == Int(ceil(6 - t)), "visible seconds match the policy clock")
    check(!p.failed && !p.lockPending, "countdown does not run the four-second lock acknowledgement timeout")
}
p.sample(-90, now: 6)
check(step(&p, 6) == .lock && p.lockPending, "fresh continued departure requests one lock after five seconds")
check(p.lockCountdownRemaining(now: 6) == 0, "hide countdown at the actual handoff")
check(step(&p, 6.1) == nil, "no duplicate lock request")
check(step(&p, 6.2, locked: true) == nil && p.ownsLock, "normal ownership handshake still works")

p = countdown()
p.sample(-62, now: 1.5)
check(p.smoothedRSSI! < p.lockRSSI, "return fixture retains a far EMA")
check(step(&p, 1.5) == nil && p.lockCountdownRemaining(now: 1.5) == 0,
      "first near raw callback cancels immediately despite EMA lag")
p.sample(-90, now: 2); check(step(&p, 2) == nil, "return discards previous departure evidence")
p.sample(-90, now: 3); check(step(&p, 3) == nil && p.lockCountdownRemaining(now: 3) == 5,
                           "a later departure gets a full new countdown")

for reason in ["connection", "permission", "sleep", "unknown", "manual-lock", "activity", "disabled", "thresholds", "invalid", "recalibration", "failure", "suspend"] {
    p = countdown()
    switch reason {
    case "disabled": p.setLockingEnabled(false)
    case "thresholds": p.thresholdsReady = false
    case "invalid": p.sample(.nan, now: 2)
    case "recalibration": p.setThresholds(lock: -79, unlock: -63)
    case "failure": p.fail()
    case "suspend": p.suspend()
    default: break
    }
    let locked: Bool? = reason == "unknown" ? nil : reason == "manual-lock"
    check(step(&p, 2, locked: locked, connected: reason != "connection", permitted: reason != "permission",
               awake: reason != "sleep", locallyActive: reason == "activity") == nil, "veto cannot execute a lock: \(reason)")
    check(p.lockCountdownRemaining(now: 2) == 0 && !p.lockPending, "veto clears countdown: \(reason)")
    check(step(&p, 8) == nil, "old deadline cannot lock after cancellation: \(reason)")
}
p = countdown()
check(step(&p, 4.01) == nil && p.lockCountdownRemaining(now: 4.01) == 0,
      "stale RSSI cancels instead of finishing from a frozen sample")
p.sample(-90, now: 5); check(step(&p, 5) == nil, "fresh evidence required after a stale gap")
p.sample(-90, now: 6); check(step(&p, 6) == nil && p.lockCountdownRemaining(now: 6) == 5,
                           "fresh recovery cannot reuse the old deadline")

p = countdown()
for t in 2...6 { p.sample(-90, now: Double(t)); _ = step(&p, Double(t)) }
check(p.lockPending, "awaiting final acknowledgement")
p.sample(-62, now: 6.1)
check(!p.lockPending && !p.hasFreshDeparture(now: 6.1), "return racing the final acknowledgement cancels")
p.reset()
check(p.lockCountdownDuration == 5, "reset preserves production warning duration")

func chartState(_ p: PresencePolicy, _ now: Double, locked: Bool = false,
                connected: Bool = true, active: Bool = false) -> String {
    p.signalState(now: now, locked: locked, connected: connected, permitted: true,
                  awake: true, locallyActive: active)
}
p = PresencePolicy()
p.sample(-62, now: 0); _ = step(&p, 0)
check(chartState(p, 0) == "normal", "near callback plots normal state")
p.sample(-100, now: 0.5); _ = step(&p, 0.5)
p.sample(-100, now: 1); _ = step(&p, 1)
check(chartState(p, 1) == "away" && p.lockDepartureSamples == 1,
      "chart uses actual smoothed lock evidence, not raw RSSI or return counts")
p.sample(-100, now: 1.5); _ = step(&p, 1.5)
check(chartState(p, 1.5) == "countdown" && p.lockCountdownRemaining(now: 1.5) == 5,
      "chart turns red at the same callback that starts the countdown")
_ = step(&p, 2, locallyActive: true)
check(chartState(p, 2, active: true) == "protected" && p.lockDepartureSamples == 0,
      "activity protection cannot appear as departure despite weak RSSI")
check(chartState(p, 5) == "stale", "expired samples cannot show live departure")
_ = step(&p, 5, connected: false)
check(chartState(p, 5, connected: false) == "disconnected", "disconnection pauses the chart")
p = countdown()
for t in 2...6 { p.sample(-90, now: Double(t)); _ = step(&p, Double(t)) }
check(chartState(p, 6) == "locking", "host handshake is distinct from confirmed system lock")
_ = step(&p, 6.1, locked: true)
check(chartState(p, 6.1, locked: true) == "locked", "only confirmed locked session plots locked state")
print("Pre-lock countdown: \(count) assertions passed; no Bluetooth or system actions")
