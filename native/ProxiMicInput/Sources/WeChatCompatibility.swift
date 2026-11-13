import Foundation

// IMK substring windows can include terminal paragraph separators outside the
// editor's native document selection. Compare representations at the selection
// boundary, without trimming either the real document or the model result.
enum EditSourceComparison: String {
    case exact, terminalParagraphs, different

    static func compare(_ cached: EditorTextRange?, with selected: String) -> Self {
        guard let cached, cached.range.location == 0,
              cached.range.length == (cached.text as NSString).length else { return .different }
        if cached.text == selected { return .exact }
        let end = (selected as NSString).length
        guard cached.text.hasPrefix(selected), cached.range.length > end else { return .different }
        let surplus = (cached.text as NSString).substring(from: end)
        return surplus.unicodeScalars.allSatisfy { $0 == "\n" || $0 == "\r" || $0 == "\u{2029}" }
            ? .terminalParagraphs : .different
    }

    /// Explain a disagreement without logging text, hashes, or changing the
    /// comparison used to authorize a write. Offsets are UTF-16, like IMK.
    static func diagnostics(_ cached: EditorTextRange?, with selected: String) -> [String: Any] {
        guard let cached else { return ["source_relation": "unavailable"] }
        let expected = Array(cached.text.utf16), observed = Array(selected.utf16)
        var prefix = 0, suffix = 0
        while prefix < min(expected.count, observed.count), expected[prefix] == observed[prefix] { prefix += 1 }
        while suffix < min(expected.count, observed.count) - prefix,
              expected[expected.count - suffix - 1] == observed[observed.count - suffix - 1] { suffix += 1 }
        func kind(_ value: UInt16?) -> String {
            guard let value else { return "end" }
            switch value {
            case 9: return "tab"
            case 10: return "lf"
            case 13: return "cr"
            case 32: return "space"
            case 0xA0: return "nbsp"
            case 0x2028: return "line_separator"
            case 0x2029: return "paragraph_separator"
            case 0xFFFC: return "object"
            case 0x200B, 0x200C, 0x200D, 0xFEFF: return "zero_width"
            case 0xD800...0xDFFF: return "surrogate"
            default: return "other"
            }
        }
        let relation = expected == observed ? "equal" :
            (prefix == expected.count ? "snapshot_prefix" : (prefix == observed.count ? "selected_prefix" : "different"))
        return ["source_relation": relation, "source_common_prefix": prefix, "source_common_suffix": suffix,
                "source_first_expected_kind": kind(prefix < expected.count ? expected[prefix] : nil),
                "source_first_observed_kind": kind(prefix < observed.count ? observed[prefix] : nil)]
    }
}

/// Default edits use native selection then one IMK commit. Sentence undo selects
/// only its own span. WeChat retains its separate selection/readback handling.
/// Qt 5 ignores document replacement ranges;
/// select through its native commands, then write through the IME selection.
/// The host posts only tagged, process-targeted keys, never clipboard content.
final class WeChatCompatibility {
    typealias SendKey = (String, Int, Int64, @escaping (String?) -> Void) -> Void
    unowned let client: CompositionClient
    let sendKey: SendKey
    let clock: () -> TimeInterval
    let selectBeforeReplacing: Bool
    let prefixOnly: Bool
    private let selectionDriver: PrefixSelectionDriver?
    private let current: () -> Bool
    private var selectionTarget = unspecifiedRange
    private var selectionUsesAX = false
    private var operationID = ""
    private var tracingSelection = false
    private(set) var pending = false
    private(set) var failure: String?
    private(set) var readbackDiagnostics: [String: Any] = [:]
    private(set) var eventTag: Int64 = 0
    private var stage = ""
    private var deadline: TimeInterval = 0
    private var earliest: TimeInterval = 0
    private var keyAcknowledged = false
    private var generation = 0
    private var expectedKeys: [(code: Int, command: Bool, shift: Bool)] = []
    private var keyDeliveryDeadline: TimeInterval = 0
    private var expectedRange = unspecifiedRange
    private var expectedBefore = ""
    private var replacement = ""
    private var result = ""
    private var resultCaret = 0
    private var requestedCaret: Int?
    private var caretMotionOrigin: Int?
    private var caretMotionCount = 0
    private(set) var verifiedReplacementCaret: NSRange?
    private(set) var verifiedReplacement: EditorTextRange?
    private var replacementTextVerified = false
    private var selectionWriteSent = false
    private var requestedReplacement: EditorTextRange?
    private var operationStartedAt: TimeInterval = 0
    private var selectionWrittenAt: TimeInterval = 0
    private var postwriteCaretTarget = unspecifiedRange
    private var writtenRange = unspecifiedRange
    private var writtenCaretObservation = unspecifiedRange
    private var writtenCaretObservedAt: TimeInterval = 0
    private var postwriteTail = ""
    private var baselineCaret = unspecifiedRange
    private var baselineObservation: EditorTextRange?
    private var baselineObservedAt: TimeInterval = 0
    private var postwriteAXObservation = unspecifiedRange
    private var postwriteAXObservedAt: TimeInterval = 0
    private var postwriteMoveAttempted = false
    private(set) var preparedEditContext: PreparedEditContext?
    private(set) var verifiedDocument: String?
    private var contextInstruction = ""
    private var contextSelectedText = ""
    private var contextPrefix = ""
    private var contextCaret = 0
    private var contextWhole = ""
    private var contextOriginal: EditorSnapshot?
    private var contextSource: EditorTextRange?
    private var contextCandidate: String?

    init(client: CompositionClient, sendKey: @escaping SendKey,
         clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
         selectBeforeReplacing: Bool = false, prefixOnly: Bool = false,
         selectionDriver: PrefixSelectionDriver? = nil, current: @escaping () -> Bool = { true }) {
        self.client = client; self.sendKey = sendKey; self.clock = clock
        self.selectBeforeReplacing = selectBeforeReplacing
        self.prefixOnly = prefixOnly; self.selectionDriver = selectionDriver; self.current = current
    }

    func cancel() {
        if pending && tracingSelection { trace("cancelled") }
        generation += 1; pending = false; stage = ""; eventTag = 0; expectedKeys = []
        tracingSelection = false
        selectionUsesAX = false
        verifiedReplacementCaret = nil; verifiedReplacement = nil; requestedReplacement = nil
        replacementTextVerified = false; selectionWriteSent = false
        postwriteCaretTarget = unspecifiedRange; postwriteAXObservation = unspecifiedRange
        writtenRange = unspecifiedRange; writtenCaretObservation = unspecifiedRange
        writtenCaretObservedAt = 0; baselineObservation = nil; baselineObservedAt = 0
        postwriteTail = ""; baselineCaret = unspecifiedRange
        postwriteAXObservedAt = 0; postwriteMoveAttempted = false
        preparedEditContext = nil; contextOriginal = nil; verifiedDocument = nil
    }

