import Foundation

/// One authenticated, nonprinting event refreshes an application's input
/// context. It is consumed before controller activation preparation completes.
final class InputContextKey {
    static let keyCode: UInt16 = 90 // F20; never a printable or navigation key.
    struct Request {
        let id: String
        let tag: Int64
        let epoch: String
        let application: String
        let pid: Int32
        let expires: TimeInterval
    }
    private(set) var pending: Request?
    private var reserved: [Int64: TimeInterval] = [:]
    var now: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }

    func arm(id: String, tag: Int64, epoch: String, application: String, pid: Int32) -> Bool {
        reserved = reserved.filter { $0.value > now() }
        guard !id.isEmpty, id.count <= 128, tag > 0, !epoch.isEmpty, !application.isEmpty,
              pid > 0, pending == nil || pending!.expires <= now() else { return false }
        pending = Request(id: id, tag: tag, epoch: epoch, application: application, pid: pid, expires: now() + 1)
        reserved[tag] = now() + 2
        return true
    }

    func consumes(code: UInt16, tag: Int64) -> Bool {
        code == Self.keyCode && (reserved[tag] ?? 0) > now()
    }

    func take(tag: Int64) -> Request? {
        guard let request = pending, request.tag == tag else { return nil }
        pending = nil
        return request.expires > now() ? request : nil
    }

    func cancel(id: String? = nil) {
        if id == nil || pending?.id == id { pending = nil }
        // A late, already posted event must still be swallowed after timeout.
    }
}
