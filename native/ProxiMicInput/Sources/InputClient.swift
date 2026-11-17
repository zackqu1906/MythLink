import AppKit
import InputMethodKit
import Carbon

final class NativeInputClient: HandoffClient {
    let client: IMKTextInput
    private(set) var activeRead = ""
    private(set) var lastReadDiagnostics: [String: Any] = [:]
    private(set) var terminalTextReadFailure: String?
    func clearTextReadFailure() { terminalTextReadFailure = nil }
    // Model context only, never write authorization. Keep our own confirmed
    // document and subsequent IME changes out of a client's stale suffix cache.
    // Native selection still verifies every candidate before replacement.
    private var modelContextHint: String?
    private var hintedMarkedRange: NSRange?
    private var hintAfterUndo: String?
    private var hintGeneration = 0
    private let isCurrent: () -> Bool
    private let preeditAttributes: ((NSRange) -> [NSAttributedString.Key: Any]?)?
    init(_ client: IMKTextInput, isCurrent: @escaping () -> Bool = { true }, preeditAttributes: ((NSRange) -> [NSAttributedString.Key: Any]?)? = nil) {
        self.client = client
        self.isCurrent = isCurrent
        self.preeditAttributes = preeditAttributes
    }
    private(set) var clipboardReader: ClipboardBodyReader?
    private var clipboardAllowed: () -> Bool = { false }
    private var passiveTextRead = false
    var clipboardReadInProgress: Bool { clipboardReader?.active ?? false }
    func configureClipboardReader(_ reader: ClipboardBodyReader, allowed: @escaping () -> Bool = { true }) {
        clipboardReader = reader; clipboardAllowed = allowed
    }
    func clipboardKeyHandling(tag: Int64) -> Bool? { clipboardReader?.handling(tag: tag) }
    private func readClipboardBody(_ scope: EditorReadScope?, stage: String) -> EditorTextRange? {
        guard let scope, let reader = clipboardReader, !passiveTextRead, clipboardAllowed() else {
            lastReadDiagnostics["clipboard_attempted"] = false
            lastReadDiagnostics["clipboard_failure"] = scope == nil ? "unanchored_range" : "reader_not_enabled_for_operation"
            return nil
        }
        let savedDiagnostics = lastReadDiagnostics
        let body = reader.read(scope, validationStage: stage)
        lastReadDiagnostics = savedDiagnostics
        lastReadDiagnostics["clipboard_attempted"] = true
        lastReadDiagnostics["clipboard_read"] = reader.diagnostics
        lastReadDiagnostics["read_source"] = body == nil ? "unavailable" : "clipboard"
        lastReadDiagnostics["returned_characters"] = body?.text.utf16.count ?? -1
        lastReadDiagnostics["actual_range"] = body.map { rangeNumbers($0.range) } ?? [-1, -1]
        lastReadDiagnostics["body_selection"] = body?.selection.map { rangeNumbers($0) } ?? [-1, -1]
        return body
    }
    var compatibility: WeChatCompatibility?
    // Default edits select the native document before one IMK commit. Explicit
    // replacement ranges alone can leave old text behind in custom editors.
    var nativeCompatibility: WeChatCompatibility?
    var pendingCompatibility: WeChatCompatibility? { compatibility ?? nativeCompatibility }
    func configureKeyDelivery(_ sendKey: @escaping WeChatCompatibility.SendKey,
                              clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
                              selectionDriver: PrefixSelectionDriver? = nil, prefixOnly: Bool = true) {
        let helper = WeChatCompatibility(client: self, sendKey: sendKey, clock: clock,
                                        selectBeforeReplacing: client.bundleIdentifier() != "com.tencent.xinWeChat",
                                        prefixOnly: prefixOnly, selectionDriver: selectionDriver, current: isCurrent)
        if client.bundleIdentifier() == "com.tencent.xinWeChat" { compatibility = helper }
        else { nativeCompatibility = helper }
    }
    // IMK document substring caches can lag live marked text, even while the
    // marked range is current (observed in both Codex and VS Code). Use live
    // composition geometry for every client's partial ACK; committed document
    // edits and undo still require exact text readback.
    var compositionTextReadbackReliable: Bool { false }
    var clientOperationPending: Bool { pendingCompatibility?.pending ?? false }
    var clientOperationFailure: String? { pendingCompatibility?.failure }
    var clientOperationDiagnostics: [String: Any] { pendingCompatibility?.readbackDiagnostics ?? [:] }
    var lastTextReadDiagnostics: [String: Any] { lastReadDiagnostics }
    var verifiedReplacementCaret: NSRange? { pendingCompatibility?.verifiedReplacementCaret }
    var verifiedReplacement: EditorTextRange? { pendingCompatibility?.verifiedReplacement }
    var editsPrefixOnly: Bool { pendingCompatibility?.prefixOnly ?? false }
    var preparesEditContext: Bool { nativeCompatibility?.selectBeforeReplacing ?? false }
    var preparedEditContext: PreparedEditContext? { nativeCompatibility?.preparedEditContext }
    func prepareEditContext(instruction: String, selectedText: String, source: EditorTextRange?, candidate: String) {
        nativeCompatibility?.prepareContext(instruction: instruction, selectedText: selectedText,
                                            source: source, candidate: candidate)
    }
    func discardEditCandidate() { nativeCompatibility?.discardEditCandidate() }
    func replaceEditing(_ range: NSRange, with text: String, restoringCaret: Int?, expected: String) {
        if let compatibility {
            compatibility.replaceSelected(range, before: expected, with: text, restoringCaret: restoringCaret)
        } else if let nativeCompatibility, nativeCompatibility.selectBeforeReplacing {
            nativeCompatibility.replaceSelected(range, before: expected, with: text, restoringCaret: restoringCaret)
        } else { replaceEditing(range, with: text, restoringCaret: restoringCaret) }
    }
    func replaceEditingPrefix(_ before: String, with text: String, tail: Int, restoringCaret: Int?) {
        pendingCompatibility?.replacePrefix(before: before, with: text, tail: tail, restoringCaret: restoringCaret)
    }
    func replaceEditingPrefix(_ baseline: EditorTextRange, with text: String, tail: Int, restoringCaret: Int?, stage: String, preservedSource: EditorTextSource?) {
        pendingCompatibility?.replacePrefix(before: baseline.text, with: text, tail: tail,
            restoringCaret: restoringCaret, baseline: baseline, validationStage: stage, preservedSource: preservedSource)
    }
    func replaceEditing(_ range: NSRange, with text: String, restoringCaret: Int?, baseline: EditorTextRange, stage: String) {
        if let helper = pendingCompatibility, compatibility != nil || helper.selectBeforeReplacing {
            helper.replaceSelected(range, before: baseline.text, with: text, restoringCaret: restoringCaret,
                                   baseline: baseline, validationStage: stage)
        } else { replaceEditing(range, with: text, restoringCaret: restoringCaret) }
    }
    func recordBodyValidation(stage: String, baseline: EditorTextRange, observed: EditorTextRange?, matches: Bool) {
        lastReadDiagnostics["validation_stage"] = stage
        lastReadDiagnostics["baseline_source"] = baseline.source.rawValue
        lastReadDiagnostics["validation_result"] = observed == nil ? "unavailable" : (matches ? "matched" : "mismatch")
        pendingCompatibility?.recordBodyValidation(stage: stage, baseline: baseline, observed: observed,
                                                   matches: matches, read: lastReadDiagnostics)
    }
    func cancelClientOperation() {
        clipboardReader?.cancel()
        if clientOperationPending { discardContextHint() }
        pendingCompatibility?.cancel()
    }
    func discardContextHint() {
        hintGeneration += 1
        modelContextHint = nil; hintedMarkedRange = nil; hintAfterUndo = nil
    }
    func advanceClientOperation() {
        let wasPending = clientOperationPending
        pendingCompatibility?.advance()
        if wasPending, !clientOperationPending, nativeCompatibility != nil {
            if clientOperationFailure != nil { discardContextHint() }
            else if !editsPrefixOnly, let document = nativeCompatibility?.verifiedDocument {
                modelContextHint = document; hintedMarkedRange = nil; hintAfterUndo = nil
            } else if let document = hintAfterUndo {
                modelContextHint = document; hintedMarkedRange = nil; hintAfterUndo = nil
            }
        }
    }
    func undo(_ range: NSRange, with text: String, preferNative: Bool) {
        hintAfterUndo = updatedHint(in: range, with: text)
        modelContextHint = nil; hintedMarkedRange = nil
        if let helper = pendingCompatibility { helper.undoSentence(range, with: text) }
        else { replace(range, with: text) }
    }

