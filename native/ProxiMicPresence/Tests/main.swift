import Foundation

var count = 0
func check(_ condition: @autoclosure () -> Bool, _ label: String) {
    count += 1
    if !condition() { fatalError("FAIL: \(label)") }
}
func step(_ p: inout PresencePolicy, _ t: Double, _ locked: Bool? = false,
          _ connected: Bool = true, _ permitted: Bool = true, _ awake: Bool = true) -> PresencePolicy.Action? {
    p.step(now: t, locked: locked, connected: connected, permitted: permitted, awake: awake)
}
func arm(_ p: inout PresencePolicy, start: Int = 0) {
    for t in start...(start + 3) { p.sample(-50, now: Double(t)); check(step(&p, Double(t)) == nil, "arming does not act") }
    check(p.armed, "arms after nearby confirmation")
}
var p = PresencePolicy()
check(step(&p, 100, false, false) == nil, "never seen ring cannot lock")
p.sample(127, now: 100); check(p.smoothedRSSI == nil, "invalid CoreBluetooth RSSI rejected")
p.sample(Double.nan, now: 100); check(p.smoothedRSSI == nil, "NaN rejected")
arm(&p)
for t in 4...6 { p.sample(-90, now: Double(t)); check(step(&p, Double(t)) == nil, "wait for two smoothed far callbacks") }
p.sample(-90, now: 7); check(step(&p, 7) == .lock, "second consecutive far callback locks")
check(step(&p, 7.5, true) == nil && p.ownsLock, "own lock confirmed")
for t in 15...23 {
    p.sample(-50, now: Double(t))
    let action = step(&p, Double(t), true)
    check(action == nil, "nearby never unlocks \(t)")
}
check(p.requestGestureUnlock(now: 24, locked: true, connected: true, permitted: true, awake: true) == .unlock, "explicit gesture unlocks")
check(step(&p, 30, true) == nil && p.failed, "failed unlock stops retries")
check(step(&p, 31, false) == nil && !p.failed && !p.ownsLock, "manual unlock clears ownership/failure")

p = PresencePolicy(); arm(&p)
p.sample(-110, now: 3.1); p.sample(-110, now: 3.5)
p.disconnected(now: 4)
check(step(&p, 4, false, false) == nil, "start disconnect grace")
check(step(&p, 18.9, false, false) == nil, "disconnect grace not expired")
check(step(&p, 19, false, false) == nil, "weak disconnect timeout never locks")
check(step(&p, 1000, false, false) == nil && !p.failed && !p.armed, "long disconnect remains paused")

p = PresencePolicy(); arm(&p)
check(step(&p, 50, false, true) == nil, "connected with stale RSSI does not falsely lock")
p.disconnected(now: 51); check(step(&p, 51, false, false) == nil, "disconnect starts its own clock")
p.sample(-50, now: 55); check(step(&p, 55) == nil, "brief disconnect recovered")
check(step(&p, 90) == nil, "old disconnect cannot lock new connection")

p = PresencePolicy(); arm(&p)
check(step(&p, 4, true) == nil && !p.ownsLock, "manual lock not owned")
for t in 5...12 { p.sample(-40, now: Double(t)); check(step(&p, Double(t), true) == nil, "nearby never unlocks manual lock") }

p = PresencePolicy(); arm(&p)
for t in 4...30 {
    p.sample(-90, now: Double(t))
    check(step(&p, Double(t), nil) == nil, "unknown session never acts")
}
check(step(&p, 100, false, false, false) == nil, "no permission never acts")
p.suspend(); check(!p.armed, "sleep requires fresh arming")
check(step(&p, 200, false, false) == nil, "wake while ring missing does not instant-lock")
check(step(&p, 201, true, true, true, false) == nil, "sleep never unlocks")

p = PresencePolicy(); arm(&p)
for t in 4...30 { p.sample(-70, now: Double(t)); check(step(&p, Double(t)) == nil, "hysteresis band holds") }


