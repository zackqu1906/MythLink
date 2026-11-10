import Foundation

/// Manual capture and shared baseline-to-threshold conversion.
struct ProximityCalibration {
    static let duration = 10.0
    static let minimumSamples = 10
    struct Result: Equatable {
        var lockRSSI: Double
        var unlockRSSI: Double
        var baseline: Double
    }
    private(set) var baseline: Double?
    private(set) var readings: [Double] = []
    private(set) var startedAt: Double?
    private var firstSampleAt: Double?
    private var lastSampleAt: Double?
    var samples: Int { readings.count }
    var qualified: Bool { baseline != nil }
    init(baseline: Double? = nil) {
        if let baseline, baseline.isFinite, (-100 ... -20).contains(baseline) {
            self.baseline = baseline
        }
    }
    mutating func begin(now: Double) {
        readings = []; startedAt = now; firstSampleAt = nil; lastSampleAt = nil
    }
    mutating func observe(_ rssi: Double, now: Double) {
        guard let start = startedAt, now >= start, now - start <= Self.duration,
              rssi.isFinite, (-100 ... -20).contains(rssi) else { return }
        readings.append(rssi); lastSampleAt = now
        if firstSampleAt == nil { firstSampleAt = now }
    }
    mutating func finish(now: Double) -> Bool {
        guard let start = startedAt, now - start >= Self.duration else { return false }
        startedAt = nil
        // Require coverage across the window, not a short burst of readings.
        guard samples >= Self.minimumSamples, let first = firstSampleAt, first - start <= 2,
              let last = lastSampleAt, now - last <= 2 else { return false }
        let sorted = readings.sorted(), middle = samples / 2
        baseline = samples % 2 == 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle]
        return true
    }
    func thresholds() -> Result? {
        guard let baseline else { return nil }
        return Result(lockRSSI: baseline - 8, unlockRSSI: baseline - 4, baseline: baseline)
    }
}

/// A capture over an existing RSSI stream. Completion waits for the host to
/// validate and persist the result before it can replace the live baseline.
struct LiveCalibrationSession {
    private(set) var identifier: String?
    private(set) var requestedAt = 0.0
    private(set) var capture = ProximityCalibration()
    private(set) var awaitingAcceptance = false
    var active: Bool { identifier != nil }

    mutating func begin(identifier: String, now: Double) {
        self = Self()
        self.identifier = identifier; requestedAt = now
    }
    mutating func observe(_ rssi: Double, now: Double) {
        guard active, !awaitingAcceptance, rssi.isFinite, (-100 ... -20).contains(rssi) else { return }
        // No pre-click samples: start the same ten-second capture on the next
        // fresh callback, rather than consuming part of it waiting for BLE.
        if capture.startedAt == nil { capture.begin(now: now) }
        capture.observe(rssi, now: now)
    }
    mutating func finishIfDue(now: Double) -> Bool? {
        guard active, !awaitingAcceptance, let start = capture.startedAt,
              now - start >= ProximityCalibration.duration else { return nil }
        let success = capture.finish(now: now)
        awaitingAcceptance = success
        return success
    }
    func result(for identifier: String) -> ProximityCalibration? {
        self.identifier == identifier && awaitingAcceptance ? capture : nil
    }
    mutating func cancel() { self = Self() }
}

/// Collect RSSI callbacks covered by a fresh, explicitly enabled activity.
/// A short interaction can recalibrate; independent activity protection needs no capture.
struct ActivityCalibration {
    enum Source: String, CaseIterable {
        case keyboardMouse, gestures, voice
        var bit: Int { self == .keyboardMouse ? 1 : self == .gestures ? 2 : 4 }
    }
    struct Capture {
        let readings: [Double]
        let duration: Double
        let sources: [String]
        var baseline: Double {
            let sorted = readings.sorted(), middle = sorted.count / 2
            return sorted.count % 2 == 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle]
        }
    }
    private struct Reading {
        let at: Double
        let rssi: Double
        let sources: [Source]
    }
    // Learning needs fresher evidence than the independent anti-lock lease:
    // the wearer may leave immediately after the last operation.
    static let evidenceLifetime = 3.0
    static let lockProtectionLifetime = 10.0
    static let minimumSamples = 5
    static let minimumDuration = 2.0
    static let window = 120.0
    private(set) var mask = 0
    private(set) var revision = 0
    private var evidence: [Source: Double] = [:]
    private var readings: [Reading] = []
    private var lastSampleAt: Double?
    private var resetAt = -Double.infinity
    var samples: Int { readings.count }

    mutating func configure(mask: Int, revision: Int, now: Double) {
        guard (0...7).contains(mask), revision >= self.revision else { return }
        // Saving an automatic baseline echoes the same options back from the
        // host. That acknowledgement must not erase the current activity lease.
        guard mask != self.mask || revision != self.revision else { return }
        self.mask = mask; self.revision = revision
        reset(now: now)
    }
    mutating func reset(now: Double) {
        evidence.removeAll(); readings.removeAll(); lastSampleAt = nil; resetAt = now
    }
    mutating func activity(_ source: Source, at: Double, now: Double) {
        guard mask & source.bit != 0, at.isFinite, now.isFinite,
              at >= resetAt, at <= now, now - at < Self.lockProtectionLifetime else { return }
        evidence[source] = max(evidence[source] ?? -Double.infinity, at)
    }
    private func sources(now: Double, lifetime: Double) -> [Source] {
        Source.allCases.filter { source in
            guard mask & source.bit != 0, let at = evidence[source] else { return false }
            return at <= now && now - at < lifetime
        }
    }
    func activeSources(now: Double) -> [Source] {
        sources(now: now, lifetime: Self.evidenceLifetime)
    }
    func preventsLock(now: Double, localIdle: Double) -> Bool {
        // Keyboard/mouse protection is independent of automatic calibration switches.
        (localIdle.isFinite && localIdle >= 0 && localIdle < Self.lockProtectionLifetime)
        || !sources(now: now, lifetime: Self.lockProtectionLifetime).isEmpty
    }
    mutating func expire(now: Double) {
        readings.removeAll { now - $0.at > Self.window || $0.at > now }
    }
    mutating func observe(_ rssi: Double, now: Double, eligible: Bool) -> Capture? {
        guard eligible else { reset(now: now); return nil }
        guard mask != 0, now.isFinite, now >= resetAt,
              rssi.isFinite, (-100 ... -20).contains(rssi),
              lastSampleAt.map({ now > $0 }) ?? true else { return nil }
        expire(now: now)
        lastSampleAt = now
        let sources = activeSources(now: now)
        // All selected activity sources qualify equally, regardless of the
        // previous baseline or the size/direction of the signal change.
        guard !sources.isEmpty else { return nil }
        readings.append(Reading(at: now, rssi: rssi, sources: sources))
        if readings.count > 100 { readings.removeFirst(readings.count - 100) }
        guard readings.count >= Self.minimumSamples, let first = readings.first,
              now - first.at >= Self.minimumDuration else { return nil }
        let result = Capture(readings: readings.map(\.rssi), duration: now - first.at,
                             sources: Source.allCases.filter { source in readings.contains { $0.sources.contains(source) } }.map(\.rawValue))
        readings.removeAll()
        return result
    }
}
