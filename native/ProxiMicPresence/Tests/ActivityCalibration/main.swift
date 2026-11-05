import Foundation

var count = 0
func check(_ condition: @autoclosure () -> Bool, _ label: String) {
    count += 1
    if !condition() { fatalError(label) }
}
var c = ActivityCalibration()
c.activity(.keyboardMouse, at: 0, now: 0)
check(c.observe(-60, now: 0, eligible: true, baseline: -70) == nil && c.samples == 0, "disabled by default")
for source in ActivityCalibration.Source.allCases {
    c.configure(mask: source.bit, revision: c.revision + 1, now: 0)
    for other in ActivityCalibration.Source.allCases where other != source {
        c.activity(other, at: 0, now: 0)
    }
    check(c.observe(-70, now: 0, eligible: true, baseline: -70) == nil && c.samples == 0, "other sources cannot authorize near samples")
    var capture: ActivityCalibration.Capture?
    c.activity(source, at: 1, now: 1)
    for i in 1...5 {
        let now = 1 + Double(i - 1) * 0.5
        capture = c.observe(i == 1 ? -66 : -70, now: now, eligible: true, baseline: -70)
        if i < 5 { check(capture == nil, "five distinct readings still required") }
    }
    check(capture?.baseline == -70 && capture?.sources == [source.rawValue], "each enabled source can recalibrate independently")
    let thresholds = ProximityCalibration(baseline: capture?.baseline).thresholds()
    check(thresholds?.lockRSSI == -78 && thresholds?.unlockRSSI == -74, "both thresholds move together")
    check(c.samples == 0, "completed samples cannot be reused")
    c.configure(mask: source.bit, revision: c.revision, now: 4)
    check(c.preventsLock(now: 10, localIdle: .infinity), "baseline save acknowledgement preserves activity protection")
}
c.configure(mask: 7, revision: c.revision + 1, now: 0)
c.activity(.gestures, at: 0, now: 0)
_ = c.observe(-60, now: 0, eligible: true, baseline: -70)
_ = c.observe(-90, now: 0, eligible: true, baseline: -70)
_ = c.observe(-90, now: -1, eligible: true, baseline: -70)
check(c.samples <= 1, "duplicate or backwards timestamps never count")
c.reset(now: 0)
c.activity(.voice, at: 0, now: 0)
_ = c.observe(-60, now: 1, eligible: true, baseline: -70)
_ = c.observe(-70, now: 2.9, eligible: true, baseline: -70)
_ = c.observe(-70, now: 3, eligible: true, baseline: -70)
_ = c.observe(-70, now: 10, eligible: true, baseline: -70)
_ = c.observe(-70, now: 14.9, eligible: true, baseline: -70)
check(c.samples == 2, "activity covers only three seconds; old protection/tail RSSI is excluded")
check(c.preventsLock(now: 9.9, localIdle: .infinity), "one operation protects for ten seconds")
check(!c.preventsLock(now: 10, localIdle: .infinity), "activity protection expires at ten seconds")
check(c.activeSources(now: 3).isEmpty, "sampling stops while ten-second protection remains active")
var expired = c
expired.expire(now: 135)
check(expired.samples == 0, "samples expire after two minutes")
c.activity(.voice, at: 62, now: 62)
_ = c.observe(-70, now: 62, eligible: true, baseline: -70)
_ = c.observe(-70, now: 63, eligible: false, baseline: -70)
check(c.samples == 0 && c.activeSources(now: 63).isEmpty, "lock/disconnect/sleep clears readings and evidence")
_ = c.observe(-70, now: 63.1, eligible: true, baseline: -70)
check(c.samples == 0, "resuming needs new evidence")
c.activity(.gestures, at: 62, now: 63.2)
check(c.activeSources(now: 63.2).isEmpty, "pre-reset evidence is stale")
c.activity(.gestures, at: 64, now: 63.2)
check(c.activeSources(now: 63.2).isEmpty, "future evidence is rejected")
c.configure(mask: 2, revision: c.revision + 1, now: 70)
c.activity(.gestures, at: 70, now: 70)
_ = c.observe(-70, now: 70, eligible: true, baseline: -70)
c.configure(mask: 4, revision: c.revision + 1, now: 71)
check(c.samples == 0 && c.activeSources(now: 71).isEmpty, "changing switches discards partial capture")
for invalid in [Double.nan, .infinity, 127, -101] {
    c.activity(.voice, at: 72, now: 72)
    check(c.observe(invalid, now: 72, eligible: true, baseline: -70) == nil && c.samples == 0, "invalid RSSI excluded")
}
c.configure(mask: 7, revision: c.revision + 1, now: 100)
var mixed: ActivityCalibration.Capture?
for i in 0...4 {
    let now = 100 + Double(i)
    c.activity(i % 2 == 0 ? .voice : .gestures, at: now, now: now)
    mixed = c.observe(-65, now: now, eligible: true, baseline: -70)
}
check(mixed?.sources == ["gestures", "voice"], "enabled evidence can be combined")