    func undo(_ range: NSRange, with text: String, preferNative: Bool, baseline: EditorTextRange?, restoredSource: EditorTextSource?) {
        hintAfterUndo = updatedHint(in: range, with: text)
        modelContextHint = nil; hintedMarkedRange = nil
        if let helper = pendingCompatibility {
            helper.undoSentence(range, with: text, baseline: baseline, restoredSource: restoredSource)
        } else { replace(range, with: text) }
    }

    private func updatedHint(in range: NSRange, with text: String) -> String? {
        guard let document = modelContextHint, isValidRange(range, length: (document as NSString).length) else { return nil }
        let result = (document as NSString).replacingCharacters(in: range, with: text)
        return (result as NSString).length <= 512 * 1024 ? result : nil
    }

    private func rangeNumbers(_ range: NSRange) -> [Int] {
        isValidRange(range) ? [range.location, range.length] : [-1, -1]
    }

    private func documentSelection(_ selected: NSRange, marked: NSRange) -> NSRange {
        // Clients can report the whole composition selected with a cached
        // origin (zero or an earlier composition's start). markedRange is current.
        // Only normalize a positive full-composition selection, never a caret.
        if !compositionTextReadbackReliable, isValidRange(marked), marked.length > 0,
           isValidRange(selected), selected.length == marked.length { return marked }
        return selected
    }