    /// Some IMK/Qt events lose CGEvent user data during conversion to NSEvent.
    /// In that case admit only a still-pending exact key/modifier sequence for
    /// 500ms. Never suppress arbitrary typing, repeat keys, or another client.
    func acceptsKey(code: Int, command: Bool, shift: Bool = false, otherModifiers: Bool, tag: Int64) -> Bool {
        guard pending, !otherModifiers, clock() < keyDeliveryDeadline,
              tag == 0 || tag == eventTag,
              let index = expectedKeys.firstIndex(where: { $0.code == code && $0.command == command && $0.shift == shift }) else { return false }
        expectedKeys.removeFirst(index + 1) // AppKit may consume Cmd+A before IMK sees it.
        return true
    }

    func replace(_ range: NSRange, with text: String, undo: Bool) {
        if undo { undoSentence(range, with: text); return }
        guard let before = client.readText(in: range) else { failure = "输入框未提供待替换的原文"; return }
        start(range: range, before: before, text: text)
    }

    func replaceSelected(_ range: NSRange, before: String, with text: String, restoringCaret: Int?) {
        start(range: range, before: before, text: text)
        requestedCaret = restoringCaret
    }

    func prepareContext(instruction: String, selectedText: String, source: EditorTextRange?, candidate: String) {
        cancel(); failure = nil; readbackDiagnostics = [:]
        readbackDiagnostics = ["operation_stage": "context_committed", "write_sent": false,
            "source_range": source.map { [$0.range.location, $0.range.length] } ?? [-1, -1],
            "instruction_characters": instruction.utf16.count, "candidate_characters": candidate.utf16.count,
            "caret_restore_limit": 512]
        contextInstruction = instruction; contextSelectedText = selectedText
        contextSource = source; contextCandidate = candidate
        contextPrefix = ""; contextWhole = ""; contextCaret = 0
        operationStartedAt = clock()
        pending = true; stage = "context_committed"
        deadline = clock() + 2.5; earliest = clock(); keyAcknowledged = true
    }

    func discardEditCandidate() { contextCandidate = nil }

    func replaceNative(_ range: NSRange, with text: String, restoringCaret: Int? = nil, write: () -> Void) {
        if selectBeforeReplacing {
            // Custom editors can apply an explicit IMK range as a partial replacement
            // while its cache appears to contain the full result. Its native
            // selection gives the editor an unambiguous replacement target.
            guard let before = client.readText(in: range) else { failure = "输入框未提供待替换的原文"; return }
            start(range: range, before: before, text: text)
            requestedCaret = restoringCaret
            return
        }
        cancel(); failure = nil
        readbackDiagnostics = [:]
        let token = generation
        let probe = client.probe()
        guard generation == token else { return }
        guard isValidRange(range), !text.isEmpty,
              isValidRange(probe.selection), probe.selection.length == 0,
              !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else {
            failure = "输入框原文光标或输入组合已变化，未执行编辑"; return
        }
        result = text
        expectedRange = NSRange(location: range.location, length: (text as NSString).length)
        requestedReplacement = EditorTextRange(text: text, range: expectedRange)
        resultCaret = NSMaxRange(expectedRange)
        requestedCaret = restoringCaret
        caretMotionOrigin = nil; caretMotionCount = 0
        stage = "native_written"; pending = true
        deadline = clock() + 2.5; earliest = clock(); keyAcknowledged = true
        write() // Exactly one document write. No marked-text replacement.
    }

    private func confirmNativeCaret(_ probe: CompositionProbe) {
        let token = generation
        guard isValidRange(probe.selection), probe.selection.length == 0 else { return }
        // Cmd+Down establishes the document end independently of the old caret.
        // Cached text/selection may arrive in separate callbacks; wait only for
        // the resulting position, never require it to match a pre-write offset.
        // Rich editors can expose a trailing paragraph sentinel beyond their
        // visual caret. Do not try to prove EOF by reading past Cmd+Down's
        // result; the acknowledged native command establishes that position.
        guard pending, generation == token else { return }
        guard let requestedCaret else {
            guard probe.selection.location >= NSMaxRange(expectedRange) else { return }
            verifiedReplacementCaret = probe.selection
            verifiedReplacement = requestedReplacement
            pending = false; stage = ""; return
        }
        if probe.selection == NSRange(location: requestedCaret, length: 0) {
            verifiedReplacementCaret = probe.selection
            verifiedReplacement = requestedReplacement
            pending = false; stage = ""; return
        }
        // Cancelling an edit restores a dictated sentence that may be in the
        // middle. Put the caret after that sentence so a second undo deletes it.
        // Chromium can return the pre-replacement caret even after Cmd+Down.
        // Read back each move and correct in either direction instead of
        // treating the first calculated displacement as final. An unchanged
        // cache is not permission to repeat the same move.
        guard probe.selection.location != caretMotionOrigin else { return }
        let motionRange = NSRange(location: min(requestedCaret, probe.selection.location),
                                  length: abs(probe.selection.location - requestedCaret))
        guard requestedCaret >= expectedRange.location, requestedCaret <= NSMaxRange(expectedRange),
              let between = client.readText(in: motionRange),
              pending, generation == token else { return }
        guard between.count > 0, between.count <= 512 else { fail("恢复听写后的光标距离超出移动范围"); return }
        guard caretMotionCount < 4 else { fail("恢复听写的光标仍未稳定，已停止定位"); return }
        caretMotionOrigin = probe.selection.location
        caretMotionCount += 1
        key(probe.selection.location > requestedCaret ? "caret_backward" : "caret_forward",
            count: between.count, next: "native_restore_caret")
    }

    /// Select only the still-owned sentence, then delete once (or restore the
    /// selection it replaced). Never selects/copies the whole field or depends
    /// on Qt's undo grouping. Live preedit cancellation uses IME commit directly.
    func undoSentence(_ range: NSRange, with text: String) {
        cancel(); failure = nil
        readbackDiagnostics = [:]
        let probe = client.probe()
        guard isValidRange(range), range.length > 0,
              probe.selection == NSRange(location: NSMaxRange(range), length: 0),
              !isValidRange(probe.markedRange) || probe.markedRange.length == 0,
              let before = client.readText(in: range),
              (before as NSString).length == range.length, before.count <= 512 else {
            failure = "本句文字或光标已变化，未执行撤销"; return
        }
        expectedRange = range; expectedBefore = before; replacement = text
        requestedReplacement = EditorTextRange(text: text, range: NSRange(location: range.location, length: (text as NSString).length))
        requestedCaret = nil
        resultCaret = range.location + (text as NSString).length
        pending = true; deadline = clock() + 2.5
        key("select_previous", count: before.count, next: "sentence_selected")
    }