// Regression: connected, but never stronger than the unlock threshold.
// Previously this stayed unarmed forever and ignored an actual disconnect.
p = PresencePolicy(lockRSSI: -70, unlockRSSI: -64, awaySamples: 5, lostDelay: 5)
for t in 0...3 {
    p.sample(-67, now: Double(t))
    check(step(&p, Double(t)) == nil, "middle-strength connection should not lock")
}
check(p.armed, "connection arms even without reaching unlock threshold")
p.sample(-90, now: 3.5)
p.disconnected(now: 4)
check(step(&p, 4, false, false) == nil, "start configured disconnect wait")
check(step(&p, 8.9, false, false) == nil, "respect configured five seconds")
check(step(&p, 9, false, false) == nil, "weak disconnect cannot lock without a near signal either")
check(step(&p, 9.5, true, false) == nil && !p.ownsLock, "external lock while disconnected is not owned")
for t in 10...15 {
    p.sample(-67, now: Double(t))
    check(step(&p, Double(t), true) == nil, "connection alone must not unlock")
}
for t in 16...23 {
    p.sample(-50, now: Double(t))
    let action = step(&p, Double(t), true)
    check(action == nil, "strong signal never unlocks")
}
p = PresencePolicy(lostDelay: 5)
check(step(&p, 0) == nil && p.armed, "connection without RSSI still arms disconnect detection")
p.disconnected(now: 1)
check(step(&p, 1, false, false) == nil, "missing RSSI does not skip disconnect grace")
check(step(&p, 60, false, false) == nil, "missing RSSI does not prove departure")


let secret = UnlockSecret()
var loads = 0
let fake = Data("test-only-credential".utf8)
check(!secret.prepare(locked: true, load: { loads += 1; return fake }), "never access Keychain on lock screen")
check(!secret.prepare(locked: nil, load: { loads += 1; return fake }), "never access Keychain with unknown session")
check(loads == 0, "blocked preparation makes no credential request")
check(secret.prepare(locked: false, load: { loads += 1; return fake }), "prepare only while unlocked")
for _ in 0..<20 { check(secret.prepare(locked: false, load: { loads += 1; return fake }), "unlocked ticks retain prepared credential") }
check(loads == 1, "no repeated Keychain reads while waiting for any lock")
check(!secret.prepare(locked: true, load: { loads += 1; return fake }) && secret.isPrepared,
      "lock racing preparation preserves the already prepared credential")
check(loads == 1, "lock race never reads Keychain")
check(secret.take(locked: true) == fake, "manual or automatic lock consumes prepared memory")
check(secret.take(locked: true) == nil && !secret.isPrepared, "credential can be consumed only once")
check(secret.prepare(locked: false, load: { fake }), "prepare next lock after unlocking")
check(secret.take(locked: nil) == nil && !secret.isPrepared, "unknown session discards credential")
check(secret.prepare(locked: false, load: { fake }), "prepare again only while unlocked")
check(secret.take(locked: false) == nil && !secret.isPrepared, "never type into an unlocked session")
check(secret.prepare(locked: false, load: { fake }), "prepare before cancellation")
secret.discard()
check(secret.take(locked: true) == nil, "disabled monitor discards credential")
check(!secret.prepare(locked: false, load: { nil }), "denied Keychain access prevents locking")
print("Presence policy and credential lease: \(count) assertions passed; no system effects")

// Local input must prevent both weak-signal and disconnected false locks.
p = PresencePolicy(lostDelay: 5); arm(&p)
for t in 4...24 {
    p.sample(-95, now: Double(t))
    check(p.step(now: Double(t), locked: false, connected: true, permitted: true, awake: true, locallyActive: true) == nil, "typing vetoes weak-signal lock")
}
p.disconnected(now: 25)
for t in 25...40 {
    check(p.step(now: Double(t), locked: false, connected: false, permitted: true, awake: true, locallyActive: true) == nil, "typing vetoes disconnected lock")
}
check(step(&p, 41, false, false) == nil, "idle starts a new departure interval")
check(step(&p, 46, false, false) == nil, "idle after activity still cannot lock disconnected device")
p.sample(-95, now: 47); check(step(&p, 47) == nil, "first fresh callback after reconnect cannot lock")
p.sample(-95, now: 48); check(step(&p, 48) == .lock, "reconnect resumes normal consecutive weak samples")
check(p.lastLockReason == "weak_signal", "only connected weak signal triggers lock")
p = PresencePolicy(lostDelay: 5); arm(&p); p.disconnected(now: 4)
check(p.disconnectEvidence == "near", "strong disconnect recorded")
check(step(&p, 4, false, false) == nil, "near disconnect does not start departure")
check(step(&p, 1000, false, false) == nil, "near disconnect never becomes a departure by timeout")
p = PresencePolicy(awaySamples: 3); arm(&p)
p.sample(-100, now: 4); _ = step(&p, 4)
p.sample(-100, now: 5); _ = step(&p, 5)
p.sample(-100, now: 9)
check(step(&p, 9) == nil, "missing samples do not count toward continuous weakness")
p.setThresholds(lock: -90, unlock: -70)
check(step(&p, 10) == nil, "new thresholds need fresh dwell")
print("Extended presence policy: \(count) assertions passed; no system effects")

