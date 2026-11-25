// Execute the production controller against reentrant, local IMK endpoints.
// Only the OS controller base and panel are replaced; no application is touched.
import AppKit
import InputMethodKit
import Carbon

class IMKInputController: NSObject {
    static var onActivate: (() -> Void)?
    func activateServer(_ sender: Any!) { let callback = Self.onActivate; Self.onActivate = nil; callback?() }
    func deactivateServer(_ sender: Any!) {}
    func inputControllerWillClose() {}
    func hidePalettes() {}
    func mark(forStyle style: Int, at range: NSRange) -> [AnyHashable: Any]! {
        [NSAttributedString.Key.underlineStyle: NSUnderlineStyle.double.rawValue]
    }
    func recognizedEvents(_ sender: Any!) -> Int { 0 }
    func client() -> IMKTextInput? { nil }
}
final class ActionPanel {
    var onFinish: (() -> Void)?
    static var sentCount = 0
    func showSent() { Self.sentCount += 1 }
    func showNotice(_ text: String) {}
    static var displayedHints = VoiceHudBindings()
    static var displayedAppearance = VoiceHudAppearance()
    var onCancel: (() -> Void)?
    var onConvert: (() -> Void)?
    var needsPosition: Bool { false }
    var positioningDiagnostics: [String: Any] { [:] }
    func hide() {}
    func configureGestureHints(_ hints: VoiceHudBindings) { Self.displayedHints = hints }
    func configureAppearance(_ value: VoiceHudAppearance) { Self.displayedAppearance = value }
    func update(_ session: CompositionSession, historyDepth: Int, redoAvailable: Bool = false, redoEnabled: Bool = true,
                retryAvailable: Bool = false, historyActionMode: String = "next", manualPolish: Bool = true) {}
    var onPosition: (([Double]) -> Void)?
    func position(caret: NSRect?, clientLevel: Int32) {}
}
final class StrokeCandidatePanel {
    static var visible = false
    static var canShow = true
    static var lastTrace: [[Double]] = []
    var exitGestureLabel = "响指"
    var tapHint = ""
    var needsPosition: Bool { false }
    var isVisible: Bool { Self.visible && Self.canShow }
    func hide() { Self.visible = false; Self.lastTrace = [] }
    func update(code: String, candidates: [String], selected: Int, glyphs: [[String: String]] = []) { Self.visible = true }
    func selection(phase: String, progress: Double, direction: String,
                   origin: [Double], points: [[Double]], selectDistance: Double,
                   clearArmed: Bool = false, clearDistance: Double = 30, clearProgress: Double = 0,
                   correctionArmed: Bool = false, correctionProgress: Double = 0) {}
    func correction(enabled: Bool, text: String, message: String, busy: Bool, canUndo: Bool) {}
    func position(caret: NSRect?, clientLevel: Int32) {}
    func trace(points: [[Double]], finished: Bool, style: [String: Any]) { Self.lastTrace = points }
}
final class LocalClient: NSObject, IMKTextInput {
    var onBundle: (() -> Void)?
    var onRead: (() -> Void)?
    var onInsert: (() -> Void)?
    var reads = 0
    var substringReads = 0
    var writes = 0
    var lastReplacement: (String, NSRange)?
    var markedReads = 0
    var text = ""
    var selection = NSRange(location: 0, length: 0)
    var marked = unspecifiedRange
    var testCaret: NSRect = .zero
    var hideDocumentRanges = false
    var bundle = "test.activation"
    func bundleIdentifier() -> String! { let f = onBundle; onBundle = nil; f?(); return bundle }
    func selectedRange() -> NSRange { reads += 1; let f = onRead; onRead = nil; f?(); return hideDocumentRanges ? unspecifiedRange : selection }
    func markedRange() -> NSRange { markedReads += 1; return hideDocumentRanges ? unspecifiedRange : marked }
    func length() -> Int { hideDocumentRanges ? Int(Int32.max) : (text as NSString).length }
    func string(from range: NSRange, actualRange: NSRangePointer!) -> String! {
        substringReads += 1
        guard !hideDocumentRanges, range.length > 0, isValidRange(range, length: (text as NSString).length) else {
            actualRange?.pointee = NSRange(location: 0, length: 0); return nil
        }
        actualRange?.pointee = range
        return (text as NSString).substring(with: range)
    }
    func insertText(_ string: Any!, replacementRange range: NSRange) {
        writes += 1
        let target = isValidRange(range) ? range : (isValidRange(marked) ? marked : selection)
        if !isValidRange(marked), target.length > 0 { lastReplacement = (text, target) }
        let value = string as! String
        text = (text as NSString).replacingCharacters(in: target, with: value)
        selection = NSRange(location: target.location + (value as NSString).length, length: 0)
        marked = unspecifiedRange
        let f = onInsert; onInsert = nil; f?()
    }
    func setMarkedText(_ string: Any!, selectionRange: NSRange, replacementRange: NSRange) {
        writes += 1
        let target = isValidRange(replacementRange) ? replacementRange : (isValidRange(marked) ? marked : selection)
        let value = (string as? NSAttributedString)?.string ?? (string as! String)
        text = (text as NSString).replacingCharacters(in: target, with: value)
        marked = value.isEmpty ? unspecifiedRange : NSRange(location: target.location, length: (value as NSString).length)
        selection = NSRange(location: target.location + selectionRange.location, length: selectionRange.length)
    }
    func attributedSubstring(from range: NSRange) -> NSAttributedString! { nil }
    func characterIndex(for point: NSPoint, tracking mode: IMKLocationToOffsetMappingMode, inMarkedRange: UnsafeMutablePointer<ObjCBool>!) -> Int { NSNotFound }
    func attributes(forCharacterIndex index: Int, lineHeightRectangle: UnsafeMutablePointer<NSRect>!) -> [AnyHashable: Any]! {
        lineHeightRectangle?.pointee = testCaret
        return [:]
    }
    func validAttributesForMarkedText() -> [Any]! { [] }
    func overrideKeyboard(withKeyboardNamed name: String!) { fatalError("must not switch input sources") }
    func selectMode(_ identifier: String!) { fatalError("must not switch input sources") }
    func supportsUnicode() -> Bool { true }
    func windowLevel() -> Int32 { 0 }
    func supportsProperty(_ property: TSMDocumentPropertyTag) -> Bool { !hideDocumentRanges }
    func uniqueClientIdentifierString() -> String! { UUID().uuidString }
    func firstRect(forCharacterRange range: NSRange, actualRange: NSRangePointer!) -> NSRect { testCaret }
}
// Use the real controller and transport without consulting another app's AX tree.
final class LocalPrefixSelection: PrefixSelectionDriver {
    let editor: LocalClient
    init(_ editor: LocalClient) { self.editor = editor }
    var metadata: [String: Any] { [:] }
    func begin(_ operation: String) {}
    func record(_ event: [String: Any]) {}
    func validateTarget(current: () -> Bool) -> Bool { current() }
    func readSelection(current: () -> Bool) -> NSRange? { current() ? editor.selection : nil }
    func select(_ range: NSRange, from caret: NSRange, current: () -> Bool) -> PrefixSelectionAttempt {
        guard current(), editor.selection == caret,
              isValidRange(range, length: editor.text.utf16.count) else { return .uncertain("target_changed") }
        editor.selection = range
        print("SELECTED_RANGE=\(range.location),\(range.length)")
        return .submitted
    }
}
func check(_ condition: @autoclosure () -> Bool, _ message: String) { if !condition() { fputs("FAIL: \(message)\n", stderr); exit(1) } }
func currentClientID(_ controller: ProxiMicInputController) -> String {
    Mirror(reflecting: controller).children.first { $0.label == "currentClientID" }!.value as! String
}
func drainActivation() {
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.10))
}
func firstSentence(_ controller: ProxiMicInputController, _ client: LocalClient) {
    let id = currentClientID(controller)
    controller.receive(["type": "begin", "client_id": id, "utterance_id": "first", "seq": 1])
    controller.receive(["type": "update", "client_id": id, "utterance_id": "first", "seq": 2, "text": "第一", "final": false])
    controller.receive(["type": "update", "client_id": id, "utterance_id": "first", "seq": 3, "text": "第一句话", "final": true])
    check(client.text == "第一句话" && !isValidRange(client.marked), "first Tap did not stream and commit")
}