    private func start(range: NSRange, before: String, text: String) {
        cancel(); failure = nil
        readbackDiagnostics = ["write_sent": false, "source_range": [range.location, range.length],
                               "caret_restore_limit": 512]
        operationStartedAt = clock()
        guard isValidRange(range), range.length == (before as NSString).length else {
            failure = "输入框原文范围无效"; return
        }
        expectedRange = range; expectedBefore = before; replacement = text
        requestedReplacement = EditorTextRange(text: text, range: NSRange(location: range.location, length: (text as NSString).length))
        requestedCaret = nil
        pending = true
        deadline = clock() + 2.5
        if prefixOnly { selectPrefix(range, next: "capture") }
        else { key("select_all", next: "capture") }
    }

    private func trace(_ event: String) {
        guard tracingSelection else { return }
        readbackDiagnostics["operation_id"] = operationID
        readbackDiagnostics["operation_stage"] = stage
        readbackDiagnostics["operation_ms"] = (clock() - operationStartedAt) * 1000
        selectionDriver?.record(readbackDiagnostics.merging(["event": event]) { _, new in new })
    }

    private func selectPrefix(_ range: NSRange, next: String) {
        operationID = UUID().uuidString; tracingSelection = true
        selectionDriver?.begin(operationID)
        let token = generation
        let probe = client.probe()
        guard pending, generation == token, current(), isValidRange(range),
              isValidRange(probe.selection), probe.selection.length == 0,
              !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else {
            fail("输入框光标或组合状态已变化，未选择上文", code: "selection_state_changed"); return
        }
        selectionTarget = range
        readbackDiagnostics["selection_target"] = [range.location, range.length]
        readbackDiagnostics["selection_caret"] = [probe.selection.location, probe.selection.length]
        stage = next
        trace("selection_begin")
        // No selection operation is needed at an already confirmed empty range.
        if range.length == 0, range == probe.selection {
            selectionUsesAX = false
            readbackDiagnostics["selection_method"] = "existing_caret"
            keyAcknowledged = true; earliest = clock()
            trace("empty_range_ready")
            return
        }
        let attempt = selectionDriver?.select(range, from: probe.selection, current: {
            self.pending && self.generation == token && self.current()
        }) ?? .unavailable("ax_driver_unavailable")
        guard pending, generation == token else { return }
        switch attempt {
        case .submitted:
            selectionUsesAX = true
            readbackDiagnostics["selection_method"] = "ax"
            keyAcknowledged = true; earliest = clock() + 0.04
            trace("ax_submitted")
        case .unavailable(let reason):
            selectionUsesAX = false
            readbackDiagnostics["ax_fallback_reason"] = reason
            // Cmd+Shift+Up selects exactly [0, caret), never an arbitrary span.
            guard range.location == 0, NSMaxRange(range) == probe.selection.location,
                  client.probe().selection == probe.selection, current() else {
                fail("快捷键无法准确选择本次范围，未写入", code: "prefix_fallback_range_unsupported"); return
            }
            readbackDiagnostics["selection_method"] = "host_keyboard"
            trace("keyboard_fallback")
            key("select_to_start", next: next)
        case .uncertain(let reason):
            fail("AX 选区结果或目标不确定，未写入", code: reason)
        }
    }

    private func key(_ command: String, count: Int = 0, next: String) {
        stage = next; keyAcknowledged = false
        readbackDiagnostics["operation_stage"] = next
        readbackDiagnostics["key_acknowledged"] = false
        readbackDiagnostics["key_command"] = command
        readbackDiagnostics["key_count"] = count
        eventTag = Int64.random(in: 1...(1 << 50))
        keyDeliveryDeadline = clock() + 0.5
        switch command {
        case "select_all": expectedKeys = [(0, true, false)]
        case "select_to_start": expectedKeys = [(126, true, true)]
        case "select_previous": expectedKeys = Array(repeating: (123, false, true), count: count)
        case "delete": expectedKeys = [(51, false, false)]
        case "caret_from_end": expectedKeys = [(125, true, false)] + Array(repeating: (123, false, false), count: count)
        case "caret_to_end": expectedKeys = [(125, true, false)]
        case "caret_backward": expectedKeys = Array(repeating: (123, false, false), count: count)
        case "caret_forward": expectedKeys = Array(repeating: (124, false, false), count: count)
        default: expectedKeys = []
        }
        let token = generation
        sendKey(command, count, eventTag) { [weak self] error in
            guard let self, self.pending, self.generation == token else { return }
            if let error { self.fail(error, code: "key_delivery_failed"); return }
            self.keyAcknowledged = true
            self.readbackDiagnostics["key_acknowledged"] = true
            if self.stage == "postwrite_caret", self.postwriteMoveAttempted {
                self.readbackDiagnostics["postwrite_key_acknowledged"] = true
            }
            self.trace("host_key_acknowledged")
            // Posting isn't delivery. Also require selection/text readback.
            self.earliest = self.clock() + 0.04
        }
    }

    private func fail(_ message: String, code: String = "unconfirmed") {
        readbackDiagnostics["failure_code"] = code
        readbackDiagnostics["operation_ms"] = (clock() - operationStartedAt) * 1000
        trace("failed")
        let sent = readbackDiagnostics["write_sent"] as? Bool == true
        failure = tracingSelection
            ? "[\(InputMethodDiagnostics.buildLabel)/\(stage)/\(code)/\(operationID.prefix(8))] \(message)；\(sent ? "写入已发出，未自动重试" : "未写入结果")"
            : message
        tracingSelection = false
        cancel()
    }