for level in [-65.0, -80.0] {
    p = PresencePolicy(lostDelay: 5)
    p.sample(level, now: 0); _ = step(&p, 0)
    p.disconnected(now: 10)
    check(step(&p, 10, false, false) == nil, "expired evidence is unknown")
    check(step(&p, 50, false, false) == nil, "old signal does not cause lock")
}
p = PresencePolicy(lostDelay: 5); p.sample(-70, now: 0); _ = step(&p, 0)
p.disconnected(now: 1)
check(step(&p, 20, false, false) == nil, "hysteresis band disconnect is not proven far")
p = PresencePolicy(lostDelay: 5); p.sample(-90, now: 0); _ = step(&p, 0)
p.disconnected(now: 1); _ = step(&p, 1, false, false)
p.disconnected(now: 3)
check(step(&p, 6, false, false) == nil && p.disconnectEvidence == "weak", "repeated failures retain return evidence without locking")
p = PresencePolicy(lostDelay: 5); p.sample(-90, now: 0); _ = step(&p, 0)
_ = step(&p, 1, false, false)
check(step(&p, 6, false, false) == nil && !p.armed, "tick pauses locking even before disconnect callback")
p = PresencePolicy(); p.thresholdsReady = false
for t in 0...30 {
    p.sample(-100, now: Double(t))
    check(step(&p, Double(t)) == nil, "uncalibrated mode cannot lock")
}
print("Automatic-only presence policy: \(count) assertions passed; no system effects")

// Boundary replay with the tighter automatically learned thresholds.
for (signal, departs) in [(-75.0, true), (-74.0, true), (-73.9, false), (-72.0, false), (-71.0, false)] {
    var boundary = PresencePolicy(lockRSSI: -75, unlockRSSI: -72, lostDelay: 5)
    boundary.sample(signal, now: 0)
    check(step(&boundary, 0) == nil, "boundary starts connected")
    boundary.disconnected(now: 1)
    check(boundary.disconnectEvidence == (departs ? "weak" : (signal >= -72 ? "near" : "unknown")), "disconnect proximity classification")
    check(step(&boundary, 1, false, false) == nil, "disconnect still waits")
    check(step(&boundary, 6, false, false) == nil, "all disconnect classifications leave locking paused")
}
print("Policy final: \(count) assertions passed")

// A separate native BLE connection cannot keep locking when the main one stops.
var host = HostConnectionState()
check(!host.isConnected(now: 0), "host starts disconnected")
host.update(connected: true, now: 1)
check(host.isConnected(now: 3.9) && !host.isConnected(now: 4), "host heartbeat must remain fresh")
host.update(connected: false, now: 5)
check(!host.isConnected(now: 5), "explicit host disconnect takes effect immediately")
p = PresencePolicy(awaySamples: 2)
p.sample(-90, now: 0); _ = step(&p, 0)
p.sample(-90, now: 1); check(step(&p, 1) == .lock && p.lockPending, "prepare a pending lock")
p.setLockingEnabled(false)
check(!p.lockPending && !p.armed, "host disconnect cancels a pending lock")
for t in 2...100 {
    p.sample(-100, now: Double(t))
    check(step(&p, Double(t)) == nil, "native BLE alone cannot lock")
}
p.setLockingEnabled(true)
check(step(&p, 101) == nil && !p.failed, "host reconnect cannot reuse paused samples or fail old request")
p.sample(-100, now: 102); check(step(&p, 102) == nil, "reconnect requires first new weak sample")
p.sample(-100, now: 103); check(step(&p, 103) == .lock, "second new weak sample can lock")
p.disconnected(now: 103.1)
check(!p.lockPending && !p.armed, "native disconnect cancels pending handshake immediately")
check(step(&p, 200, false, false) == nil && !p.failed, "cancelled handshake cannot time out or lock")