func currentSession(_ controller: ProxiMicInputController) -> CompositionSession? {
    (Mirror(reflecting: controller).children.first { $0.label == "sessionLifetime" }!.value as! CompositionSessionLifetime).current
}

func suspendSentence(_ controller: ProxiMicInputController, token: String = "return-source") {
    controller.receive(["type": "suspend_input", "client_id": currentClientID(controller),
        "utterance_id": "first", "seq": 4, "token": token, "revision": currentSession(controller)!.revision])
}

// Local clients do not belong to the desktop's foreground application.
IMEService.shared.activationForegroundPID = { ProcessInfo.processInfo.processIdentifier }

do {
    let service = IMEService.shared
    service.transport.onMessage?(["type": "welcome", "protocol": 1, "epoch": "metadata-only"])
    let client = LocalClient(), controller = ProxiMicInputController()
    client.text = "上下文"; client.selection = NSRange(location: 3, length: 0)
    controller.activateServer(client); drainActivation()
    check(client.substringReads == 0, "activation read document text before context preparation")
    service.transport.onMessage?(["type": "ping", "epoch": "metadata-only", "request_id": "focus-only", "capture_context": false])
    check(client.substringReads == 0, "metadata focus probe read text before handshake")
    service.transport.onMessage?(["type": "ping", "epoch": "metadata-only", "request_id": "read-context", "capture_context": true])
    check(client.substringReads > 0, "explicit context request did not read document")
    controller.deactivateServer(client)
    print("PASS activation/focus probes defer document reads until context is requested")
}

do {
    let service = IMEService.shared
    let savedFront = service.inputContextForeground
    let controller = ProxiMicInputController(), editor = LocalClient()
    let frame = NSScreen.main!.visibleFrame
    editor.testCaret = NSRect(x: frame.midX, y: frame.midY, width: 1, height: 16)
    service.inputContextForeground = { ("test.activation", ProcessInfo.processInfo.processIdentifier) }
    controller.makeSelectionDriver = { _, _, _ in LocalPrefixSelection(editor) }
    let epoch = "dictation-preview"
    service.transport.onMessage?(["type": "welcome", "protocol": 1, "epoch": epoch])
    service.transport.onMessage?(["type": "configure", "epoch": epoch, "automatic_polish": true])
    controller.activateServer(editor); drainActivation(); firstSentence(controller, editor)
    let token = service.suggestion!.token
    let session = currentSession(controller)!
    check(session.phase == .dictated && editor.text == "第一句话", "raw dictation was not committed before preview")
    service.transport.onMessage?(["type": "dictation_preview_result", "epoch": epoch, "token": token, "text": "第一句话。"])
    check(service.suggestion?.isVisible == true && !session.polished, "passive suggestion missing or already applied")
    controller.receive(["type": "polish", "client_id": currentClientID(controller), "utterance_id": "first", "seq": 4])
    check(session.phase == .dictated, "old polish ran while preview mode enabled")
    controller.receive(["type": "accept_dictation_preview", "client_id": currentClientID(controller),
                        "utterance_id": "first", "seq": 5, "token": token, "revision": session.revision])
    for _ in 0..<12 where session.awaitingReadback { drainActivation() }
    check(editor.text == "第一句话。" && session.polished && service.suggestion == nil,
          "preview acceptance failed: text=\(editor.text) phase=\(session.phase) error=\(session.error) stage=\(session.readbackStage) offered=\(service.suggestion != nil) diagnostics=\(session.readbackDiagnostics)")
    controller.receive(["type": "cancel", "client_id": currentClientID(controller), "utterance_id": "first", "seq": 6])
    for _ in 0..<12 where session.awaitingReadback { drainActivation() }
    check(editor.text == "第一句话", "first undo did not restore raw: \(session.error)")
    service.transport.onMessage?(["type": "dictation_preview_result", "epoch": epoch, "token": token, "text": "迟到"])
    check(service.suggestion == nil && editor.text == "第一句话", "late output resurrected the preview")
    controller.deactivateServer(editor)
    service.transport.onMessage?(["type": "configure", "epoch": epoch, "automatic_polish": false])
    service.inputContextForeground = savedFront
    print("PASS native passive caret preview, mutually exclusive gesture, verified acceptance and undo")
}

// A key can create a controller before its deferred preparation runs. Our
// authenticated wake event must be consumed even in that interval, without
// inserting a character, moving selection, or requiring a document range.
do {
    let service = IMEService.shared
    service.transport.onMessage?(["type": "welcome", "protocol": 1, "epoch": "context-key-test"])
    let savedFront = service.inputContextForeground
    service.inputContextForeground = { ("test.activation", 504) }
    let client = LocalClient(), controller = ProxiMicInputController()
    client.hideDocumentRanges = true
    let stale = ProxiMicInputController(), staleClient = LocalClient()
    staleClient.hideDocumentRanges = true
    stale.activateServer(staleClient); drainActivation()
    let arm: [String: Any] = ["type": "prepare_input_context", "epoch": "context-key-test", "request_id": "wake",
                             "event_tag": Int64(123456), "application": client.bundle, "foreground_pid": Int32(504)]
    service.transport.onMessage?(arm.merging(["foreground_pid": Int32(505)]) { _, next in next })
    check(service.contextKey.pending == nil, "wrong foreground armed context key")
    service.transport.onMessage?(arm)
    check(service.contextKey.pending != nil, "context key was not armed")
    controller.activateServer(client)
    let own = CGEvent(keyboardEventSource: nil, virtualKey: InputContextKey.keyCode, keyDown: true)!
    own.setIntegerValueField(.eventSourceUserData, value: 123456)
    check(controller.handle(NSEvent(cgEvent: own), client: client), "pending controller leaked own context key")
    check(client.writes == 0 && client.selection == NSRange(location: 0, length: 0), "context key changed text/caret")
    let ordinary = CGEvent(keyboardEventSource: nil, virtualKey: InputContextKey.keyCode, keyDown: true)!
    check(!controller.handle(NSEvent(cgEvent: ordinary), client: client), "ordinary F20 was swallowed")
    drainActivation()
    firstSentence(controller, client)
    controller.deactivateServer(client)
    service.inputContextForeground = savedFront
    print("PASS own context key is consumed during activation; unknown-range dictation still works")
}

do {
    let gate = InputContextKey()
    var clock = 10.0
    gate.now = { clock }
    check(gate.arm(id: "expired", tag: 101, epoch: "e", application: "mail", pid: 1), "gate arm failed")
    check(!gate.consumes(code: 124, tag: 101), "tagged arrow was swallowed")
    clock += 1.1
    check(gate.take(tag: 101) == nil && gate.consumes(code: 90, tag: 101), "late key revived expired request")
    check(gate.arm(id: "cancel", tag: 102, epoch: "e", application: "mail", pid: 1), "second arm failed")
    gate.cancel(id: "unrelated")
    check(gate.pending != nil, "unrelated cancellation affected request")
    gate.cancel(id: "cancel")
    check(gate.take(tag: 102) == nil && gate.consumes(code: 90, tag: 102), "cancelled key leaked or revived request")
    clock += 2
    check(!gate.consumes(code: 90, tag: 102), "event reservation did not expire")
    print("PASS context-key expiry, cancellation and unrelated-event boundaries")
}