    private func selectedDocument() -> String? {
        let token = generation
        let probe = client.probe()
        readbackDiagnostics["operation_stage"] = stage
        readbackDiagnostics["observed_selection"] = isValidRange(probe.selection)
            ? [probe.selection.location, probe.selection.length] : [-1, -1]
        readbackDiagnostics["observed_marked"] = isValidRange(probe.markedRange)
            ? [probe.markedRange.location, probe.markedRange.length] : [-1, -1]
        let range = prefixOnly ? selectionTarget : probe.selection
        let matches = prefixOnly ? confirmedSelection(probe, token: token) :
            (isValidRange(probe.selection) && probe.selection.location == 0)
        guard pending, generation == token,
              !isValidRange(probe.markedRange) || probe.markedRange.length == 0,
              matches, isValidRange(range), range.length <= 512 * 1024 else {
            readbackDiagnostics["selection_matches"] = false
            trace("selection_wait"); return nil
        }
        readbackDiagnostics["selection_matches"] = true
        // A confirmed AX selection can coexist with stale IMK selection geometry.
        // Prefer IMK text, then read AX against the already verified target range.
        var selected = client.readIMKText(in: range)
        guard pending, generation == token, current() else { return nil }
        readbackDiagnostics["source_imk_read"] = client.lastTextReadDiagnostics
        readbackDiagnostics["source_read_method"] = "imk"
        if selected == nil, prefixOnly {
            selected = readAXText(in: range, selection: range)
            readbackDiagnostics["source_read_method"] = selected == nil ? "unavailable" : "ax"
            guard pending, generation == token, current() else { return nil }
            if selected == nil { fail("IMK 和 AX 均未提供完整选区正文，未执行替换", code: "source_read_unavailable"); return nil }
        }
        readbackDiagnostics["source_read_available"] = selected != nil
        readbackDiagnostics["selected_characters"] = selected?.utf16.count ?? -1
        guard pending, generation == token, let value = selected, value.utf16.count == range.length else {
            trace("source_read_wait"); return nil
        }
        // A dropped Cmd+A must not turn a prefix selection or an empty caret
        // into a claimed full document. Check the boundary using both lengths
        // so a surrogate pair at the boundary is not mistaken for no suffix.
        if prefixOnly || selectBeforeReplacing { return value }
        let end = NSMaxRange(probe.selection)
        guard client.readIMKText(in: NSRange(location: end, length: 1)) == nil,
              client.readIMKText(in: NSRange(location: end, length: 2)) == nil else { return nil }
        return value
    }

    private func confirmedSelection(_ probe: CompositionProbe, token: Int) -> Bool {
        let observed: NSRange?
        if selectionUsesAX {
            observed = selectionDriver?.readSelection(current: {
                self.pending && self.generation == token && self.current()
            })
            readbackDiagnostics["selection_verifier"] = "ax"
        } else {
            observed = probe.selection
            readbackDiagnostics["selection_verifier"] = "imk"
        }
        readbackDiagnostics["selection_read_available"] = observed != nil
        readbackDiagnostics["verified_selection"] = observed.map { [$0.location, $0.length] } ?? [-1, -1]
        readbackDiagnostics["imk_selection_matches"] = probe.selection == selectionTarget
        return pending && generation == token && current() && observed == selectionTarget
    }

    private func moveCaret() {
        let string = result as NSString
        let localCaret = resultCaret - (prefixOnly ? selectionTarget.location : 0)
        guard localCaret >= 0, localCaret <= string.length else { fail("输入框恢复光标的位置无效"); return }
        let tail = string.substring(from: localCaret)
        // Key motion uses composed characters; refuse a split grapheme.
        let prefix = string.substring(to: localCaret)
        readbackDiagnostics["tail_characters"] = tail.count
        readbackDiagnostics["tail_utf16"] = tail.utf16.count
        readbackDiagnostics["target_selection"] = [resultCaret, 0]
        readbackDiagnostics["grapheme_boundary"] = (prefix + tail).count == prefix.count + tail.count
        guard (prefix + tail).count == prefix.count + tail.count, tail.count <= 512 else {
            fail("输入框原文过长或光标位于复合字符内部，未移动光标", code: "caret_restore_limit_or_boundary"); return
        }
        let selected = client.selection()
        readbackDiagnostics["observed_selection"] = isValidRange(selected) ? [selected.location, selected.length] : [-1, -1]
        if selected == NSRange(location: resultCaret, length: 0) {
            stage = "verify"; keyAcknowledged = true; earliest = clock()
            return
        }
        if prefixOnly {
            guard selected == NSRange(location: selectionTarget.location + result.utf16.count, length: 0) else {
                fail("替换后光标尚未确认，未移动光标", code: "prefix_caret_unconfirmed"); return
            }
            key("caret_backward", count: tail.count, next: "verify")
        } else { key("caret_from_end", count: tail.count, next: "verify") }
    }

    private func writeSelected(_ observed: String) {
        if prefixOnly {
            let token = generation
            let targetValid = selectionDriver?.validateTarget(current: {
                self.pending && self.generation == token && self.current()
            }) != false
            // AX and IMK calls can pump cancellation callbacks. Probe after
            // target validation and recheck the generation after both calls.
            let probe = client.probe()
            let matches = confirmedSelection(probe, token: token)
            guard targetValid, pending, generation == token, current(),
                  matches,
                  !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else {
                if pending { fail("写入前选区或目标已变化，未替换", code: "prewrite_selection_changed") }
                return
            }
            if selectionTarget.length == 0, result.isEmpty {
                // Backspace at an empty range would delete outside our ownership.
                writtenRange = selectionTarget
                readbackDiagnostics["ack_source"] = "empty_noop"
                beginPostwriteCaretCheck(selectionTarget)
                return
            }
        }
        // capture already read the selected original in this same run-loop
        // turn. No second full-document query before the single native write.
        selectionWriteSent = true
        stage = "written"
        readbackDiagnostics["write_sent"] = true
        readbackDiagnostics["operation_stage"] = "written"
        selectionWrittenAt = clock()
        trace("write_sent")
        if result.isEmpty {
            key("delete", next: "written")
        } else {
            client.commit(result)
            stage = "written"; keyAcknowledged = true; earliest = clock() + 0.04
        }
    }

    private func completeSelectedWrite(_ caret: NSRange) {
        if prefixOnly { beginPostwriteBaseline(caret); return }
        verifiedReplacement = requestedReplacement
        verifiedReplacementCaret = caret
        if !prefixOnly { verifiedDocument = result }
        trace("confirmed"); tracingSelection = false
        pending = false; stage = ""
    }

