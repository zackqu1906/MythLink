import AppKit
import ApplicationServices

struct ClipboardReadError: Error { let code: String }
typealias ClipboardContents = [[String: Data]]
enum ClipboardReadKey { case prefix, suffix, copy, collapseStart, collapseEnd }

/// Transport is injectable: tests never touch the user's general pasteboard or send keys.
protocol ClipboardReadEnvironment: AnyObject {
    var now: TimeInterval { get }
    var changeCount: Int { get }
    func current() -> Bool
    func selection() -> NSRange
    func marked() -> NSRange
    func backup() throws -> ClipboardContents
    func text() -> String?
    func restore(_ saved: ClipboardContents, replacing count: Int) throws
    func post(_ key: ClipboardReadKey, tag: Int64) throws
    func pump()
}

/// Copies application-owned selections. Native coordinates are used ONLY to
/// confirm/restore the selection; returned text has its own UTF-16 coordinates.
/// A saved clipboard baseline can never silently become an IMK or AX baseline.
final class ClipboardBodyReader {
    let environment: ClipboardReadEnvironment
    private(set) var active = false
    private var cancelled = false
    private var tag: Int64 = 0
    private var retiredTags: [Int64: TimeInterval] = [:]
    private(set) var diagnostics: [String: Any] = [:]
    var onFinished: (() -> Void)?
    var record: (([String: Any]) -> Void)?
    private var stage = ""
    private var started: TimeInterval = 0
    private var stageStarted: TimeInterval = 0
    private var times: [String: Double] = [:]
    init(_ environment: ClipboardReadEnvironment) { self.environment = environment }
    func cancel() { if active { cancelled = true } }
    // nil = ordinary key; false = let an active own key reach the editor;
    // true = consume a late key from a completed/aborted read.
    func handling(tag incoming: Int64) -> Bool? {
        if active, incoming == tag { return false }
        if let expiry = retiredTags[incoming], environment.now <= expiry { return true }
        return nil
    }
    private func enter(_ next: String) {
        if !stage.isEmpty { times[stage, default: 0] += max(0, environment.now - stageStarted) * 1000 }
        stage = next; stageStarted = environment.now
        diagnostics["clipboard_step"] = next
    }
    private func requireCurrent() throws {
        guard !cancelled, environment.current() else { throw ClipboardReadError(code: "target_or_session_changed") }
        let mark = environment.marked()
        guard !isValidRange(mark) || mark.length == 0 else { throw ClipboardReadError(code: "marked_text_active") }
    }
    private func wait(_ predicate: () throws -> Bool, timeout: TimeInterval, failure: String) throws {
        let end = environment.now + timeout
        repeat {
            try requireCurrent()
            if try predicate() { return }
            environment.pump()
        } while environment.now < end
        throw ClipboardReadError(code: failure)
    }