// Protection survives both expired sampling evidence and a rejected weak RSSI.
c.reset(now: 0)
c.activity(.voice, at: 0, now: 4)
check(c.activeSources(now: 4).isEmpty && c.preventsLock(now: 9.9, localIdle: .infinity),
      "delayed evidence can protect without authorizing calibration")
check(!c.preventsLock(now: 10, localIdle: .infinity), "delayed evidence cannot extend the original protection deadline")
c.reset(now: 0); c.activity(.voice, at: 0, now: 0)
_ = c.observe(-62, now: 0, eligible: true, baseline: -62)
_ = c.observe(-64, now: 0.5, eligible: true, baseline: -62)
_ = c.observe(-65, now: 1, eligible: true, baseline: -62)
check(c.samples == 3, "near readings can start a capture")
_ = c.observe(-68, now: 1.5, eligible: true, baseline: -62)
check(c.samples == 0 && !c.signalAllowsLearning, "falling below near boundary discards the partial capture")
check(c.preventsLock(now: 9.9, localIdle: .infinity), "freezing learning preserves activity protection")
for i in 4...24 {
    let now = Double(i) * 0.5
    c.activity(.voice, at: now, now: now)
    check(c.observe(-74, now: now, eligible: true, baseline: -62) == nil && c.samples == 0,
          "even renewed activity cannot calibrate a far signal as near")
}
var recovered: ActivityCalibration.Capture?
for i in 0...4 {
    let now = 13 + Double(i)
    c.activity(.voice, at: now, now: now)
    recovered = c.observe(-64, now: now, eligible: true, baseline: -62)
}
check(recovered?.baseline == -64 && recovered?.readings.count == 5,
      "near return with fresh operations still learns modest baseline drift")

// Ordinary reading pauses can still gather enough samples from sparse actions.
c.configure(mask: 1, revision: c.revision + 1, now: 0)
var sparse: ActivityCalibration.Capture?
for t in [0.0, 25, 50, 75, 100] {
    c.activity(.keyboardMouse, at: t, now: t)
    sparse = c.observe(-72, now: t, eligible: true, baseline: -70)
}
check(sparse?.readings.count == 5 && sparse?.duration == 100, "sparse operations accumulate within two minutes")
c.reset(now: 101); c.activity(.keyboardMouse, at: 101, now: 101)
for i in 0...4 { check(c.observe(-70, now: 101 + Double(i) * 0.1, eligible: true, baseline: -70) == nil, "instant burst cannot recalibrate") }
check(c.observe(-70, now: 103, eligible: true, baseline: -70) != nil, "burst needs at least two seconds of coverage")

// Protection works immediately without reaching the baseline-update quota.
c.configure(mask: 2, revision: c.revision + 1, now: 0)
c.activity(.gestures, at: 0, now: 0)
var protection = PresencePolicy(lockRSSI: -60, unlockRSSI: -56, lockCountdownDuration: 0)
for t in [1.0, 4, 7, 9] {
    protection.sample(-95, now: t)
    check(protection.step(now: t, locked: false, connected: true, permitted: true, awake: true,
                          locallyActive: c.preventsLock(now: t, localIdle: .infinity)) == nil,
          "one operation prevents lock even when baseline never updates")
}
check(c.samples == 0, "protection does not require any calibration samples")
protection.sample(-95, now: 10)
check(protection.step(now: 10, locked: false, connected: true, permitted: true, awake: true,
                      locallyActive: c.preventsLock(now: 10, localIdle: .infinity)) == nil, "after protection collect new far samples")
