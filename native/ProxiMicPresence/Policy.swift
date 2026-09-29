import Foundation

/// Count distinct consecutive RSSI callbacks, shared by locking and return detection.
struct DepartureEvidence {
    static let maxSampleGap = 3.0
    private(set) var samples = 0
    private var lastSample: Double?

    mutating func reset() { self = DepartureEvidence() }

    @discardableResult
    mutating func observe(_ rssi: Double, at sampledAt: Double, threshold: Double) -> Bool {
        guard rssi.isFinite, sampledAt.isFinite else { reset(); return false }
        if lastSample == sampledAt { return false }
        if let previous = lastSample, sampledAt < previous || sampledAt - previous > Self.maxSampleGap {
            samples = 0
        }
        lastSample = sampledAt
        samples = rssi < threshold ? min(samples + 1, 60) : 0
        return true
    }
}

/// Pure policy. Neither timers nor system effects live here.
struct PresencePolicy {
    enum Action: Equatable { case lock, unlock }
    var lockRSSI = -78.0
    // Legacy field name: classifies dropped links and display-only returns. Never grants unlock.
    var unlockRSSI = -62.0
    var awaySamples = 2
    var lostDelay = 15.0
    var thresholdsReady = true
    private var disconnectedFromWeakSignal = false
    private var wasConnected = false
    private(set) var disconnectEvidence = "unknown"
    private(set) var lastLockReason = ""
    private(set) var armed = false
    private(set) var ownsLock = false
    private(set) var failed = false
    private(set) var smoothedRSSI: Double?
    private var lastSample: Double?
    private var lostSince: Double?
    private var departure = DepartureEvidence()
    private var lockRequestedAt: Double?
    private var unlockRequestedAt: Double?
    private var previouslyLocked = false

    mutating func sample(_ rssi: Double, now: Double) {
        guard rssi.isFinite, rssi >= -110, rssi < 0 else { invalidateSignal(); return }
        if let previous = lastSample, now - previous > DepartureEvidence.maxSampleGap { invalidateSignal() }
        smoothedRSSI = smoothedRSSI.map { $0 * 0.6 + rssi * 0.4 } ?? rssi
        lastSample = now
        departure.observe(smoothedRSSI!, at: now, threshold: lockRSSI)
    }
    mutating func invalidateSignal() {
        lastSample = nil; smoothedRSSI = nil; departure.reset()
    }
    mutating func disconnected(now: Double) {
        // Latch evidence once, before clearing the last live sample. Repeated
        // connection failures must neither overwrite it nor restart the timer.
        guard wasConnected || smoothedRSSI != nil else { return }
        let fresh = lastSample.map { now >= $0 && now - $0 <= 5 } == true
        disconnectedFromWeakSignal = thresholdsReady && fresh && smoothedRSSI.map {
            // A dropped link within 1 dB of the departure threshold counts as
            // departure evidence, but never override a confirmed near signal.
            $0 <= lockRSSI + 1 && $0 < unlockRSSI
        } == true
        disconnectEvidence = disconnectedFromWeakSignal ? "weak" :
            (thresholdsReady && fresh && smoothedRSSI.map { $0 >= unlockRSSI } == true ? "near" : "unknown")
        wasConnected = false
        invalidateSignal()
        lostSince = nil
    }
    mutating func suspend() {
        armed = false; invalidateSignal()
        lostSince = nil; lockRequestedAt = nil
        disconnectedFromWeakSignal = false; wasConnected = false; disconnectEvidence = "unknown"
    }
    mutating func reset() {
        let ready = thresholdsReady
        self = PresencePolicy(lockRSSI: lockRSSI, unlockRSSI: unlockRSSI,
                              awaySamples: awaySamples, lostDelay: lostDelay)
        thresholdsReady = ready
    }
    init(lockRSSI: Double = -78, unlockRSSI: Double = -62, awaySamples: Int = 2,
         lostDelay: Double = 15) {
        self.lockRSSI = lockRSSI; self.unlockRSSI = unlockRSSI
        self.awaySamples = awaySamples; self.lostDelay = lostDelay
    }
    mutating func fail() { failed = true; lockRequestedAt = nil; unlockRequestedAt = nil }
    mutating func cancelLockRequest() { lockRequestedAt = nil; departure.reset(); lostSince = nil }
    mutating func setThresholds(lock: Double, unlock: Double) {
        if lock != lockRSSI || unlock != unlockRSSI { departure.reset() }
        lockRSSI = lock; unlockRSSI = unlock
    }
    var lockPending: Bool { lockRequestedAt != nil }
    var awaitingGesture: Bool { ownsLock && !failed && unlockRequestedAt == nil }

    /// A fresh explicit gesture is the only entry to unlocking. No RSSI/dwell gate.
    mutating func requestGestureUnlock(now: Double, locked: Bool?, connected: Bool,
                                       permitted: Bool, awake: Bool) -> Action? {
        guard locked == true, previouslyLocked, awaitingGesture,
              connected, permitted, awake else { return nil }
        unlockRequestedAt = now
        return .unlock
    }
    mutating func step(now: Double, locked: Bool?, connected: Bool, permitted: Bool, awake: Bool,
                       locallyActive: Bool = false) -> Action? {
        guard awake, permitted, let locked else {
            departure.reset(); lostSince = nil
            return nil
        }
        if locked && !previouslyLocked {
            ownsLock = lockRequestedAt.map { now - $0 <= 4 } ?? false
            lockRequestedAt = nil
        } else if !locked && previouslyLocked {
            ownsLock = false; armed = false; failed = false
            departure.reset(); unlockRequestedAt = nil
        }
        previouslyLocked = locked
        if let since = lockRequestedAt, now - since > 4 { fail() }
        if let since = unlockRequestedAt, now - since > 5 { fail() }
        guard !failed else { return nil }
        if connected {
            wasConnected = true; disconnectedFromWeakSignal = false; disconnectEvidence = "unknown"
        } else if wasConnected { disconnected(now: now) }
        guard thresholdsReady else { departure.reset(); lostSince = nil; return nil }
        let fresh = connected && lastSample.map { now >= $0 && now - $0 <= DepartureEvidence.maxSampleGap } == true
        if locked { return nil } // Signal strength never grants unlock.
        // A confirmed device connection arms departure monitoring. The stronger
        // near boundary only classifies departure evidence on a dropped link.
        if connected { armed = true }
        guard armed, lockRequestedAt == nil else { return nil }
        // A local user is still at the computer. This veto does not grant unlock.
        if locallyActive { departure.reset(); lostSince = nil; return nil }
        if !connected && disconnectedFromWeakSignal { if lostSince == nil { lostSince = now } }
        else { lostSince = nil }
        let lost = lostSince.map { now - $0 >= lostDelay } == true
        if !fresh { departure.reset() }
        if lost || departure.samples >= awaySamples {
            lastLockReason = lost ? "disconnected" : "weak_signal"
            lockRequestedAt = now
            return .lock
        }
        return nil
    }
}