    func read(_ scope: EditorReadScope, validationStage: String) -> EditorTextRange? {
        guard !active else { return nil } // Reentry must not reset an in-flight transaction.
        active = true; cancelled = false; tag = Int64.random(in: 1..<(1 << 50))
        retiredTags = retiredTags.filter { $0.value >= environment.now }
        started = environment.now; stageStarted = started; stage = ""; times = [:]
        diagnostics = ["clipboard_operation_id": UUID().uuidString, "clipboard_scope": String(describing: scope),
            "validation_stage": validationStage, "read_source": "clipboard", "clipboard_result": "pending"]
        var saved: ClipboardContents?
        var ownedCount: Int?
        var original = unspecifiedRange
        var temporary = unspecifiedRange
        var collapse: ClipboardReadKey?
        var collapsePosted = false
        var result: EditorTextRange?
        var failed = false
        func failure(_ error: Error, cleanup: Bool = false) {
            failed = true
            let code = (error as? ClipboardReadError)?.code ?? "transport_error"
            if cleanup {
                diagnostics["clipboard_cleanup_failure"] = code
                diagnostics["clipboard_cleanup_step"] = stage
            } else {
                diagnostics["clipboard_failure"] = code
                diagnostics["clipboard_failure_step"] = stage
            }
        }
        func restoreSelection() throws {
            guard let key = collapse else { return }
            enter("caret_restore")
            try requireCurrent()
            let observed = environment.selection()
            if observed == original { collapse = nil; return }
            // Never send an arrow to an unselected or changed range: that would
            // move the caret instead of collapsing the selection we created.
            guard observed == temporary, temporary.length > 0 else {
                throw ClipboardReadError(code: "caret_restore_selection_changed")
            }
            if !collapsePosted {
                try environment.post(key, tag: tag)
                collapsePosted = true
            }
            try wait({ environment.selection() == original }, timeout: 0.25, failure: "caret_restore_timeout")
            collapse = nil
            diagnostics["clipboard_caret_restored"] = true
        }
        func copyCurrent() throws -> String {
            enter("copy_preflight")
            try requireCurrent()
            let selected = environment.selection()
            guard isValidRange(selected), selected.length > 0 else { throw ClipboardReadError(code: "copy_selection_empty") }
            let previous = environment.changeCount
            if let ownedCount, previous != ownedCount { throw ClipboardReadError(code: "pasteboard_changed_between_copies") }
            enter("copy_wait")
            try environment.post(.copy, tag: tag)
            try wait({ environment.changeCount != previous }, timeout: 0.35, failure: "copy_timeout")
            let copiedCount = environment.changeCount
            // Retain the copy's ownership even if extracting text fails, so
            // cleanup can restore the user's original multi-format contents.
            ownedCount = copiedCount
            guard let value = environment.text() else { throw ClipboardReadError(code: "copy_text_unavailable") }
            guard !value.isEmpty else { throw ClipboardReadError(code: "copy_text_empty") }
            guard value.utf16.count <= 512 * 1024 else { throw ClipboardReadError(code: "copy_text_too_large") }
            try requireCurrent()
            guard environment.changeCount == copiedCount else { throw ClipboardReadError(code: "pasteboard_changed_during_read") }
            guard environment.selection() == selected else { throw ClipboardReadError(code: "copy_selection_changed") }
            diagnostics["clipboard_copies"] = (diagnostics["clipboard_copies"] as? Int ?? 0) + 1
            return value
        }
        func part(prefix: Bool) throws -> String {
            if prefix, original.location == 0 { return "" }
            enter(prefix ? "prefix_select" : "suffix_select")
            try requireCurrent()
            guard environment.selection() == original else { throw ClipboardReadError(code: "selection_changed_before_key") }
            collapse = prefix ? .collapseEnd : .collapseStart
            collapsePosted = false
            try environment.post(prefix ? .prefix : .suffix, tag: tag)
            try wait({
                let selected = environment.selection()
                guard isValidRange(selected), selected.length > 0 else { return false }
                let matches = prefix ? selected.location == 0 && NSMaxRange(selected) == original.location
                    : selected.location == original.location
                // Even a partial prefix selection can be safely collapsed
                // back to the original end on failure, without reading it.
                if prefix ? NSMaxRange(selected) == original.location : selected.location == original.location {
                    temporary = selected
                }
                return matches
            }, timeout: 0.25, failure: "selection_not_confirmed")
            diagnostics["clipboard_selected_range"] = [temporary.location, temporary.length]
            let value = try copyCurrent()
            try restoreSelection()
            return value
        }
        do {
            enter("read_preflight"); try requireCurrent()
            original = environment.selection()
            diagnostics["clipboard_original_selection"] = [original.location, original.length]
            guard isValidRange(original) else { throw ClipboardReadError(code: "selection_unavailable") }
            guard scope != .document else { throw ClipboardReadError(code: "document_boundary_unavailable") }
            guard scope == .selected || original.length == 0 else { throw ClipboardReadError(code: "scope_requires_caret") }
            enter("pasteboard_backup")
            let backupCount = environment.changeCount
            saved = try environment.backup()
            guard environment.changeCount == backupCount else { throw ClipboardReadError(code: "pasteboard_changed_during_backup") }
            ownedCount = backupCount
            let text: String, range: NSRange, selection: NSRange
            switch scope {
            case .selected:
                text = original.length == 0 ? "" : try copyCurrent()
                // A selected span has its own body length; the origin is only
                // bookkeeping, never reused to address an IMK write.
                range = NSRange(location: original.location, length: text.utf16.count); selection = range
            case .prefix(let tail):
                guard tail >= 0, tail <= 512 * 1024 else { throw ClipboardReadError(code: "invalid_scope") }
                let prefix = try part(prefix: true)
                let suffix = tail == 0 ? "" : try part(prefix: false)
                guard suffix.utf16.count >= tail else { throw ClipboardReadError(code: "copied_tail_incomplete") }
                text = prefix + (suffix as NSString).substring(to: tail)
                range = NSRange(location: 0, length: text.utf16.count)
                selection = NSRange(location: prefix.utf16.count, length: 0)
            case .before(let count), .after(let count):
                guard count >= 0, count <= 512 * 1024 else { throw ClipboardReadError(code: "invalid_scope") }
                let prefix = try part(prefix: true)
                let before: Bool
                if case .before = scope { before = true } else { before = false }
                let available = before ? prefix : try part(prefix: false)
                guard available.utf16.count >= count else { throw ClipboardReadError(code: "copied_span_incomplete") }
                text = before ? (available as NSString).substring(from: available.utf16.count - count)
                    : (available as NSString).substring(to: count)
                range = NSRange(location: before ? prefix.utf16.count - count : prefix.utf16.count, length: count)
                selection = NSRange(location: prefix.utf16.count, length: 0)
            case .document: throw ClipboardReadError(code: "document_boundary_unavailable")
            }
            guard text.utf16.count <= 512 * 1024 else { throw ClipboardReadError(code: "copy_text_too_large") }
            try requireCurrent()
            guard environment.selection() == original else { throw ClipboardReadError(code: "caret_restore_mismatch") }
            diagnostics["clipboard_returned_characters"] = text.utf16.count
            result = EditorTextRange(text: text, range: range, source: .clipboard, selection: selection, readScope: scope)
        } catch { failure(error) }
        do { try restoreSelection() } catch { failure(error, cleanup: true) }
        enter("pasteboard_restore")
        if let saved, let ownedCount {
            do { try environment.restore(saved, replacing: ownedCount); diagnostics["clipboard_pasteboard_restored"] = true }
            catch { failure(error, cleanup: true) }
        }
        enter("finished")
        diagnostics["clipboard_stage_ms"] = times
        diagnostics["clipboard_elapsed_ms"] = max(0, environment.now - started) * 1000
        diagnostics["clipboard_result"] = failed ? "failed" : "available"
        diagnostics["event"] = failed ? "clipboard_read_failed" : "clipboard_read_complete"
        record?(diagnostics)
        retiredTags[tag] = environment.now + 2
        active = false
        onFinished?()
        return failed ? nil : result
    }
}