protection.sample(-95, now: 11)
check(protection.step(now: 11, locked: false, connected: true, permitted: true, awake: true) == .lock, "protection does not disable departure forever")
c.activity(.gestures, at: 11.1, now: 11.1)
check(protection.step(now: 11.1, locked: false, connected: true, permitted: true, awake: true,
                      locallyActive: c.preventsLock(now: 11.1, localIdle: .infinity)) == nil && !protection.lockPending,
      "fresh operation cancels a lock already awaiting host acknowledgement")
check(c.preventsLock(now: 21, localIdle: .infinity) && !c.preventsLock(now: 21.2, localIdle: .infinity),
      "another operation restarts protection from its own timestamp without a baseline update")
c.configure(mask: 0, revision: c.revision + 1, now: 40)
check(c.preventsLock(now: 40, localIdle: 9.9), "keyboard protection works with automatic calibration disabled")
check(!c.preventsLock(now: 40, localIdle: 10), "disabled source cannot retain old protection")
var policy = PresencePolicy(lockRSSI: -68, unlockRSSI: -64, lockCountdownDuration: 0)
policy.sample(-75, now: 1); policy.sample(-75, now: 2)
policy.setThresholds(lock: -78, unlock: -74); policy.invalidateSignal()
check(policy.step(now: 3, locked: false, connected: true, permitted: true, awake: true) == nil,
      "old weak readings cannot lock under new baseline")
var wake = ReturnWakePolicy()
wake.sample(-80, now: 0, farThreshold: -78)
wake.sample(-80, now: 1, farThreshold: -78)
_ = wake.step(now: 1, locked: true, connected: true, available: true, locallyActive: false,
              rawRSSI: -80, sampledAt: 1, weakDisconnect: false, farThreshold: -78,
              nearThreshold: -74, awaySamples: 2, lostDelay: 15)
check(wake.awaitingReturn, "departure arms return under new threshold")
check(wake.step(now: 2, locked: true, connected: true, available: true, locallyActive: false,
                rawRSSI: -74, sampledAt: 2, weakDisconnect: false, farThreshold: -78,
                nearThreshold: -74, awaySamples: 2, lostDelay: 15), "single raw reading still confirms return")
// Reproduce leaving immediately after one operation. Apply automatic captures
// exactly as the monitor does, including clearing signal/departure state.
for source in ActivityCalibration.Source.allCases {
    var leaving = ActivityCalibration()
    leaving.configure(mask: source.bit, revision: 1, now: 0)
    leaving.activity(source, at: 0, now: 0)
    var baseline = -62.0
    var departure = PresencePolicy(lockRSSI: -70, unlockRSSI: -66)
    var updates = 0
    var lockAt: Double?
    for i in 0...40 {
        let now = Double(i) * 0.5
        let rssi = now < 0.5 ? -62.0 : -74.0
        if let capture = leaving.observe(rssi, now: now, eligible: true, baseline: baseline) {
            baseline = capture.baseline
            departure.setThresholds(lock: baseline - 8, unlock: baseline - 4)
            departure.invalidateSignal()
            updates += 1
        }
        departure.sample(rssi, now: now)
        let localIdle = source == .keyboardMouse ? now : .infinity
        if departure.step(now: now, locked: false, connected: true, permitted: true, awake: true,
                          locallyActive: leaving.preventsLock(now: now, localIdle: localIdle)) == .lock {
            lockAt = now
            break
        }
    }
    check(updates == 0 && baseline == -62, "departure must not become the new near baseline: \(source)")
    check(lockAt == 15.5, "ten-second protection expires, then fresh departure and countdown lock: \(source)")
}
print("Activity calibration: \(count) assertions passed; no Bluetooth or system actions")