// The IPC regression uses the production service and a process-local editor.
if CommandLine.arguments.contains("--return-ipc") {
    var editor = LocalClient()
    if CommandLine.arguments.contains("--with-suffix") { editor.text = "🍎后缀" }
    if CommandLine.arguments.contains("--wechat") { editor.bundle = "com.tencent.xinWeChat" }
    let controller = ProxiMicInputController(), service = IMEService.shared
    controller.makeSelectionDriver = { _, _, _ in LocalPrefixSelection(editor) }
    let receive = service.transport.onMessage
    service.transport.onMessage = { message in
        switch message["type"] as? String {
        case "test_apply_preview":
            guard let session = currentSession(controller) else { fatalError("no active preview session") }
            session.acceptSuggestion("明天我带苹果。", originalText: session.raw, revision: session.revision)
        case "test_collapse_preview":
            let key = CGEvent(keyboardEventSource: nil, virtualKey: 124, keyDown: true)!
            key.setIntegerValueField(.eventSourceUserData, value: message["event_tag"] as! Int64)
            key.flags = []
            if !controller.handle(NSEvent(cgEvent: key)!, client: editor) {
                check(editor.selection.length > 0, "moved an already collapsed caret")
                editor.selection = NSRange(location: NSMaxRange(editor.selection), length: 0)
            }
            print("PREVIEW_UNDO_CARET=\(editor.selection.location),\(editor.selection.length)")
        case "test_reactivate":
            controller.deactivateServer(editor)
            switch message["change"] as? String {
            case "client":
                let other = LocalClient()
                other.text = editor.text
                other.selection = editor.selection
                editor = other
            case "text": editor.text = "不是原句"
            case "caret": editor.selection = NSRange(location: 0, length: 0)
            default: break
            }
            controller.activateServer(editor)
        case "test_local_key":
            print("LOCAL_KEY=" + (message["command"] as? String ?? ""))
            if message["command"] as? String == "system_undo" {
                if message["stripped"] as? Bool == true {
                    let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [.command],
                        timestamp: 0, windowNumber: 0, context: nil, characters: "z", charactersIgnoringModifiers: "z",
                        isARepeat: false, keyCode: 6)!
                    check(!controller.handle(event, client: editor), "IME consumed host-posted ⌘Z")
                }
                guard let saved = editor.lastReplacement else { fatalError("no native replacement to undo") }
                editor.text = saved.0
                editor.selection = saved.1
                editor.lastReplacement = nil
                print("SYSTEM_UNDO_TEXT=" + editor.text)
            } else if message["command"] as? String == "select_all" {
                editor.selection = NSRange(location: 0, length: editor.text.utf16.count)
            } else if message["command"] as? String == "caret_from_end", let count = message["count"] as? Int {
                let prefix = String(editor.text.dropLast(count))
                editor.selection = NSRange(location: prefix.utf16.count, length: 0)
            } else if message["command"] as? String == "caret_backward", let count = message["count"] as? Int {
                let before = (editor.text as NSString).substring(to: editor.selection.location)
                check(count <= before.count, "unexpected local caret movement")
                editor.selection = NSRange(location: String(before.dropLast(count)).utf16.count, length: 0)
            } else if message["command"] as? String == "select_previous", let count = message["count"] as? Int {
                let end = editor.selection.location
                check(count <= end, "unexpected local selection")
                editor.selection = NSRange(location: end - count, length: count)
            } else {
                check(message["command"] as? String == "delete" && editor.selection.length > 0, "unexpected local key")
                editor.insertText("", replacementRange: editor.selection)
                print("AFTER_UNDO=" + editor.text)
            }
        case "test_record_redo":
            print("AFTER_REDO=" + editor.text)
            print("AFTER_REDO_CARET=\(editor.selection.location),\(editor.selection.length)")
        case "test_exit":
            print("FINAL_TEXT=" + editor.text)
            service.transport.stop()
            exit(0)
        default: receive?(message)
        }
    }
    controller.activateServer(editor)
    service.start()
    RunLoop.main.run(until: Date().addingTimeInterval(10))
    fatalError("input source return IPC test timed out")
}

do {
    let old = ProxiMicInputController(), client = LocalClient()
    old.activateServer(client); drainActivation(); firstSentence(old, client)
    let revision = currentSession(old)!.revision
    let writes = client.writes
    suspendSentence(old)
    check(currentSession(old) == nil && IMEService.shared.suspendedInput?.token == "return-source", "suspend did not detach settled input")
    old.deactivateServer(client)
    let next = ProxiMicInputController()
    next.activateServer(client); drainActivation()
    next.receive(["type": "resume_input", "client_id": currentClientID(next), "utterance_id": "first", "seq": 5, "token": "return-source"])
    check(currentSession(next)?.phase == .dictated && currentSession(next)?.revision == revision,
          "new controller did not resume the same settled transaction")
    check(currentSession(next)?.canPolish == true && IMEService.shared.suspendedInput == nil, "resume lost polish or retained a replayable token")
    check(client.writes == writes, "suspend/resume performed a text write")
    next.receive(["type": "polish", "client_id": currentClientID(next), "utterance_id": "first", "seq": 6])
    check(currentSession(next)?.phase == .polishing, "polish gesture could not operate after resume")
    next.deactivateServer(client)
    print("PASS explicit input-source suspension survives native controller replacement")
}

for scenario in ["application", "client", "text", "selection", "mark"] {
    let old = ProxiMicInputController(), client = LocalClient()
    old.activateServer(client); drainActivation(); firstSentence(old, client); suspendSentence(old)
    old.deactivateServer(client)
    let other = scenario == "client" ? LocalClient() : client
    other.text = client.text
    other.selection = client.selection
    if scenario == "application" { other.bundle = "other.application" }
    if scenario == "text" { other.text = "外部替换句" }
    if scenario == "selection" { other.selection = NSRange(location: 0, length: 0) }
    if scenario == "mark" { other.marked = NSRange(location: 0, length: 1) }
    let writes = other.writes
    let next = ProxiMicInputController()
    next.activateServer(other); drainActivation()
    next.receive(["type": "resume_input", "client_id": currentClientID(next), "utterance_id": "first", "seq": 5, "token": "return-source"])
    check(currentSession(next) == nil && other.writes == writes, "resumed over changed \(scenario)")
    check(IMEService.shared.suspendedInput == nil, "failed restoration retained an invalid snapshot")
    next.deactivateServer(other)
    print("PASS input-source resume rejects changed \(scenario) without writing")
}

for scenario in ["new_sentence", "disconnect", "reset"] {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation(); firstSentence(controller, client); suspendSentence(controller)
    switch scenario {
    case "new_sentence": controller.receive(["type": "begin", "client_id": currentClientID(controller), "utterance_id": "new", "seq": 1])
    case "disconnect": controller.hostDisconnected()
    default: controller.receive(["type": "reset", "all": true])
    }
    check(IMEService.shared.suspendedInput == nil, "\(scenario) did not discard suspended input")
    controller.deactivateServer(client)
    print("PASS \(scenario) discards suspended input")
}

// A new BEGIN can inherit history, but must never revive the old sentence or write text.
let historyService = IMEService.shared
historyService.transport.onMessage?(["type": "welcome", "protocol": 1, "epoch": "history-test"])
for scenario in ["same", "client", "text", "caret", "mark", "wrong_token", "toggle", "discard", "newer_begin"] {
    let old = ProxiMicInputController(), editor = LocalClient()
    old.activateServer(editor); drainActivation(); firstSentence(old, editor); suspendSentence(old)
    old.deactivateServer(editor)
    if scenario == "toggle" {
        for enabled in [false, true] {
            historyService.transport.onMessage?(["type": "configure", "epoch": "history-test", "multi_undo_enabled": enabled])
        }
    }
    let next = ProxiMicInputController(), target = scenario == "client" ? LocalClient() : editor
    target.text = scenario == "text" ? "外部替换句" : editor.text
    target.selection = scenario == "caret" ? NSRange(location: 0, length: 0) : editor.selection
    if scenario == "mark" { target.marked = NSRange(location: 0, length: 1) }
    next.activateServer(target); drainActivation()
    let id = currentClientID(next), writes = target.writes
    if scenario == "discard" {
        target.onRead = { historyService.transport.onMessage?(["type": "discard_suspended_input", "epoch": "history-test", "token": "return-source"]) }
    } else if scenario == "newer_begin" {
        target.onRead = { next.receive(["type": "begin", "client_id": id, "utterance_id": "newer", "seq": 1]) }
    }
    next.receive(["type": "begin", "client_id": id, "utterance_id": "second", "seq": 1,
                  "undo_history_token": scenario == "wrong_token" ? "wrong" : "return-source"])
    let history = Mirror(reflecting: next).children.first { $0.label == "undoHistory" }!.value as! CompositionUndoHistory
    check(currentSession(next)?.utteranceID == (scenario == "newer_begin" ? "newer" : "second")
          && currentSession(next)?.phase == .listening, "history verification prevented a fresh BEGIN: \(scenario)")
    check(history.count == (["same", "toggle"].contains(scenario) ? 1 : 0) && historyService.suspendedInput == nil,
          "wrong history or replayable token after BEGIN: \(scenario)")
    check(target.writes == writes, "history transfer wrote old text: \(scenario)")
    next.deactivateServer(target)
    print("PASS cross-sentence input-source history: \(scenario)")
}

