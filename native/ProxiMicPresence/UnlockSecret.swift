import Foundation

/// Holds a credential only for one app-owned lock episode. The lock-screen path
/// consumes memory; it never calls Keychain (which can display authorization UI).
final class UnlockSecret {
    private var value: Data?
    func prepare(locked: Bool?, load: () -> Data?) -> Bool {
        discard()
        guard locked == false, let candidate = load(), !candidate.isEmpty else { return false }
        value = candidate
        return true
    }
    func take(locked: Bool?, ownsLock: Bool) -> Data? {
        guard locked == true, ownsLock else { discard(); return nil }
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