    func snapshot() throws -> EditorSnapshot { try readSnapshot(includeComposition: true) }
    func snapshotForBeginning() throws -> EditorSnapshot { try readSnapshot(includeComposition: false) }
    func startingSelection() -> NSRange { client.selectedRange() }

    /// Only a short, exact prefix is needed for local next-character suggestions.
    /// An unreadable range is distinct from a caret at the start of the field.
    func strokeContext() -> String? {
        passiveTextRead = true
        defer { passiveTextRead = false }
        guard isCurrent() else { return nil }
        let selected = client.selectedRange()
        guard isCurrent(), isValidRange(selected) else { return nil }
        let count = min(8, selected.location)
        let value = readText(in: NSRange(location: selected.location - count, length: count))
        guard isCurrent(), client.selectedRange() == selected, isCurrent() else { return nil }
        return value.map { String($0.suffix(4)) }
    }

    private func validateReadLease() throws {
        guard isCurrent() else { throw CompositionError.interrupted("输入连接已更新，已丢弃旧读取") }
    }

    private func readAXSnapshot(_ scope: EditorReadScope, selection expected: NSRange) throws -> EditorTextRange? {
        var diagnostics = lastReadDiagnostics
        defer { lastReadDiagnostics = diagnostics }
        try validateReadLease()
        let before = probe()
        guard before.selection == expected, !isValidRange(before.markedRange) || before.markedRange.length == 0 else {
            terminalTextReadFailure = "[B84/body_read/geometry_changed] 读取 AX 正文前光标或组合已变化"
            throw CompositionError.interrupted(terminalTextReadFailure!)
        }
        let text = pendingCompatibility?.readAXBody(scope)
        diagnostics["snapshot_ax_read"] = pendingCompatibility?.axReadDiagnostics ?? [:]
        diagnostics["snapshot_body_range"] = text.map { rangeNumbers($0.range) } ?? [-1, -1]
        diagnostics["snapshot_body_selection"] = text?.selection.map { rangeNumbers($0) } ?? [-1, -1]
        try validateReadLease()
        let after = probe()
        try validateReadLease()
        guard after.selection == before.selection, !isValidRange(after.markedRange) || after.markedRange.length == 0 else {
            terminalTextReadFailure = "[B84/body_read/geometry_changed] 读取 AX 正文期间光标或组合已变化"
            throw CompositionError.interrupted(terminalTextReadFailure!)
        }
        if text == nil { terminalTextReadFailure = "[B84/body_read/imk_ax_unavailable] IMK 和 AX 未提供完整正文" }
        return text
    }

    /// IMK and AX each determine their own prefix boundary. Initial context
    /// uses the reader's paired selection, without selecting or changing text.
    private func readBeginningPrefix(selection expected: NSRange) throws -> EditorTextRange? {
        var diagnostics = lastReadDiagnostics
        let previousFailure = terminalTextReadFailure
        defer {
            lastReadDiagnostics = diagnostics
            // Unavailable model context must not disable ordinary dictation.
            terminalTextReadFailure = previousFailure
        }
        try validateReadLease()
        let before = probe()
        try validateReadLease()
        guard before.selection == expected else {
            throw CompositionError.interrupted("[B84/context_read/geometry_changed] 读取编辑上文前光标已变化")
        }
        guard !isValidRange(before.markedRange) || before.markedRange.length == 0 else {
            diagnostics["context_prefix_failure"] = "[B84/context_read/marked_text_active] 输入框仍有未结束的输入组合，未读取编辑上文"
            return nil
        }
        let range = NSRange(location: 0, length: NSMaxRange(expected))
        let body = readBody(in: range, fallback: .prefix(tail: 0), source: nil, stage: "context_read")
        let read = lastReadDiagnostics
        // Reuse the host's existing body-read metadata allowlist so the initial
        // input snapshot retains range, source, AX attempt and failed IMK read.
        diagnostics.merge(read) { _, new in new }
        diagnostics["query"] = "snapshot"
        diagnostics["context_prefix_read"] = true
        try validateReadLease()
        let after = probe()
        try validateReadLease()
        guard after.selection == expected,
              !isValidRange(after.markedRange) || after.markedRange.length == 0 else {
            throw CompositionError.interrupted("[B84/context_read/geometry_changed] 读取编辑上文期间光标或输入组合已变化")
        }
        guard let body else {
            diagnostics["context_prefix_failure"] = read["clipboard_attempted"] as? Bool == true
                ? (terminalTextReadFailure ?? "[B84/context_read/clipboard/read_unavailable] 复制读取未提供完整上文")
                : "[B84/context_read/imk_ax_unavailable] IMK 和 AX 均未提供完整的编辑上文（范围 0,\(range.length)），已保留听写"
            return nil
        }
        return body
    }

