import Foundation

var count = 0
func check(_ value: @autoclosure () -> Bool, _ label: String) {
    count += 1; if !value() { fatalError(label) }
}
func sample(_ p: inout ReturnWakePolicy, _ t: Double, _ rssi: Double = -60,
            locked: Bool? = true, connected: Bool = true, available: Bool = true,
            active: Bool = false, weakLoss: Bool = false, at: Double? = nil,
            awaySamples: Int = 2) -> Bool {
    if connected { p.sample(rssi, now: at ?? t, farThreshold: -72) }
    return p.step(now: t, locked: locked, connected: connected, available: available,
           locallyActive: active, rawRSSI: rssi, sampledAt: at ?? t, weakDisconnect: weakLoss,
           farThreshold: -72, nearThreshold: -68, awaySamples: awaySamples, lostDelay: 5)
}
func departed(_ locked: Bool = true) -> ReturnWakePolicy {
    var p = ReturnWakePolicy()
    check(!sample(&p, 0, -80, locked: locked) && !p.awaitingReturn, "one far callback waits")
    check(!sample(&p, 1.05, -80, locked: locked) && p.awaitingReturn, "second far callback confirms departure")
    return p
}

// No departure: first launch, manual/idle lock, and prolonged inactivity.
var p = ReturnWakePolicy()
var wakes = 0
for t in 0...300 { if sample(&p, Double(t), locked: t >= 10) { wakes += 1 } }
check(wakes == 0 && !p.awaitingReturn, "remaining at desk never wakes an idle lock")
for weakLoss in [false, true] {
    p = ReturnWakePolicy()
    _ = sample(&p, 0)
    for t in 1...3 { _ = sample(&p, Double(t), connected: false, weakLoss: weakLoss) }
    for t in 4...20 { check(!sample(&p, Double(t)), "near/unknown drops and brief weak drops are not departures") }
}
p = ReturnWakePolicy()
for t in 0...100 { _ = sample(&p, Double(t), connected: false) }
for t in 101...110 { check(!sample(&p, Double(t)), "unknown disconnect cannot manufacture a return") }
p = ReturnWakePolicy()
_ = sample(&p, 0, -80)
for t in 1...10 { check(!sample(&p, Double(t)), "one far outlier cannot wake screen") }

// Configurable count, not elapsed time; duplicate ticks, interrupted/invalid data
// and stale gaps cannot turn a single callback into a departure.
for required in [1, 2, 3, 5, 60] {
    p = ReturnWakePolicy()
    for i in 0..<required {
        _ = sample(&p, Double(i) * 0.25, -80, awaySamples: required)
        check(p.awaitingReturn == (i == required - 1), "departure occurs on configured callback count")
    }
}
p = ReturnWakePolicy()
_ = sample(&p, 0, -80)
for tick in 1...10 { _ = sample(&p, Double(tick) * 0.25, -80, at: 0) }
check(!p.awaitingReturn && p.departureSamples == 1, "timer cannot manufacture far callbacks")
_ = sample(&p, 4, -80)
check(!p.awaitingReturn && p.departureSamples == 1, "stale gap restarts consecutive count")
_ = sample(&p, 5.05, -80)
check(p.awaitingReturn, "real callbacks above one-second spacing still count")
for interruption in [-72.0, -70.0, -60.0, Double.nan] {
    p = ReturnWakePolicy()
    _ = sample(&p, 0, -80); _ = sample(&p, 1, interruption); _ = sample(&p, 2, -80)
    check(!p.awaitingReturn, "boundary, gray, near or invalid callback breaks far streak")
    _ = sample(&p, 3.05, -80)
    check(p.awaitingReturn, "a new consecutive pair confirms departure")
}
p = ReturnWakePolicy()
_ = sample(&p, 0, -80); p.connectionChanged(); _ = sample(&p, 1, -80)
check(!p.awaitingReturn, "far count cannot cross a connection boundary")
p.invalidateSignal(); _ = sample(&p, 2, -80)
check(!p.awaitingReturn, "invalid callback between ticks clears far count")

