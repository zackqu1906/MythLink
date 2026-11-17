/// Keep unsettled IMK callbacks distinct from prepared input sessions. A
/// transient controller may leave an earlier callback pending, but a session
/// that has started reading its client must never regain ownership this way.
final class InputActivation<Controller: AnyObject> {
    private struct Pending {
        weak var controller: Controller?
        let lease: UInt64
    }
    private(set) weak var current: Controller?
    private var generation: UInt64 = 0
    private var currentLease: UInt64 = 0
    private var pending: [Pending] = []

    func begin(_ controller: Controller) -> UInt64 {
        generation &+= 1
        pending.removeAll { $0.controller == nil || $0.controller === controller }
        pending.append(Pending(controller: controller, lease: generation))
        current = controller
        currentLease = generation
        return generation
    }

    func owns(_ controller: Controller, _ lease: UInt64) -> Bool {
        current === controller && currentLease == lease
    }

    /// Called once after the settling window, before any remote client read.
    /// A rejected request expires here; it cannot linger for a later switch.
    func prepare(_ controller: Controller, _ lease: UInt64) -> Bool {
        let registered = pending.contains { $0.controller === controller && $0.lease == lease }
        pending.removeAll { $0.controller == nil || ($0.controller === controller && $0.lease == lease) }
        guard registered, owns(controller, lease) else { return false }
        pending.removeAll()
        return true
    }

    @discardableResult
    func end(_ controller: Controller, _ lease: UInt64) -> Bool {
        // Deactivation/close must also cancel callbacks superseded during the
        // settling window, even when they do not currently own the service.
        pending.removeAll { $0.controller == nil || ($0.controller === controller && $0.lease == lease) }
        guard owns(controller, lease) else { return false }
        current = pending.last?.controller
        currentLease = pending.last?.lease ?? 0
        return true
    }
}

/// A controller may be reused for a different client. Only callbacks known to
/// belong to an older sender are discarded; nil/unknown senders remain valid.
final class InputActivationSenders {
    private(set) var current: AnyObject?
    private var retired: [AnyObject] = []
    func begin(_ sender: AnyObject?) {
        if let old = current, let sender, old !== sender {
            retired.append(old)
            if retired.count > 8 { retired.removeFirst() }
        }
        current = sender
        if let sender { retired.removeAll { $0 === sender } }
    }
    func isKnownRetired(_ sender: AnyObject?) -> Bool {
        guard let sender, current !== sender else { return false }
        return retired.contains { $0 === sender }
    }
}
