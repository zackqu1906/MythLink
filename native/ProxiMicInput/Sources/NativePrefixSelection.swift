import AppKit
import ApplicationServices
import os

/// Resolve provenance from the built bundle so normal releases never retain a
/// frozen test-build number. Standalone test executables use the dev label.
enum InputMethodDiagnostics {
    static let buildNumber = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "dev"
    static var buildLabel: String { "B\(buildNumber)" }
    static var buildValue: Any { Int(buildNumber).map { $0 as Any } ?? buildNumber }
    static let sourceFingerprint = Bundle.main.object(forInfoDictionaryKey: "MythlinkInputMethodSourceFingerprint") as? String
}

enum PrefixSelectionAttempt {
    case submitted
    case unavailable(String)
    case uncertain(String)
}

/// Native selection and a guarded, read-only AX text fallback for edit history.
protocol PrefixSelectionDriver: AnyObject {
    var metadata: [String: Any] { get }
    func begin(_ operation: String)
    func select(_ range: NSRange, from caret: NSRange, current: () -> Bool) -> PrefixSelectionAttempt
    func readSelection(current: () -> Bool) -> NSRange?
    func sampleCaret(current: () -> Bool) -> NSRange?
    func entireSelectionMatches(_ text: String, current: () -> Bool) -> Bool
    func validateTarget(current: () -> Bool) -> Bool
    func validateTarget(current: () -> Bool, step: String) -> Bool
    func record(_ event: [String: Any])
    func readBody(_ scope: EditorReadScope, current: () -> Bool) -> EditorTextRange?
}

extension PrefixSelectionDriver {
    func sampleCaret(current: () -> Bool) -> NSRange? { readSelection(current: current) }
    func entireSelectionMatches(_ text: String, current: () -> Bool) -> Bool {
        guard let doc = readBody(.document, current: current) else { return false }
        return doc.selection == doc.range && doc.text == text
    }
    func validateTarget(current: () -> Bool, step: String) -> Bool { validateTarget(current: current) }
    func readBody(_ scope: EditorReadScope, current: () -> Bool) -> EditorTextRange? { nil }
}

/// One driver is bound to the activation's PID and bundle, never a newly resolved app.
final class NativePrefixSelection: PrefixSelectionDriver {
    private let pid: pid_t
    private let bundle: String
    private let context: () -> [String: Any]
    private var focused: AXUIElement?
    private var app: AXUIElement?
    private var file: FileHandle?
    private var records = 0
    private let logger = Logger(subsystem: "com.proximic.inputmethod.ProxiMicVoice", category: "PrefixSelection")
    private(set) var metadata: [String: Any] = [:]

    init(pid: pid_t, bundle: String, context: @escaping () -> [String: Any] = { [:] }) {
        self.pid = pid; self.bundle = bundle; self.context = context
    }

