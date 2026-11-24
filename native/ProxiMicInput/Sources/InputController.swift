import AppKit
import InputMethodKit
import Carbon

final class IMEService {
    struct SuspendedInput {
        let token: String
        let application: String
        // IMK's uniqueClientIdentifierString can generate a fresh string on each call.
        weak var client: IMKTextInput?
        let session: CompositionSession.Suspension
        var history: CompositionUndoHistory.Snapshot?
        let undoSourceUtteranceID: String?
    }
    static let shared = IMEService()
    let transport = IPCClient()
    let activation = InputActivation<ProxiMicInputController>()
    let contextKey = InputContextKey()
    var activeController: ProxiMicInputController? { activation.current }
    var activationForegroundPID: () -> pid_t = { NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0 }
    var inputContextForeground: () -> (application: String, pid: pid_t)? = {
        guard let app = NSWorkspace.shared.frontmostApplication else { return nil }
        return (app.bundleIdentifier ?? "", app.processIdentifier)
    }
    private(set) var epoch: String?
    var cancelShortcut = "Esc"
    var gestureHints = VoiceHudBindings()
    var hudAppearance = VoiceHudAppearance()
    var automaticPolish = false // Older hosts keep the manual command.
    private(set) var suggestion: DictationSuggestion?

    func clearSuggestion(token: String? = nil, reason: String = "target_changed") {
        guard let offer = suggestion, token == nil || token == offer.token else { return }
        suggestion = nil
        offer.close()
        send(["type": "dictation_preview_state", "token": offer.token, "visible": false, "reason": reason])
    }

    func beginSuggestion(_ session: CompositionSession, adapter: NativeInputClient, caret: NSRect?) -> String? {
        clearSuggestion(reason: "next_sentence")
        guard automaticPolish, let front = inputContextForeground(), let caret,
              front.application == (adapter.client.bundleIdentifier() ?? "") else { return nil }
        let offer = DictationSuggestion(session: session, pid: front.pid, caret: caret)
        suggestion = offer
        offer.observe { [weak self, weak offer] in
            guard let self, let offer, self.suggestion === offer, self.inputContextForeground()?.pid == front.pid else { return false }
            if self.activeController?.ownsSuggestion(offer) == true { return true }
            return self.suspendedInput?.session.utteranceID == offer.utterance &&
                self.suspendedInput?.session.revision == offer.revision && offer.suspendedFieldUnchanged()
        } invalidated: { [weak self, weak offer] reason in
            if let offer { self?.clearSuggestion(token: offer.token, reason: reason) }
        }
        return offer.token
    }

    let multiUndoEnabled = true
    var historyMode: HistoryActionMode = .retry
    var redoEnabled: Bool { historyMode == .next }
    var suspendedInput: SuspendedInput?
    private lazy var noticePanel = ActionPanel()
    private var clipboardToken = ""
    private lazy var clipboardPanel: ClipboardSuggestionPanel = {
        let panel = ClipboardSuggestionPanel()
        panel.onVisibility = { [weak self] token, visible in
            if !visible, self?.clipboardToken == token { self?.clipboardToken = "" }
            self?.send(["type": "clipboard_offer_state", "token": token, "visible": visible])
        }
        return panel
    }()

    func hideClipboardSuggestion() { clipboardToken = ""; clipboardPanel.hide() }

    private init() {
        transport.onMessage = { [weak self] in self?.receive($0) }
        transport.onDisconnect = { [weak self] in
            guard let self else { return }
            self.clearSuggestion()
            self.epoch = nil
            self.contextKey.cancel()
            self.suspendedInput = nil
            self.noticePanel.hide()
            self.hideClipboardSuggestion()
            self.activeController?.hostDisconnected()
        }
    }
    func start() { transport.start() }
    func send(_ message: [String: Any]) {
        guard let epoch else { return }
        var envelope = message
        envelope["epoch"] = epoch
        transport.send(envelope)
    }
    func inactiveState(requestID: String? = nil, lifecycleEvent: String = "inactive") {
        var state: [String: Any] = ["type": "state", "ready": false, "phase": "idle", "client_id": "",
                                    "utterance_id": "", "application": "", "revision": 0,
                                    "edit_requested": false, "raw": "", "error": "请手动选中 Mythlink 语音输入法并进入文本框",
                                    "lifecycle_event": lifecycleEvent, "has_composition": false,
                                    "original": "", "selection": [0, 0], "context_complete": false,
                                    "capabilities": ["marked_text": false, "context_complete": false,
                                                     "document_access": false, "range_replacement": "unavailable",
                                                     "selection_restore": false]]
        if let requestID { state["request_id"] = requestID }
        send(state)
    }

    private func receive(_ message: [String: Any]) {
        guard let type = message["type"] as? String else { return }
        if type == "welcome" {
            guard message["protocol"] as? Int == 1, let next = message["epoch"] as? String, !next.isEmpty else { return }
            if epoch != next {
                automaticPolish = false
                clearSuggestion()
                contextKey.cancel()
                suspendedInput = nil
                activeController?.hostConnected()
            }
            epoch = next
            if let active = activeController { active.sendState(includeSnapshot: true, metadataOnly: true) }
            else { inactiveState() }
            return
        }
        guard let epoch, message["epoch"] as? String == epoch else { return }
        if type == "prepare_input_context" {
            guard let request = message["request_id"] as? String else { return }
            let front = inputContextForeground()
            let armed = (message["application"] as? String == front?.application
                && message["foreground_pid"] as? Int32 == front?.pid
                && activeController?.permitsContextKey == true)
                && contextKey.arm(id: request, tag: message["event_tag"] as? Int64 ?? 0, epoch: epoch,
                                  application: front?.application ?? "", pid: front?.pid ?? 0)
            send(["type": "input_context_prepared", "request_id": request, "armed": armed])
            return
        }
        if type == "cancel_input_context" {
            if let request = message["request_id"] as? String { contextKey.cancel(id: request) }
            return
        }
        if type == "dictation_preview_dismiss" {
            if let token = message["token"] as? String { clearSuggestion(token: token, reason: "dismissed") }
            return
        }
        if type == "dictation_preview_result" {
            guard let offer = suggestion, offer.token == message["token"] as? String else { return }
            guard let text = message["text"] as? String, offer.show(text) else {
                clearSuggestion(token: offer.token, reason: offer.stillValid() ? "preview_unavailable" : "target_changed")
                return
            }
            activeController?.hideDictationHUD()
            send(["type": "dictation_preview_state", "token": offer.token, "visible": true])
            return
        }
        if type == "begin" || type == "stroke_update" || type == "input_notice" ||
            (["end_input", "cancel", "system_undo_posted", "redo", "retry_edit"].contains(type) && message["utterance_id"] as? String == suggestion?.utterance) {
            clearSuggestion(reason: type == "begin" ? "next_sentence" : type)
        }
        if type == "clipboard_dismiss" {
            if let token = message["token"] as? String {
                if clipboardToken == token { clipboardToken = "" }
                clipboardPanel.hide(token: token)
            }
            return
        }
        if type == "clipboard_offer" {
            guard let token = message["token"] as? String, !token.isEmpty else { return }
            clipboardToken = token
            guard let pid = message["foreground_pid"] as? Int32,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                  let revision = message["revision"] as? Int,
                  let values = message["rect"] as? [Double], values.count == 4,
                  values.allSatisfy({ $0.isFinite }), values[2] >= 0, values[3] > 0,
                  let screen = NSScreen.screens.first,
                  let preview = ClipboardPreview.read(.general, revision: revision),
                  clipboardToken == token,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else {
                if clipboardToken == token { clipboardToken = "" }
                send(["type": "clipboard_offer_state", "token": token, "visible": false]); return
            }
            let caret = NSRect(x: values[0], y: screen.frame.maxY - values[1] - values[3],
                               width: max(1, values[2]), height: values[3])
            if !clipboardPanel.show(preview, token: token, caret: caret) {
                send(["type": "clipboard_offer_state", "token": token, "visible": false])
            }
            return
        }
        if ["begin", "stroke_update", "input_notice"].contains(type) { hideClipboardSuggestion() }
        if type == "discard_suspended_input" {
            if message["token"] as? String == suspendedInput?.token { suspendedInput = nil }
            if let token = message["token"] as? String { activeController?.discardResumedInput(token: token) }
            return
        }
        if type == "input_notice" {
            guard let pid = message["foreground_pid"] as? Int32,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                  let text = message["text"] as? String, !text.isEmpty, text.count <= 512 else { return }
            if let active = activeController, active.showInputNotice(message) { return }
            // Even before the first dictation, show a passive IME hint. Never
            // activate an app or manufacture a composition to display feedback.
            noticePanel.hide()
            noticePanel.configureAppearance(hudAppearance)
            noticePanel.showNotice(text)
            let point = NSEvent.mouseLocation
            noticePanel.position(caret: NSRect(x: point.x, y: point.y, width: 1, height: 1),
                                 clientLevel: Int32(NSWindow.Level.floating.rawValue))
            return
        }
        if type == "configure_hud" {
            gestureHints.apply(message["gesture_hints"])
            hudAppearance.apply(message["scale_percent"])
            activeController?.configureGestureHints()
            return // Presentation only: never reconfigure shortcuts, undo or a sentence.
        }
        if type == "configure" {
            if let enabled = message["automatic_polish"] as? Bool, automaticPolish != enabled {
                automaticPolish = enabled
                if !enabled { clearSuggestion(reason: "mode_changed") }
                activeController?.refreshPolishHUD()
            }
            if let value = message["cancel_shortcut"] as? String { cancelShortcut = value }
            // Ignore the retired multi_undo_enabled preference from older hosts.
            let mode = (message["history_action_mode"] as? String).flatMap(HistoryActionMode.init(rawValue:))
                ?? (message["redo_enabled"] as? Bool).map { $0 ? .next : .undo }
            if let mode {
                historyMode = mode
                if mode != .next {
                    let retained = suspendedInput?.history?.withoutRedo()
                    suspendedInput?.history = retained
                }
                activeController?.configureRedoHistory(mode == .next)
            }
            return
        }
        if type == "recover_activation" {
            // Older hosts must not initiate an ABC round-trip after an update.
            send(["type": "activation_recovery", "request_id": message["request_id"] as? String ?? "",
                  "result": "disabled", "error": ""])
            return
        }
        if type == "ping" {
            let request = message["request_id"] as? String
            if let active = activeController {
                active.sendState(includeSnapshot: true, requestID: request,
                                 metadataOnly: message["capture_context"] as? Bool != true)
            }
            else { inactiveState(requestID: request) }
            return
        }
        guard let active = activeController else { inactiveState(); return }
        active.receive(message)
    }