// Only an explicit gesture can unlock a monitored session, regardless of RSSI or lock origin.
func lockedPolicy() -> PresencePolicy {
    var p = PresencePolicy(lockRSSI: -75, unlockRSSI: -62, awaySamples: 3)
    for t in 0...3 { p.sample(-90, now: Double(t)); _ = step(&p, Double(t)) }
    check(step(&p, 3.1, true) == nil && p.ownsLock, "test owns lock")
    return p
}
var gesture = lockedPolicy()
for t in 4...100 {
    gesture.sample(-30, now: Double(t))
    check(step(&gesture, Double(t), true) == nil, "strong fresh samples never unlock")
}
gesture = lockedPolicy()
gesture.thresholdsReady = false // Reconnecting need not wait for a new baseline.
check(gesture.requestGestureUnlock(now: 4, locked: true, connected: true, permitted: true, awake: true) == .unlock, "gesture unlocks with weak/stale or uncalibrated signal")
check(gesture.requestGestureUnlock(now: 4.1, locked: true, connected: true, permitted: true, awake: true) == nil, "duplicate gesture never retries password")
check(step(&gesture, 10, true) == nil && gesture.failed, "unlock timeout fails closed")
check(gesture.requestGestureUnlock(now: 11, locked: true, connected: true, permitted: true, awake: true) == nil, "failed unlock cannot retry")
for (locked, connected, permitted, awake) in [(nil as Bool?, true, true, true), (false, true, true, true), (true, false, true, true), (true, true, false, true), (true, true, true, false)] {
    gesture = lockedPolicy()
    check(gesture.requestGestureUnlock(now: 4, locked: locked, connected: connected, permitted: permitted, awake: awake) == nil, "invalid live session cannot unlock")
}
gesture = PresencePolicy()
_ = step(&gesture, 0, true)
check(gesture.requestGestureUnlock(now: 1, locked: true, connected: true, permitted: true, awake: true) == nil, "process started locked has no observed unlocked session")
gesture = lockedPolicy()
_ = step(&gesture, 4, false)
check(gesture.requestGestureUnlock(now: 5, locked: true, connected: true, permitted: true, awake: true) == nil, "old ownership cannot unlock next session")
print("Gesture unlock final: \(count) assertions passed; no system effects")

// The user can lock in place, without any departure or return transition.
for signal in [-40.0, -68.0, -95.0] {
    var manual = PresencePolicy()
    let prepared = UnlockSecret()
    manual.sample(signal, now: 0)
    _ = manual.step(now: 0, locked: false, connected: true, permitted: true, awake: true, locallyActive: true)
    check(prepared.prepare(locked: false, load: { fake }), "arm credential before shortcut/idle lock")
    check(step(&manual, 1, true) == nil && !manual.ownsLock && manual.awaitingGesture,
          "manual lock waits for snap without being classified as app-owned")
    for t in 2...5 { check(step(&manual, Double(t), true) == nil, "manual lock never unlocks merely by waiting") }
    check(manual.requestGestureUnlock(now: 6, locked: true, connected: true, permitted: true, awake: true) == .unlock,
          "fresh snap unlocks stationary manual lock at any RSSI")
    check(prepared.take(locked: true) == fake && manual.unlockPending, "manual unlock consumes prepared credential")
    check(manual.requestGestureUnlock(now: 6.1, locked: true, connected: true, permitted: true, awake: true) == nil,
          "duplicate manual-lock snap cannot retype password")
    _ = step(&manual, 7, false)
    check(!manual.awaitingGesture && !manual.unlockPending, "system unlock ends previous gesture request")
    check(prepared.prepare(locked: false, load: { fake }), "re-arm credential for next manual lock")
    _ = step(&manual, 8, true)
    check(manual.awaitingGesture, "successive manual locks remain supported")
    _ = step(&manual, 9, nil)
    prepared.discard()
    _ = step(&manual, 10, true)
    check(!manual.awaitingGesture && !prepared.isPrepared, "unknown/other user session revokes unlock eligibility")
}
var sleeping = PresencePolicy()
_ = step(&sleeping, 0, false)
sleeping.suspend()
_ = step(&sleeping, 1, true, true, true, false)
check(sleeping.requestGestureUnlock(now: 2, locked: true, connected: true, permitted: true, awake: false) == nil,
      "sleeping computer cannot receive password")