// One near callback returns, wakes once, and enables gesture recovery together.
p = departed()
check(sample(&p, 2.1, -68) && !p.awaitingReturn && p.returnConfirmed, "first reading at near boundary confirms return")
for t in 3...100 { check(!sample(&p, Double(t)), "continued presence cannot relight a timed-out display") }
check(!sample(&p, 101, active: true) && p.returnConfirmed, "wake-related local activity preserves recovery retries")
_ = sample(&p, 102, -80); _ = sample(&p, 103.05, -80)
check(p.awaitingReturn && !p.returnConfirmed, "another departure revokes prior return permission")
check(sample(&p, 104.1), "second trip can wake once again")

// Replay the actual failed return on 2026-09-28: requests every 250 ms, but
// callbacks ~1.05 seconds apart. Feed the historical smoothed values as test
// inputs to isolate callback cadence; raw-vs-smoothed integration follows below.
p = departed(); p.connectionChanged()
let recordedReturn: [(Double, Double)] = [
    (28.212, -91), (29.267, -89.80000000000001), (30.306, -85.08000000000001),
    (31.347, -82.64800000000001), (32.388, -79.5888), (33.428, -75.75328),
    (34.481, -73.451968), (35.500, -71.2711808), (36.516, -70.36270848),
    (37.522, -69.417625088), (38.556, -69.2505750528), (39.604, -68.75034503168001)
]
var recordedWakes: [Double] = []
var reading = 0
for tick in 113...160 {
    let t = Double(tick) * 0.25
    while reading + 1 < recordedReturn.count && recordedReturn[reading + 1].0 <= t { reading += 1 }
    let (sampledAt, rssi) = recordedReturn[reading]
    p.sample(rssi, now: sampledAt, farThreshold: -76.5)
    if p.step(now: t, locked: true, connected: true, available: true, locallyActive: false,
              rawRSSI: rssi, sampledAt: sampledAt, weakDisconnect: false,
              farThreshold: -76.5, nearThreshold: -72.5, awaySamples: 2, lostDelay: 15) {
        recordedWakes.append(t)
    }
}
check(recordedWakes == [35.5] && p.returnConfirmed, "logged first near callback wakes once and permits recovery")

// Actual 11:42 return: raw RSSI reaches -71 at 05.060; EMA only reaches it
// at 07.152. The existing far streak must not block the first raw near callback.
p = departed(); p.connectionChanged()
var recordedLock = PresencePolicy(lockRSSI: -75, unlockRSSI: -71)
let rawReturn: [(Double, Double, Double)] = [
    (1.955, -83, -83), (2.978, -80, -81.8), (4.008, -74, -78.68),
    (5.060, -71, -75.608), (6.105, -66, -71.7648), (7.152, -69, -70.65888)
]
var rawWakes: [Double] = []
for (t, raw, expectedEMA) in rawReturn {
    recordedLock.sample(raw, now: t)
    check(abs(recordedLock.smoothedRSSI! - expectedEMA) < 0.000001, "departure smoothing remains unchanged")
    p.sample(recordedLock.smoothedRSSI!, now: t, farThreshold: recordedLock.lockRSSI)
    if p.step(now: t, locked: true, connected: true, available: true, locallyActive: false,
              rawRSSI: raw, sampledAt: t, weakDisconnect: false, farThreshold: recordedLock.lockRSSI,
              nearThreshold: recordedLock.unlockRSSI, awaySamples: 2, lostDelay: 15) {
        rawWakes.append(t)
        check(recordedLock.smoothedRSSI! < recordedLock.lockRSSI, "raw return bypasses even a still-far EMA")
        check(p.departureSamples == 0, "confirmed return clears historical far count")
    }
    check(t < 5.060 || p.returnConfirmed, "lagging EMA cannot revoke a raw return")
}
check(rawWakes == [5.060], "recorded return is confirmed 2.092 seconds earlier, exactly once")

// Several callbacks may leave EMA far after a sharp return. A new far streak
// must start after the raw near readings, not reuse the pre-return evidence.
p = departed()
for t in 2...5 {
    p.sample(-80, now: Double(t), farThreshold: -72)
    let returned = p.step(now: Double(t), locked: true, connected: true, available: true, locallyActive: false,
                          rawRSSI: -68, sampledAt: Double(t), weakDisconnect: false,
                          farThreshold: -72, nearThreshold: -68, awaySamples: 2, lostDelay: 5)
    check(returned == (t == 2) && p.returnConfirmed && p.departureSamples == 0,
          "raw near maintains one return while EMA catches up")
}
_ = sample(&p, 6, -80)
check(p.returnConfirmed && !p.awaitingReturn, "one new far sample cannot reuse the previous departure")
_ = sample(&p, 7, -80)
check(!p.returnConfirmed && p.awaitingReturn, "two new far samples confirm the next departure")