    private func readSnapshot(includeComposition: Bool) throws -> EditorSnapshot {
        let readStartedAt = ProcessInfo.processInfo.systemUptime
        try validateReadLease()
        let previousRead = activeRead
        defer { activeRead = previousRead }
        activeRead = "snapshot.selectedRange"
        let reportedSelection = client.selectedRange()
        try validateReadLease()
        activeRead = includeComposition ? "snapshot.markedRange" : "snapshot.beginning"
        let marked = includeComposition ? client.markedRange() : unspecifiedRange
        try validateReadLease()
        let selected = documentSelection(reportedSelection, marked: marked)
        activeRead = "snapshot.supportsProperty"
        let access = client.supportsProperty(TSMDocumentPropertyTag(kTSMDocumentSupportDocumentAccessPropertyTag))
        try validateReadLease()
        var whole: String?
        var readable: EditorTextRange?
        var usedContextHint = false
        var usedBeginningPrefix = false
        var contextSelection: NSRange?
        var contextReadFailure: String?
        activeRead = "snapshot.length"
        let length = client.length()
        try validateReadLease()
        lastReadDiagnostics = ["query": "snapshot", "document_length": length,
                               "reported_selection": rangeNumbers(reportedSelection),
                               "selection": rangeNumbers(selected), "marked_range": rangeNumbers(marked),
                               "document_access": access, "composition_queried": includeComposition, "text_queried": false, "returned_characters": -1,
                               "actual_range": [-1, -1]]
        // Do not misclassify an unavailable/oversized document as an empty one.
        if length == 0, isValidRange(marked), marked.length > 0 {
            // An independently readable composition contradicts an empty full
            // document. Keep local composition usable without claiming context.
            whole = nil
        } else if length > 0, length != NSNotFound, length <= 512 * 1024 {
            var actual = unspecifiedRange
            let desired = NSRange(location: 0, length: length)
            lastReadDiagnostics["text_queried"] = true
            activeRead = "snapshot.documentString"
            let value = client.string(from: desired, actualRange: &actual)
            try validateReadLease()
            lastReadDiagnostics["returned_characters"] = value.map { ($0 as NSString).length } ?? -1
            lastReadDiagnostics["actual_range"] = rangeNumbers(actual)
            if let value, actual == desired, (value as NSString).length == length {
                whole = value
            } else if !isValidRange(marked) || marked.length == 0 {
                let body = try readAXSnapshot(.document, selection: selected)
                try validateReadLease()
                // A full AX value is model context, not permission to write
                // a full IMK document using a different coordinate system.
                if let body { readable = body; contextSelection = body.selection }
                lastReadDiagnostics["snapshot_body_source"] = body == nil ? "unavailable" : "ax"
            }
        }
        if whole == nil, readable == nil, let hint = modelContextHint, isValidRange(selected, length: (hint as NSString).length) {
            usedContextHint = true
            readable = EditorTextRange(text: hint, range: NSRange(location: 0, length: (hint as NSString).length))
            lastReadDiagnostics["context_queries"] = 0
            lastReadDiagnostics["context_characters"] = (hint as NSString).length
        } else if whole == nil, readable == nil {
            var queries = 0
            readable = EditorContextReader.read(around: selected) { requested in
                guard isCurrent() else { return nil }
                queries += 1
                var actual = unspecifiedRange
                activeRead = "snapshot.contextString"
                let value = client.string(from: requested, actualRange: &actual)
                guard isCurrent() else { return nil }
                lastReadDiagnostics["context_requested_range"] = rangeNumbers(requested)
                lastReadDiagnostics["context_actual_range"] = rangeNumbers(actual)
                lastReadDiagnostics["context_returned_characters"] = value.map { ($0 as NSString).length } ?? -1
                return value.map { EditorTextRange(text: $0, range: actual) }
            }
            try validateReadLease()
            lastReadDiagnostics["context_queries"] = queries
            lastReadDiagnostics["context_readable_range"] = readable.map { rangeNumbers($0.range) } ?? [-1, -1]
            lastReadDiagnostics["context_characters"] = readable?.range.length ?? -1
        }
        // Only fill missing initial prefix context. Exploratory suffix/EOF
        // probes, live composition snapshots and historical validation retain
        // their existing semantics. A readable IMK prefix is never replaced.
        if !includeComposition, editsPrefixOnly, whole == nil, !usedContextHint, contextSelection == nil,
           isValidRange(selected), NSMaxRange(selected) > 0,
           readable?.range.location != 0 || (readable?.range.length ?? 0) < NSMaxRange(selected) {
            let prefix = try readBeginningPrefix(selection: selected)
            contextReadFailure = lastReadDiagnostics["context_prefix_failure"] as? String
            if let prefix {
                readable = prefix
                contextSelection = prefix.selection
                usedBeginningPrefix = true
                lastReadDiagnostics["context_readable_range"] = rangeNumbers(prefix.range)
                lastReadDiagnostics["context_characters"] = prefix.range.length
            }
        }
        // Trust IMK's empty field at the origin when no readable text contradicts
        // it. An empty substring may legitimately be nil (as in WeChat). Keep
        // this as a bounded empty source, without claiming a full-document read.
        if whole == nil, readable == nil, access, length == 0,
           selected == NSRange(location: 0, length: 0),
           !isValidRange(marked) || marked.length == 0 {
            readable = EditorTextRange(text: "", range: NSRange(location: 0, length: 0))
            lastReadDiagnostics["context_readable_range"] = [0, 0]
            lastReadDiagnostics["context_characters"] = 0
            lastReadDiagnostics["empty_context_from_imk"] = true
        }
        // A positive marked range can outlive the reported document length.
        // Query that exact range rather than treating an unqueried -1 as nil.
        // Diagnostics deliberately contain only lengths/ranges, never text.
        lastReadDiagnostics["marked_text_queried"] = false
        if isValidRange(marked), marked.length > 0, marked.length <= 512 * 1024 {
            var actual = unspecifiedRange
            activeRead = "snapshot.markedString"
            let value = client.string(from: marked, actualRange: &actual)
            try validateReadLease()
            lastReadDiagnostics["marked_text_queried"] = true
            lastReadDiagnostics["marked_returned_characters"] = value.map { ($0 as NSString).length } ?? -1
            lastReadDiagnostics["marked_actual_range"] = rangeNumbers(actual)
        }
        var selectedText: String?
        if let contextSelection, let readable,
           isValidRange(contextSelection), contextSelection.location >= readable.range.location,
           NSMaxRange(contextSelection) <= NSMaxRange(readable.range) {
            selectedText = (readable.text as NSString).substring(with: NSRange(
                location: contextSelection.location - readable.range.location, length: contextSelection.length))
        } else if let whole, isValidRange(selected, length: (whole as NSString).length) {
            selectedText = (whole as NSString).substring(with: selected)
        } else if isValidRange(selected), selected.length == 0 {
            selectedText = ""
        } else if usedBeginningPrefix, let readable, isValidRange(selected),
                  selected.location >= readable.range.location, NSMaxRange(selected) <= NSMaxRange(readable.range) {
            selectedText = (readable.text as NSString).substring(with: NSRange(
                location: selected.location - readable.range.location, length: selected.length))
        } else if isValidRange(selected) {
            var actual = unspecifiedRange
            activeRead = "snapshot.selectedString"
            if let value = client.string(from: selected, actualRange: &actual), actual == selected,
               (value as NSString).length == selected.length { selectedText = value }
            else if !isValidRange(marked) || marked.length == 0 {
                try validateReadLease()
                selectedText = try readAXSnapshot(.selected, selection: selected)?.text
            }
        }
        try validateReadLease()
        lastReadDiagnostics["context_origin"] = whole != nil ? "document" :
            (usedContextHint ? "confirmed_hint" : (readable != nil ? "bounded" : "unavailable"))
        lastReadDiagnostics["context_complete"] = whole != nil && isValidRange(selected, length: (whole! as NSString).length)
        lastReadDiagnostics["snapshot_ms"] = (ProcessInfo.processInfo.systemUptime - readStartedAt) * 1000
        return EditorSnapshot(text: whole, selection: selected, markedRange: marked,
                              selectedText: selectedText, documentAccess: access, readableContext: readable,
                              contextReadFailure: contextReadFailure, contextSelection: contextSelection)
    }