    func consumeContextKey(_ event: NSEvent, sender: Any?) -> Bool {
        let tag = event.cgEvent?.getIntegerValueField(.eventSourceUserData) ?? 0
        guard event.type == .keyDown, contextKey.consumes(code: event.keyCode, tag: tag) else { return false }
        guard let request = contextKey.take(tag: tag), request.epoch == epoch,
              let input = sender as? IMKTextInput, input.bundleIdentifier() == request.application else { return true }
        // Event dispatch may create a new controller whose 25 ms activation
        // preparation is still pending. Reply from that settled active client,
        // never from the controller that happened to receive this event.
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: false) { [weak self] _ in
            guard let self, self.epoch == request.epoch,
                  let front = self.inputContextForeground(),
                  front.pid == request.pid, front.application == request.application else { return }
            if let active = self.activeController {
                active.sendState(includeSnapshot: true, requestID: request.id, metadataOnly: true)
            } else { self.inactiveState(requestID: request.id) }
        }
        return true
    }
}

@objc(ProxiMicInputController)
final class ProxiMicInputController: IMKInputController {
    // Keep OS selection behind the same driver boundary as text readback;
    // local controller/IPC tests supply a driver bound to their local editor.
    var makeSelectionDriver: (pid_t, String, @escaping () -> [String: Any]) -> PrefixSelectionDriver = {
        NativePrefixSelection(pid: $0, bundle: $1, context: $2)
    }
    private var adapter: NativeInputClient?
    private let sessionLifetime = CompositionSessionLifetime()
    private var applyingSuggestion: (token: String, session: CompositionSession, text: String)?
    private var resumedInputToken: String?

    private func reportSuggestionWrite(interrupted: Bool = false) {
        guard let pending = applyingSuggestion else { return }
        let current = pending.session
        guard interrupted || session !== current || current.phase != .polishing else { return }
        applyingSuggestion = nil
        let applied = !interrupted && current === session && current.phase == .dictated && current.polished && current.raw == pending.text
        IMEService.shared.send(["type": "dictation_preview_state", "token": pending.token, "visible": false,
            "reason": applied ? "applied" : "write_unconfirmed", "detail": current.error])
    }

    private var session: CompositionSession? {
        get { sessionLifetime.current }
        set { sessionLifetime.current = newValue; resumedInputToken = nil }
    }
    private var timer: Timer?
    private var activationTimer: Timer?
    private var currentClientID = UUID().uuidString
    private var activated = false
    private var preparingActivation = false
    private var stateReadInProgress = false
    private var activationLease: UInt64 = 0
    private let activationSenders = InputActivationSenders()
    private var activationApplication = ""
    private var ownsActivation: Bool { IMEService.shared.activation.owns(self, activationLease) }
    private let panel = ActionPanel()
    private var strokePanel: StrokeCandidatePanel?
    private var strokeCode = ""
    private var strokeCandidates: [String] = []
    private var strokeActive = false
    private var strokeSessionID = ""
    private var strokeCorrectionEnabled = false
    private var strokeCorrectionSeed: EditorTextRange?
    private var strokeCorrection: StrokeTextCorrection?
    private var isWriting = false
    private var lastError = ""
    private var lastContext: EditorSnapshot?
    private var lastLifecycleEvent = "created"
    private var rejectedBeginID: String?
    private var pendingBeginID: String?
    private var compatibilityKeyReply: (id: String, callback: (String?) -> Void)?
    private var earlySystemUndo: UUID?
    private static let undoCaretMarker: Int64 = 1 << 52
    private var undoCaretKey: (tag: Int64, revision: Int, range: NSRange, expires: TimeInterval)?
    private var systemUndoKey: (tag: Int64, expires: TimeInterval)?
    private let undoHistory = CompositionUndoHistory()
    private var undoSourceUtteranceID: String?
    private var preservingHistory = false

    var isUnboundIdleClient: Bool {
        activated && !preparingActivation && session == nil
            && lastContext == nil
    }

    var permitsContextKey: Bool {
        activated && ownsActivation && !preparingActivation && !isWriting && !strokeActive
            && pendingBeginID == nil && adapter?.clipboardReadInProgress != true
            && adapter?.clientOperationPending != true
            && session?.hasComposition != true && session?.awaitingReadback != true
            && !(session.map { [.listening, .finishing, .editing, .polishing].contains($0.phase) } ?? false)
    }

    func hideDictationHUD() { panel.hide() }

    func refreshPolishHUD() {
        if session != nil { changed() }
    }

    func ownsSuggestion(_ offer: DictationSuggestion) -> Bool {
        guard activated, ownsActivation, let session, let adapter,
              session.utteranceID == offer.utterance, session.revision == offer.revision,
              session.canSuggestReplacement, session.raw == offer.raw else { return false }
        return adapter.selection() == session.previewSelection
    }

    func configureGestureHints() {
        panel.configureGestureHints(IMEService.shared.gestureHints)
        panel.configureAppearance(IMEService.shared.hudAppearance)
    }

    func showInputNotice(_ message: [String: Any]) -> Bool {
        guard activated, ownsActivation, !preparingActivation, !strokeActive, let adapter,
              message["client_id"] as? String == currentClientID,
              message["utterance_id"] as? String == (session?.utteranceID ?? ""),
              let text = message["text"] as? String else { return false }
        configureGestureHints()
        panel.showNotice(text)
        panel.position(caret: adapter.caret(compositionLength: 0, trace: false),
                       clientLevel: adapter.client.windowLevel())
        return true
    }

    func configureUndoHistory(_ enabled: Bool) {
        undoHistory.configure(enabled: enabled)
        if let session, !session.systemUndoTrial, [.dictated, .edited].contains(session.phase) {
            undoHistory.settled(session, sourceUtteranceID: undoSourceUtteranceID)
        }
        if activated { changed() }
    }

    func configureRedoHistory(_ enabled: Bool) {
        undoHistory.configureRedo(enabled: enabled)
        if activated { changed() }
    }

    private func traceLifecycle(_ event: String, sender: AnyObject? = nil) {
        let fields: [String: Any] = [
            "event": event, "controller": String(describing: ObjectIdentifier(self)),
            "lease": activationLease, "client_id": currentClientID,
            "activated": activated, "preparing": preparingActivation,
            "native_read": adapter?.activeRead ?? "", "state_read": stateReadInProgress,
            "phase": session?.phase.rawValue ?? "idle",
            "utterance_id": session?.utteranceID ?? "", "revision": session?.revision ?? 0,
            "application": activationApplication,
            "front_application": NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "",
            "sender": sender.map { String(describing: ObjectIdentifier($0)) } ?? "",
            "bound_sender": activationSenders.current.map { String(describing: ObjectIdentifier($0)) } ?? ""]
        CaretDiagnostics.shared.recordLifecycle(fields)
        IMEService.shared.send(fields.merging(["type": "activation_trace"]) { _, value in value })
    }

    override func activateServer(_ sender: Any!) {
        let service = IMEService.shared
        let foregroundPID = service.activationForegroundPID()
        let previous = service.activeController
        let lease = service.activation.begin(self)
        activationLease = lease
        var scheduled = false
        defer {
            // Reentry before scheduling cannot leave a dormant request that
            // could become current when a newer controller deactivates.
            if !scheduled { service.activation.end(self, lease) }
        }
        activationSenders.begin(sender as AnyObject?)
        // Retire local timers before any remote IMK call can reenter us.
        activated = false
        preparingActivation = false
        activationTimer?.invalidate()
        activationTimer = nil
        timer?.invalidate()
        timer = nil
        panel.hide()
        undoHistory.clear()
        strokePanel?.hide()
        strokeCode = ""
        strokeCandidates = []
        strokeActive = false
        strokeSessionID = ""
        strokeCorrection?.invalidate("")
        strokeCorrection = nil
        strokeCorrectionSeed = nil
        undoHistory.configure(enabled: service.multiUndoEnabled)
        undoHistory.configureRedo(enabled: service.redoEnabled)
        undoSourceUtteranceID = nil
        if let previous, previous !== self {
            previous.lastLifecycleEvent = "client_switched"
            previous.activated = false
            previous.timer?.invalidate()
            previous.timer = nil
            previous.panel.hide()
            previous.strokePanel?.hide()
            previous.strokeCode = ""
            previous.strokeCandidates = []
            previous.strokeActive = false
            previous.strokeSessionID = ""
            previous.strokeCorrection?.invalidate("")
            previous.strokeCorrection = nil
            previous.invalidate("已切换输入框")
        }
        guard service.activation.owns(self, lease) else { return }
        if session != nil {
            lastLifecycleEvent = "reactivate"
            invalidate("输入法已重新激活")
        }
        guard service.activation.owns(self, lease) else { return }
        session = nil
        super.activateServer(sender)
        guard service.activation.owns(self, lease) else { return }
        traceLifecycle("activate_callback")
        // DispatchQueue.main.async can run before the remaining IMK switching
        // callbacks: our first remote read then pumps a queued deactivation.
        // Allow a short, cancellable run-loop settling window before querying.
        // This is only preparation, never readiness or permission to write.
        preparingActivation = true
        activationTimer = Timer.scheduledTimer(withTimeInterval: 0.025, repeats: false) { [weak self] _ in
            guard let self, self.activationLease == lease else { return }
            self.activationTimer = nil
            self.preparingActivation = false
            guard service.activation.prepare(self, lease) else { return }
            guard foregroundPID > 0, service.activationForegroundPID() == foregroundPID else {
                self.retireActivation("activation_target_changed", reason: "已切换前台应用")
                return
            }
            self.prepareActivation(sender, lease: lease, foregroundPID: foregroundPID)
        }
        scheduled = true
    }