// Real locking still waits for consecutive smoothed far callbacks, even if
// the raw readings cross the departure threshold earlier.
var smoothedLock = PresencePolicy(lockRSSI: -72, unlockRSSI: -68, awaySamples: 2)
for (i, raw) in [-60.0, -80, -80, -80].enumerated() {
    smoothedLock.sample(raw, now: Double(i))
    let action = smoothedLock.step(now: Double(i), locked: false, connected: true, permitted: true, awake: true)
    check(action == (i == 3 ? .lock : nil), "lock still requires two smoothed far callbacks")
}

// Weak drop still uses its seconds-based timeout; near reconnect then confirms once.
p = ReturnWakePolicy()
_ = sample(&p, 0, -71.5)
p.connectionChanged()
for t in 1...5 { check(!sample(&p, Double(t), connected: false, weakLoss: true), "wait for weak drop timeout") }
check(!p.awaitingReturn, "disconnect grace not complete")
_ = sample(&p, 6, connected: false, weakLoss: true)
check(p.awaitingReturn, "weak disconnect timeout confirms departure")
p.connectionChanged()
for t in 7...12 { check(!sample(&p, Double(t), -75), "far reconnect never wakes") }
check(sample(&p, 13), "first near callback after reconnect wakes")

// A manual lock can wake after a trip but does not acquire gesture unlock ownership.
p = departed()
var manualLock = PresencePolicy()
_ = manualLock.step(now: 0, locked: true, connected: true, permitted: true, awake: true)
check(sample(&p, 2.1), "actual return wakes a manual lock")
check(!manualLock.ownsLock, "wake cannot claim manual lock")
check(manualLock.requestGestureUnlock(now: 3, locked: true, connected: true, permitted: true, awake: true) == nil,
      "manual lock still requires system unlock")

// Unlock consumes this trip, including return before an idle lock.
p = departed(false)
check(!sample(&p, 2.1, locked: false) && !p.awaitingReturn && !p.returnConfirmed, "unlocked return is consumed")
for t in 3...10 { check(!sample(&p, Double(t)), "idle lock cannot replay a prior return") }
p = departed()
_ = sample(&p, 2.1, locked: false)
for t in 3...10 { check(!sample(&p, Double(t)), "manual unlock discards the trip") }

// Freshness, invalid callbacks, and exact near threshold.
p = departed()
check(!sample(&p, 6, at: 2), "stale near reading cannot confirm return")
check(!sample(&p, 6, at: 7), "future-dated reading cannot confirm return")
check(!sample(&p, 7, .nan), "invalid near reading cannot confirm return")
for invalid in [Double.infinity, -Double.infinity, 127, 0, -19, -101] {
    check(!sample(&p, 7.1, invalid) && p.awaitingReturn, "invalid raw RSSI cannot confirm return")
}
check(!sample(&p, 8, -68.01), "gray zone does not return")
check(sample(&p, 9, -68), "one fresh callback at threshold returns")
check(!sample(&p, 9.25, at: 9), "duplicate tick cannot repeat wake")
p = departed(); p.invalidateSignal()
check(sample(&p, 2.1), "invalid data does not erase confirmed departure")

// Sleep, unavailable Bluetooth/permissions/calibration, unknown session, and input.
for mode in 0...2 {
    p = departed()
    _ = sample(&p, 2, locked: mode == 0 ? nil : true, available: mode != 1, active: mode == 2)
    check(!p.awaitingReturn, "invalid context/local input discards departure")
    for t in 3...10 { check(!sample(&p, Double(t)), "near after context reset cannot wake") }
}
p = ReturnWakePolicy()
for t in 0...30 { check(!sample(&p, Double(t), -80, active: true), "keyboard input vetoes departure") }
check(!p.awaitingReturn, "local input prevents arming wake")
p = departed()
_ = sample(&p, 2, connected: false, available: false, weakLoss: true)
for t in 3...20 { _ = sample(&p, Double(t), connected: false, weakLoss: true) }
check(!p.awaitingReturn, "radio recovery cannot reuse old disconnect evidence")
for t in 21...25 { check(!sample(&p, Double(t)), "near after radio recovery cannot fabricate return") }

