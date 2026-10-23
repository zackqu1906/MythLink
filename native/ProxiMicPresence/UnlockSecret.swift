import Foundation

/// Prepared while this user's session is unlocked, for its next lock of any
/// origin. The lock-screen path consumes memory and never calls Keychain.
final class UnlockSecret {
    private var value: Data?
    var isPrepared: Bool { value != nil }
    func prepare(locked: Bool?, load: () -> Data?) -> Bool {
        guard locked == false else { return false }
        if isPrepared { return true }
        guard let candidate = load(), !candidate.isEmpty else { return false }
        value = candidate
        return true
    }
    func take(locked: Bool?) -> Data? {
        guard locked == true else { discard(); return nil }
        let result = value
        value = nil
        return result
    }
    func discard() {
        if value != nil { value!.resetBytes(in: 0..<value!.count) }
        value = nil
    }
    deinit { discard() }
}