    func probe() -> CompositionProbe {
        let reportedSelection = client.selectedRange()
        let marked = client.markedRange()
        let selected = documentSelection(reportedSelection, marked: marked)
        lastReadDiagnostics = ["query": "probe", "selection": rangeNumbers(selected),
                               "reported_selection": rangeNumbers(reportedSelection),
                               "marked_range": rangeNumbers(marked), "text_queried": false, "returned_characters": -1,
                               "actual_range": [-1, -1]]
        var markedText: String?
        lastReadDiagnostics["composition_geometry_only"] = !compositionTextReadbackReliable
        if compositionTextReadbackReliable, isValidRange(marked), marked.length <= 512 * 1024 {
            var actual = unspecifiedRange
            lastReadDiagnostics["text_queried"] = true
            let text = client.string(from: marked, actualRange: &actual)
            lastReadDiagnostics["returned_characters"] = text.map { ($0 as NSString).length } ?? -1
            lastReadDiagnostics["actual_range"] = rangeNumbers(actual)
            if let text, actual == marked, (text as NSString).length == marked.length {
                markedText = text
            }
        }
        return CompositionProbe(selection: selected, markedRange: marked, markedText: markedText, reportedSelection: reportedSelection)
    }

    func selection() -> NSRange { documentSelection(client.selectedRange(), marked: client.markedRange()) }
    /// New baselines may choose IMK then AX. Validation supplies the saved
    /// source explicitly: unavailable IMK/AX must never change that source.
    func readText(in range: NSRange) -> String? { readBody(in: range, fallback: nil)?.text }
    func readBody(in range: NSRange, fallback: EditorReadScope?) -> EditorTextRange? {
        readBody(in: range, fallback: fallback, source: nil, stage: "body_read")
    }
    func readBody(in range: NSRange, fallback: EditorReadScope?, source: EditorTextSource?, stage: String) -> EditorTextRange? {
        let readStartedAt = ProcessInfo.processInfo.systemUptime
        defer { lastReadDiagnostics["read_elapsed_ms"] = (ProcessInfo.processInfo.systemUptime - readStartedAt) * 1000 }
        clearTextReadFailure()
        lastReadDiagnostics = ["query": "range", "validation_stage": stage,
            "baseline_source": source?.rawValue ?? "unbound", "read_policy": source == nil ? "imk_then_ax_then_clipboard" : "saved_source",
            "requested_range": rangeNumbers(range)]
        func unavailable(_ reason: String) -> EditorTextRange? {
            lastReadDiagnostics["failure"] = reason
            lastReadDiagnostics["read_source"] = "unavailable"
            lastReadDiagnostics["required_source"] = source?.rawValue ?? "imk_then_ax_then_clipboard"
            terminalTextReadFailure = "[B84/\(stage)/\(source?.rawValue ?? "imk_then_ax_then_clipboard")/\(reason)] 正文来源未提供完整且位置稳定的读取，已停止本次操作"
            return nil
        }
        guard isCurrent(), isValidRange(range) else { return unavailable("invalid_range_or_client") }
        if source == .clipboard {
            let before = probe()
            guard isCurrent(), isValidRange(before.selection),
                  !isValidRange(before.markedRange) || before.markedRange.length == 0 else { return unavailable("geometry_changed") }
            lastReadDiagnostics = ["query": "range", "validation_stage": stage, "baseline_source": "clipboard",
                "read_policy": "saved_source", "imk_attempted": false, "ax_attempted": false]
            let body = readClipboardBody(fallback ?? EditorReadScope.anchored(range, at: before.selection), stage: stage)
            let read = lastReadDiagnostics
            let after = probe()
            lastReadDiagnostics = read
            guard isCurrent(), after.selection == before.selection else { return unavailable("clipboard_geometry_changed") }
            guard let body else {
                let details = read["clipboard_read"] as? [String: Any] ?? [:]
                return unavailable("clipboard_" + (details["clipboard_failure"] as? String ?? details["clipboard_cleanup_failure"] as? String ?? "read_unavailable"))
            }
            return body
        }
        var imkRead: [String: Any] = [:]
        if source != .ax {
            let imkText = readIMKText(in: range)
            imkRead = lastReadDiagnostics
            lastReadDiagnostics["validation_stage"] = stage
            lastReadDiagnostics["baseline_source"] = source?.rawValue ?? "unbound"
            lastReadDiagnostics["read_policy"] = source == nil ? "imk_then_ax_then_clipboard" : "saved_source"
            guard isCurrent() else { return unavailable("client_changed") }
            if let imkText { return EditorTextRange(text: imkText, range: range, readScope: fallback) }
            if source == .imk { return unavailable("read_unavailable") }
        }
        let before = probe()
        guard isCurrent(), isValidRange(before.selection),
              !isValidRange(before.markedRange) || before.markedRange.length == 0 else { return unavailable("geometry_changed") }
        let scope = fallback ?? EditorReadScope.anchored(range, at: before.selection)
        let body = scope.flatMap { pendingCompatibility?.readAXBody($0) }
        let axRead = pendingCompatibility?.axReadDiagnostics ?? [:]
        guard isCurrent() else { return unavailable("client_changed") }
        let after = probe()
        let stable = isCurrent() && before.selection == after.selection &&
            (!isValidRange(after.markedRange) || after.markedRange.length == 0)
        lastReadDiagnostics = ["query": "range", "validation_stage": stage,
            "baseline_source": source?.rawValue ?? "unbound", "read_policy": source == nil ? "imk_then_ax_then_clipboard" : "saved_source",
            "read_source": body != nil && stable ? "ax" : "unavailable",
            "text_queried": true, "requested_range": rangeNumbers(range),
            "returned_characters": stable ? (body?.text.utf16.count ?? -1) : -1,
            "actual_range": body.map { rangeNumbers($0.range) } ?? [-1, -1],
            "body_selection": body?.selection.map { rangeNumbers($0) } ?? [-1, -1],
            "imk_read": imkRead, "imk_attempted": source != .ax,
            "ax_read": axRead, "ax_attempted": scope != nil,
            "ax_scope": scope.map { String(describing: $0) } ?? "unanchored_range",
            "read_selection_stable": stable]
        guard stable else { return unavailable("geometry_changed") }
        if let body { return body }
        guard scope != nil else { return unavailable("unanchored_range") }
        if source == .ax { return unavailable("read_unavailable") }
        let copied = readClipboardBody(scope, stage: stage)
        let read = lastReadDiagnostics
        guard read["clipboard_attempted"] as? Bool == true else { return unavailable("imk_ax_unavailable") }
        let final = probe()
        lastReadDiagnostics = read
        guard isCurrent(), final.selection == before.selection,
              !isValidRange(final.markedRange) || final.markedRange.length == 0 else { return unavailable("clipboard_geometry_changed") }
        if let copied { return copied }
        let details = read["clipboard_read"] as? [String: Any] ?? [:]
        return unavailable(read["clipboard_attempted"] as? Bool == true
            ? "clipboard_" + (details["clipboard_failure"] as? String ?? details["clipboard_cleanup_failure"] as? String ?? "read_unavailable")
            : "imk_ax_unavailable")
    }