final class NativeClipboardReadEnvironment: ClipboardReadEnvironment {
    let pasteboard: NSPasteboard
    let pid: pid_t
    let bundle: String
    let lease: () -> Bool
    let selected: () -> NSRange
    let markedRange: () -> NSRange
    init(pid: pid_t, bundle: String, pasteboard: NSPasteboard = .general,
         current: @escaping () -> Bool, selection: @escaping () -> NSRange, marked: @escaping () -> NSRange) {
        self.pid = pid; self.bundle = bundle; self.pasteboard = pasteboard
        lease = current; selected = selection; markedRange = marked
    }
    var now: TimeInterval { ProcessInfo.processInfo.systemUptime }
    var changeCount: Int { pasteboard.changeCount }
    func current() -> Bool {
        guard lease(), let app = NSWorkspace.shared.frontmostApplication else { return false }
        return app.processIdentifier == pid && (bundle.isEmpty || app.bundleIdentifier == bundle)
    }
    func selection() -> NSRange { selected() }
    func marked() -> NSRange { markedRange() }
    func backup() throws -> ClipboardContents {
        var size = 0
        let items = pasteboard.pasteboardItems ?? []
        guard items.count <= 128 else { throw ClipboardReadError(code: "pasteboard_backup_too_large") }
        return try items.map { item in
            var result: [String: Data] = [:]
            for type in item.types {
                guard let data = item.data(forType: type) else { throw ClipboardReadError(code: "pasteboard_backup_unavailable") }
                size += data.count
                guard size <= 16 * 1024 * 1024 else { throw ClipboardReadError(code: "pasteboard_backup_too_large") }
                result[type.rawValue] = data
            }
            return result
        }
    }
    func text() -> String? { pasteboard.string(forType: .string) }
    func restore(_ saved: ClipboardContents, replacing count: Int) throws {
        guard changeCount == count else { throw ClipboardReadError(code: "pasteboard_restore_conflict") }
        let existing = try backup()
        guard changeCount == count else { throw ClipboardReadError(code: "pasteboard_restore_conflict") }
        if existing == saved { return }
        let items = saved.map { values -> NSPasteboardItem in
            let item = NSPasteboardItem()
            for (type, data) in values { item.setData(data, forType: NSPasteboard.PasteboardType(type)) }
            return item
        }
        guard changeCount == count else { throw ClipboardReadError(code: "pasteboard_restore_conflict") }
        pasteboard.clearContents()
        guard items.isEmpty || pasteboard.writeObjects(items) else { throw ClipboardReadError(code: "pasteboard_restore_write_failed") }
        guard try backup() == saved else { throw ClipboardReadError(code: "pasteboard_restore_verify_failed") }
    }
    func post(_ key: ClipboardReadKey, tag: Int64) throws {
        guard current() else { throw ClipboardReadError(code: "target_or_session_changed") }
        guard CGPreflightPostEventAccess() else { throw ClipboardReadError(code: "post_event_permission_denied") }
        let code: CGKeyCode, flags: CGEventFlags
        switch key {
        case .prefix: code = 126; flags = [.maskCommand, .maskShift]
        case .suffix: code = 125; flags = [.maskCommand, .maskShift]
        case .copy: code = 8; flags = .maskCommand
        case .collapseStart: code = 123; flags = []
        case .collapseEnd: code = 124; flags = []
        }
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) else {
            throw ClipboardReadError(code: "key_creation_failed")
        }
        for event in [down, up] {
            event.flags = flags; event.setIntegerValueField(.eventSourceUserData, value: tag)
            event.postToPid(pid)
        }
    }
    func pump() { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.005)) }
}
