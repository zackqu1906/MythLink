import Foundation

/// An owned lock lights up only after the host has installed its snap route.
/// Manual locks retain display-only return wake and never acquire unlock rights.
struct ReturnDisplayGate {
    private(set) var wokeForReturn = false

    mutating func step(returnConfirmed: Bool, locked: Bool, ownsLock: Bool, available: Bool,
                       connected: Bool, gestureReady: Bool, token: String, installedToken: String) -> Bool {
        guard returnConfirmed, locked, available else { wokeForReturn = false; return false }
        guard !wokeForReturn, connected else { return false }
        if ownsLock && (!gestureReady || token.isEmpty || installedToken != token) { return false }
        wokeForReturn = true
        return true
    }
}

/// Detect a departure -> return transition. Screen locking alone is not departure
/// evidence. This policy can request display wake, never session unlock.
struct ReturnWakePolicy {
    private(set) var awaitingReturn = false
    private(set) var returnConfirmed = false
    private var departure = DepartureEvidence()
    private var lastProcessedSample: Double?
    private var lostSince: Double?
    private var previouslyLocked = false
    private var observedConnection = false

    var departureSamples: Int { departure.samples }

    mutating func reset() { self = ReturnWakePolicy() }

    mutating func connectionChanged() {
        invalidateSignal(); lostSince = nil
    }

    mutating func invalidateSignal() { departure.reset(); lastProcessedSample = nil }

    mutating func sample(_ smoothedRSSI: Double, now: Double, farThreshold: Double) {
        departure.observe(smoothedRSSI, at: now, threshold: farThreshold)
    }

    mutating func step(now: Double, locked: Bool?, connected: Bool, available: Bool,
                       locallyActive: Bool, rawRSSI: Double?, sampledAt: Double?,
                       weakDisconnect: Bool, farThreshold: Double, nearThreshold: Double,
                       awaySamples: Int, lostDelay: Double) -> Bool {
        guard available, let locked, farThreshold.isFinite, nearThreshold.isFinite,
              nearThreshold > farThreshold else { reset(); return false }
        // Unlock consumes the trip so it cannot wake a later idle lock. Activity
        // clears unconfirmed departure, but preserves an already confirmed
        // return for gesture-recovery retries within this same locked session.
        if previouslyLocked && !locked { reset(); return false }
        if locallyActive {
            let confirmed = locked && returnConfirmed
            reset(); previouslyLocked = locked; returnConfirmed = confirmed
            return false
        }
        previouslyLocked = locked

        if !connected {
            invalidateSignal()
            if weakDisconnect && observedConnection {
                if lostSince == nil { lostSince = now }
                if now - lostSince! >= lostDelay { awaitingReturn = true; returnConfirmed = false }
            } else { lostSince = nil }
            return false
        }
        lostSince = nil
        guard let rawRSSI, rawRSSI.isFinite, (-100 ... -20).contains(rawRSSI), let sampledAt,
              sampledAt <= now, now - sampledAt <= DepartureEvidence.maxSampleGap else {
            invalidateSignal(); return false
        }
        observedConnection = true
        // Repeated timer ticks never count as additional RSSI callbacks.
        if lastProcessedSample == sampledAt { return false }
        lastProcessedSample = sampledAt
        if rawRSSI >= nearThreshold {
            // A fresh raw near reading takes precedence over the lagging EMA.
            // Old far samples must neither delay nor revoke this return.
            departure.reset()
            if awaitingReturn {
                // Consume even while unlocked, so a later idle lock cannot replay it.
                awaitingReturn = false; returnConfirmed = locked
                return locked
            }
        } else if departure.samples >= awaySamples {
            awaitingReturn = true; returnConfirmed = false
        }
        return false
    }
}