    /// Establish the inserted span from IMK's settled insertion-end caret,
    /// before any deliberate restoration into the inserted text. Never infer
    /// this length from the model payload (controls may normalize that payload).
    private func confirmPrefixWrite() {
        let token = generation
        let probe = client.probe()
        guard pending, generation == token, current(), selectionWriteSent,
              isValidRange(probe.selection), probe.selection.length == 0,
              probe.selection.location >= selectionTarget.location,
              probe.selection.location - selectionTarget.location <= 512 * 1024,
              !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else {
            trace("write_caret_wait"); return
        }
        readbackDiagnostics["observed_selection"] = [probe.selection.location, probe.selection.length]
        if writtenCaretObservation != probe.selection {
            writtenCaretObservation = probe.selection; writtenCaretObservedAt = clock()
            trace("write_caret_observed"); return
        }
        guard clock() - writtenCaretObservedAt >= 0.04 else { return }
        let localCaret = resultCaret - selectionTarget.location
        guard localCaret >= 0, localCaret <= result.utf16.count else {
            fail("待恢复的光标范围无效", code: "invalid_result_caret"); return
        }
        let tail = (result as NSString).substring(from: localCaret)
        let head = (result as NSString).substring(to: localCaret)
        guard head.count + tail.count == result.count else {
            fail("恢复位置位于复合字符内部，未移动光标", code: "caret_restore_limit_or_boundary"); return
        }
        let actualRange = NSRange(location: selectionTarget.location,
                                  length: probe.selection.location - selectionTarget.location)
        // Only an original selection / requested interior caret needs a tail.
        // Its boundary must still be provable; this is not a model-body check.
        guard tail.utf16.count <= actualRange.length else {
            fail("无法界定恢复光标后保留的文字", code: "postwrite_tail_boundary_unavailable"); return
        }
        if !tail.isEmpty {
            let tailRange = NSRange(location: NSMaxRange(actualRange) - tail.utf16.count, length: tail.utf16.count)
            let observedTail = client.readText(in: tailRange)
            guard pending, generation == token, current() else { return }
            guard observedTail == tail else { trace("postwrite_tail_wait"); return }
        }
        let after = client.probe()
        guard pending, generation == token, current(), after.selection == probe.selection,
              !isValidRange(after.markedRange) || after.markedRange.length == 0 else { return }
        writtenRange = actualRange
        postwriteTail = tail
        resultCaret = NSMaxRange(actualRange) - tail.utf16.count
        replacementTextVerified = false
        readbackDiagnostics["write_sent"] = true
        readbackDiagnostics["ack_source"] = "imk_caret"
        readbackDiagnostics["model_characters"] = result.utf16.count
        readbackDiagnostics["written_range"] = [actualRange.location, actualRange.length]
        readbackDiagnostics["native_text_verified"] = false
        if tail.isEmpty { beginPostwriteCaretCheck(after.selection) }
        else {
            guard tail.count <= 512 else { fail("恢复光标距离超限", code: "caret_restore_limit_or_boundary"); return }
            key("caret_backward", count: tail.count, next: "verify")
        }
    }

    func readAXText(in range: NSRange, selection: NSRange, whole: Bool = false) -> String? {
        let token = generation
        guard prefixOnly, current(), isValidRange(range), range.length <= 512 * 1024 else { return nil }
        let currentRead = { self.generation == token && self.current() }
        let text = whole ? selectionDriver?.readDocument(in: range, selection: selection, current: currentRead)
                         : selectionDriver?.readText(in: range, selection: selection, current: currentRead)
        guard generation == token, current(), let text, text.utf16.count == range.length else { return nil }
        return text
    }

    private func beginPostwriteBaseline(_ caret: NSRange) {
        // B70 may refresh the IMK coordinate during relative arrow repair.
        // Use the final agreed caret, not its pre-move target or model length.
        guard prefixOnly, isValidRange(caret), caret.length == 0,
              caret.location >= selectionTarget.location,
              caret.location - selectionTarget.location <= 512 * 1024 - postwriteTail.utf16.count else {
            fail("写后范围尚未确认，无法保存撤销基准", code: "postwrite_baseline_range_unavailable"); return
        }
        baselineCaret = caret
        writtenRange = NSRange(location: selectionTarget.location,
                               length: caret.location - selectionTarget.location + postwriteTail.utf16.count)
        stage = "postwrite_baseline"; keyAcknowledged = true; earliest = clock()
        deadline = max(deadline, clock() + 0.5)
        readbackDiagnostics["undo_baseline_source"] = "post_caret_imk"
        readbackDiagnostics["baseline_range"] = [writtenRange.location, writtenRange.length]
        readbackDiagnostics["baseline_caret"] = [caret.location, caret.length]
        trace("postwrite_baseline_begin")
        confirmPostwriteBaseline()
    }

    /// Read only after native/AX caret correction, and require a stable exact
    /// read in the same IMK coordinate space. A nil/partial read is never a
    /// model-backed receipt or an empty-document observation.
    private func confirmPostwriteBaseline() {
        let token = generation
        let before = client.probe()
        guard pending, generation == token, current() else { return }
        guard before.selection == baselineCaret,
              !isValidRange(before.markedRange) || before.markedRange.length == 0 else {
            fail("保存撤销基准时光标已变化", code: "postwrite_baseline_caret_changed"); return
        }
        let text = client.readText(in: writtenRange)
        let read = client.lastTextReadDiagnostics
        let source: EditorTextSource = read["read_source"] as? String == "ax" ? .ax : .imk
        guard pending, generation == token, current() else { return }
        readbackDiagnostics["baseline_read_method"] = read["read_source"] ?? "imk"
        readbackDiagnostics["undo_baseline_source"] = "post_caret_" + source.rawValue
        let after = client.probe()
        guard pending, generation == token, current() else { return }
        guard selectionDriver?.validateTarget(current: {
            self.pending && self.generation == token && self.current()
        }) != false else {
            if pending, generation == token { fail("读取撤销基准期间输入目标已变化", code: "target_changed") }
            return
        }
        guard pending, generation == token, current() else { return }
        guard after.selection == before.selection,
              !isValidRange(after.markedRange) || after.markedRange.length == 0 else {
            fail("读取撤销基准期间光标已变化", code: "postwrite_baseline_caret_changed"); return
        }
        readbackDiagnostics["baseline_read_available"] = text != nil
        readbackDiagnostics["baseline_read"] = read
        readbackDiagnostics["baseline_characters"] = text?.utf16.count ?? -1
        guard let text, text.utf16.count == writtenRange.length else {
            baselineObservation = nil
            trace("postwrite_baseline_read_wait")
            if read["ax_attempted"] as? Bool == true {
                fail("IMK 和 AX 均未提供完整的写后正文，无法建立撤销基准", code: "postwrite_baseline_unavailable")
            }
            return
        }
        guard postwriteTail.isEmpty || text.hasSuffix(postwriteTail) else {
            fail("光标恢复后的保留文字边界不一致，未建立撤销基准", code: "postwrite_baseline_tail_changed"); return
        }
        let snapshot = EditorTextRange(text: text, range: writtenRange, source: source)
        if baselineObservation != snapshot {
            baselineObservation = snapshot; baselineObservedAt = clock()
            trace("postwrite_baseline_observed"); return
        }
        guard clock() - baselineObservedAt >= 0.04 else { return }
        // Model comparison is diagnostic only; the receipt carries native text.
        readbackDiagnostics["replacement_text_matches"] = text == result
        readbackDiagnostics["native_text_verified"] = true
        readbackDiagnostics["ack_source"] = "post_caret_" + source.rawValue
        readbackDiagnostics["baseline_stable"] = true
        verifiedReplacement = snapshot
        verifiedReplacementCaret = after.selection
        replacementTextVerified = true
        trace("confirmed"); tracingSelection = false
        pending = false; stage = ""
    }