    func readIMKText(in range: NSRange) -> String? {
        guard isValidRange(range), range.length <= 512 * 1024 else {
            lastReadDiagnostics = ["query": "range", "read_source": "imk", "text_queried": false,
                "requested_range": rangeNumbers(range), "returned_characters": -1, "failure": "invalid_or_oversized_range"]
            return nil
        }
        // An empty replacement is checked by its caret and cleared marked
        // range; IMK may legitimately return nil for a zero-length substring.
        if range.length == 0 {
            lastReadDiagnostics = ["query": "range", "read_source": "imk", "returned_characters": 0, "text_queried": false]
            return ""
        }
        var actual = unspecifiedRange
        let text = client.string(from: range, actualRange: &actual)
        lastReadDiagnostics = ["query": "range", "read_source": "imk", "text_queried": true,
                               "requested_range": rangeNumbers(range), "actual_range": rangeNumbers(actual),
                               "returned_characters": text.map { ($0 as NSString).length } ?? -1]
        guard let text, actual == range, (text as NSString).length == range.length else { return nil }
        return text
    }
    func mark(_ text: String) {
        if modelContextHint != nil {
            let range = hintedMarkedRange ?? selection()
            modelContextHint = updatedHint(in: range, with: text)
            hintedMarkedRange = modelContextHint == nil ? nil : NSRange(location: range.location, length: (text as NSString).length)
        }
        let range = NSRange(location: 0, length: (text as NSString).length)
        // Use the IMK controller's system marking, including its clause and
        // underline color/style. A hand-written clause 0 + generic underline
        // is not equivalent to the system's selected raw-text marking.
        let attributes = preeditAttributes?(range) ?? [
            .underlineStyle: NSUnderlineStyle.double.rawValue,
            .underlineColor: NSColor.textColor
        ]
        let marked = NSAttributedString(string: text, attributes: attributes)
        client.setMarkedText(marked, selectionRange: NSRange(location: marked.length, length: 0), replacementRange: unspecifiedRange)
    }
    func commit(_ text: String) {
        let generation = hintGeneration
        let hint = modelContextHint == nil ? nil : updatedHint(in: hintedMarkedRange ?? selection(), with: text)
        CompositionCommitDelivery.send(text, markedRange: { client.markedRange() },
                                       clearMarkedText: { mark("") },
                                       insertText: { client.insertText($0, replacementRange: unspecifiedRange) })
        if generation == hintGeneration { modelContextHint = hint; hintedMarkedRange = nil }
    }
    func replace(_ range: NSRange, with text: String) {
        if let compatibility { compatibility.replace(range, with: text, undo: false); return }
        // Committed document writes use insertText in every default client.
        // Empty marked text cancels preedit; it is not a portable deletion API.
        guard isValidRange(range) else { return }
        if let nativeCompatibility, !text.isEmpty || nativeCompatibility.selectBeforeReplacing {
            nativeCompatibility.replaceNative(range, with: text) { self.client.insertText(text, replacementRange: range) }
            return
        }
        CommittedReplacementDelivery.send(text, range: range,
            insert: { client.insertText($0, replacementRange: $1) })
    }
    func replaceEditing(_ range: NSRange, with text: String, restoringCaret: Int?) {
        if let nativeCompatibility, !text.isEmpty || nativeCompatibility.selectBeforeReplacing {
            nativeCompatibility.replaceNative(range, with: text, restoringCaret: restoringCaret) {
                self.client.insertText(text, replacementRange: range)
            }
        } else { replace(range, with: text) }
    }
    func endStaleComposition(at caret: NSRange) {
        discardContextHint()
        guard isValidRange(caret), caret.length == 0 else { return }
        // Empty insertText can be ignored across the IMK client bridge. This
        // activation-only operation updates an unreadable, detached cache at
        // the verified caret without replacing a committed document range.
        client.setMarkedText("", selectionRange: NSRange(location: 0, length: 0), replacementRange: caret)
    }