do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation(); firstSentence(controller, client); suspendSentence(controller)
    let id = currentClientID(controller)
    client.onRead = {
        for enabled in [false, true] {
            historyService.transport.onMessage?(["type": "configure", "epoch": "history-test", "multi_undo_enabled": enabled])
        }
    }
    controller.receive(["type": "resume_input", "client_id": id, "utterance_id": "first",
                        "seq": 5, "token": "return-source"])
    check(currentSession(controller)?.phase == .dictated, "disabling earlier history also lost current sentence")
    controller.receive(["type": "begin", "client_id": id, "utterance_id": "after-toggle", "seq": 1])
    let history = Mirror(reflecting: controller).children.first { $0.label == "undoHistory" }!.value as! CompositionUndoHistory
    check(history.count == 1, "legacy configuration cleared history during the editor read")
    controller.deactivateServer(client)
    print("PASS retired settings cannot revoke earlier history during resume")
}

do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation(); firstSentence(controller, client); suspendSentence(controller)
    controller.receive(["type": "resume_input", "client_id": currentClientID(controller), "utterance_id": "first", "seq": 5, "token": "wrong-token"])
    check(currentSession(controller) == nil && IMEService.shared.suspendedInput?.token == "return-source", "unrelated token consumed suspended input")
    controller.receive(["type": "resume_input", "client_id": currentClientID(controller), "utterance_id": "first", "seq": 5, "token": "return-source"])
    check(currentSession(controller)?.phase == .dictated, "restore after failed source switch could not reattach")
    controller.deactivateServer(client)
    check(IMEService.shared.suspendedInput == nil, "ordinary deactivation saved an unrequested snapshot")
    print("PASS token isolation and reattachment after source-switch failure")
}

do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "begin", "client_id": id, "utterance_id": "first", "seq": 1])
    controller.receive(["type": "cancel", "client_id": id, "utterance_id": "first", "seq": 2])
    check(currentSession(controller)?.phase == .undone, "empty sentence was not cancelled")
    suspendSentence(controller)
    check(currentSession(controller) == nil && IMEService.shared.suspendedInput == nil && client.writes == 0,
          "final undo did not allow source return without a writable snapshot")
    controller.deactivateServer(client)
    print("PASS final undo returns without retaining unusable empty-field history")
}

do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation(); firstSentence(controller, client)
    client.onRead = { controller.deactivateServer(client) }
    suspendSentence(controller)
    check(IMEService.shared.suspendedInput == nil, "suspension captured a retired editor")
    print("PASS lifecycle reentry cannot save a stale suspended editor")
}

for stage in ["snapshot", "reply"] {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation(); firstSentence(controller, client); suspendSentence(controller)
    let id = currentClientID(controller)
    let reenter = { controller.receive(["type": "begin", "client_id": id, "utterance_id": "new", "seq": 1]) }
    if stage == "snapshot" { client.onRead = reenter } else { client.onBundle = reenter }
    let writes = client.writes
    controller.receive(["type": "resume_input", "client_id": id, "utterance_id": "first", "seq": 5, "token": "return-source"])
    check(currentSession(controller)?.utteranceID == "new" && IMEService.shared.suspendedInput == nil,
          "resume replaced a reentrant new sentence")
    check(client.writes == writes, "resume reentry wrote old text")
    controller.deactivateServer(client)
    print("PASS new sentence wins over reentrant resume \(stage)")
}

for confirmed in [false, true] {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation(); firstSentence(controller, client); suspendSentence(controller)
    let id = currentClientID(controller), writes = client.writes
    controller.receive(["type": "resume_input", "client_id": id, "utterance_id": "first", "seq": 5, "token": "return-source"])
    if confirmed { controller.receive(["type": "polish", "client_id": id, "utterance_id": "first", "seq": 6]) }
    controller.discardResumedInput(token: "return-source")
    check(confirmed ? currentSession(controller)?.phase == .polishing : currentSession(controller) == nil,
          "resume timeout discarded confirmed work or retained an unacknowledged session")
    check(client.writes == writes, "discarding a resumed session wrote text")
    controller.deactivateServer(client)
    print("PASS resume timeout cleanup, confirmed=\(confirmed)")
}

// Source-launched Mythlink has an empty bundle ID. Committed undo must use
// the same verified native selection/delete protocol as bundled clients.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    client.bundle = ""
    controller.activateServer(client); drainActivation()
    firstSentence(controller, client)
    let writes = client.writes
    controller.receive(["type": "cancel", "client_id": currentClientID(controller), "utterance_id": "first", "seq": 4])
    let adapter = Mirror(reflecting: controller).children.first { $0.label == "adapter" }!.value as! NativeInputClient?
    check(adapter?.clientOperationPending == true, "unbundled undo skipped native key delivery")
    check(client.writes == writes && client.text == "第一句话", "unbundled undo used an unreliable IMK replacement")
    controller.deactivateServer(client)
    print("PASS unbundled committed undo uses verified native selection before deletion")
}

for outcome in ["sent", "replaced"] {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    firstSentence(controller, client)
    let id = currentClientID(controller)
    let writes = client.writes, sentCount = ActionPanel.sentCount
    controller.receive(["type": "end_input", "outcome": outcome, "client_id": id, "utterance_id": "first", "seq": 4])
    check(currentSession(controller) == nil, "old session survived \(outcome)")
    check(client.writes == writes && client.text == "第一句话", "retiring input changed submitted text")
    check(ActionPanel.sentCount == sentCount + (outcome == "sent" ? 1 : 0), "wrong submitted feedback")
    client.text = ""; client.selection = NSRange(location: 0, length: 0)
    controller.receive(["type": "begin", "client_id": id, "utterance_id": "new-answer", "seq": 1])
    controller.receive(["type": "end_input", "outcome": "sent", "client_id": id, "utterance_id": "first", "seq": 90])
    controller.receive(["type": "update", "client_id": id, "utterance_id": "first", "seq": 91, "text": "旧结果", "final": true])
    check(currentSession(controller)?.utteranceID == "new-answer" && client.text.isEmpty, "late completion replaced a new answer")
    controller.receive(["type": "end_input", "outcome": "sent", "client_id": id, "utterance_id": "new-answer", "seq": 2])
    check(currentSession(controller)?.phase == .listening, "unfinished input was reported as sent")
    controller.receive(["type": "update", "client_id": id, "utterance_id": "new-answer", "seq": 3, "text": "新回答", "final": true])
    check(client.text == "新回答", "next answer failed after retirement")
    controller.deactivateServer(client)
    print("PASS \(outcome) retires native input without writes, fences late replies, and starts a fresh answer")
}

// A short-lived controller must not discard another client's still-pending
// activation. The longer sequence replays the 2026-11-16 14:16:54 trace.
for sequence in ["minimal", "recorded"] {
    let target = ProxiMicInputController(), transient = ProxiMicInputController()
    let editor = LocalClient(), temporary = LocalClient()
    temporary.bundle = ""
    if sequence == "recorded" { transient.activateServer(temporary) }
    target.activateServer(editor)
    if sequence == "recorded" { transient.deactivateServer(temporary) }
    transient.activateServer(temporary)
    transient.deactivateServer(temporary)
    check(editor.reads == 0 && temporary.reads == 0, "callback burst read an unsettled client")
    drainActivation()
    check(IMEService.shared.activeController === target,
          "\(sequence) callback sequence lost the pending target")
    check(target.sendState(includeSnapshot: true, requestID: "after-transient"),
          "\(sequence) target remained stuck preparing")
    firstSentence(target, editor)
    check(temporary.reads == 0 && temporary.writes == 0, "retired transient client was accessed")
    target.deactivateServer(editor)
    check(IMEService.shared.activeController == nil, "completed target resurrected a retired client")
    print("PASS \(sequence) callback burst preserves only the live pending activation")
}