    private func prepareActivation(_ sender: Any!, lease: UInt64, foregroundPID: pid_t) {
        let service = IMEService.shared
        guard service.activation.owns(self, lease) else { return }
        preparingActivation = true
        defer { if activationLease == lease { preparingActivation = false } }
        guard let input = sender as? IMKTextInput ?? client() else {
            if service.activation.end(self, lease) { service.inactiveState() }
            return
        }
        guard service.activation.owns(self, lease) else { return }
        let nextAdapter = NativeInputClient(input, isCurrent: { [weak self] in
            guard let self else { return false }
            return self.activated && service.activation.owns(self, lease)
        }, preeditAttributes: { [weak self] range in
            self?.mark(forStyle: kTSMHiliteSelectedRawText, at: range) as? [NSAttributedString.Key: Any]
        })
        let bundle = input.bundleIdentifier() ?? ""
        activationApplication = bundle
        guard service.activation.owns(self, lease) else { return }
        guard service.activationForegroundPID() == foregroundPID else {
            retireActivation("activation_target_changed", reason: "已切换前台应用")
            return
        }
        // Source-launched Qt clients have no bundle identifier. They still
        // need native selection/deletion; the host pins their begin-time PID.
        let selectionDriver = makeSelectionDriver(
            NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0, bundle,
            { [weak self] in
                ["client_id": self?.currentClientID ?? "", "utterance_id": self?.session?.utteranceID ?? ""]
            })
        nextAdapter.configureKeyDelivery({ [weak self] command, count, tag, reply in
            guard let self, self.activated, self.ownsActivation,
                  self.session?.active == true || self.strokeCorrection?.busy == true else { reply("输入会话已结束"); return }
            let id = UUID().uuidString
            self.compatibilityKeyReply = (id, reply)
            if self.strokeActive {
                IMEService.shared.send(["type": "stroke_correction_key", "client_id": self.currentClientID,
                    "stroke_session": self.strokeSessionID, "request_id": id, "command": command, "count": count,
                    "event_tag": tag, "application": bundle])
            } else {
                self.sendEvent("wechat_key", extra: ["request_id": id, "command": command, "count": count,
                                                     "event_tag": tag, "application": bundle])
            }
        }, selectionDriver: selectionDriver)
        let clipboardReader = ClipboardBodyReader(NativeClipboardReadEnvironment(
            pid: foregroundPID, bundle: bundle,
            current: { [weak self, weak nextAdapter] in
                guard let self, let nextAdapter else { return false }
                return self.activated && service.activation.owns(self, lease) && self.adapter === nextAdapter
            }, selection: { [weak nextAdapter] in nextAdapter?.client.selectedRange() ?? unspecifiedRange },
            marked: { [weak nextAdapter] in nextAdapter?.client.markedRange() ?? unspecifiedRange }))
        clipboardReader.record = { [weak selectionDriver] event in selectionDriver?.record(event) }
        clipboardReader.onFinished = { [weak self] in
            DispatchQueue.main.async { [weak self] in self?.drainClipboardMessages() }
        }
        nextAdapter.configureClipboardReader(clipboardReader, allowed: { [weak self] in
            guard let self else { return false }
            return !self.strokeActive && (self.pendingBeginID != nil || self.isWriting)
        })
        guard service.activation.owns(self, lease) else { return }
        adapter = nextAdapter
        currentClientID = UUID().uuidString
        activated = true
        lastLifecycleEvent = "activate"
        lastError = ""
        lastContext = nil
        rejectedBeginID = nil
        panel.onPosition = { [weak self] frame in
            guard let self, self.ownsActivation, let session = self.session else { return }
            service.send(["type": "voice_panel_frame", "client_id": self.currentClientID,
                          "utterance_id": session.utteranceID, "frame": frame])
        }
        panel.onCancel = { [weak self] in self?.cancelCurrentOrHistory() }
        panel.onConvert = { [weak self] in
            guard let self else { return }
            if self.session?.historyReady == true { self.historyForward() }
            else { self.perform { $0.convert() } }
        }
        panel.onFinish = { [weak self] in self?.perform { $0.finish() } }
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in self?.tick() }
        guard service.activation.owns(self, lease) else { return }
        preparingActivation = false
        traceLifecycle("activate_prepared")
        sendState(includeSnapshot: true, metadataOnly: true)
    }

    private func retireActivation(_ event: String, reason: String) {
        clipboardMessages.removeAll()
        clearStroke()
        traceLifecycle(event)
        let lease = activationLease
        activated = false
        preparingActivation = false
        activationTimer?.invalidate()
        activationTimer = nil
        timer?.invalidate()
        timer = nil
        panel.hide()
        lastLifecycleEvent = event
        // Publish inactivity before cleanup (which can activate a newer client).
        if IMEService.shared.activation.end(self, lease) {
            IMEService.shared.inactiveState(lifecycleEvent: event)
        }
        invalidate(reason)
        if activationLease == lease { adapter = nil }
    }

    override func deactivateServer(_ sender: Any!) {
        let source = sender as AnyObject?
        if activationSenders.isKnownRetired(source) {
            traceLifecycle("ignored_retired_sender", sender: source)
            return
        }
        traceLifecycle("deactivate_received", sender: source)
        retireActivation("deactivate", reason: "已切换输入法或输入框")
        super.deactivateServer(sender)
    }

    override func inputControllerWillClose() {
        retireActivation("close", reason: "输入会话已关闭")
        super.inputControllerWillClose()
    }

    override func hidePalettes() { panel.hide(); strokePanel?.hide() }

    override func recognizedEvents(_ sender: Any!) -> Int {
        // Geometry-only ACKs normalize full-composition selections. Observe
        // direct mouse takeover in every client before a same-length user
        // selection could be mistaken for the client's composition convention.
        return Int(NSEvent.EventTypeMask.keyDown.union(.leftMouseDown).rawValue)
    }

    override func handle(_ event: NSEvent!, client sender: Any!) -> Bool {
        if let event, IMEService.shared.consumeContextKey(event, sender: sender) {
            traceLifecycle("context_key_consumed")
            return true
        }
        guard activated, ownsActivation, let event else { return false }
        if event.type == .keyDown, event.keyCode == 124, !event.isARepeat,
           event.modifierFlags.intersection([.command, .shift, .control, .option]).isEmpty {
            let tag = event.cgEvent?.getIntegerValueField(.eventSourceUserData) ?? 0
            let ours = tag >> 50 == Self.undoCaretMarker >> 50
            if let key = undoCaretKey, key.tag == tag || (tag == 0 && ProcessInfo.processInfo.systemUptime < key.expires) {
                undoCaretKey = nil
                let current = session
                let matches = adapter?.selection() == key.range && self.session === current &&
                    current?.phase == .undone && current?.revision == key.revision &&
                    current?.hostUndoTag == key.tag - Self.undoCaretMarker &&
                    ProcessInfo.processInfo.systemUptime < key.expires
                if matches || ours { return !matches }
            } else if ours { return true } // A delayed tagged key belongs to a retired editor.
        }
        // ⌘Z belongs to the editor. The host delivery receipt and the editor's
        // key event travel on separate channels and may arrive in either order.
        if event.type == .keyDown, event.keyCode == 6, !event.isARepeat,
           event.modifierFlags.intersection([.command, .shift, .control, .option]) == [.command] {
            let tag = event.cgEvent?.getIntegerValueField(.eventSourceUserData) ?? 0
            if let key = systemUndoKey, ProcessInfo.processInfo.systemUptime < key.expires,
               tag == key.tag || tag == 0 {
                systemUndoKey = nil
                return false
            }
            if let session, let hostTag = session.hostUndoTag {
                if tag == hostTag { return false }
                if tag == 0, session.canSystemUndo {
                    // Qt can strip the tag. Deliver immediately; only cleanup of
                    // an unmatched physical keyboard event waits for the receipt.
                    let token = UUID()
                    earlySystemUndo = token
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self, weak session] in
                        guard let self, let session, self.session === session, self.earlySystemUndo == token else { return }
                        self.invalidate("键盘输入已接管本句")
                    }
                    return false
                }
            }
        }
        IMEService.shared.hideClipboardSuggestion()
        if event.type == .keyDown,
           let handling = adapter?.clipboardKeyHandling(tag: event.cgEvent?.getIntegerValueField(.eventSourceUserData) ?? 0) {
            return handling
        }
        if adapter?.clipboardReadInProgress == true { adapter?.clipboardReader?.cancel() }
        if event.type == .leftMouseDown || event.type == .keyDown {
            IMEService.shared.clearSuggestion(reason: "user_input")
        }
        if event.type == .leftMouseDown {
            strokeCorrection?.invalidate("光标已移动，未继续关联手写词")
            clearStroke(ending: strokeSessionID.isEmpty)
            if session?.active == true || undoHistory.enabled || adapter?.clientOperationPending == true {
                invalidate("鼠标操作已接管本句")
            }
            return false
        }
        guard event.type == .keyDown else { return false }
        if !event.isARepeat, let compatibility = adapter?.pendingCompatibility,
           compatibility.acceptsKey(code: Int(event.keyCode), command: event.modifierFlags.contains(.command),
                                    shift: event.modifierFlags.contains(.shift),
                                    otherModifiers: !event.modifierFlags.intersection([.control, .option]).isEmpty,
                                    tag: event.cgEvent?.getIntegerValueField(.eventSourceUserData) ?? 0) { return false }
        if strokeActive, strokeCode.isEmpty, event.keyCode == 6,
           event.modifierFlags.intersection([.command, .shift, .control, .option]) == [.command],
           strokeCorrection?.canUndo == true {
            isWriting = true
            strokeCorrection?.undoCorrection()
            isWriting = false
            return true
        }
        if strokeActive, strokeCode.isEmpty, event.keyCode == 51,
           event.modifierFlags.intersection([.command, .shift, .control, .option]).isEmpty {
            strokeCorrection?.backspace()
        } else { strokeCorrection?.invalidate("键盘输入已接管，未继续关联手写词") }
        clearStroke(ending: strokeSessionID.isEmpty)
        guard let session else { return false }
        if matches(IMEService.shared.cancelShortcut, event: event),
           session.active || session.canSystemUndo || (session.phase == .undone && undoHistory.count > 0) {
            cancelCurrentOrHistory()
            return true
        }
        if session.phase == .error {
            lastLifecycleEvent = "keyboard_input"
            invalidate("键盘输入已接管本句")
            return false
        }
        guard session.active else {
            if session.systemUndoTrial { invalidate("键盘输入已接管本句") }
            else { undoHistory.clear(); sendState() }
            return false
        }
        if (event.keyCode == 36 || event.keyCode == 76), [.listening, .finishing].contains(session.phase) {
            perform { $0.finish() }
            return true
        }
        lastLifecycleEvent = "keyboard_input"
        invalidate("键盘输入已接管本句")
        // This component intentionally has no Pinyin engine. Ordinary keys go to the client.
        return false
    }

    override func commitComposition(_ sender: Any!) {
        if adapter?.clipboardReadInProgress == true { return }
        if strokeActive {
            if !isWriting { clearStroke() }
            return
        }
        guard activated, ownsActivation, !isWriting, let session else { return }
        lastLifecycleEvent = session.active && session.hasComposition ? "commit_composition" : "commit_composition_ignored"
        isWriting = true
        let interrupted = session.commitCompositionRequested()
        isWriting = false
        if interrupted { panel.hide() }
        else { sendState() }
    }

    func invalidate(_ reason: String, preservingUndo: Bool = false) {
        reportSuggestionWrite(interrupted: true)
        systemUndoKey = nil
        earlySystemUndo = nil
        undoCaretKey = nil
        session?.retireSystemUndo()
        resumedInputToken = nil
        preservingHistory = preservingUndo
        defer { preservingHistory = false }
        if !preservingUndo { pendingBeginID = nil; undoHistory.clear(); adapter?.discardContextHint() }
        compatibilityKeyReply = nil
        adapter?.cancelClientOperation()
        guard let session, session.active || session.phase == .error else {
            panel.hide()
            if activated { sendState() }
            return
        }
        let wasWriting = isWriting
        isWriting = true
        defer { isWriting = wasWriting }
        session.interrupt(reason, commitIfOwned: session.active)
        panel.hide()
    }

    func hostDisconnected() {
        pendingBeginID = nil
        IMEService.shared.suspendedInput = nil
        resumedInputToken = nil
        clearStroke()
        undoHistory.clear()
        rejectedBeginID = nil
        lastLifecycleEvent = "host_disconnected"
        let wasWriting = isWriting
        isWriting = true
        defer { isWriting = wasWriting }
        sessionLifetime.clear("主程序连接已断开，已保留当前文字")
        lastContext = nil
        panel.hide()
        lastError = "主程序未连接"
    }

    func hostConnected() {
        pendingBeginID = nil
        IMEService.shared.suspendedInput = nil
        resumedInputToken = nil
        clearStroke()
        undoHistory.clear()
        rejectedBeginID = nil
        lastLifecycleEvent = "host_connected"
        let wasWriting = isWriting
        isWriting = true
        defer { isWriting = wasWriting }
        sessionLifetime.clear("输入连接已重新建立")
        lastContext = nil
        lastError = ""
        panel.hide()
    }

    private func matches(_ shortcut: String, event: NSEvent) -> Bool {
        let pieces = shortcut.lowercased().replacingOccurrences(of: " ", with: "").split(separator: "+").map(String.init)
        guard let key = pieces.last, !key.isEmpty else { return false }
        var required: NSEvent.ModifierFlags = []
        for modifier in pieces.dropLast() {
            switch modifier {
            case "cmd", "command", "meta", "⌘": required.insert(.command)
            case "ctrl", "control", "⌃": required.insert(.control)
            case "alt", "option", "opt", "⌥": required.insert(.option)
            case "shift", "⇧": required.insert(.shift)
            default: return false
            }
        }
        let meaningful: NSEvent.ModifierFlags = [.command, .control, .option, .shift]
        guard event.modifierFlags.intersection(meaningful) == required else { return false }
        let codes: [String: UInt16] = ["esc": 53, "escape": 53, "f1": 122, "f2": 120, "f3": 99, "f4": 118,
                                      "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
                                      "f11": 103, "f12": 111, "return": 36, "enter": 36, "space": 49]
        if let code = codes[key] { return event.keyCode == code }
        return event.charactersIgnoringModifiers?.lowercased() == key
    }

    private var clipboardMessages: [[String: Any]] = []
    private func drainClipboardMessages() {
        guard adapter?.clipboardReadInProgress != true else { return }
        let messages = clipboardMessages; clipboardMessages = []
        for message in messages { receive(message) }
    }

    func receive(_ message: [String: Any]) {
        if adapter?.clipboardReadInProgress == true {
            clipboardMessages.append(message)
            return
        }
        guard activated, ownsActivation, !preparingActivation, let adapter, let type = message["type"] as? String else { return }
        if type == "reset", message["all"] as? Bool == true {
            IMEService.shared.suspendedInput = nil
            clearStroke()
            lastLifecycleEvent = "reset"
            invalidate("主程序已结束本句")
            sendState(includeSnapshot: true)
            return
        }
        if type.hasPrefix("stroke_"), message["client_id"] as? String == currentClientID {
            receiveStroke(message, adapter: adapter, type: type)
            return
        }
        guard message["client_id"] as? String == currentClientID,
              let utteranceID = message["utterance_id"] as? String,
              let sequence = message["seq"] as? Int else { return }
        if type == "resume_input" {
            resumeInput(message, adapter: adapter, utteranceID: utteranceID, sequence: sequence)
            return
        }
        if type == "begin" {
            if strokeActive { return }
            let requestedClientID = currentClientID
            if session?.utteranceID == utteranceID || pendingBeginID == utteranceID { return }
            let service = IMEService.shared
            let token = message["undo_history_token"] as? String
            if service.suspendedInput?.token != token { service.suspendedInput = nil }
            pendingBeginID = utteranceID
            defer {
                if pendingBeginID == utteranceID { pendingBeginID = nil }
                if service.suspendedInput?.token == token { service.suspendedInput = nil }
            }
            func ownsBegin() -> Bool {
                pendingBeginID == utteranceID && activated && ownsActivation
                    && currentClientID == requestedClientID && self.adapter === adapter
            }
            if let session, session.phase == .error, session.hasComposition || session.awaitingReadback {
                // A queued tap may predate ready=false. Keep the old ownership
                // proof until recovery completes, but retire the new request.
                rejectedBeginID = utteranceID
                sendState(includeSnapshot: true)
                IMEService.shared.send(["type": "interrupted", "client_id": currentClientID,
                                        "utterance_id": utteranceID,
                                        "reason": "上一句输入组合尚未结束，请稍后重试"])
                return
            }
            rejectedBeginID = nil
            invalidate("开始了新一句", preservingUndo: true)
            guard ownsBegin() else { return }
            lastLifecycleEvent = "begin"
            traceLifecycle("begin_snapshot")
            guard ownsBegin() else { return }
            do {
                let next = try CompositionSession(client: adapter, utteranceID: utteranceID, sequence: sequence)
                next.systemUndoTrial = message["system_undo_trial"] as? Bool == true
                if next.systemUndoTrial, message["host_posts_undo"] as? Bool == true {
                    next.hostUndoTag = Int64.random(in: 1..<(1 << 50))
                }
                next.automaticMode = message["automatic_mode"] as? Bool ?? false
                next.routedEditMode = !next.automaticMode && (message["routed_edit_mode"] as? Bool ?? false)
                guard ownsBegin() else { return }
                if !next.systemUndoTrial, session == nil, undoHistory.enabled, let saved = service.suspendedInput,
                   saved.token == token, saved.history != nil, saved.application == activationApplication,
                   saved.client === adapter.client, saved.session.utteranceID != utteranceID {
                    // A new sentence owns its own BEGIN snapshot. Only the undo stack crosses the handoff.
                    let verified = (try? saved.session.verify(next.original, with: adapter)) != nil
                    guard ownsBegin() else { return }
                    if verified, service.suspendedInput?.token == token, let history = service.suspendedInput?.history {
                        undoHistory.restore(history)
                    }
                }
                // Release the snapshot's shared array before BEGIN appends to potentially long history.
                if service.suspendedInput?.token == token { service.suspendedInput = nil }
                if next.systemUndoTrial { undoHistory.clear() } else { undoHistory.begin(next.original) }
                undoSourceUtteranceID = nil
                session = next
                lastContext = next.original
                lastError = ""
                wire(next)
                traceLifecycle("begin_ready")
                changed(includeSnapshot: true)
            } catch {
                guard ownsBegin() else { return }
                session = nil
                undoHistory.clear()
                lastError = error.localizedDescription
                sendState(includeSnapshot: true, failedUtteranceID: utteranceID)
                IMEService.shared.send(["type": "interrupted", "client_id": currentClientID,
                                        "utterance_id": utteranceID, "revision": 0,
                                        "lifecycle_event": lastLifecycleEvent, "has_composition": false,
                                        "reason": lastError, "error": lastError])
            }
            return
        }
        guard let session, session.utteranceID == utteranceID, session.accepts(sequence: sequence) else { return }
        // A real follow-up command confirms the host accepted the resume ACK.
        // A later timeout from that handshake must not cancel this new work.
        resumedInputToken = nil
        if type == "suspend_input" {
            suspendInput(message, session: session, adapter: adapter)
            return
        }
        if type == "end_input" {
            guard let outcome = message["outcome"] as? String, ["sent", "replaced"].contains(outcome),
                  !session.hasComposition, !session.awaitingReadback,
                  [.dictated, .edited, .undone, .interrupted, .error].contains(session.phase) else { return }
            let clientID = currentClientID
            let lease = activationLease
            // Detach before cleanup so old callbacks cannot publish UI or write
            // into the next answer. This never modifies the editor's contents.
            sessionLifetime.clear("主程序已结束本句")
            guard activated, IMEService.shared.activation.owns(self, lease),
                  currentClientID == clientID, self.adapter === adapter, self.session == nil else { return }
            undoHistory.clear()
            undoSourceUtteranceID = nil
            rejectedBeginID = nil
            compatibilityKeyReply = nil
            lastContext = nil
            lastError = ""
            lastLifecycleEvent = outcome
            adapter.discardContextHint()
            if outcome == "sent" {
                let caret = adapter.caret(compositionLength: 0, trace: false)
                let level = adapter.client.windowLevel()
                guard activated, IMEService.shared.activation.owns(self, lease),
                      currentClientID == clientID, self.adapter === adapter, self.session == nil else { return }
                panel.showSent()
                panel.position(caret: caret, clientLevel: level)
            } else { panel.hide() }
            sendState(includeSnapshot: true)
            return
        }
        if type == "system_undo_posted" {
            guard let tag = session.hostUndoTag, message["event_tag"] as? Int64 == tag else { return }
            let failure = (message["error"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            systemUndoKey = failure == nil && earlySystemUndo == nil
                ? (tag, ProcessInfo.processInfo.systemUptime + 0.5) : nil
            earlySystemUndo = nil
            session.systemUndo { $0(failure) }
            return
        }
        if type == "cancel" {
            cancelCurrentOrHistory()
            return
        }
        if type == "redo" {
            redoHistory()
            return
        }
        if type == "retry_edit" {
            if IMEService.shared.historyMode == .retry { perform { $0.retryEdit() } }
            return
        }
        if type == "wechat_key_result" {
            guard let reply = compatibilityKeyReply, message["request_id"] as? String == reply.id else { return }
            compatibilityKeyReply = nil
            let failure = (message["error"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            if session.systemUndoPending, let key = systemUndoKey {
                systemUndoKey = failure == nil ? (key.tag, ProcessInfo.processInfo.systemUptime + 0.5) : nil
            }
            reply.callback(failure)
            return
        }
        perform { session in
            switch type {
            case "automatic_progress":
                if let stage = message["stage"] as? String, let tasks = message["tasks"] as? [String] {
                    session.setAutomaticProgress(stage, tasks: tasks)
                }
            case "update":
                if let error = message["error"] as? String, !error.isEmpty { session.failASR(error) }
                else if let text = message["text"] as? String {
                    if (text as NSString).length > 65_536 { session.failASR("本句超过长度限制，已保留当前听写") }
                    else {
                        if message["final"] as? Bool == true, let task = message["automatic_task"] as? String {
                            session.setAutomaticTask(task)
                        }
                        session.update(text, final: message["final"] as? Bool ?? false)
                    }
                }
            case "convert": session.convert()
            case "finish": session.finish()
            case "finish_dictation": session.finishAsDictation()
            case "accept_dictation_preview":
                let service = IMEService.shared
                guard let token = message["token"] as? String else { return }
                guard service.automaticPolish, let offer = service.suggestion, token == offer.token,
                      offer.isVisible, self.ownsSuggestion(offer), offer.stillValid() else {
                    service.clearSuggestion(token: token, reason: "target_changed")
                    service.send(["type": "dictation_preview_state", "token": token, "visible": false, "reason": "write_unconfirmed"])
                    return
                }
                let text = offer.text
                self.applyingSuggestion = (token, session, text)
                service.clearSuggestion(token: token, reason: "applying")
                session.acceptSuggestion(text, originalText: offer.raw, revision: offer.revision)
                self.reportSuggestionWrite()
            case "polish":
                if !IMEService.shared.automaticPolish {
                    session.polish(restoringRawOnUndo: message["restore_raw_on_undo"] as? Bool ?? true)
                }
            case "polish_result":
                if let revision = message["revision"] as? Int {
                    let result = message["text"] as? String ?? ""
                    let error = (result as NSString).length > 512 * 1024 ? "润色结果超过长度限制，已保留听写" : message["error"] as? String
                    session.applyPolish(text: result, revision: revision, error: error)
                }
            case "edit_result":
                if let revision = message["revision"] as? Int {
                    let result = message["text"] as? String ?? ""
                    let error = (result as NSString).length > 512 * 1024 ? "编辑结果超过长度限制，已保留听写" : message["error"] as? String
                    session.applyEdit(text: result, revision: revision, error: error)
                }
            case "reset":
                undoHistory.clear()
                lastLifecycleEvent = "reset"
                session.interrupt("主程序已结束本句")
            default: break
            }
        }
    }

    private func suspensionReply(_ type: String, token: String, utteranceID: String,
                                 revision: Int, success: Bool, error: String = "") -> [String: Any] {
        ["type": type, "token": token, "client_id": currentClientID,
         "utterance_id": utteranceID, "revision": revision, "success": success,
         "error": error, "reason": error]
    }

    func discardResumedInput(token: String) {
        guard !token.isEmpty, resumedInputToken == token, let previous = session else { return }
        // A host timeout can arrive after resume consumed its saved snapshot.
        // Detach first so cleanup cannot emit callbacks or commit old text.
        session = nil
        previous.interrupt("输入恢复已取消", commitIfOwned: false)
        undoHistory.clear()
        undoSourceUtteranceID = nil
        compatibilityKeyReply = nil
        lastContext = nil
        lastError = ""
        lastLifecycleEvent = "resume_discarded"
        panel.hide()
        sendState(includeSnapshot: true)
    }

    private func suspendInput(_ message: [String: Any], session current: CompositionSession, adapter: NativeInputClient) {
        let token = message["token"] as? String ?? ""
        let lease = activationLease
        let clientID = currentClientID
        let revision = current.revision
        func owns() -> Bool {
            activated && IMEService.shared.activation.owns(self, lease) && currentClientID == clientID
                && self.adapter === adapter && session === current && current.revision == revision
        }
        do {
            guard !token.isEmpty, token.count <= 128, message["revision"] as? Int == revision,
                  !isWriting, !strokeActive else {
                throw CompositionError.unsupported("输入会话已变化，未切回输入法")
            }
            isWriting = true
            defer { isWriting = false }
            let restorable = current.phase != .undone || undoHistory.count > 0 || undoHistory.redoCount > 0
            if restorable {
                let saved = try current.suspension()
                guard owns() else { throw CompositionError.interrupted("输入会话已变化，未切回输入法") }
                IMEService.shared.suspendedInput = IMEService.SuspendedInput(token: token,
                    application: activationApplication, client: adapter.client, session: saved,
                    history: undoHistory.snapshot(), undoSourceUtteranceID: undoSourceUtteranceID)
            } else {
                guard !current.hasComposition, !current.awaitingReadback, !adapter.clientOperationPending else {
                    throw CompositionError.unsupported("本句尚未完成，暂不能切回输入法")
                }
                // No operation remains after the final undo. Returning to the
                // keyboard does not need to grant a new text-write capability.
                IMEService.shared.suspendedInput = nil
            }
            // Detach without interruption; all retained values are immutable.
            session = nil
            undoHistory.clear()
            undoSourceUtteranceID = nil
            panel.hide()
            lastLifecycleEvent = "suspended"
            IMEService.shared.send(suspensionReply("input_suspended", token: token,
                utteranceID: current.utteranceID, revision: revision, success: true)
                .merging(["restorable": restorable]) { _, value in value })
            sendState(includeSnapshot: true)
        } catch {
            IMEService.shared.send(suspensionReply("input_suspended", token: token,
                utteranceID: current.utteranceID, revision: revision, success: false, error: error.localizedDescription))
        }
    }

    private func resumeInput(_ message: [String: Any], adapter: NativeInputClient,
                             utteranceID: String, sequence: Int) {
        let service = IMEService.shared
        let token = message["token"] as? String ?? ""
        let lease = activationLease
        let clientID = currentClientID
        let saved = service.suspendedInput
        func owns() -> Bool {
            activated && service.activation.owns(self, lease) && currentClientID == clientID
                && self.adapter === adapter && session == nil && service.suspendedInput?.token == token
        }
        do {
            guard !token.isEmpty, let saved, saved.token == token,
                  saved.session.utteranceID == utteranceID, !isWriting, !strokeActive,
                  session == nil, saved.application == activationApplication else {
                throw CompositionError.interrupted("原输入会话已失效，未恢复语音操作")
            }
            isWriting = true
            defer { isWriting = false }
            guard owns(), saved.client === adapter.client else {
                throw CompositionError.interrupted("已切换输入框，未恢复语音操作")
            }
            let next = try CompositionSession(client: adapter, resuming: saved.session, sequence: sequence)
            guard owns() else { throw CompositionError.interrupted("输入会话已变化，未恢复语音操作") }
            // A settings change during the IMK reads may have revoked the suspended history.
            if let history = service.suspendedInput?.history { undoHistory.restore(history) }
            service.suspendedInput = nil
            session = next
            resumedInputToken = token
            undoSourceUtteranceID = saved.undoSourceUtteranceID
            lastContext = next.original
            rejectedBeginID = nil
            lastError = ""
            lastLifecycleEvent = "resumed"
            wire(next)
            guard sendState(includeSnapshot: true, reply: suspensionReply("input_resumed", token: token,
                utteranceID: utteranceID, revision: next.revision, success: true)) else {
                throw CompositionError.interrupted("输入会话已变化，未恢复语音操作")
            }
            configureGestureHints()
            panel.update(next, historyDepth: undoHistory.count, redoAvailable: undoHistory.canRedo && next.historyReady,
                         redoEnabled: undoHistory.redoEnabled, retryAvailable: next.canRetryEdit,
                         historyActionMode: IMEService.shared.historyMode.rawValue, manualPolish: !IMEService.shared.automaticPolish)
        } catch {
            if service.suspendedInput?.token == token { service.suspendedInput = nil }
            IMEService.shared.send(suspensionReply("input_resumed", token: token,
                utteranceID: utteranceID, revision: saved?.session.revision ?? 0,
                success: false, error: error.localizedDescription))
        }
    }

    private func clearStroke(notify: Bool = true, ending: Bool = true) {
        guard strokeActive || !strokeCode.isEmpty else { return }
        let clientID = currentClientID
        let request = strokeSessionID
        let hadCode = !strokeCode.isEmpty
        strokeCode = ""
        strokeCandidates = []
        if ending {
            strokeActive = false
            strokeSessionID = ""
            strokeCorrection?.invalidate("")
            strokeCorrection = nil
            strokeCorrectionSeed = nil
            strokePanel?.hide()
        } else {
            strokePanel?.update(code: "", candidates: [], selected: 0)
            strokePanel?.trace(points: [], finished: false, style: [:])
        }
        if hadCode && activated && ownsActivation {
            let wasWriting = isWriting
            isWriting = true
            adapter?.mark("")
            isWriting = wasWriting
        }
        if notify {
            IMEService.shared.send(["type": ending ? "stroke_cleared" : "stroke_ink_cleared",
                                    "client_id": clientID, "stroke_session": request])
        }
    }

    private func receiveStroke(_ message: [String: Any], adapter: NativeInputClient, type: String) {
        let clientID = currentClientID
        let token = message["stroke_session"] as? String ?? ""
        if type != "stroke_begin", token != strokeSessionID { return }
        if type == "stroke_end" { clearStroke(notify: false); return }
        if type == "stroke_clear" { clearStroke(notify: false, ending: token.isEmpty); return }
        if type == "stroke_correction_configure" {
            configureStrokeCorrection(message["enabled"] as? Bool == true, adapter: adapter)
            return
        }
        if type == "stroke_correction_result" {
            isWriting = true
            strokeCorrection?.apply(id: message["request_id"] as? String ?? "",
                fragment: message["original_text"] as? String ?? "", error: message["error"] as? String ?? "")
            isWriting = false
            return
        }
        if type == "stroke_correction_key_result" {
            guard let reply = compatibilityKeyReply, strokeCorrection?.busy == true,
                  message["request_id"] as? String == reply.id else { return }
            compatibilityKeyReply = nil
            reply.callback((message["error"] as? String).flatMap { $0.isEmpty ? nil : $0 })
            return
        }
        if strokeCorrection?.busy == true { return }
        guard !isWriting, !adapter.clientOperationPending,
              session?.hasComposition != true, session?.awaitingReadback != true else { return }
        if let session, [.listening, .finishing, .editing, .polishing].contains(session.phase) { return }
        switch type {
        case "stroke_begin":
            guard !token.isEmpty, !strokeActive || token == strokeSessionID else { return }
            let context = try? adapter.snapshotForBeginning()
            guard activated, ownsActivation, currentClientID == clientID, self.adapter === adapter else { return }
            guard let context, isValidRange(context.selection) else {
                IMEService.shared.send(["type": "stroke_ready", "client_id": clientID, "stroke_session": token,
                                        "success": false, "error": "请先点入可编辑的文本框"])
                return
            }
            // Only completed voice bookkeeping is retired. No voice BEGIN,
            // finish, cancellation, or input-source restoration happens here.
            session = nil
            undoHistory.clear()
            lastContext = context
            panel.hide()
            strokeActive = true
            strokeSessionID = token
            strokeCorrectionSeed = StrokeTextCorrection.precedingText(in: context)
            if strokePanel == nil { strokePanel = StrokeCandidatePanel() }
            strokePanel?.exitGestureLabel = String((message["gesture_label"] as? String ?? "双指双击").prefix(20))
            strokePanel?.tapHint = String((message["tap_hint"] as? String ?? "单指空格 · 双指首选").prefix(40))
            strokePanel?.update(code: "", candidates: [], selected: 0)
            configureStrokeCorrection(message["correction_enabled"] as? Bool == true, adapter: adapter)
            tick()
            guard activated, ownsActivation, currentClientID == clientID, strokeSessionID == token else { return }
            guard strokePanel?.isVisible == true else {
                clearStroke(notify: false)
                IMEService.shared.send(["type": "stroke_ready", "client_id": clientID, "stroke_session": token,
                                        "success": false, "panel_visible": false,
                                        "error": "无法定位笔画浮窗，已回到鼠标模式"])
                return
            }
            let prefix = adapter.strokeContext()
            guard activated, ownsActivation, currentClientID == clientID, self.adapter === adapter else { return }
            IMEService.shared.send(["type": "stroke_ready", "client_id": clientID, "stroke_session": token,
                                    "success": true, "panel_visible": true, "context": prefix ?? ""])
            sendState()
        case "stroke_trace":
            guard strokeActive else { return }
            strokePanel?.trace(points: message["points"] as? [[Double]] ?? [],
                               finished: message["finished"] as? Bool ?? false,
                               style: message["style"] as? [String: Any] ?? [:])
        case "stroke_gestures":
            guard strokeActive else { return }
            strokePanel?.tapHint = String((message["tap_hint"] as? String ?? "").prefix(40))
        case "stroke_update":
            let code = String((message["code"] as? String ?? "").prefix(64))
            let candidates = Array((message["candidates"] as? [String] ?? []).prefix(6))
            let prediction = code.isEmpty && message["prediction"] as? Bool == true
                && strokeActive && !token.isEmpty && !candidates.isEmpty
            if code.isEmpty && !prediction { clearStroke(notify: false, ending: token.isEmpty); return }
            // A completed voice sentence can yield ownership without rewriting it.
            if strokeCode.isEmpty, session?.active == true {
                invalidate("笔画输入已接管")
                guard activated, ownsActivation, currentClientID == clientID, self.adapter === adapter else { return }
            }
            if code != strokeCode {
                isWriting = true
                strokeActive = true
                strokeCode = code
                adapter.mark(code)
                isWriting = false
                guard activated, ownsActivation, currentClientID == clientID, self.adapter === adapter else { return }
            }
            strokeCandidates = candidates
            if strokePanel == nil { strokePanel = StrokeCandidatePanel() }
            strokePanel?.update(code: code, candidates: strokeCandidates, selected: message["selected"] as? Int ?? 0,
                                glyphs: Array((message["glyphs"] as? [[String: String]] ?? []).prefix(6)))
            tick()
        case "stroke_selection":
            guard strokeActive else { return }
            strokePanel?.selection(phase: message["phase"] as? String ?? "idle",
                                   progress: message["progress"] as? Double ?? 0,
                                   direction: message["direction"] as? String ?? "",
                                   origin: message["origin"] as? [Double] ?? [],
                                   points: message["points"] as? [[Double]] ?? [],
                                   selectDistance: message["select_distance"] as? Double ?? 3,
                                   clearArmed: message["clear_armed"] as? Bool ?? false,
                                   clearDistance: message["clear_distance"] as? Double ?? 30,
                                   clearProgress: message["clear_progress"] as? Double ?? 0,
                                   correctionArmed: message["correction_armed"] as? Bool ?? false,
                                   correctionProgress: message["correction_progress"] as? Double ?? 0)
        case "stroke_correction_undo":
            guard strokeActive, strokeCode.isEmpty, strokeCorrection?.canUndo == true else { return }
            isWriting = true
            strokeCorrection?.undoCorrection()
            isWriting = false
        case "stroke_correction_request":
            guard strokeCode.isEmpty else {
                publishStrokeCorrection(message: "请先确认正在书写的字"); return
            }
            if let correction = strokeCorrection {
                correction.request(message["request_id"] as? String ?? "")
            } else { publishStrokeCorrection(message: "请将光标放在前文之后，再进入手写") }
        case "stroke_commit":
            let request = message["request_id"] as? String ?? ""
            let value = message["text"] as? String ?? ""
            // Commit the first candidate and space together so focus cannot split them.
            let candidateWithSpace = strokeCandidates.first.map { value == $0 + " " } ?? false
            let accepted = !request.isEmpty
                && (candidateWithSpace || (value.count == 1
                    && (strokeCandidates.contains(value) || ["，", "。", "？", " "].contains(value))))
                && (!strokeCode.isEmpty || (strokeActive && !token.isEmpty))
            var prefix: String?
            if accepted {
                let probe = adapter.probe()
                let insertion = isValidRange(probe.markedRange) && probe.markedRange.length > 0
                    ? probe.markedRange : probe.selection
                guard activated, ownsActivation, currentClientID == clientID, self.adapter === adapter else { return }
                isWriting = true
                adapter.commit(value)
                isWriting = false
                guard activated, ownsActivation, currentClientID == clientID, self.adapter === adapter else { return }
                strokeCode = ""
                strokeCandidates = []
                if strokeSessionID.isEmpty { strokeActive = false; strokePanel?.hide() }
                else { strokePanel?.update(code: "", candidates: [], selected: 0) }
                strokePanel?.trace(points: [], finished: false, style: [:])
                prefix = adapter.strokeContext()
                guard activated, ownsActivation, currentClientID == clientID, self.adapter === adapter else { return }
                strokeCorrection?.recordCommit(value, word: candidateWithSpace ? String(value.dropLast()) : value,
                                               replacing: insertion)
            }
            var result: [String: Any] = ["type": "stroke_result", "client_id": clientID,
                                    "request_id": request, "stroke_session": token, "success": accepted,
                                    "error": accepted ? "" : "笔画组合已失效，请重新书写"]
            if let prefix { result["context"] = prefix }
            IMEService.shared.send(result)
        default: break
        }
    }

    private func configureStrokeCorrection(_ enabled: Bool, adapter: NativeInputClient) {
        strokeCorrection?.invalidate("")
        strokeCorrection = nil
        strokeCorrectionEnabled = enabled
        if enabled, let preceding = strokeCorrectionSeed {
            let clientID = currentClientID, token = strokeSessionID
            let correction = StrokeTextCorrection(preceding: preceding, client: adapter, owns: { [weak self, weak adapter] in
                guard let self, let adapter else { return false }
                return self.activated && self.ownsActivation && self.adapter === adapter
                    && self.currentClientID == clientID && self.strokeSessionID == token && self.strokeActive
            })
            guard activated, ownsActivation, currentClientID == clientID, strokeSessionID == token,
                  self.adapter === adapter else { return }
            strokeCorrection = correction
            correction.onChanged = { [weak self, weak correction] in
                guard let self, self.strokeCorrection === correction else { return }
                self.publishStrokeCorrection()
            }
            correction.onRequest = { [weak self, weak correction] id, original, word in
                guard let self, self.strokeCorrection === correction, self.ownsActivation else { return }
                IMEService.shared.send(["type": "stroke_correction_requested", "client_id": clientID,
                    "stroke_session": token, "request_id": id, "original": original, "handwriting": word])
            }
        }
        publishStrokeCorrection()
    }

    private func publishStrokeCorrection(message: String? = nil) {
        guard activated, ownsActivation, strokeActive else { return }
        let correction = strokeCorrection
        let word = correction?.word ?? ""
        let hint = message ?? correction?.message ?? (strokeCorrectionEnabled ? "请将光标放在前文之后，再进入手写" : "")
        let range = correction?.handwritingRange
        strokePanel?.correction(enabled: strokeCorrectionEnabled, text: word, message: hint,
                                busy: correction?.busy == true, canUndo: correction?.canUndo == true)
        IMEService.shared.send(["type": "stroke_correction_state", "client_id": currentClientID,
            "stroke_session": strokeSessionID, "text": word, "message": hint,
            "range": range.map { [$0.location, $0.length] } ?? [],
            "busy": correction?.busy == true,
            "available": correction?.valid == true && correction?.preceding.text.isEmpty == false,
            "can_undo": correction?.canUndo == true])
    }

    private func wire(_ next: CompositionSession) {
        adapter?.pendingCompatibility?.systemUndoTrial = next.systemUndoTrial
        let isCurrent = { [weak self, weak next] in
            guard let self, let next else { return false }
            return self.activated && self.ownsActivation && self.session === next
        }
        next.onChanged = { [weak self] in
            if isCurrent() { self?.reportSuggestionWrite(); self?.changed() }
        }
        next.onFinishAudio = { [weak self] in if isCurrent() { self?.sendEvent("finish_audio") } }
        next.onInterrupted = { [weak self] reason in
            if isCurrent() {
                if self?.preservingHistory != true { self?.undoHistory.clear() }
                self?.sendEvent("interrupted", extra: ["reason": reason])
            }
        }
        next.onSettled = { [weak self] phase, text in
            if isCurrent(), let self, let session = self.session {
                if !session.systemUndoTrial {
                    self.undoHistory.settled(session, sourceUtteranceID: self.undoSourceUtteranceID)
                }
                guard isCurrent() else { return }
                self.sendEvent("settled", extra: ["phase": phase, "text": text])
            }
        }
        next.onEdit = { [weak self] original, instruction, revision in
            guard isCurrent() else { return }
            self?.sendEvent("edit_requested", extra: ["original": original, "instruction": instruction,
                "revision": revision, "retry_edit": self?.session?.retryingEdit ?? false])
        }
        next.onDictationCommitted = { [weak self, weak next] in
            guard isCurrent(), let self, let next, IMEService.shared.automaticPolish,
                  let adapter = self.adapter else { return }
            let caret = next.canSuggestReplacement ? adapter.caret(compositionLength: 0) : nil
            guard isCurrent() else { return }
            if let token = IMEService.shared.beginSuggestion(next, adapter: adapter, caret: caret) {
                self.sendEvent("dictation_preview_requested", extra: ["token": token, "before_cursor": next.beforeDictationCursor])
            } else {
                self.sendEvent("dictation_preview_requested", extra: ["token": UUID().uuidString,
                    "before_cursor": next.beforeDictationCursor, "blocked": next.canSuggestReplacement ? "caret_unavailable" : "range_unverified"])
            }
        }
        next.onPolish = { [weak self] text, revision in
            guard isCurrent() else { return }
            self?.sendEvent("polish_requested", extra: ["text": text, "revision": revision])
        }
    }

    private func cancelCurrentOrHistory() {
        guard activated, ownsActivation, !isWriting, let current = session, let adapter else { return }
        if current.systemUndoTrial {
            if [.dictated, .edited, .undone].contains(current.phase) {
                guard current.canSystemUndo else { return }
                IMEService.shared.clearSuggestion(reason: "undo")
                let id = UUID().uuidString
                let tag = Int64.random(in: 1..<(1 << 50))
                current.systemUndo { reply in
                    compatibilityKeyReply = (id, reply)
                    systemUndoKey = (tag, ProcessInfo.processInfo.systemUptime + 1.5)
                    sendEvent("wechat_key", extra: ["request_id": id, "command": "system_undo", "count": 0,
                                                    "event_tag": tag, "application": activationApplication])
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { [weak self] in
                        guard let self, let pending = self.compatibilityKeyReply, pending.id == id else { return }
                        self.compatibilityKeyReply = nil
                        pending.callback("系统撤销未返回发送回执")
                    }
                }
            } else { perform { $0.cancel() } }
            return
        }
        if current.phase == .undone, let record = undoHistory.pop() {
            let restored = CompositionSession(client: adapter, undo: record,
                                              utteranceID: current.utteranceID,
                                              sequence: current.lastSequence, revision: current.revision + 1)
            undoSourceUtteranceID = record.sourceUtteranceID
            session = restored
            lastContext = restored.original
            wire(restored)
            // Publish this transaction phase before an asynchronous native key
            // request, so the host never posts keys for an inactive old phase.
            sendState()
        }
        perform { self.undoHistory.undo($0, sourceUtteranceID: self.undoSourceUtteranceID) }
    }

    private func redoHistory() {
        guard activated, ownsActivation, !isWriting, let current = session, let adapter,
              let step = undoHistory.prepareRedo(current, sourceUtteranceID: undoSourceUtteranceID) else { return }
        let restored = CompositionSession(client: adapter, undo: step.result,
            utteranceID: current.utteranceID, sequence: current.lastSequence, revision: current.revision + 1)
        undoSourceUtteranceID = step.result.sourceUtteranceID
        session = restored
        lastContext = restored.original
        wire(restored)
        perform { $0.restoreRedo(step) }
    }

    private func historyForward() {
        if IMEService.shared.historyMode == .retry { perform { $0.retryEdit() } }
        else if IMEService.shared.historyMode == .next { redoHistory() }
    }

    private func perform(_ operation: (CompositionSession) -> Void) {
        guard let session, activated, ownsActivation, !isWriting else { return }
        isWriting = true
        operation(session)
        isWriting = false
        tick()
    }

    private func sendEvent(_ type: String, extra: [String: Any] = [:]) {
        guard activated, ownsActivation, let session else { return }
        let lease = activationLease
        sendState()
        guard IMEService.shared.activation.owns(self, lease), self.session === session else { return }
        var event = extra
        event["type"] = type
        event["client_id"] = currentClientID
        event["utterance_id"] = session.utteranceID
        event["revision"] = session.revision
        event["raw"] = session.raw
        event["error"] = session.error
        event["lifecycle_event"] = lastLifecycleEvent
        event["has_composition"] = session.hasComposition
        event["edit_diagnostics"] = session.editDiagnostics
        event["readback_diagnostics"] = session.readbackDiagnostics
        event["edit_context_available"] = session.editContextAvailable
        event["edit_scope"] = session.editScope
        event["source_utterance_id"] = undoSourceUtteranceID ?? session.utteranceID
        IMEService.shared.send(event)
    }

    @discardableResult
    func sendState(includeSnapshot: Bool = false, requestID: String? = nil, failedUtteranceID: String? = nil,
                   reply: [String: Any]? = nil, metadataOnly: Bool = false) -> Bool {
        guard activated, ownsActivation, !preparingActivation, !stateReadInProgress, let adapter,
              !adapter.clipboardReadInProgress else { return false }
        // IMK reads can pump the run loop; coalesce nested host pings instead
        // of recursively reading the same document on a half-built snapshot.
        stateReadInProgress = true
        defer { stateReadInProgress = false }
        let stateClientID = currentClientID
        let replySession = session
        let context: EditorSnapshot?
        if metadataOnly, requestID == nil, let session, session.active { context = session.original }
        else if metadataOnly { context = try? adapter.focusSnapshot() }
        else if strokeActive, includeSnapshot, requestID != nil {
            // A Backspace focus probe needs the current selection, not the entry snapshot.
            context = EditorSnapshot(text: nil, selection: adapter.startingSelection(), markedRange: unspecifiedRange,
                                     selectedText: nil, documentAccess: false)
        } else if strokeActive { context = lastContext }
        else if let session, session.active, requestID == nil || [.listening, .finishing].contains(session.phase) { context = session.original }
        else if includeSnapshot { context = try? adapter.snapshotForBeginning() }
        else { context = lastContext }
        guard activated, ownsActivation, currentClientID == stateClientID, self.adapter === adapter,
              reply == nil || session === replySession else { return false }
        if includeSnapshot, requestID == nil { lastContext = context }
        let complete = context?.complete ?? false
        let access = context?.documentAccess ?? false
        // An activated client may support preedit/commit without exposing its
        // document selection. Range-dependent operations check their own proof.
        let targetError = context == nil ? "请先点入可编辑的文本框" : nil
        let recovering = session.map { $0.phase == .error && ($0.hasComposition || $0.awaitingReadback) } ?? false
        let ready = IMEService.shared.epoch != nil && targetError == nil && !recovering
        needsIdleTargetRefresh = !ready
        let capabilities: [String: Any] = ["marked_text": true, "context_complete": complete,
                                           "document_access": access,
                                           "document_verified": session?.documentVerified ?? (complete && access),
                                           "committed_range_verified": session?.committedRangeVerified ?? false,
                                           "range_replacement": session?.replacementVerified == true ? "verified" : (access && complete && (session?.documentVerified ?? true) ? "advertised" : "unavailable"),
                                           "selection_restore": false]
        let application = adapter.client.bundleIdentifier() ?? ""
        guard activated, ownsActivation, currentClientID == stateClientID, self.adapter === adapter,
              reply == nil || session === replySession else { return false }
        var state: [String: Any] = ["type": "state", "ready": ready,
                                    "context_key_supported": permitsContextKey,
                                    "client_id": currentClientID, "utterance_id": rejectedBeginID ?? session?.utteranceID ?? failedUtteranceID ?? "",
                                    "phase": session?.phase.rawValue ?? (failedUtteranceID == nil ? "idle" : "error"),
                                    "application": application,
                                    "revision": session?.revision ?? 0, "edit_requested": session?.editRequested ?? false,
                                    "can_polish": session?.canPolish ?? false, "polished": session?.polished ?? false,
                                    "polish_restore_available": session?.canRestorePolish ?? false,
                                    "raw": session?.raw ?? "", "error": session?.error ?? (targetError ?? lastError),
                                    "lifecycle_event": lastLifecycleEvent, "has_composition": session?.hasComposition ?? false,
                                    "awaiting_readback": session?.awaitingReadback ?? false, "readback_stage": session?.readbackStage ?? "",
                                    "readback_diagnostics": session?.readbackDiagnostics ?? [:],
                                    "edit_diagnostics": session?.editDiagnostics ?? [:],
                                    "edit_trace_version": 1,
                                    "native_pid": ProcessInfo.processInfo.processIdentifier,
                                    "native_build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unbundled",
                                    "client_read_diagnostics": adapter.lastReadDiagnostics,
                                    "capabilities": capabilities, "can_convert": session?.canEdit ?? false,
                                    "system_undo_trial": session?.systemUndoTrial ?? false,
                                    "host_undo_tag": session?.hostUndoTag ?? 0,
                                    "system_undo_pending": session?.systemUndoPending ?? false,
                                    "can_system_undo": session?.canSystemUndo ?? false,
                                    "can_cancel": (session?.canSystemUndo ?? false) || (session?.canCancel ?? false) || (session?.phase == .undone && undoHistory.count > 0),
                                    "undo_history_depth": undoHistory.count,
                                    "can_redo": undoHistory.canRedo && (session?.historyReady ?? false),
                                    "redo_history_depth": undoHistory.redoCount,
                                    "redo_version": 1,
                                    "redo_enabled": undoHistory.redoEnabled,
                                    "history_action_mode": IMEService.shared.historyMode.rawValue,
                                    "edit_retry_version": 1,
                                    "can_retry_edit": session?.canRetryEdit ?? false,
                                    "retry_source_utterance_id": undoSourceUtteranceID ?? session?.utteranceID ?? "",
                                    "multi_undo_enabled": undoHistory.enabled,
                                    "edit_context_available": session?.editContextAvailable ?? false,
                                    "edit_scope": session?.editScope ?? "unavailable",
                                    "automatic_mode": session?.automaticMode ?? false,
                                    "routed_edit_mode": session?.routedEditMode ?? false,
                                    "routed_edit_version": 1,
                                    "automatic_task": session?.automaticTask ?? "",
                                    "automatic_stage": session?.automaticStage ?? "",
                                    "automatic_tasks": session?.automaticTasks ?? [],
                                    "automatic_routing_version": 3,
                                    "edit_source_text": session?.editRequestText ?? "",
                                    "context_complete": complete]
        // A sentence snapshot is stable. Fresh idle snapshots only answer explicit refreshes.
        if includeSnapshot {
            state["original"] = context?.text ?? ""
            let selection = context?.selection ?? unspecifiedRange
            state["selection"] = isValidRange(selection) ? [selection.location, selection.length] : [-1, -1]
        }
        if let requestID { state["request_id"] = requestID }
        if let reply { IMEService.shared.send(reply.merging(["state": state]) { _, value in value }) }
        IMEService.shared.send(state)
        return true
    }

    private func changed(includeSnapshot: Bool = false) {
        guard activated, ownsActivation, let session else { return }
        if !preservingHistory, [.error, .interrupted].contains(session.phase) { undoHistory.clear() }
        sendState(includeSnapshot: includeSnapshot || session.phase == .error)
        guard activated, ownsActivation, self.session === session else { return }
        configureGestureHints()
        panel.update(session, historyDepth: undoHistory.count, redoAvailable: undoHistory.canRedo && session.historyReady,
                     redoEnabled: undoHistory.redoEnabled, retryAvailable: session.canRetryEdit,
                     historyActionMode: IMEService.shared.historyMode.rawValue, manualPolish: !IMEService.shared.automaticPolish)
        tick()
    }

    private var selectionPolls = 0
    private var needsIdleTargetRefresh = false
    private func tick() {
        guard activated, ownsActivation, let adapter, !adapter.clipboardReadInProgress else { return }
        let clientID = currentClientID
        if !isWriting, adapter.clientOperationPending {
            isWriting = true
            adapter.advanceClientOperation()
            isWriting = false
        }
        guard activated, ownsActivation, currentClientID == clientID, self.adapter === adapter else { return }
        selectionPolls += 1
        if strokeActive {
            if !isWriting {
                isWriting = true
                strokeCorrection?.advance()
                isWriting = false
            }
            guard activated, ownsActivation, currentClientID == clientID, self.adapter === adapter else { return }
            if strokePanel?.needsPosition == true {
                strokePanel?.position(caret: adapter.caret(compositionLength: (strokeCode as NSString).length, trace: false),
                                      clientLevel: adapter.client.windowLevel())
            }
            return
        }
        if !isWriting, needsIdleTargetRefresh, selectionPolls % 15 == 0,
           session?.hasComposition != true, session?.awaitingReadback != true {
            // Retry an unavailable selection without reading or modifying
            // composition left by another input source.
            if adapter.startingSelection() != lastContext?.selection {
                sendState(includeSnapshot: true)
            }
        }
        guard let session else { return }
        if !isWriting, let tag = session.hostUndoTag, let range = session.takeRestoredReplacementSelection(),
           activated, ownsActivation, currentClientID == clientID, self.session === session {
            let eventTag = Self.undoCaretMarker + tag
            let expires = ProcessInfo.processInfo.systemUptime + 0.25
            undoCaretKey = (eventTag, session.revision, range, expires)
            // B106 hosts already accept this selection-collapse message.
            IMEService.shared.send(["type": "preview_undo_caret", "client_id": clientID,
                "utterance_id": session.utteranceID, "revision": session.revision,
                "host_undo_tag": tag, "event_tag": eventTag, "expires": expires])
        }
        if !isWriting, session.awaitingReadback || (session.phase == .error && session.hasComposition && selectionPolls % 15 == 0) {
            isWriting = true
            session.advanceReadback()
            isWriting = false
        }
        if !isWriting, session.active, selectionPolls % 3 == 0 {
            isWriting = true
            session.checkSelection()
            isWriting = false
        }
        guard session.active || session.canSystemUndo || (session.phase == .undone && (undoHistory.count > 0 || undoHistory.redoCount > 0)) else { return }
        let trace = CaretDiagnostics.shared.shouldSample()
        let length = session.hasComposition ? (session.raw as NSString).length : 0
        if panel.needsPosition && IMEService.shared.suggestion?.isVisible != true {
            panel.position(caret: adapter.caret(compositionLength: length, trace: trace), clientLevel: adapter.client.windowLevel())
        }
        if trace {
            CaretDiagnostics.shared.record(["application": adapter.client.bundleIdentifier() ?? "",
                "phase": session.phase.rawValue, "characters": length, "edit_requested": session.editRequested,
                "geometry": adapter.caretDiagnostics, "panel": panel.positioningDiagnostics])
        }
    }
}
