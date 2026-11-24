import AppKit
import ApplicationServices

/// One passive preview. Only the active CompositionSession can issue a write.
final class DictationSuggestion {
    let token = UUID().uuidString
    let utterance: String
    let revision: Int
    let raw: String
    private let pid: pid_t
    private let caret: NSRect
    private let panel = ClipboardSuggestionPanel()
    private(set) var text = ""
    private var timer: Timer?
    private var monitors = [Any]()
    private var activationObserver: NSObjectProtocol?
    private var validate: (() -> Bool)?
    private var invalidated: ((String) -> Void)?
    private var closed = false
    private let axQueue = DispatchQueue(label: "mythlink.dictation-preview.focus", qos: .utility)
    private var sampling = false
    private var baseline: Field?
    private var suspendedMatches = false

    private struct Field {
        let element: AXUIElement
        let selection: CFRange
    }

    init(session: CompositionSession, pid: pid_t, caret: NSRect) {
        self.utterance = session.utteranceID
        self.revision = session.revision
        self.raw = session.raw
        self.pid = pid
        self.caret = caret
    }

    var isVisible: Bool { !closed && panel.panel.isVisible && !text.isEmpty }

    func stillValid() -> Bool {
        !closed && validate?() == true
    }

    func observe(valid: @escaping () -> Bool, invalidated: @escaping (String) -> Void) {
        self.validate = valid
        self.invalidated = invalidated
        let mask: NSEvent.EventTypeMask = [.keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown]
        let handleInput: (NSEvent) -> Void = { [weak self] event in
            guard let self, !self.panel.handlesPreviewClick(event) else { return }
            self.invalidated?("user_input")
        }
        if let global = NSEvent.addGlobalMonitorForEvents(matching: mask, handler: handleInput) {
            monitors.append(global)
        }
        if let local = NSEvent.addLocalMonitorForEvents(matching: mask, handler: { event in
            handleInput(event)
            return event
        }) { monitors.append(local) }
        activationObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] note in
                guard let self, let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
                      app.processIdentifier != self.pid else { return }
                self.invalidated?("target_changed")
            }
        sampleField(initial: true)
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            guard let self else { return }
            if !self.stillValid() { self.invalidated?("target_changed") }
        }
    }

    // A suspended IMK client must never be queried. Check the original AX field
    // and selection on a background queue while the user's other IME is active.
    func suspendedFieldUnchanged() -> Bool {
        sampleField(initial: false)
        return suspendedMatches
    }

    private func sampleField(initial: Bool) {
        guard !closed, !sampling else { return }
        sampling = true
        let pid = self.pid
        axQueue.async { [weak self] in
            let field = Self.readField(pid)
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.closed else { return }
                self.sampling = false
                if initial { self.baseline = field }
                if let base = self.baseline, let field {
                    self.suspendedMatches = CFEqual(base.element, field.element)
                        && base.selection.location == field.selection.location
                        && base.selection.length == field.selection.length
                } else { self.suspendedMatches = false }
                if !initial && !self.suspendedMatches { self.invalidated?("target_changed") }
            }
        }
    }

    private static func readField(_ pid: pid_t) -> Field? {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.1)
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute as CFString, &value) == .success,
              let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        let field = value as! AXUIElement
        AXUIElementSetMessagingTimeout(field, 0.1)
        var selected: CFTypeRef?
        var range = CFRange()
        guard AXUIElementCopyAttributeValue(field, kAXSelectedTextRangeAttribute as CFString, &selected) == .success,
              let selected, CFGetTypeID(selected) == AXValueGetTypeID(),
              AXValueGetValue(selected as! AXValue, .cfRange, &range) else { return nil }
        return Field(element: field, selection: range)
    }

    func show(_ proposed: String) -> Bool {
        guard stillValid(), !proposed.isEmpty, proposed != raw,
              proposed.rangeOfCharacter(from: .newlines) == nil else { return false }
        text = proposed
        return panel.show(ClipboardPreview(text: proposed, image: nil), token: token,
                          caret: caret, duration: nil, replacement: true) && panel.panel.isVisible
    }

    func close() {
        closed = true
        timer?.invalidate()
        for monitor in monitors { NSEvent.removeMonitor(monitor) }
        monitors.removeAll()
        if let activationObserver { NSWorkspace.shared.notificationCenter.removeObserver(activationObserver) }
        validate = nil
        invalidated = nil
        panel.hide()
    }
}
