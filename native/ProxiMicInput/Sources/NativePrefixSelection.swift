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
    func validateTarget(current: () -> Bool) -> Bool
    func record(_ event: [String: Any])
    func readText(in range: NSRange, selection: NSRange, current: () -> Bool) -> String?
    func readDocument(in range: NSRange, selection: NSRange, current: () -> Bool) -> String?
}

extension PrefixSelectionDriver {
    func readDocument(in range: NSRange, selection: NSRange, current: () -> Bool) -> String? { nil }
    func readText(in range: NSRange, selection: NSRange, current: () -> Bool) -> String? { nil }
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

    private func foregroundMatches() -> Bool {
        guard let front = NSWorkspace.shared.frontmostApplication else { return false }
        return front.processIdentifier == pid && (bundle.isEmpty || front.bundleIdentifier == bundle)
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
        guard error == .success, let value, CFGetTypeID(value) == AXValueGetTypeID() else { return (error, nil) }
        let wrapped = value as! AXValue
        guard AXValueGetType(wrapped) == .cfRange else { return (.illegalArgument, nil) }
        var range = CFRange()
        guard AXValueGetValue(wrapped, .cfRange, &range), range.location >= 0, range.length >= 0,
              range.location < NSNotFound, range.length <= Int.max - range.location else { return (.illegalArgument, nil) }
        return (error, NSRange(location: range.location, length: range.length))
    }

    func validateTarget(current: () -> Bool) -> Bool {
        guard current(), foregroundMatches() else { return false }
        if let app, let focused {
            let (error, now) = focusedElement(app)
            guard current(), error == .success, let now, CFEqual(focused, now) else { return false }
        }
        return current() && foregroundMatches()
    }

    func readSelection(current: () -> Bool) -> NSRange? {
        guard validateTarget(current: current), let focused else {
            metadata["ax_readback_state"] = "target_unavailable"
            return nil
        }
        let (error, range) = selection(focused)
        metadata["ax_after_error"] = error.rawValue
        metadata["ax_after_range"] = range.map { [$0.location, $0.length] } ?? [-1, -1]
        metadata["ax_readback_state"] = range == nil ? "unavailable" : "available"
        // AX messaging can re-enter the activation controller.
        guard validateTarget(current: current) else { return nil }
        return range
    }

    func select(_ range: NSRange, from caret: NSRange, current: () -> Bool) -> PrefixSelectionAttempt {
        guard current(), foregroundMatches() else { return .uncertain("target_changed") }
        guard AXIsProcessTrusted() else { return .unavailable("ax_permission_denied") }
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.12)
        let (focusError, element) = focusedElement(application)
        metadata["ax_focus_error"] = focusError.rawValue
        guard current(), foregroundMatches() else { return .uncertain("target_changed") }
        guard let element else { return .unavailable("ax_focus_unavailable") }
        var elementPID: pid_t = 0
        let pidError = AXUIElementGetPid(element, &elementPID)
        guard pidError == .success, elementPID == pid else { return .uncertain("ax_target_pid_mismatch") }
        app = application; focused = element
        AXUIElementSetMessagingTimeout(element, 0.12)
        let (readError, before) = selection(element)
        metadata["ax_range_error"] = readError.rawValue
        metadata["ax_before_range"] = before.map { [$0.location, $0.length] } ?? [-1, -1]
        guard validateTarget(current: current) else { return .uncertain("target_changed") }
        guard let before else { return .unavailable("ax_range_unavailable") }
        // Never assume AX and IMK use the same offset without this agreement.
        guard before == caret else { return .unavailable("ax_imk_caret_disagreement") }
        var writable = DarwinBoolean(false)
        let capability = AXUIElementIsAttributeSettable(element, kAXSelectedTextRangeAttribute as CFString, &writable)
        metadata["ax_settable_error"] = capability.rawValue
        metadata["ax_range_settable"] = writable.boolValue
        guard capability == .success, writable.boolValue else { return .unavailable("ax_range_not_writable") }
        guard validateTarget(current: current) else { return .uncertain("target_changed") }
        let (finalError, finalCaret) = selection(element)
        guard finalError == .success, finalCaret == caret, current() else { return .uncertain("ax_caret_changed") }
        var cfRange = CFRange(location: range.location, length: range.length)
        guard let value = AXValueCreate(.cfRange, &cfRange) else { return .unavailable("ax_range_encoding_failed") }
        metadata["ax_write_sent"] = true
        let result = AXUIElementSetAttributeValue(element, kAXSelectedTextRangeAttribute as CFString, value)
        metadata["ax_set_error"] = result.rawValue
        guard validateTarget(current: current) else { return .uncertain("target_changed") }
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