    private func beginPostwriteCaretCheck(_ caret: NSRange) {
        guard prefixOnly else { completeSelectedWrite(caret); return }
        postwriteCaretTarget = caret
        stage = "postwrite_caret"; keyAcknowledged = true; earliest = clock()
        // A bounded tail for the AX read/key/read round trip, even if IMK's
        // earlier result acknowledgement arrived close to its deadline.
        deadline = max(deadline, clock() + 0.75)
        readbackDiagnostics["postwrite_imk_target"] = [caret.location, caret.length]
        trace("postwrite_caret_begin")
        confirmPostwriteCaret()
    }

    private func confirmPostwriteCaret() {
        let token = generation
        let probe = client.probe()
        guard pending, generation == token, current() else { return }
        readbackDiagnostics["postwrite_imk_selection"] = [probe.selection.location, probe.selection.length]
        guard isValidRange(probe.selection), probe.selection.length == 0,
              !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else {
            fail("写后 IMK 光标或组合状态已变化，未继续定位", code: "postwrite_imk_caret_changed"); return
        }
        if !postwriteMoveAttempted && probe.selection != postwriteCaretTarget {
            fail("移动前 IMK 光标已变化，未发送按键", code: "postwrite_imk_caret_changed"); return
        }
        let ax = selectionDriver?.readSelection(current: {
            self.pending && self.generation == token && self.current()
        })
        // Remote AX reads can pump both cancellation and new IMK state.
        let after = client.probe()
        guard pending, generation == token, current() else { return }
        readbackDiagnostics["postwrite_imk_after_ax"] = [after.selection.location, after.selection.length]
        guard isValidRange(after.selection), after.selection.length == 0,
              !isValidRange(after.markedRange) || after.markedRange.length == 0 else {
            fail("AX 查询期间 IMK 光标已变化，未继续定位", code: "postwrite_imk_caret_changed"); return
        }
        guard selectionDriver?.validateTarget(current: {
            self.pending && self.generation == token && self.current()
        }) != false else {
            if pending, generation == token { fail("写后输入目标已变化，已停止定位", code: "target_changed") }
            return
        }
        guard pending, generation == token, current() else { return }
        readbackDiagnostics["postwrite_ax_selection"] = ax.map { [$0.location, $0.length] } ?? [-1, -1]
        readbackDiagnostics["postwrite_ax_read_available"] = ax != nil
        if postwriteMoveAttempted {
            readbackDiagnostics["postwrite_imk_after_move"] = [after.selection.location, after.selection.length]
            readbackDiagnostics["postwrite_ax_after_move"] = ax.map { [$0.location, $0.length] } ?? [-1, -1]
            // The arrow keys can refresh IMK's coordinate. Record it without
            // chasing a new target or assuming host posting means alignment.
            guard let ax else {
                fail("移动后AX光标不可读，未继续操作", code: "postwrite_caret_read_unavailable"); return
            }
            guard probe.selection == after.selection else {
                fail("移动后采样期间IMK位置变化，未继续操作", code: "postwrite_caret_sample_changed"); return
            }
            if ax == after.selection {
                readbackDiagnostics["postwrite_caret_status"] = "moved_and_verified"
                completeSelectedWrite(after.selection)
            } else {
                readbackDiagnostics["postwrite_caret_status"] = "moved_but_disagreed"
                fail("移动后AX与IMK光标仍不一致，未继续移动", code: "postwrite_caret_not_aligned")
            }
            return
        }
        guard after.selection == postwriteCaretTarget else {
            fail("AX 查询期间 IMK 光标已变化，未发送移动按键", code: "postwrite_imk_caret_changed"); return
        }
        guard let ax else {
            // AX-unavailable applications retain the existing IMK acknowledgement.
            readbackDiagnostics["postwrite_caret_status"] = "ax_unavailable"
            trace("postwrite_caret_skipped"); completeSelectedWrite(after.selection); return
        }
        if ax == postwriteCaretTarget {
            readbackDiagnostics["postwrite_caret_status"] = "already_matched"
            completeSelectedWrite(after.selection); return
        }
        readbackDiagnostics["postwrite_caret_status"] = "mismatch"
        readbackDiagnostics["postwrite_caret_delta"] = postwriteCaretTarget.location - ax.location
        guard ax.length == 0 else { trace("postwrite_ax_selection_wait"); return }
        // Require two unchanged AX observations before one relative key batch.
        if postwriteAXObservation != ax {
            postwriteAXObservation = ax; postwriteAXObservedAt = clock()
            readbackDiagnostics["postwrite_ax_before_move"] = [ax.location, ax.length]
            trace("postwrite_caret_mismatch"); return
        }
        guard clock() - postwriteAXObservedAt >= 0.04 else { return }
        let delta = postwriteCaretTarget.location - ax.location
        guard delta != 0, abs(delta) <= 512 else {
            fail("写后光标移动步数不在1到512范围内，未发送按键", code: "postwrite_caret_delta_invalid"); return
        }
        postwriteMoveAttempted = true
        readbackDiagnostics["postwrite_move_attempted"] = true
        let command = delta > 0 ? "caret_forward" : "caret_backward"
        readbackDiagnostics["postwrite_move_method"] = "host_arrow"
        readbackDiagnostics["postwrite_key_command"] = command
        readbackDiagnostics["postwrite_key_count"] = abs(delta)
        readbackDiagnostics["postwrite_key_requested"] = true
        readbackDiagnostics["postwrite_key_acknowledged"] = false
        trace("postwrite_keys_requested")
        // Do not query AXNumberOfCharacters or set an absolute AX range here.
        // Send the signed difference as one arrow-key batch.
        key(command, count: abs(delta), next: "postwrite_caret")
    }