    private(set) var caretDiagnostics: [String: Any] = [:]
    func caret(compositionLength: Int, trace: Bool = false) -> NSRect? {
        var diagnostic: [String: Any] = [:]
        if trace { diagnostic["composition_length"] = compositionLength }
        defer {
            if trace {
                var candidate = NSRect.zero
                _ = client.attributes(forCharacterIndex: compositionLength, lineHeightRectangle: &candidate)
                diagnostic["candidate_end"] = CaretDiagnostics.rect(candidate)
                var last = NSRect.zero
                _ = client.attributes(forCharacterIndex: max(0, compositionLength - 1), lineHeightRectangle: &last)
                diagnostic["candidate_last"] = CaretDiagnostics.rect(last)
                var actual = unspecifiedRange
                let relative = client.firstRect(forCharacterRange: NSRange(location: compositionLength, length: 0), actualRange: &actual)
                diagnostic["relative_rect"] = CaretDiagnostics.rect(relative)
                diagnostic["relative_actual"] = CaretDiagnostics.range(actual)
                caretDiagnostics = diagnostic
            }
        }
        // A valid IMK candidate anchor may stay at the start of the inline
        // session. Query the live insertion point before that fallback so the
        // palette follows growth, wrapping and scrolling of marked text.
        // markedRange uses document coordinates; selectedRange can instead
        // report a cached, composition-relative selection in bridged clients.
        var insertion = unspecifiedRange
        if compositionLength > 0 {
            let marked = client.markedRange()
            if trace { diagnostic["marked"] = CaretDiagnostics.range(marked) }
            if isValidRange(marked), marked.length > 0 {
                insertion = NSRange(location: NSMaxRange(marked), length: 0)
            }
        }
        if !isValidRange(insertion) {
            let selected = client.selectedRange()
            if trace { diagnostic["selected"] = CaretDiagnostics.range(selected) }
            if isValidRange(selected) {
                insertion = NSRange(location: NSMaxRange(selected), length: 0)
            }
        }
        if isValidRange(insertion) {
            var actual = unspecifiedRange
            let rectangle = client.firstRect(forCharacterRange: insertion, actualRange: &actual)
            if trace {
                diagnostic["query"] = CaretDiagnostics.range(insertion)
                diagnostic["first_rect"] = CaretDiagnostics.rect(rectangle)
                diagnostic["actual"] = CaretDiagnostics.range(actual)
                if insertion.location > 0 {
                    var glyphActual = unspecifiedRange
                    let glyph = client.firstRect(forCharacterRange: NSRange(location: insertion.location - 1, length: 1), actualRange: &glyphActual)
                    diagnostic["last_glyph_rect"] = CaretDiagnostics.rect(glyph)
                    diagnostic["last_glyph_actual"] = CaretDiagnostics.range(glyphActual)
                }
            }
            // A substituted whole-selection/first-line rectangle is not the
            // requested caret, even when it lies on screen.
            if actual == insertion, let rectangle = visibleCaret(rectangle) {
                if trace { diagnostic["source"] = "document_caret" }
                return rectangle
            }
        }
        var rectangle = NSRect.zero
        // The inline API takes a CHARACTER index, not an insertion offset.
        // length is one past the last character and real IMK bridges return
        // NSZeroRect there. Index the final UTF-16 character to follow the
        // current line; with no composition, 0 means the current selection.
        let inlineIndex = max(0, compositionLength - 1)
        _ = client.attributes(forCharacterIndex: inlineIndex, lineHeightRectangle: &rectangle)
        if trace {
            diagnostic["inline_index"] = inlineIndex
            diagnostic["source"] = visibleCaret(rectangle) == nil ? "unavailable" : "inline_character"
        }
        return visibleCaret(rectangle)
    }

    private func visibleCaret(_ rectangle: NSRect) -> NSRect? {
        guard rectangle.width.isFinite, rectangle.height.isFinite,
              rectangle.origin.x.isFinite, rectangle.origin.y.isFinite,
              rectangle.height > 0, rectangle.height < 1000,
              NSScreen.screens.contains(where: { $0.frame.intersects(rectangle.insetBy(dx: -1, dy: -1)) }) else { return nil }
        return rectangle.standardized
    }
}