    /// Read-only fallback. AX must agree with the guarded selection; its text
    /// is never used to infer an IMK coordinate or to move the caret.
    func readText(in range: NSRange, selection: NSRange, current: () -> Bool) -> String? {
        readBody(in: range, selection: selection, requireWhole: false, current: current)
    }
    func readDocument(in range: NSRange, selection: NSRange, current: () -> Bool) -> String? {
        readBody(in: range, selection: selection, requireWhole: true, current: current)
    }
    private func readBody(in range: NSRange, selection expected: NSRange, requireWhole: Bool, current: () -> Bool) -> String? {
        metadata["ax_text_requested_range"] = [range.location, range.length]
        metadata["ax_text_returned_characters"] = -1
        metadata["ax_text_read_state"] = "unavailable"
        metadata.removeValue(forKey: "ax_text_method")
        guard isValidRange(range), range.length <= 512 * 1024,
              isValidRange(expected), current(), foregroundMatches(), AXIsProcessTrusted() else { return nil }
        // Historical undo may arrive on a new activation before any AX selection.
        if focused == nil {
            let application = AXUIElementCreateApplication(pid)
            AXUIElementSetMessagingTimeout(application, 0.12)
            let (error, element) = focusedElement(application)
            guard current(), foregroundMatches(), error == .success, let element else { return nil }
            var elementPID: pid_t = 0
            guard AXUIElementGetPid(element, &elementPID) == .success, elementPID == pid,
                  current(), foregroundMatches() else { return nil }
            app = application; focused = element
            AXUIElementSetMessagingTimeout(element, 0.12)
        }
        guard validateTarget(current: current), let focused else { return nil }
        let (beforeError, before) = selection(focused)
        metadata["ax_text_selection_before"] = before.map { [$0.location, $0.length] } ?? [-1, -1]
        guard beforeError == .success, before == expected, validateTarget(current: current) else {
            metadata["ax_text_read_state"] = "selection_or_target_changed"; return nil
        }
        var cfRange = CFRange(location: range.location, length: range.length)
        guard let parameter = AXValueCreate(.cfRange, &cfRange) else { return nil }
        var raw: CFTypeRef?
        let rangeError = AXUIElementCopyParameterizedAttributeValue(focused,
            kAXStringForRangeParameterizedAttribute as CFString, parameter, &raw)
        metadata["ax_text_range_error"] = rangeError.rawValue
        metadata["ax_text_range_returned_characters"] = (raw as? String)?.utf16.count ?? -1
        guard validateTarget(current: current) else { return nil }
        var text = rangeError == .success ? raw as? String : nil
        var method = "string_for_range"
        if requireWhole || text?.utf16.count != range.length {
            // Some Electron controls expose AXValue but no AXStringForRange.
            // Slice only an explicitly bounded UTF-16 range of the real value.
            var value: CFTypeRef?
            let error = AXUIElementCopyAttributeValue(focused, kAXValueAttribute as CFString, &value)
            metadata["ax_text_value_error"] = error.rawValue
            metadata["ax_text_value_characters"] = (value as? String)?.utf16.count ?? -1
            guard validateTarget(current: current) else { return nil }
            if error == .success, let whole = value as? String,
               whole.utf16.count <= 512 * 1024, isValidRange(range, length: whole.utf16.count),
               !requireWhole || (range.location == 0 && range.length == whole.utf16.count),
               Range(range, in: whole) != nil {
                text = (whole as NSString).substring(with: range); method = "value_range"
            } else { text = nil }
        }
        let (afterError, after) = selection(focused)
        metadata["ax_text_selection_after"] = after.map { [$0.location, $0.length] } ?? [-1, -1]
        guard afterError == .success, after == expected, validateTarget(current: current) else {
            metadata["ax_text_read_state"] = "selection_or_target_changed"; return nil
        }
        guard let text, text.utf16.count == range.length else { return nil }
        metadata["ax_text_method"] = method
        metadata["ax_text_returned_characters"] = text.utf16.count
        metadata["ax_text_read_state"] = "available"
        return text
    }

    func record(_ event: [String: Any]) {
        let terminal = ["failed", "confirmed", "cancelled", "deadline"].contains(event["event"] as? String ?? "")
        guard records < 160 || terminal else { return }
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
