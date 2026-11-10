import Foundation

/// Undo history for one continuously owned editor activation. No polling,
/// I/O or document reads are added to BEGIN or partial updates.
final class CompositionUndoHistory {
    struct Snapshot {
        fileprivate let enabled: Bool
        fileprivate let entries: [CompositionUndoRecord]
        fileprivate let current: CompositionUndoRecord?
    }
    private(set) var enabled = false
    private var entries: [CompositionUndoRecord] = []
    private var current: CompositionUndoRecord?
    var count: Int { entries.count }
    var totalRetainedUnits: Int {
        entries.reduce(current?.retainedUnits ?? 0) { $0 + $1.retainedUnits }
    }

    func configure(enabled: Bool) {
        if self.enabled != enabled { clear() }
        self.enabled = enabled
    }

    func clear() {
        entries.removeAll(keepingCapacity: false)
        current = nil
    }

    func snapshot() -> Snapshot { Snapshot(enabled: enabled, entries: entries, current: current) }

    func restore(_ snapshot: Snapshot) {
        clear()
        guard enabled, snapshot.enabled else { return }
        entries = snapshot.entries
        current = snapshot.current
    }

    func settled(_ session: CompositionSession, sourceUtteranceID: String? = nil) {
        guard enabled else { return }
        current = session.undoRecord(sourceUtteranceID: sourceUtteranceID)
        // An unrecordable changed operation is a barrier, not a gap that older
        // undo records may jump across. Empty/undone sentences are transparent.
        if current == nil, session.phase != .undone, session.hasModifiedText { clear() }
    }

    func begin(_ snapshot: EditorSnapshot) {
        guard enabled else { return }
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
        guard enabled, let record = entries.popLast() else { return nil }
        current = nil
        return record
    }
}