// Both production policies: smoothed departure, raw return, ownership and gesture requirement.
for loseConnection in [false, true] {
    var lock = PresencePolicy(lockRSSI: -72, unlockRSSI: -68, awaySamples: loseConnection ? 30 : 2, lostDelay: 5)
    var wake = ReturnWakePolicy()
    var locked = false
    var wakeCount = 0
    for tick in 0...100 {
        let t = Double(tick) * 0.5
        let connected = !loseConnection || t < 9 || t >= 20
        let raw = t < 3 || t >= 25 ? -60.0 : -80.0
        if connected { lock.sample(raw, now: t) }
        if connected { wake.sample(lock.smoothedRSSI!, now: t, farThreshold: lock.lockRSSI) }
        let action = lock.step(now: t, locked: locked, connected: connected, permitted: true, awake: true)
        check(action != .unlock, "RSSI never unlocks in production replay")
        if action == .lock { locked = true }
        if wake.step(now: t, locked: locked, connected: connected, available: true, locallyActive: false,
                     rawRSSI: connected ? raw : nil, sampledAt: connected ? t : nil,
                     weakDisconnect: lock.disconnectEvidence == "weak", farThreshold: lock.lockRSSI,
                     nearThreshold: lock.unlockRSSI, awaySamples: lock.awaySamples, lostDelay: lock.lostDelay) {
            wakeCount += 1
        }
    }
    check(wakeCount == 1 && locked && lock.ownsLock, "one wake preserves locked owned session")
    check(lock.lastLockReason == (loseConnection ? "disconnected" : "weak_signal"), "both departure paths exercised")
    check(lock.requestGestureUnlock(now: 51, locked: true, connected: true, permitted: true, awake: true) == .unlock,
          "new snap remains required after waking")
}
print("Return display wake: \(count) assertions passed; no system effects")

// Regression: return/reconnect may precede gesture readiness by seconds. Delay
// only the owned-lock display wake until the host has installed the snap route.
func display(_ g: inout ReturnDisplayGate, returned: Bool = true, locked: Bool = true,
             owned: Bool = true, available: Bool = true, connected: Bool = true,
             healthy: Bool = true, token: String = "this-lock", installed: String = "this-lock") -> Bool {
    g.step(returnConfirmed: returned, locked: locked, ownsLock: owned, available: available,
           connected: connected, gestureReady: healthy, token: token, installedToken: installed)
}
var displayGate = ReturnDisplayGate()
check(!display(&displayGate, returned: false), "readiness alone cannot wake stationary idle lock")
check(!display(&displayGate, connected: false), "confirmed return cannot wake before connection")
check(!display(&displayGate, healthy: false, installed: ""), "reconnection alone cannot wake owned lock")
check(!display(&displayGate, installed: ""), "model ready but snap route not installed cannot wake")
check(!display(&displayGate, installed: "previous-lock"), "old ack cannot authorize wake")
check(!display(&displayGate, token: "", installed: ""), "empty tokens cannot authorize wake")
check(display(&displayGate), "wake at the same transition that makes first snap usable")
for _ in 0...20 { check(!display(&displayGate), "health and installation heartbeats do not relight screen") }
check(!display(&displayGate, healthy: false), "temporary health loss does not repeat wake")
check(!display(&displayGate), "health recovery after wake does not repeat wake")
_ = display(&displayGate, returned: false)
check(!display(&displayGate, installed: ""), "next trip again waits for installed route")
check(display(&displayGate), "new actual trip can wake once again")
for context in 0...2 {
    displayGate = ReturnDisplayGate()
    _ = display(&displayGate, installed: "")
    check(!display(&displayGate, returned: context != 0, locked: context != 1, available: context != 2),
          "departure, manual unlock or sleep cancels pending wake")
    check(!display(&displayGate, returned: false), "old readiness cannot recreate a cancelled return")
}
displayGate = ReturnDisplayGate()
check(display(&displayGate, owned: false, healthy: false, token: "", installed: ""),
      "manual lock still wakes on actual return without acquiring snap unlock")
print("Return wake readiness gate: \(count) assertions passed; no system effects")
