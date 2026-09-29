import Foundation

/// A saved baseline is immutable. Only an explicit ten-second capture replaces it.
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
