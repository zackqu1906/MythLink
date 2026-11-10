import Foundation

/// Undo history for one continuously owned editor activation. No polling,
/// I/O or document reads are added to BEGIN or partial updates.
final class CompositionUndoHistory {
    struct Snapshot {
        fileprivate let entries: [CompositionUndoRecord]
        fileprivate let current: CompositionUndoRecord?
        fileprivate let future: [CompositionRedoRecord]
        func withoutRedo() -> Snapshot { Snapshot(entries: entries, current: current, future: []) }
    }
    let enabled = true
    private(set) var redoEnabled = true
    private var entries: [CompositionUndoRecord] = []
    private var current: CompositionUndoRecord?
    private var future: [CompositionRedoRecord] = []
    private var undoing: CompositionUndoRecord?
    private var redoing: CompositionRedoRecord?
    private var redoPrevious: CompositionUndoRecord?
    private var redoFromPast = false
    private var generation = 0
    var count: Int { entries.count }
    var redoCount: Int { future.count }
    var canRedo: Bool { redoEnabled && !future.isEmpty && undoing == nil && redoing == nil }
    var totalRetainedUnits: Int {
        entries.reduce(current?.retainedUnits ?? 0) { $0 + $1.retainedUnits } +
            future.reduce(0) { $0 + $1.result.retainedUnits + $1.before.text.utf16.count }
    }

    // Compatibility with older hosts: history can no longer be disabled.
    func configure(enabled: Bool) {
    }

    func configureRedo(enabled: Bool) {
        redoEnabled = enabled
        if !enabled {
            future.removeAll()
            undoing = nil
            // An already-issued write must finish verification. Keep its past
            // bookkeeping, but never retain a future while the switch is off.
        }
    }

    func clear() {
        generation += 1
        entries.removeAll(keepingCapacity: false)
        current = nil
        future.removeAll()
        undoing = nil
        redoing = nil
        redoPrevious = nil
        redoFromPast = false
    }

    func snapshot() -> Snapshot { Snapshot(entries: entries, current: current, future: future) }

    func restore(_ snapshot: Snapshot) {
        clear()
        entries = snapshot.entries
        current = snapshot.current
        future = redoEnabled ? snapshot.future : []
    }

    func settled(_ session: CompositionSession, sourceUtteranceID: String? = nil) {
        let token = generation
        let next = session.undoRecord(sourceUtteranceID: sourceUtteranceID)
        if let undoing {
            let step = session.redoRecord(afterUndoing: undoing)
            // IMK reads can pump deactivation/new-BEGIN callbacks. Never
            // resurrect history after ownership changed during the receipt.
            guard token == generation else { return }
            if redoEnabled, self.undoing == undoing, let step { future.append(step) }
            else { future.removeAll() }
        } else if let redoing {
            if let previous = redoPrevious, previous.sourceUtteranceID != redoing.result.sourceUtteranceID {
                entries.append(previous)
            } else if redoFromPast {
                // A cancelled intervening recording may have archived this
                // same sentence at BEGIN. Redo makes it current again.
                entries.removeLast()
            }
            if !future.isEmpty { future.removeLast() }
        } else if let next, next != current {
            // BEGIN/partial/cancel do not destroy the future. A successful new
            // text operation does; a cancelled polish retaining the same text does not.
            future.removeAll()
        }
        undoing = nil
        redoing = nil
        redoPrevious = nil
        redoFromPast = false
        current = next
        // An unrecordable changed operation is a barrier, not a gap that older
        // undo records may jump across. Empty/undone sentences are transparent.
        if current == nil, session.phase != .undone, session.hasModifiedText { clear() }
    }

    func begin(_ snapshot: EditorSnapshot) {
        // A new sentence can retire an unconfirmed history write. Its outcome
        // is unknown, so it cannot remain a pending step for the new sentence.
        if undoing != nil || redoing != nil { clear(); return }
        generation += 1
        if let step = future.last {
            guard snapshot.selection == step.selection else { clear(); return }
            if let text = snapshot.text {
                guard isValidRange(step.before.range, length: text.utf16.count),
                      (text as NSString).substring(with: step.before.range) == step.before.text else { clear(); return }
            }
        }
        if let record = current {
            guard snapshot.selection == record.selection, snapshot.startError == nil else { clear(); return }
            // Reuse the mandatory BEGIN snapshot if it includes this range;
            // do not make another native read or scan earlier history entries.
            let range = record.editedText.map {
                NSRange(location: record.original.readableContext!.range.location,
                        length: ($0 as NSString).length)
            } ?? record.committedRange ?? NSRange(location: record.original.selection.location, length: (record.raw as NSString).length)
            if let text = snapshot.text {
                guard isValidRange(range, length: (text as NSString).length),
                      (text as NSString).substring(with: range) == (record.editedText ?? record.raw) else { clear(); return }
            }
            entries.append(record)
            current = nil
        } else if let top = entries.last, snapshot.selection != top.selection {
            clear()
        }
    }

    func pop() -> CompositionUndoRecord? {
        guard let record = entries.popLast() else { return nil }
        current = nil
        return record
    }

    func undo(_ session: CompositionSession, sourceUtteranceID: String? = nil) {
        guard redoing == nil, undoing == nil else { return }
        undoing = redoEnabled && session.historyReady ? session.undoRecord(sourceUtteranceID: sourceUtteranceID) : nil
        session.cancel()
        // Rejected/no-op cancellation must not label a later operation as undo.
        if !session.awaitingReadback { undoing = nil }
    }

    func prepareRedo(_ session: CompositionSession, sourceUtteranceID: String? = nil) -> CompositionRedoRecord? {
        guard session.historyReady, canRedo, let step = future.last else { return nil }
        redoPrevious = session.undoRecord(sourceUtteranceID: sourceUtteranceID)
        redoFromPast = redoPrevious == nil && entries.last?.sourceUtteranceID == step.result.sourceUtteranceID
        redoing = step
        return step
    }
}