// A pending request is eligible only while its own lifecycle remains open.
for event in ["deactivate", "close"] {
    let target = ProxiMicInputController(), transient = ProxiMicInputController()
    let editor = LocalClient(), temporary = LocalClient()
    target.activateServer(editor)
    transient.activateServer(temporary)
    if event == "close" { target.inputControllerWillClose() }
    else { target.deactivateServer(editor) }
    transient.deactivateServer(temporary)
    drainActivation()
    check(IMEService.shared.activeController == nil, "\(event) restored a retired pending target")
    check(editor.reads == 0 && editor.writes == 0, "\(event) accessed the retired editor")
    print("PASS \(event) cancels a superseded pending activation")
}

// Once a newer client reaches preparation, older pending requests are obsolete.
do {
    let old = ProxiMicInputController(), newest = ProxiMicInputController()
    let oldClient = LocalClient(), newClient = LocalClient()
    old.activateServer(oldClient)
    newest.activateServer(newClient)
    drainActivation()
    firstSentence(newest, newClient)
    newest.deactivateServer(newClient)
    drainActivation()
    check(IMEService.shared.activeController == nil, "settled newer client revived an older request")
    check(oldClient.reads == 0 && oldClient.writes == 0, "superseded pending target was accessed")
    check(!(Mirror(reflecting: old).children.first { $0.label == "preparingActivation" }!.value as! Bool),
          "superseded target remained stuck preparing")
    old.deactivateServer(oldClient)
    print("PASS settled newer activation permanently retires older pending requests")
}

// A preserved pending request still belongs to its original foreground app.
for stage in ["settling", "bundle"] {
    let service = IMEService.shared
    let foreground = service.activationForegroundPID
    var pid: pid_t = 101
    service.activationForegroundPID = { pid }
    let target = ProxiMicInputController(), transient = ProxiMicInputController()
    let editor = LocalClient(), temporary = LocalClient()
    target.activateServer(editor)
    transient.activateServer(temporary)
    transient.deactivateServer(temporary)
    if stage == "settling" { pid = 102 }
    else { editor.onBundle = { pid = 102 } }
    drainActivation()
    check(service.activeController == nil, "\(stage) activation survived a foreground change")
    check(editor.reads == 0 && editor.writes == 0, "\(stage) activation accessed an old foreground editor")
    service.activationForegroundPID = foreground
    target.deactivateServer(editor)
    print("PASS preserved pending activation revalidates foreground at \(stage)")
}

// Remote reads can reenter lifecycle callbacks. Once preparation starts, a
// short-lived successor must not return ownership to that in-flight read.
do {
    let target = ProxiMicInputController(), transient = ProxiMicInputController()
    let editor = LocalClient(), temporary = LocalClient()
    editor.onBundle = {
        transient.activateServer(temporary)
        transient.deactivateServer(temporary)
    }
    target.activateServer(editor); drainActivation()
    check(IMEService.shared.activeController == nil, "transient switch revived an in-flight client read")
    check(editor.reads == 0 && editor.writes == 0, "retired preparation continued accessing its client")
    check(!target.sendState(includeSnapshot: true, requestID: "retired-preparation"),
          "retired preparation published a focus proof")
    target.deactivateServer(editor)
    print("PASS in-flight preparation cannot regain ownership after nested activation")
}

// A previously prepared editor is never a fallback after a transient switch.
do {
    let old = ProxiMicInputController(), transient = ProxiMicInputController()
    let oldClient = LocalClient(), temporary = LocalClient()
    old.activateServer(oldClient); drainActivation(); firstSentence(old, oldClient)
    transient.activateServer(temporary)
    transient.deactivateServer(temporary)
    drainActivation()
    check(IMEService.shared.activeController == nil, "transient switch resurrected an old input session")
    check(!old.sendState(includeSnapshot: true, requestID: "stale-target"), "old session published a focus proof")
    old.deactivateServer(oldClient)
    print("PASS transient switch never revives an already prepared session")
}

for point in ["super", "bundle", "configure", "snapshot"] {
    let old = ProxiMicInputController(), newest = ProxiMicInputController()
    let oldClient = LocalClient(), newClient = LocalClient()
    let reenter = { newest.activateServer(newClient) }
    switch point {
    case "super": IMKInputController.onActivate = reenter
    case "bundle": oldClient.onBundle = reenter
    case "configure": oldClient.onBundle = { oldClient.onBundle = reenter }
    default: oldClient.onRead = reenter
    }
    old.activateServer(oldClient)
    drainActivation()
    check(IMEService.shared.activeController === newest, "stale \(point) activation replaced the newest target")
    old.deactivateServer(oldClient)
    check(IMEService.shared.activeController === newest, "old deactivation cleared new target")
    firstSentence(newest, newClient)
    check(oldClient.writes == 0, "first Tap wrote into the old window")
    newest.deactivateServer(newClient)
    print("PASS reentrant activation at \(point)")
}

// An IMK controller may be reused while its own previous activation is in flight.
do {
    let controller = ProxiMicInputController()
    let oldClient = LocalClient(), newClient = LocalClient()
    oldClient.onBundle = { controller.activateServer(newClient) }
    controller.activateServer(oldClient)
    drainActivation()
    firstSentence(controller, newClient)
    check(oldClient.writes == 0, "same-controller reentry restored an old adapter")
    controller.deactivateServer(newClient)
    print("PASS same-controller reactivation")
}

// Native composition cleanup can itself activate a different target.
do {
    let old = ProxiMicInputController(), newest = ProxiMicInputController()
    let oldClient = LocalClient(), newClient = LocalClient()
    old.activateServer(oldClient)
    drainActivation()
    let id = currentClientID(old)
    old.receive(["type": "begin", "client_id": id, "utterance_id": "cleanup", "seq": 1])
    old.receive(["type": "update", "client_id": id, "utterance_id": "cleanup", "seq": 2, "text": "保留", "final": false])
    oldClient.onInsert = { newest.activateServer(newClient) }
    old.deactivateServer(oldClient)
    check(IMEService.shared.activeController === newest, "cleanup cleared a nested activation")
    drainActivation()
    firstSentence(newest, newClient)
    newest.deactivateServer(newClient)
    print("PASS reactivation during composition cleanup")
}
// A transient activation must never read/write a field before AppKit returns.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client)
    check(client.reads == 0, "activation queried the editor synchronously")
    check(!controller.isUnboundIdleClient, "source recovery interrupted an unfinished activation")
    controller.receive(["type": "begin", "client_id": currentClientID(controller), "utterance_id": "early", "seq": 1])
    check(client.reads == 0 && client.writes == 0, "BEGIN entered an unfinished activation")
    controller.deactivateServer(client)
    drainActivation()
    check(client.reads == 0 && client.writes == 0, "deferred callback resurrected a retired client")
    print("PASS transient activation never queries or writes")
}
// A host ping can arrive while a synchronous IMK read pumps the run loop.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client)
    drainActivation()
    client.onRead = {
        let reads = client.reads
        controller.sendState(includeSnapshot: true, requestID: "nested-ping")
        check(client.reads == reads, "nested state ping recursively read the editor")
    }
    controller.sendState(includeSnapshot: true, requestID: "outer-ping")
    firstSentence(controller, client)
    controller.deactivateServer(client)
    print("PASS reentrant state reads coalesced")
}
print("PASS 8 activation lifecycle regressions")

do {
    let controller = ProxiMicInputController(), client = LocalClient()
    client.hideDocumentRanges = true
    controller.activateServer(client)
    drainActivation()
    check(!controller.isUnboundIdleClient, "unavailable ranges triggered source recovery")
    firstSentence(controller, client)
    let id = currentClientID(controller)
    controller.receive(["type": "cancel", "client_id": id, "utterance_id": "first", "seq": 4])
    check(currentSession(controller)?.phase == .dictated && currentSession(controller)?.error.isEmpty == false,
          "unsupported controller undo blocked dictation or failed silently")
    controller.receive(["type": "begin", "client_id": id, "utterance_id": "second", "seq": 1])
    controller.receive(["type": "update", "client_id": id, "utterance_id": "second", "seq": 2, "text": "继续听写", "final": true])
    check(client.text == "第一句话继续听写" && currentSession(controller)?.phase == .dictated,
          "controller retained an unsupported-operation error across sentences")
    controller.receive(["type": "begin", "client_id": id, "utterance_id": "retired", "seq": 1])
    controller.deactivateServer(client)
    let writes = client.writes
    controller.receive(["type": "update", "client_id": id, "utterance_id": "retired", "seq": 2, "text": "不得写入", "final": true])
    check(client.writes == writes, "range-free dictation bypassed deactivation ownership")
    print("PASS controller permits range-free dictation after failed undo and rejects retired clients")
}