    func begin(_ operation: String) {
        try? file?.close(); file = nil; records = 0; focused = nil; app = nil
        metadata = ["target_pid": Int(pid), "application": bundle,
                    "ax_trusted": AXIsProcessTrusted(), "key_delivery": "host_ipc"]
        let directory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Mythlink/IMESelection")
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let path = directory.appendingPathComponent("build\(InputMethodDiagnostics.buildNumber)-\(operation).jsonl")
            guard FileManager.default.createFile(atPath: path.path, contents: nil,
                                                  attributes: [.posixPermissions: 0o600]) else {
                metadata["trace_file_error"] = "create_failed"; return
            }
            file = try FileHandle(forWritingTo: path)
            metadata["trace_file"] = path.lastPathComponent
        } catch { metadata["trace_file_error"] = "open_failed" }
    }

    /// Diagnostic only: retain the failed sample even if a later query succeeds.
    /// No additional AX/foreground queries are made to explain a failure.
    private func targetFailed(_ category: String, _ reason: String, step: String,
                              details: [String: Any] = [:]) -> Bool {
        var failure: [String: Any] = ["category": category, "reason": reason, "step": step,
            "expected_pid": Int(pid), "expected_bundle": bundle, "saved_ax_focus": focused != nil]
        failure.merge(details) { _, new in new }
        metadata["target_check_result"] = "failed"
        metadata["target_last_failure"] = failure
        record(["event": "target_check_failed", "target_failure": failure])
        return false
    }

    private func targetCurrent(_ current: () -> Bool, step: String) -> Bool {
        guard current() else { return targetFailed("session", "current_guard_failed", step: step) }
        return true
    }

    private func foregroundMatches(step: String) -> Bool {
        guard let front = NSWorkspace.shared.frontmostApplication else {
            return targetFailed("foreground", "application_unavailable", step: step)
        }
        let details: [String: Any] = ["observed_pid": Int(front.processIdentifier),
            "observed_bundle": front.bundleIdentifier ?? ""]
        metadata["target_observed_foreground"] = details
        guard front.processIdentifier == pid && (bundle.isEmpty || front.bundleIdentifier == bundle) else {
            return targetFailed("foreground", "application_changed", step: step, details: details)
        }
        return true
    }

    private func focusedElement(_ app: AXUIElement) -> (AXError, AXUIElement?) {
        var value: CFTypeRef?
        let error = AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute as CFString, &value)
        guard error == .success, let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return (error, nil) }
        return (error, (value as! AXUIElement))
    }

    private func selection(_ element: AXUIElement) -> (AXError, NSRange?) {
        var value: CFTypeRef?
        let error = AXUIElementCopyAttributeValue(element, kAXSelectedTextRangeAttribute as CFString, &value)
        guard error == .success, let value else { return (error, nil) }
        guard let range = decodeSelection(value) else { return (.illegalArgument, nil) }
        return (.success, range)
    }

    private func decodeSelection(_ value: CFTypeRef) -> NSRange? {
        guard CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
        let wrapped = value as! AXValue
        guard AXValueGetType(wrapped) == .cfRange else { return nil }
        var range = CFRange()
        guard AXValueGetValue(wrapped, .cfRange, &range), range.location >= 0, range.length >= 0,
              range.location < NSNotFound, range.length <= Int.max - range.location else { return nil }
        return NSRange(location: range.location, length: range.length)
    }

    // Optional probes use the already bound element and one AX round trip.
    // Keep required selection and body verification on their normal timeout.
    private func quickSample<T>(current: () -> Bool, read: (AXUIElement) -> T?) -> T? {
        guard let element = focused, validateTarget(current: current, step: "caret_sample_before"),
              AXUIElementSetMessagingTimeout(element, 0.012) == .success else { return nil }
        defer { AXUIElementSetMessagingTimeout(element, 0.12) }
        let result = read(element)
        return validateTarget(current: current, step: "caret_sample_after") ? result : nil
    }

    func sampleCaret(current: () -> Bool) -> NSRange? {
        quickSample(current: current) { selection($0).1 }
    }

    func entireSelectionMatches(_ text: String, current: () -> Bool) -> Bool {
        quickSample(current: current) { element -> Bool? in
            let keys = [kAXSelectedTextRangeAttribute, kAXValueAttribute] as CFArray
            var values: CFArray?
            guard AXUIElementCopyMultipleAttributeValues(element, keys, [], &values) == .success,
                  let items = values as? [AnyObject], items.count == 2, let body = items[1] as? String,
                  decodeSelection(items[0]) == NSRange(location: 0, length: body.utf16.count) else { return nil }
            return body == text
        } ?? false
    }

    func validateTarget(current: () -> Bool) -> Bool { validateTarget(current: current, step: "target_check") }

    func validateTarget(current: () -> Bool, step: String) -> Bool {
        metadata["target_check_step"] = step
        guard targetCurrent(current, step: step + ".before"), foregroundMatches(step: step + ".before") else { return false }
        // Target ownership uses the IMK session and foreground app only.
        // Cached AX elements remain available for selection and body reads.
        metadata["target_ax_focus_check"] = "disabled"
        let valid = targetCurrent(current, step: step + ".after") && foregroundMatches(step: step + ".after")
        if valid { metadata["target_check_result"] = "passed" }
        return valid
    }

    func readSelection(current: () -> Bool) -> NSRange? {
        guard validateTarget(current: current, step: "ax_selection_before_read"), let focused else {
            metadata["ax_readback_state"] = "target_unavailable"
            return nil
        }
        let (error, range) = selection(focused)
        metadata["ax_after_error"] = error.rawValue
        metadata["ax_after_range"] = range.map { [$0.location, $0.length] } ?? [-1, -1]
        metadata["ax_readback_state"] = range == nil ? "unavailable" : "available"
        // AX messaging can re-enter the activation controller.
        guard validateTarget(current: current, step: "ax_selection_after_read") else { return nil }
        return range
    }

    func select(_ range: NSRange, from caret: NSRange, current: () -> Bool) -> PrefixSelectionAttempt {
        guard targetCurrent(current, step: "ax_select.before"), foregroundMatches(step: "ax_select.before") else { return .uncertain("target_changed") }
        guard AXIsProcessTrusted() else { return .unavailable("ax_permission_denied") }
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.12)
        let (focusError, element) = focusedElement(application)
        metadata["ax_focus_error"] = focusError.rawValue
        guard targetCurrent(current, step: "ax_select.after_focus"), foregroundMatches(step: "ax_select.after_focus") else { return .uncertain("target_changed") }
        guard let element else { return .unavailable("ax_focus_unavailable") }
        var elementPID: pid_t = 0
        let pidError = AXUIElementGetPid(element, &elementPID)
        guard pidError == .success, elementPID == pid else {
            _ = targetFailed("ax_focus", pidError == .success ? "element_pid_mismatch" : "pid_query_failed",
                step: "ax_select.element_pid", details: ["ax_error": Int(pidError.rawValue), "observed_pid": Int(elementPID)])
            return .uncertain("ax_target_pid_mismatch")
        }
        app = application; focused = element
        AXUIElementSetMessagingTimeout(element, 0.12)
        let (readError, before) = selection(element)
        metadata["ax_range_error"] = readError.rawValue
        metadata["ax_before_range"] = before.map { [$0.location, $0.length] } ?? [-1, -1]
        guard validateTarget(current: current, step: "ax_select_after_range") else { return .uncertain("target_changed") }
        guard let before else { return .unavailable("ax_range_unavailable") }
        // Never assume AX and IMK use the same offset without this agreement.
        guard before == caret else { return .unavailable("ax_imk_caret_disagreement") }
        var writable = DarwinBoolean(false)
        let capability = AXUIElementIsAttributeSettable(element, kAXSelectedTextRangeAttribute as CFString, &writable)
        metadata["ax_settable_error"] = capability.rawValue
        metadata["ax_range_settable"] = writable.boolValue
        guard capability == .success, writable.boolValue else { return .unavailable("ax_range_not_writable") }
        guard validateTarget(current: current, step: "ax_select_before_set") else { return .uncertain("target_changed") }
        let (finalError, finalCaret) = selection(element)
        guard finalError == .success, finalCaret == caret, targetCurrent(current, step: "ax_select.final_range") else { return .uncertain("ax_caret_changed") }
        var cfRange = CFRange(location: range.location, length: range.length)
        guard let value = AXValueCreate(.cfRange, &cfRange) else { return .unavailable("ax_range_encoding_failed") }
        metadata["ax_write_sent"] = true
        let result = AXUIElementSetAttributeValue(element, kAXSelectedTextRangeAttribute as CFString, value)
        metadata["ax_set_error"] = result.rawValue
        guard validateTarget(current: current, step: "ax_select_after_set") else { return .uncertain("target_changed") }
        if result == .success { return .submitted }
        let (afterError, after) = selection(element)
        metadata["ax_after_error"] = afterError.rawValue
        metadata["ax_after_range"] = after.map { [$0.location, $0.length] } ?? [-1, -1]
        if after == range { return .submitted }
        // A timeout may have applied the selection. Do not stack a shortcut onto
        // an unresolved mutation. Only a definite rejection can fall back.
        if after == caret, result == .attributeUnsupported || result == .notImplemented || result == .illegalArgument {
            return .unavailable("ax_set_rejected")
        }
        return .uncertain("ax_set_outcome_unknown")
    }

    /// Capture range, text and selection entirely in AX coordinates.
    /// No IMK offset is accepted by this interface.
    func readBody(_ scope: EditorReadScope, current: () -> Bool) -> EditorTextRange? {
        // Initial context has no selection operation yet. Give its read errors
        // a persistent trace too, without resetting an active operation/target.
        if file == nil { openReadTrace() }
        for key in Array(metadata.keys) where key.hasPrefix("ax_text_") { metadata.removeValue(forKey: key) }
        metadata["ax_text_scope"] = String(describing: scope)
        metadata["ax_text_selection_policy"] = "ax_owned_snapshot"
        metadata["ax_text_body_attempted"] = false
        metadata["ax_text_returned_characters"] = -1
        metadata["ax_text_read_state"] = "target_or_permission_unavailable"
        defer { record(["event": "body_read"]) }
        guard targetCurrent(current, step: "ax_body.before"), foregroundMatches(step: "ax_body.before"), AXIsProcessTrusted() else { return nil }
        if focused == nil {
            let application = AXUIElementCreateApplication(pid)
            AXUIElementSetMessagingTimeout(application, 0.12)
            let (error, element) = focusedElement(application)
            metadata["ax_text_focus_error"] = error.rawValue
            guard targetCurrent(current, step: "ax_body.after_focus"), foregroundMatches(step: "ax_body.after_focus"), error == .success, let element else { return nil }
            var elementPID: pid_t = 0
            guard AXUIElementGetPid(element, &elementPID) == .success, elementPID == pid,
                  targetCurrent(current, step: "ax_body.element_pid"), foregroundMatches(step: "ax_body.element_pid") else { return nil }
            app = application; focused = element
            AXUIElementSetMessagingTimeout(element, 0.12)
        }
        guard validateTarget(current: current, step: "ax_body_before_selection"), let focused else { return nil }
        let (beforeError, before) = selection(focused)
        metadata["ax_text_selection_before_error"] = beforeError.rawValue
        metadata["ax_text_selection_before"] = before.map { [$0.location, $0.length] } ?? [-1, -1]
        guard beforeError == .success, let before, validateTarget(current: current, step: "ax_body_after_selection") else {
            metadata["ax_text_read_state"] = "selection_or_target_unavailable"; return nil
        }
        var value: String?
        func wholeValue() -> String? {
            var raw: CFTypeRef?
            let error = AXUIElementCopyAttributeValue(focused, kAXValueAttribute as CFString, &raw)
            metadata["ax_text_value_error"] = error.rawValue
            metadata["ax_text_value_characters"] = (raw as? String)?.utf16.count ?? -1
            guard validateTarget(current: current, step: "ax_body_after_value"), error == .success,
                  let text = raw as? String, text.utf16.count <= 512 * 1024 else { return nil }
            return text
        }
        if scope == .document { value = wholeValue() }
        guard let range = scope.range(at: before, documentLength: value?.utf16.count) else {
            metadata["ax_text_read_state"] = "own_range_unavailable"; return nil
        }
        metadata["ax_text_requested_range"] = [range.location, range.length]
        metadata["ax_text_body_attempted"] = true
        var text: String?
        var method = "string_for_range"
        if scope != .document {
            var cfRange = CFRange(location: range.location, length: range.length)
            guard let parameter = AXValueCreate(.cfRange, &cfRange) else { return nil }
            var raw: CFTypeRef?
            let error = AXUIElementCopyParameterizedAttributeValue(focused,
                kAXStringForRangeParameterizedAttribute as CFString, parameter, &raw)
            metadata["ax_text_range_error"] = error.rawValue
            metadata["ax_text_range_returned_characters"] = (raw as? String)?.utf16.count ?? -1
            guard validateTarget(current: current, step: "ax_body_after_range") else { return nil }
            if error == .success { text = raw as? String }
        }
        if scope == .document || text?.utf16.count != range.length {
            if value == nil { value = wholeValue() }
            if let value, isValidRange(before, length: value.utf16.count),
               isValidRange(range, length: value.utf16.count), Range(range, in: value) != nil {
                text = (value as NSString).substring(with: range); method = "value_range"
            } else { text = nil }
        }
        let (afterError, after) = selection(focused)
        metadata["ax_text_selection_after_error"] = afterError.rawValue
        metadata["ax_text_selection_after"] = after.map { [$0.location, $0.length] } ?? [-1, -1]
        guard afterError == .success, after == before, validateTarget(current: current, step: "ax_body_after_final_selection") else {
            metadata["ax_text_read_state"] = "selection_or_target_changed"; return nil
        }
        guard let text, text.utf16.count == range.length else {
            metadata["ax_text_read_state"] = "own_range_incomplete"; return nil
        }
        metadata["ax_text_method"] = method
        metadata["ax_text_returned_characters"] = text.utf16.count
        metadata["ax_text_read_state"] = "available"
        return EditorTextRange(text: text, range: range, source: .ax, selection: before, readScope: scope)
    }

    private func openReadTrace() {
        let directory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Mythlink/IMESelection")
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let path = directory.appendingPathComponent("build\(InputMethodDiagnostics.buildNumber)-read-\(UUID().uuidString).jsonl")
            guard FileManager.default.createFile(atPath: path.path, contents: nil,
                                                attributes: [.posixPermissions: 0o600]) else { return }
            file = try FileHandle(forWritingTo: path); records = 0
            metadata["trace_file"] = path.lastPathComponent
            metadata["target_pid"] = Int(pid); metadata["application"] = bundle
        } catch { metadata["trace_file_error"] = "open_failed" }
    }

    func record(_ event: [String: Any]) {
        if file == nil { openReadTrace() }
        let validationFailure = event["event"] as? String == "body_validation" && event["validation_result"] as? String != "matched"
        let terminal = validationFailure || ["failed", "confirmed", "cancelled", "deadline", "target_check_failed"].contains(event["event"] as? String ?? "")
        guard records < 160 || (event["event"] as? String)?.hasPrefix("clipboard_") == true || terminal else { return }
        records += 1
        var value = metadata.merging(context()) { _, new in new }.merging(event) { _, new in new }
        value["build"] = InputMethodDiagnostics.buildValue
        if let fingerprint = InputMethodDiagnostics.sourceFingerprint { value["source_fingerprint"] = fingerprint }
        value["timestamp"] = ISO8601DateFormatter().string(from: Date())
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let string = String(data: data, encoding: .utf8) else { return }
        logger.notice("ime_selection \(string, privacy: .public)")
        do { try file?.write(contentsOf: data + Data([10])) }
        catch { file = nil; logger.error("ime_selection trace_file_write_failed; unified log remains available") }
    }
}