_ = step(&sleeping, 3, true)
check(sleeping.awaitingGesture && !sleeping.ownsLock, "wake into system lock supports fresh snap")
check(sleeping.requestGestureUnlock(now: 4, locked: true, connected: true, permitted: true, awake: true) == .unlock,
      "new gesture can unlock after wake")
_ = step(&sleeping, 4.1, nil)
check(!sleeping.unlockPending, "unknown session cancels delayed password input")
print("All lock sources: \(count) assertions passed; no system effects")

// An owned lock survives physical loss and reconnect. Only a new gesture unlocks.
var returned = lockedPolicy()
returned.disconnected(now: 4)
check(step(&returned, 20, true, false) == nil && returned.ownsLock, "disconnected owned lock retained")
returned.sample(-90, now: 21)
check(step(&returned, 21, true, true) == nil && returned.ownsLock, "reconnect does not unlock or lose ownership")
check(returned.requestGestureUnlock(now: 22, locked: true, connected: true, permitted: true, awake: true) == .unlock, "gesture after reconnect unlocks")
check(returned.requestGestureUnlock(now: 23, locked: true, connected: true, permitted: true, awake: true) == nil, "reconnect gesture remains single use")
print("Reconnect policy final: \(count) assertions passed; no system effects")

// The default is two actual far callbacks, independent of timer polling rate.
p = PresencePolicy(lockRSSI: -72, unlockRSSI: -68)
p.sample(-80, now: 0)
for tick in 0...4 { check(step(&p, Double(tick) * 0.25) == nil, "one far callback cannot lock through repeated ticks") }
p.sample(-80, now: 1.05)
check(step(&p, 1.05) == .lock, "default locks on second actual far callback")
for required in [1, 3, 5, 60] {
    p = PresencePolicy(awaySamples: required)
    for i in 0..<required {
        let t = Double(i) * 0.25
        p.sample(-90, now: t)
        check(step(&p, t) == (i == required - 1 ? .lock : nil), "configured count never substitutes elapsed seconds")
    }
}
p = PresencePolicy(lockRSSI: -72, unlockRSSI: -68)
p.sample(-80, now: 0); _ = step(&p, 0)
p.sample(-50, now: 1); check(step(&p, 1) == nil, "recovered signal breaks far streak")
p.sample(-100, now: 2); check(step(&p, 2) == nil, "far streak starts again after recovery")
p.sample(-100, now: 3.05); check(step(&p, 3.05) == .lock, "new far pair locks")
p = PresencePolicy()
p.sample(-90, now: 0); _ = step(&p, 0)
p.sample(.nan, now: 0.5); p.sample(-90, now: 1)
check(step(&p, 1) == nil, "invalid callback between timer ticks clears departure count")
p.sample(-90, now: 2.05); check(step(&p, 2.05) == .lock, "pair after invalid callback locks")
p = PresencePolicy()
p.sample(-90, now: 0); _ = step(&p, 0)
p.disconnected(now: 0.5); p.sample(-90, now: 1)
check(step(&p, 1) == nil, "reconnect clears partial departure count")
p.sample(-90, now: 4.1); check(step(&p, 4.1) == nil, "stale gap clears partial departure count")
p.sample(-90, now: 5.15); check(step(&p, 5.15) == .lock, "slow fresh callback cadence supports two-count locking")
print("Callback-count departure final: \(count) assertions passed; no system effects")

// Every callback matters even when several arrive between timer ticks.
p = PresencePolicy(lockRSSI: -72, unlockRSSI: -68)
p.sample(-80, now: 0); _ = step(&p, 0)
p.sample(-50, now: 0.1); p.sample(-100, now: 0.2)
check(step(&p, 0.25) == nil, "a recovered callback between ticks breaks consecutive far readings")
p.sample(-100, now: 0.3); check(step(&p, 0.5) == .lock, "the next actual far callback completes the pair")
p = PresencePolicy()
p.sample(-90, now: 0.1); p.sample(-90, now: 0.2)
check(step(&p, 0.25) == .lock, "two real callbacks in one timer interval both count")
print("All callback departure checks: \(count) assertions passed; no system effects")