// Exercise the production recovery policy with a simulated OS; never switch
// the machine's real input source from a test.
for scenario in ["normal", "busy", "other_source", "target_changed", "source_changed", "failed"] {
    let recovery = InputSourceRecovery()
    var selected = "voice", target = true, operations: [String] = []
    if scenario == "other_source" { selected = "pinyin" }
    var result = ""
    var scheduled: (() -> Void)?
    recovery.recover(request: "tap", now: 10,
        eligible: { target && scenario != "busy" }, selected: { selected == "voice" },
        leave: {
            operations.append("leave")
            if scenario == "failed" { return false }
            selected = "ascii"
            if scenario == "target_changed" { target = false }
            if scenario == "source_changed" { selected = "pinyin" }
            return true
        }, mayReturn: { target && selected == "ascii" },
        restore: { operations.append("return"); selected = "voice"; return true },
        schedule: { scheduled = $0 }, completion: { result = $0 })
    check(!operations.contains("return"), "source returned before deactivation could drain")
    scheduled?()
    if scenario == "normal" {
        check(result == "refreshed" && operations == ["leave", "return"], "missing client was not recovered")
        var duplicate = ""
        recovery.recover(request: "tap", now: 11, eligible: { true }, selected: { true },
            leave: { fatalError("repeated recovery") }, mayReturn: { true }, restore: { true },
            schedule: { $0() }, completion: { duplicate = $0 })
        check(duplicate == "duplicate", "duplicate recovery ran twice")
        var cooldown = ""
        recovery.recover(request: "tap2", now: 11, eligible: { true }, selected: { true },
            leave: { fatalError("recovery loop") }, mayReturn: { true }, restore: { true },
            schedule: { $0() }, completion: { cooldown = $0 })
        check(cooldown == "cooldown", "recovery was not rate limited")
    } else {
        check(!operations.contains("return"), "recovery overwrote a changed target/source")
        if scenario == "busy" || scenario == "other_source" { check(operations.isEmpty, "recovery touched an active sentence or another source") }
    }
    print("PASS input-source recovery \(scenario)")
}

for scenario in ["cancelled", "source_changed", "target_changed"] {
    let recovery = InputSourceRecovery()
    var allowed = true
    var selected = "voice"
    var callback: (() -> Void)?
    var result = ""
    recovery.recover(request: scenario, now: 10, eligible: { true }, selected: { true },
        leave: { selected = "ascii"; return true }, mayReturn: { allowed && selected == "ascii" },
        restore: { fatalError("restored over a newer action") }, schedule: { callback = $0 }, completion: { result = $0 })
    check(callback != nil && result.isEmpty, "recovery did not yield asynchronously")
    if scenario == "source_changed" { selected = "pinyin" } else { allowed = false }
    callback?()
    check(result == "changed", "delayed recovery ignored newer user action")
    print("PASS asynchronous recovery fences \(scenario)")
}


// Old sender callbacks on a reused controller cannot retire the newer target.
do {
    let controller = ProxiMicInputController()
    let old = LocalClient(), fresh = LocalClient()
    controller.activateServer(old); drainActivation()
    controller.activateServer(fresh); drainActivation()
    controller.deactivateServer(old)
    check(IMEService.shared.activeController === controller, "retired sender ended current activation")
    firstSentence(controller, fresh)
    check(old.writes == 0, "retired sender received new dictation")
    controller.deactivateServer(fresh)
    check(IMEService.shared.activeController == nil, "real deactivation was ignored")
    print("PASS reused controller discards old sender but accepts current deactivation")
}
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    client.marked = NSRange(location: 0, length: 99)
    controller.activateServer(client); drainActivation()
    controller.sendState(includeSnapshot: true, requestID: "startup")
    controller.receive(["type": "begin", "client_id": currentClientID(controller), "utterance_id": "no-old-mark", "seq": 1])
    check(client.markedReads == 0, "activation/BEGIN queried old composition")
    check(client.reads > 0 && client.writes == 0, "selection not retained or old text rewritten")
    controller.deactivateServer(client)
    print("PASS activation/BEGIN reads selection but never old composition")
}
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    client.onRead = { controller.deactivateServer(client) }
    controller.activateServer(client); drainActivation()
    check(client.markedReads == 0, "retired snapshot continued querying remote client")
    check(IMEService.shared.activeController == nil, "retired read resurrected its target")
    print("PASS retired remote snapshot stops immediately")
}

// Delayed switching callbacks must retire preparation before any editor read.
do {
    let controller = ProxiMicInputController(), old = LocalClient(), fresh = LocalClient()
    controller.activateServer(old)
    Timer.scheduledTimer(withTimeInterval: 0.005, repeats: false) { _ in
        controller.sendState(includeSnapshot: true, requestID: "early-ping")
        check(old.reads == 0, "ping bypassed activation settling")
        controller.deactivateServer(old)
        controller.activateServer(fresh)
    }
    drainActivation()
    check(old.reads == 0 && old.writes == 0, "retired preparation touched old client")
    firstSentence(controller, fresh)
    controller.deactivateServer(fresh)
    print("PASS queued deactivate retires preparation before first remote read")
}

// Retired conversion preferences must never intercept function keys, even
// while the sentence is eligible for editing or has just been committed.
for final in [false, true] {
    for code: UInt16 in [96, 97, 98, 100, 101, 109, 103, 111] { // F5–F12
        let service = IMEService.shared
        service.transport.onMessage?(["type": "welcome", "protocol": 1, "epoch": "retired-keys"])
        service.transport.onMessage?(["type": "configure", "epoch": "retired-keys", "mode_switch_shortcut": "F7"])
        let controller = ProxiMicInputController(), client = LocalClient()
        client.text = "原文"; client.selection = NSRange(location: 2, length: 0)
        controller.activateServer(client); drainActivation()
        let id = currentClientID(controller)
        controller.receive(["type": "begin", "client_id": id, "utterance_id": "function-key", "seq": 1])
        controller.receive(["type": "update", "client_id": id, "utterance_id": "function-key", "seq": 2,
                            "text": "改得简短一些", "final": final])
        let session = currentSession(controller)!
        check(final || session.canEdit, "function-key test did not reach editable composition")
        let key = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [.function], timestamp: 0,
                                  windowNumber: 0, context: nil, characters: "", charactersIgnoringModifiers: "",
                                  isARepeat: false, keyCode: code)!
        check(!controller.handle(key, client: client), "retired function key was consumed")
        check(!session.editRequested && session.phase != .editing, "retired function key requested editing")
        check(client.text == "原文改得简短一些", "function-key takeover lost current text")
        controller.deactivateServer(client)
    }
}
print("PASS retired F5–F12 conversion keys pass through before and after final text, including legacy F7 preference")

// The gesture/toolbar command remains the explicit editing entry point.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    client.text = "原文"; client.selection = NSRange(location: 2, length: 0)
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "begin", "client_id": id, "utterance_id": "gesture-edit", "seq": 1])
    controller.receive(["type": "update", "client_id": id, "utterance_id": "gesture-edit", "seq": 2,
                        "text": "改得简短一些", "final": false])
    controller.receive(["type": "convert", "client_id": id, "utterance_id": "gesture-edit", "seq": 3])
    check(currentSession(controller)!.editRequested, "gesture conversion lost editing intent")
    controller.receive(["type": "update", "client_id": id, "utterance_id": "gesture-edit", "seq": 4,
                        "text": "改得简短一些", "final": true])
    check(currentSession(controller)!.phase == .editing, "gesture conversion did not start editing after final text")
    controller.deactivateServer(client)
    print("PASS gesture conversion still starts editing from the active composition")
}