    func advance() {
        guard pending else { return }
        client.clearTextReadFailure()
        defer {
            if pending, let failure = client.terminalTextReadFailure {
                fail(failure, code: "body_read_unavailable")
            }
        }
        if tracingSelection, (!current() || selectionDriver?.validateTarget(current: current) == false) {
            fail("输入目标已变化，已停止操作", code: "target_changed"); return
        }
        if stage == "postwrite_caret", postwriteMoveAttempted,
           !keyAcknowledged, clock() >= keyDeliveryDeadline {
            fail("写后光标移动请求未收到主程序回执，未重复发送", code: "postwrite_key_ack_timeout"); return
        }
        guard clock() < deadline else {
            if stage == "postwrite_baseline" {
                fail("文字写入已发出，但 IMK 和 AX 均未提供完整稳定的写后正文，无法建立撤销基准",
                     code: baselineObservation == nil ? "postwrite_baseline_unavailable" : "postwrite_baseline_unstable")
                return
            }
            if stage == "postwrite_caret", postwriteMoveAttempted {
                fail("移动已发送，但未取得AX和IMK一致的读回，未继续移动", code: "postwrite_caret_not_aligned"); return
            }
            fail(stage.hasPrefix("native_") ? "输入框未确认编辑结果或末尾光标，已停止本次操作" : (stage.hasPrefix("sentence_") ? "输入框未确认本句选取或撤销结果，已停止本次操作" : "输入框未确认选区或替换结果，已停止本次操作"), code: "operation_timeout")
            return
        }
        guard keyAcknowledged, clock() >= earliest else { return }
        let token = generation
        switch stage {
        case "postwrite_baseline":
            confirmPostwriteBaseline()
        case "postwrite_caret":
            confirmPostwriteCaret()
        case "context_committed":
            let probe = client.probe()
            readbackDiagnostics["observed_selection"] = isValidRange(probe.selection) ? [probe.selection.location, probe.selection.length] : [-1, -1]
            readbackDiagnostics["observed_marked"] = isValidRange(probe.markedRange) ? [probe.markedRange.location, probe.markedRange.length] : [-1, -1]
            guard pending, generation == token, isValidRange(probe.selection), probe.selection.length == 0,
                  !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else { return }
            // Keep the committed caret. Only select the prefix, then verify
            // the selected source and instruction through the existing IMK path.
            contextCaret = probe.selection.location
            readbackDiagnostics["committed_caret"] = contextCaret
            if prefixOnly { selectPrefix(NSRange(location: 0, length: contextCaret), next: "context_whole") }
            else { key("select_all", next: "context_whole") }
        case "context_whole":
            guard let whole = selectedDocument(), pending, generation == token else { return }
            guard contextCaret <= (whole as NSString).length else {
                fail("输入框光标与当前原文不一致，未执行编辑", code: "caret_outside_document"); return
            }
            contextPrefix = (whole as NSString).substring(to: contextCaret)
            readbackDiagnostics["instruction_matches"] = contextPrefix.hasSuffix(contextInstruction)
            guard contextPrefix.hasSuffix(contextInstruction) else {
                fail("当前选区未包含刚才的口述指令，未执行编辑", code: "instruction_mismatch"); return
            }
            contextWhole = whole
            let instructionRange = NSRange(location: (contextPrefix as NSString).length - (contextInstruction as NSString).length,
                                           length: (contextInstruction as NSString).length)
            let source = (whole as NSString).replacingCharacters(in: instructionRange, with: contextSelectedText)
            contextOriginal = EditorSnapshot(text: nil,
                selection: NSRange(location: instructionRange.location, length: (contextSelectedText as NSString).length),
                markedRange: unspecifiedRange, selectedText: contextSelectedText, documentAccess: true,
                readableContext: EditorTextRange(text: source, range: NSRange(location: 0, length: (source as NSString).length)))
            let comparison = EditSourceComparison.compare(contextSource, with: source)
            readbackDiagnostics.merge(["expected_characters": contextSource?.range.length ?? -1,
                "observed_characters": (source as NSString).length,
                "source_expected_characters": contextSource?.range.length ?? -1,
                "source_observed_characters": (source as NSString).length,
                "text_matches": comparison != .different, "source_match": comparison.rawValue]) { _, new in new }
            readbackDiagnostics.merge(EditSourceComparison.diagnostics(contextSource, with: source)) { _, new in new }
            if comparison != .different, let original = contextOriginal, let candidate = contextCandidate {
                // Source and native selection agree. Replace immediately while
                // selected; no restore/reselect cycle, clipboard, or second read.
                preparedEditContext = PreparedEditContext(original: original, dictatedText: whole,
                    caret: NSRange(location: (contextPrefix as NSString).length, length: 0))
                // Model output covers only the text before the instruction's
                // original insertion point. Restore the initial selection and
                // keep all following text verbatim, using this existing read.
                result = candidate + (prefixOnly ? contextSelectedText :
                    (source as NSString).substring(from: instructionRange.location))
                resultCaret = (candidate as NSString).length
                requestedReplacement = EditorTextRange(text: result, range: NSRange(location: 0, length: (result as NSString).length))
                writeSelected(whole)
                return
            }
            let tail = (whole as NSString).substring(from: (contextPrefix as NSString).length)
            readbackDiagnostics["tail_characters"] = tail.count
            readbackDiagnostics["tail_utf16"] = tail.utf16.count
            readbackDiagnostics["target_selection"] = [(contextPrefix as NSString).length, 0]
            guard tail.count <= 512 else { fail("编辑位置距文末过远，未移动光标或调用模型", code: "source_mismatch_restore_limit"); return }
            if prefixOnly {
                fail("编辑上文与模型原文不一致，已保留听写", code: "source_mismatch"); return
            }
            key("caret_from_end", count: tail.count, next: "context_caret")
        case "context_caret":
            let probe = client.probe()
            readbackDiagnostics["observed_selection"] = isValidRange(probe.selection) ? [probe.selection.location, probe.selection.length] : [-1, -1]
            readbackDiagnostics["observed_marked"] = isValidRange(probe.markedRange) ? [probe.markedRange.location, probe.markedRange.length] : [-1, -1]
            guard pending, generation == token, let original = contextOriginal,
                  probe.selection == NSRange(location: (contextPrefix as NSString).length, length: 0),
                  !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else { return }
            preparedEditContext = PreparedEditContext(original: original, dictatedText: contextWhole, caret: probe.selection)
            verifiedDocument = contextWhole
            readbackDiagnostics.merge(["context_characters": (contextWhole as NSString).length,
                "context_prepare_ms": (clock() - operationStartedAt) * 1000, "ack_source": "native_selection"]) { _, new in new }
            pending = false; stage = ""
        case "native_written", "native_caret", "native_restore_caret":
            let probe = client.probe()
            let observed = replacementTextVerified ? result : client.readText(in: expectedRange)
            guard pending, generation == token else { return }
            let numbers: (NSRange) -> [Int] = { isValidRange($0) ? [$0.location, $0.length] : [-1, -1] }
            readbackDiagnostics = ["expected_characters": (result as NSString).length,
                "observed_characters": observed.map { ($0 as NSString).length } ?? -1,
                "text_matches": observed == result, "expected_selection": [requestedCaret ?? resultCaret, 0],
                "observed_selection": numbers(probe.selection), "expected_marked": [-1, -1],
                "observed_marked": numbers(probe.markedRange)]
            guard pending, generation == token, observed == result,
                  !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else { return }
            replacementTextVerified = true
            if stage == "native_written" { key("caret_to_end", next: "native_caret") }
            else { confirmNativeCaret(probe) }
        case "sentence_selected":
            let probe = client.probe()
            guard probe.selection == expectedRange,
                  !isValidRange(probe.markedRange) || probe.markedRange.length == 0,
                  client.readText(in: expectedRange) == expectedBefore else { return }
            if replacement.isEmpty { key("delete", next: "sentence_written") }
            else {
                client.commit(replacement)
                stage = "sentence_written"; keyAcknowledged = true; earliest = clock() + 0.04
            }
        case "sentence_written":
            let probe = client.probe()
            guard probe.selection == NSRange(location: resultCaret, length: 0),
                  !isValidRange(probe.markedRange) || probe.markedRange.length == 0,
                  client.readText(in: NSRange(location: expectedRange.location, length: (replacement as NSString).length)) == replacement else { return }
            pending = false; stage = ""
        case "capture":
            guard let whole = selectedDocument(), prefixOnly || isValidRange(expectedRange, length: (whole as NSString).length) else { return }
            guard pending, generation == token else { return }
            let selectedSource = prefixOnly ? whole : (whole as NSString).substring(with: expectedRange)
            guard selectedSource == expectedBefore else {
                readbackDiagnostics.merge(EditSourceComparison.diagnostics(
                    EditorTextRange(text: expectedBefore, range: expectedRange),
                    with: selectedSource)) { _, new in new }
                fail("输入框原文已变化，未执行替换", code: "replacement_source_mismatch"); return
            }
            result = prefixOnly ? replacement : (whole as NSString).replacingCharacters(in: expectedRange, with: replacement)
            resultCaret = requestedCaret ?? (prefixOnly ? expectedRange.location + result.utf16.count :
                (selectBeforeReplacing ? result.utf16.count : expectedRange.location + replacement.utf16.count))
            let localCaret = resultCaret - (prefixOnly ? expectedRange.location : 0)
            guard localCaret >= 0, localCaret <= result.utf16.count else {
                fail("替换后的光标范围无效，未写入", code: "invalid_result_caret"); return
            }
            let prefix = (result as NSString).substring(to: localCaret)
            let tail = (result as NSString).substring(from: localCaret)
            guard prefix.count + tail.count == result.count, tail.count <= 512 else {
                fail("输入框待恢复光标离文末过远，未修改文字"); return
            }
            writeSelected(whole)
        case "written":
            if prefixOnly { confirmPrefixWrite(); return }
            if selectBeforeReplacing {
                let probe = client.probe()
                let observed = client.readText(in: NSRange(location: prefixOnly ? selectionTarget.location : 0, length: (result as NSString).length))
                guard pending, generation == token else { return }
                let wholeMatches = observed == result
                readbackDiagnostics.merge(["replacement_expected_characters": (result as NSString).length,
                    "replacement_observed_characters": observed.map { ($0 as NSString).length } ?? -1,
                    "expected_characters": (result as NSString).length,
                    "observed_characters": observed.map { ($0 as NSString).length } ?? -1,
                    "text_matches": wholeMatches,
                    "replacement_text_matches": wholeMatches, "ack_source": wholeMatches ? "text" : "selection_collapse",
                    "operation_stage": "written", "observed_selection": isValidRange(probe.selection) ? [probe.selection.location, probe.selection.length] : [-1, -1],
                    "operation_ms": (clock() - operationStartedAt) * 1000,
                    "post_write_ms": (clock() - selectionWrittenAt) * 1000]) { _, new in new }
                // A native selection was verified before a single insertText.
                // If the editor drops its document cache after that write,
                // selection collapse at the result end acknowledges delivery.
                // A dropped write leaves the old nonempty selection in place.
                let caret = NSRange(location: (prefixOnly ? selectionTarget.location : 0) + result.utf16.count, length: 0)
                guard selectionWriteSent,
                      !isValidRange(probe.markedRange) || probe.markedRange.length == 0,
                      wholeMatches || (selectBeforeReplacing && observed == nil && (!prefixOnly || selectionTarget.length > 0) && probe.selection == caret) else { trace("readback_wait"); return }
                replacementTextVerified = true
                readbackDiagnostics["native_text_verified"] = wholeMatches
                if probe.selection == NSRange(location: resultCaret, length: 0) {
                    beginPostwriteCaretCheck(probe.selection)
                } else { moveCaret() }
                return
            }
            guard client.readText(in: NSRange(location: 0, length: (result as NSString).length)) == result,
                  client.selection() == NSRange(location: (result as NSString).length, length: 0) else { return }
            moveCaret()
        case "verify":
            let probe = client.probe()
            readbackDiagnostics["observed_selection"] = isValidRange(probe.selection) ? [probe.selection.location, probe.selection.length] : [-1, -1]
            readbackDiagnostics["observed_marked"] = isValidRange(probe.markedRange) ? [probe.markedRange.location, probe.markedRange.length] : [-1, -1]
            if prefixOnly, isValidRange(writtenRange) {
                guard probe.selection == NSRange(location: resultCaret, length: 0),
                      !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else { return }
                beginPostwriteCaretCheck(probe.selection); return
            }
            if selectBeforeReplacing, replacementTextVerified {
                guard probe.selection == NSRange(location: resultCaret, length: 0),
                      !isValidRange(probe.markedRange) || probe.markedRange.length == 0 else { return }
                beginPostwriteCaretCheck(probe.selection); return
            }
            guard probe.selection == NSRange(location: resultCaret, length: 0),
                  !isValidRange(probe.markedRange) || probe.markedRange.length == 0,
                  client.readText(in: NSRange(location: 0, length: (result as NSString).length)) == result,
                  client.readIMKText(in: NSRange(location: (result as NSString).length, length: 1)) == nil,
                  client.readIMKText(in: NSRange(location: (result as NSString).length, length: 2)) == nil else { return }
            verifiedReplacement = requestedReplacement
            verifiedReplacementCaret = probe.selection
            pending = false; stage = ""
        default: break
        }
    }
}