// UI-only messages must not read/write a client, reconfigure keys or reset a sentence.
do {
    let service = IMEService.shared
    service.transport.onMessage?(["type": "welcome", "protocol": 1, "epoch": "hud-test"])
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "begin", "client_id": id, "utterance_id": "hud-sentence", "seq": 1])
    controller.receive(["type": "update", "client_id": id, "utterance_id": "hud-sentence", "seq": 2,
                        "text": "仍在听写", "final": false])
    let before = (client.reads, client.writes, client.text, client.marked)
    let settings = (service.cancelShortcut, service.multiUndoEnabled)
    service.transport.onMessage?([
        "type": "configure_hud", "epoch": "hud-test",
        "scale_percent": 120,
        "gesture_hints": ["confirm": ["tap", ""], "undo": ["snap", ""], "switch_mode": ["swipe-right", ""]],
        "mode_switch_shortcut": "F1", "cancel_shortcut": "F2", "multi_undo_enabled": !settings.1
    ])
    check(client.reads == before.0 && client.writes == before.1 && client.text == before.2 && client.marked == before.3,
          "HUD configuration touched the active sentence")
    check(service.cancelShortcut == settings.0 && service.multiUndoEnabled == settings.1,
          "HUD configuration applied unrelated voice settings")
    check(ActionPanel.displayedHints.actions["undo"] == ["snap"], "new icons did not reach the active panel")
    check(ActionPanel.displayedAppearance.scalePercent == 120, "HUD size did not reach the active panel")
    controller.receive(["type": "update", "client_id": id, "utterance_id": "hud-sentence", "seq": 3,
                        "text": "仍在听写完成", "final": true])
    check(client.text == "仍在听写完成" && !isValidRange(client.marked), "HUD update interrupted the normal final commit")
    controller.deactivateServer(client)
    print("PASS HUD metadata changes icons without touching voice state or the editor")
}

// A gesture session shows an empty palette immediately, keeps it after a word,
// and rejects delayed events from the previous session in the same IMK client.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "stroke_begin", "client_id": id, "stroke_session": "s1"])
    check(StrokeCandidatePanel.visible && client.writes == 0, "stroke wake must show a blank palette without starting dictation")
    let points = [[0.2, 0.5], [0.8, 0.5]]
    controller.receive(["type": "stroke_trace", "client_id": id, "stroke_session": "s1", "points": points, "finished": true])
    check(StrokeCandidatePanel.lastTrace == points, "native view did not receive the shared canonical path")
    controller.receive(["type": "stroke_update", "client_id": id, "stroke_session": "s1", "code": "一", "candidates": ["二"]])
    controller.receive(["type": "stroke_commit", "client_id": id, "stroke_session": "s1", "request_id": "commit", "text": "二"])
    check(StrokeCandidatePanel.visible && client.text == "二", "committing a word closed stroke mode")
    let writes = client.writes
    controller.receive(["type": "stroke_clear", "client_id": id, "stroke_session": "s1"])
    check(StrokeCandidatePanel.visible && client.writes == writes, "clearing an empty code ended the session or erased committed text")
    controller.receive(["type": "stroke_end", "client_id": id, "stroke_session": "s1"])
    check(!StrokeCandidatePanel.visible && client.text == "二", "exit failed to preserve committed text")
    controller.receive(["type": "stroke_begin", "client_id": id, "stroke_session": "s2"])
    controller.receive(["type": "stroke_update", "client_id": id, "stroke_session": "s1", "code": "一", "candidates": ["一"]])
    controller.receive(["type": "stroke_end", "client_id": id, "stroke_session": "s1"])
    check(client.writes == writes && StrokeCandidatePanel.visible, "stale session changed the new composition")
    controller.deactivateServer(client)
    check(!StrokeCandidatePanel.visible, "empty stroke palette survived losing focus")
    print("PASS gesture stroke session lifecycle and stale-session fence")
}

// Space confirms the first candidate in the same insertion, including predictions.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "stroke_begin", "client_id": id, "stroke_session": "space"])
    controller.receive(["type": "stroke_update", "client_id": id, "stroke_session": "space", "code": "一", "candidates": ["二", "三"]])
    let writes = client.writes
    controller.receive(["type": "stroke_commit", "client_id": id, "stroke_session": "space", "request_id": "wrong", "text": "三 "])
    check(client.writes == writes, "space accepted a non-first candidate")
    controller.receive(["type": "stroke_commit", "client_id": id, "stroke_session": "space", "request_id": "first", "text": "二 "])
    check(client.text == "二 " && client.writes == writes + 1 && !isValidRange(client.marked),
          "candidate and space must replace preedit together")
    controller.receive(["type": "stroke_update", "client_id": id, "stroke_session": "space", "code": "", "prediction": true, "candidates": ["的"]])
    controller.receive(["type": "stroke_commit", "client_id": id, "stroke_session": "space", "request_id": "prediction", "text": "的 "])
    check(client.text == "二 的 ", "space did not confirm the first prediction")
    controller.receive(["type": "stroke_commit", "client_id": id, "stroke_session": "space", "request_id": "empty", "text": " "])
    check(client.text == "二 的  " && StrokeCandidatePanel.visible, "empty space ended stroke mode")
    controller.deactivateServer(client)
    print("PASS candidate plus space is one IMK insertion")
}

// Predictions remain unmarked until confirmed, share the stroke session fence,
// and disappear immediately when the editor takes over.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    client.text = "你好"; client.selection = NSRange(location: 2, length: 0)
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "stroke_begin", "client_id": id, "stroke_session": "predict"])
    let update: [String: Any] = ["type": "stroke_update", "client_id": id, "stroke_session": "predict",
                                "code": "", "prediction": true, "candidates": ["吗", "的"]]
    controller.receive(update)
    check(client.writes == 0 && client.text == "你好" && !isValidRange(client.marked),
          "showing a prediction wrote into the editor")
    controller.receive(["type": "stroke_commit", "client_id": id, "stroke_session": "predict", "request_id": "invalid", "text": "啊"])
    check(client.writes == 0, "a character outside the displayed predictions was committed")
    let commit: [String: Any] = ["type": "stroke_commit", "client_id": id, "stroke_session": "predict", "request_id": "pred1", "text": "吗"]
    controller.receive(commit)
    check(client.writes == 1 && client.text == "你好吗" && StrokeCandidatePanel.visible,
          "a prediction without ink could not be confirmed")
    controller.receive(commit)
    check(client.writes == 1, "a prediction confirmation was replayed")
    controller.receive(update)
    let key = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
                              windowNumber: 0, context: nil, characters: "x", charactersIgnoringModifiers: "x",
                              isARepeat: false, keyCode: 7)!
    _ = controller.handle(key, client: client)
    controller.receive(commit)
    check(client.writes == 1 && client.text == "你好吗", "keyboard takeover left stale prediction candidates")
    controller.receive(update)
    controller.receive(["type": "stroke_end", "client_id": id, "stroke_session": "predict"])
    controller.receive(update); controller.receive(commit)
    check(client.writes == 1 && !StrokeCandidatePanel.visible, "prediction revived an ended session")
    controller.deactivateServer(client)
    print("PASS unmarked predictions, explicit confirmation, replay and lifecycle fences")
}

// A successful mode switch requires the real palette to be visible.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client)
    drainActivation()
    let id = currentClientID(controller)
    StrokeCandidatePanel.canShow = false
    controller.receive(["type": "stroke_begin", "client_id": id, "stroke_session": "invisible"])
    let active = Mirror(reflecting: controller).children.first { $0.label == "strokeActive" }!.value as! Bool
    check(!active && !StrokeCandidatePanel.visible && client.writes == 0,
          "an invisible stroke palette left the input method in stroke mode")
    StrokeCandidatePanel.canShow = true
    controller.deactivateServer(client)
    print("PASS invisible stroke palette cancels entry without writing")
}

// Leaving with pending strokes cancels them rather than selecting a candidate.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "stroke_begin", "client_id": id, "stroke_session": "cancel"])
    controller.receive(["type": "stroke_update", "client_id": id, "stroke_session": "cancel", "code": "一", "candidates": ["二"]])
    let key = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
                              windowNumber: 0, context: nil, characters: "x", charactersIgnoringModifiers: "x",
                              isARepeat: false, keyCode: 7)!
    check(!controller.handle(key, client: client) && StrokeCandidatePanel.visible && client.text.isEmpty,
          "ordinary typing inside the same field must clear ink without ending stroke mode")
    controller.receive(["type": "stroke_update", "client_id": id, "stroke_session": "cancel", "code": "一", "candidates": ["二"]])
    controller.receive(["type": "stroke_end", "client_id": id, "stroke_session": "cancel"])
    check(client.text.isEmpty && !StrokeCandidatePanel.visible, "stroke exit committed an unselected candidate")
    controller.deactivateServer(client)
    print("PASS stroke exit discards only unconfirmed composition")
}

// Correction captures the current field's preceding text regardless of how it was entered.
for handoff in ["existing-text", "immediate", "source-return", "different-client", "changed-text"] {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    if handoff == "existing-text" {
        client.text = "已有的输入框文本"; client.selection = NSRange(location: client.text.utf16.count, length: 0)
    } else { firstSentence(controller, client) }
    var target = client
    if !["immediate", "existing-text"].contains(handoff) {
        suspendSentence(controller)
        controller.deactivateServer(client)
        if handoff == "different-client" {
            target = LocalClient(); target.text = client.text; target.selection = client.selection
        }
        if handoff == "changed-text" {
            target.text = "另一段内容"; target.selection = NSRange(location: target.text.utf16.count, length: 0)
        }
        controller.activateServer(target); drainActivation()
    }
    let writes = target.writes
    controller.receive(["type": "stroke_begin", "client_id": currentClientID(controller),
                        "stroke_session": "correct-preceding-text", "correction_enabled": true])
    let correction = Mirror(reflecting: controller).children.first { $0.label == "strokeCorrection" }!.value as? StrokeTextCorrection
    check(correction?.valid == true && correction?.preceding.text == target.text,
          "did not capture the current editor's preceding text after \(handoff)")
    check(target.writes == writes, "capturing correction context must not write")
    controller.deactivateServer(target)
    print("PASS preceding-text correction scope: \(handoff)")
}

// Stroke composition uses the existing native client and never starts a voice sentence.
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "stroke_update", "client_id": id, "code": "一", "candidates": ["一", "二"], "selected": 0])
    check(client.text == "一" && client.marked.length == 1, "stroke preedit was not marked")
    controller.receive(["type": "stroke_commit", "client_id": "stale", "request_id": "old", "text": "二"])
    check(client.text == "一" && client.marked.length == 1, "stale client committed stroke")
    client.onInsert = { controller.commitComposition(client) }
    controller.receive(["type": "stroke_commit", "client_id": id, "request_id": "one", "text": "二"])
    check(client.text == "二" && client.marked.location == NSNotFound, "stroke commit failed during client reentry")
    let writes = client.writes
    controller.receive(["type": "stroke_commit", "client_id": id, "request_id": "one", "text": "二"])
    check(client.writes == writes, "replayed stroke commit wrote twice")
    controller.deactivateServer(client)
    print("PASS native stroke preedit/commit, ownership and reentry")
}
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "begin", "client_id": id, "utterance_id": "voice-before-stroke", "seq": 1])
    controller.receive(["type": "update", "client_id": id, "utterance_id": "voice-before-stroke", "seq": 2,
                        "text": "正在听写", "final": false])
    let before = (client.text, client.writes)
    controller.receive(["type": "stroke_update", "client_id": id, "code": "一", "candidates": ["一"], "selected": 0])
    check(client.text == before.0 && client.writes == before.1, "stroke interrupted active voice composition")
    controller.deactivateServer(client)
    print("PASS active voice composition rejects stroke input")
}
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation()
    let id = currentClientID(controller)
    controller.receive(["type": "stroke_update", "client_id": id, "code": "一", "candidates": ["一"], "selected": 0])
    controller.commitComposition(client)
    check(client.text.isEmpty && client.marked.location == NSNotFound, "IME takeover committed raw stroke symbols")
    let writes = client.writes
    controller.receive(["type": "stroke_commit", "client_id": id, "request_id": "late", "text": "一"])
    check(client.writes == writes, "cleared composition accepted late commit")
    controller.deactivateServer(client)
    print("PASS native takeover cancels uncommitted strokes")
}

final class ControllerClipboardFixture: ClipboardReadEnvironment {
    let client: LocalClient
    var now: TimeInterval = 0
    var changeCount = 1
    var contents: ClipboardContents = [["public.utf8-plain-text": Data("保留".utf8)]]
    var onCopy: ((Int64) -> Void)?
    init(_ client: LocalClient) { self.client = client }
    func current() -> Bool { true }
    func selection() -> NSRange { client.selection }
    func marked() -> NSRange { client.marked }
    func backup() throws -> ClipboardContents { contents }
    func text() -> String? { contents.first?["public.utf8-plain-text"].flatMap { String(data: $0, encoding: .utf8) } }
    func restore(_ saved: ClipboardContents, replacing count: Int) throws {
        guard changeCount == count else { throw ClipboardReadError(code: "pasteboard_restore_conflict") }
        contents = saved; changeCount += 1
    }
    func post(_ key: ClipboardReadKey, tag: Int64) throws {
        switch key {
        case .prefix: client.selection = NSRange(location: 0, length: client.selection.location)
        case .suffix: client.selection.length = client.text.utf16.count - client.selection.location
        case .copy:
            contents = [["public.utf8-plain-text": Data((client.text as NSString).substring(with: client.selection).utf8)]]
            changeCount += 1; onCopy?(tag)
        case .collapseStart: client.selection.length = 0
        case .collapseEnd: client.selection = NSRange(location: NSMaxRange(client.selection), length: 0)
        }
    }
    func pump() { now += 0.01 }
}
do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation(); firstSentence(controller, client)
    let adapter = Mirror(reflecting: controller).children.first { $0.label == "adapter" }!.value as! NativeInputClient
    let environment = ControllerClipboardFixture(client)
    let reader = ClipboardBodyReader(environment)
    reader.onFinished = adapter.clipboardReader?.onFinished
    adapter.configureClipboardReader(reader)
    let original = client.selection, writes = client.writes
    var ownTag: Int64 = 0
    environment.onCopy = { tag in
        ownTag = tag
        let event = CGEvent(keyboardEventSource: nil, virtualKey: 8, keyDown: true)!
        event.flags = .maskCommand; event.setIntegerValueField(.eventSourceUserData, value: tag)
        check(!controller.handle(NSEvent(cgEvent: event), client: client), "active own copy key was swallowed")
        controller.receive(["type": "reset", "all": true])
        check(currentSession(controller)?.phase == .dictated, "host reset ran in the middle of copying")
        check(client.writes == writes, "copy caused preedit/write reentry")
    }
    check(reader.read(.prefix(tail: 0), validationStage: "coordination")?.text == "第一句话", "controller copy failed")
    check(client.selection == original && client.writes == writes, "controller copy moved the caret or wrote text")
    let late = CGEvent(keyboardEventSource: nil, virtualKey: 8, keyDown: true)!
    late.flags = .maskCommand; late.setIntegerValueField(.eventSourceUserData, value: ownTag)
    check(controller.handle(NSEvent(cgEvent: late), client: client), "late own copy key was not consumed")
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.03))
    let queued = Mirror(reflecting: controller).children.first { $0.label == "clipboardMessages" }!.value as! [[String: Any]]
    check(queued.isEmpty && currentSession(controller)?.phase != .dictated, "deferred reset was lost after copy cleanup")
    controller.deactivateServer(client)
    print("PASS B84 controller serializes copy keys host messages and late delivery")
}


do {
    let controller = ProxiMicInputController(), client = LocalClient()
    controller.activateServer(client); drainActivation(); firstSentence(controller, client)
    let adapter = Mirror(reflecting: controller).children.first { $0.label == "adapter" }!.value as! NativeInputClient
    let environment = ControllerClipboardFixture(client)
    let active = ClipboardBodyReader(environment)
    active.onFinished = adapter.clipboardReader?.onFinished
    adapter.configureClipboardReader(active)
    environment.onCopy = { _ in
        controller.receive(["type": "reset", "all": true])
        controller.deactivateServer(client)
        let queued = Mirror(reflecting: controller).children.first { $0.label == "clipboardMessages" }!.value as! [[String: Any]]
        check(queued.isEmpty, "old activation retained deferred reset for the next input session")
    }
    check(active.read(.prefix(tail: 0), validationStage: "retire") == nil, "retired activation returned a copy receipt")
    print("PASS B84 retiring activation discards deferred clipboard messages")
}
