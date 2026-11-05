import AppKit

private final class PassivePanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// Native IME palette. It never activates the input method or steals the client caret.
final class ActionPanel: NSObject {
    private let panel: NSPanel
    private let effect = InputPaletteSurface()
    private let status = NSTextField(labelWithString: "听写中")
    private let progress = VoiceHudProgress()
    private let statusSymbol = NSImageView()
    private let spinner = VoiceHudSpinner(frame: .zero)
    private let cancelButton = VoiceHudHint(action: "undo")
    private let convertButton = VoiceHudHint(action: "switch_mode")
    private let polishHint = VoiceHudHint(action: "polish")
    private let finishButton = VoiceHudHint(action: "end")
    private var actionHints: [VoiceHudHint] { [cancelButton, convertButton, polishHint, finishButton] }
    private var automatic = false
    private var finishAvailable = false
    private let stack = NSStackView()
    private let state = NSStackView()
    private let hints = NSStackView()
    private var hintWidthConstraint: NSLayoutConstraint!
    private var stateWidthConstraint: NSLayoutConstraint!
    private var scaledConstraints: [(NSLayoutConstraint, CGFloat)] = []
    private var appearance = VoiceHudAppearance()
    private var visualState = ""
    private var gestureHints = VoiceHudBindings()
    private var cancelAvailable = false
    private var editAvailable = false
    private var polishAvailable = false
    private var polishDetail = "润色本句"
    private var cancelDetail = "取消本句"
    private var dismissTimer: Timer?
    private var resizeTimer: Timer?
    private var resizeTarget: NSSize?
    private var shouldShow = false
    private var anchor = ActionPanelAnchor()
    private var busy = false
    var needsPosition: Bool { shouldShow && (anchor.needsCaret || !panel.isVisible) }
    var positioningDiagnostics: [String: Any] {
        ["shown": shouldShow, "visible": panel.isVisible, "application_hidden": NSApp.isHidden, "needs_position": needsPosition,
         "frozen": anchor.heldOrigin != nil, "frame": CaretDiagnostics.rect(panel.frame),
         "anchor_caret": anchor.caret.map(CaretDiagnostics.rect) ?? []]
    }
    var onCancel: (() -> Void)?
    var onConvert: (() -> Void)?
    var onFinish: (() -> Void)?

    override init() {
        panel = PassivePanel(contentRect: NSRect(x: 0, y: 0, width: 290, height: 60),
                             styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        super.init()
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.animationBehavior = .none
        panel.contentView = effect

        status.identifier = NSUserInterfaceItemIdentifier("status")
        status.font = VoiceHudStyle.font(12)
        status.textColor = VoiceHudStyle.text
        status.lineBreakMode = .byClipping // Width is measured; never replace a state with an ellipsis.
        status.maximumNumberOfLines = 1
        status.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        progress.identifier = NSUserInterfaceItemIdentifier("voiceProcessingDots")
        progress.image = VoiceHudAssets.image("processing-dots")
        progress.imageScaling = .scaleProportionallyUpOrDown
        progress.setAccessibilityLabel("正在处理")
        progress.isHidden = true
        statusSymbol.identifier = NSUserInterfaceItemIdentifier("voiceStatusSymbol")
        statusSymbol.imageScaling = .scaleProportionallyUpOrDown
        statusSymbol.image = VoiceHudAssets.image("listening-waveform")
        let symbolSlot = NSView()
        spinner.identifier = NSUserInterfaceItemIdentifier("voiceProcessingSpinner")
        for view in [statusSymbol, spinner] {
            view.translatesAutoresizingMaskIntoConstraints = false
            symbolSlot.addSubview(view)
            NSLayoutConstraint.activate([
                view.centerXAnchor.constraint(equalTo: symbolSlot.centerXAnchor),
                view.centerYAnchor.constraint(equalTo: symbolSlot.centerYAnchor),
                view.widthAnchor.constraint(equalTo: symbolSlot.widthAnchor),
                view.heightAnchor.constraint(equalTo: symbolSlot.heightAnchor)
            ])
        }
        state.setViews([symbolSlot, progress, status], in: .center)
        state.orientation = .horizontal
        state.distribution = .fill
        state.alignment = .centerY
        state.spacing = 8
        state.detachesHiddenViews = true
        hints.setViews(actionHints, in: .center)
        hints.identifier = NSUserInterfaceItemIdentifier("voiceGestureHints")
        hints.orientation = .horizontal
        hints.alignment = .centerY
        hints.spacing = 6
        hints.detachesHiddenViews = true
        hintWidthConstraint = hints.widthAnchor.constraint(equalToConstant: 0)
        for hint in actionHints {
            scaledConstraints += [(hint.widthAnchor.constraint(equalToConstant: VoiceHudHint.baseWidth), VoiceHudHint.baseWidth),
                                  (hint.heightAnchor.constraint(equalToConstant: 32), 32)]
        }
        stateWidthConstraint = state.widthAnchor.constraint(equalToConstant: 180)
        scaledConstraints += [
            (progress.widthAnchor.constraint(equalToConstant: 31), 31),
            (progress.heightAnchor.constraint(equalToConstant: 5), 5),
            (symbolSlot.widthAnchor.constraint(equalToConstant: 30), 30),
            (symbolSlot.heightAnchor.constraint(equalToConstant: 24), 24),
            (stateWidthConstraint, 180)
        ]
        stack.setViews([state, hints], in: .center)
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.detachesHiddenViews = true
        stack.wantsLayer = true
        stack.identifier = NSUserInterfaceItemIdentifier("voiceHudContent")
        stack.translatesAutoresizingMaskIntoConstraints = false
        effect.addSubview(stack)
        scaledConstraints += [(stack.leadingAnchor.constraint(equalTo: effect.leadingAnchor, constant: 16), 16)]
        NSLayoutConstraint.activate(scaledConstraints.map { $0.0 } + [hintWidthConstraint, stack.centerYAnchor.constraint(equalTo: effect.centerYAnchor)])
        cancelButton.target = self; cancelButton.action = #selector(cancel)
        convertButton.target = self; convertButton.action = #selector(convert)
        finishButton.target = self; finishButton.action = #selector(finish)
        // Polish gestures remain routed by the host, including waiting for final ASR.
        polishHint.setAccessibilityRole(.image)
        refreshHints()
        applyAppearance()
    }

    @objc private func cancel() { onCancel?() }
    @objc private func convert() { onConvert?() }
    @objc private func finish() { onFinish?() }

    func configureGestureHints(_ hints: VoiceHudBindings) {
        guard hints != gestureHints else { return }
        gestureHints = hints
        refreshHints() // Updating icons must not replay the panel or reset its timer/anchor.
    }

    func configureAppearance(_ value: VoiceHudAppearance) {
        guard appearance != value else { return }
        appearance = value
        applyAppearance() // No state update, caret read, replay or timer reset.
    }

    private func applyAppearance() {
        let scale = appearance.scale
        for (constraint, original) in scaledConstraints { constraint.constant = original * scale }
        status.font = VoiceHudStyle.font(max(11, 12 * scale))
        state.spacing = 8 * scale
        hints.spacing = 6 * scale
        for hint in actionHints { hint.uiScale = scale }
        effect.cornerRadius = InputPaletteStyle.cornerRadius
        fitVisibleActions()
    }

    private func fitVisibleActions() {
        let scale = appearance.scale
        let count = actionHints.filter { !$0.isHidden }.count
        let actionsWidth = (CGFloat(count) * VoiceHudHint.baseWidth + CGFloat(max(0, count - 1) * 6)) * scale
        let textWidth = (status.stringValue as NSString).size(withAttributes: [.font: status.font!]).width
        stateWidthConstraint.constant = max(180 * scale, ceil(textWidth) + 82 * scale)
        hintWidthConstraint.constant = actionsWidth
        hints.isHidden = count == 0
        stack.spacing = count == 0 ? 0 : 18 * scale
        let size = NSSize(width: 30 * scale + stateWidthConstraint.constant + actionsWidth + stack.spacing,
                          height: InputPaletteStyle.rowHeight * scale / 0.8)
        // Keep the status region and left anchor steady. Only actual visible
        // actions occupy space; hiding the last action also removes its gap.
        resize(to: size)
        effect.layoutSubtreeIfNeeded()
    }

    private func resize(to size: NSSize) {
        if resizeTarget == size && resizeTimer != nil { return }
        resizeTimer?.invalidate()
        resizeTimer = nil
        resizeTarget = size
        let start = panel.frame.size
        guard start != size else { return }
        guard automatic, panel.isVisible, !VoiceHudMotion.reduced else {
            let origin = panel.frame.origin
            panel.setContentSize(size)
            panel.setFrameOrigin(origin)
            return
        }
        // Start at the currently displayed width, including an interrupted resize.
        // The left caret anchor stays fixed while the right edge eases to its target.
        let started = ProcessInfo.processInfo.systemUptime
        let timer = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] timer in
            guard let self else { timer.invalidate(); return }
            let t = min(1, max(0, (ProcessInfo.processInfo.systemUptime - started) / 0.20))
            let eased = t * t * (3 - 2 * t)
            let current = NSSize(width: start.width + (size.width - start.width) * eased,
                                 height: start.height + (size.height - start.height) * eased)
            let origin = self.panel.frame.origin
            self.panel.setContentSize(current)
            self.panel.setFrameOrigin(origin)
            if t >= 1 { timer.invalidate(); self.resizeTimer = nil }
        }
        resizeTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func refreshHints() {
        cancelButton.configure(gestures: gestureHints.actions["undo"] ?? [], caption: cancelDetail.hasPrefix("取消") ? "取消" : "撤销",
                               available: cancelAvailable, detail: cancelDetail)
        convertButton.configure(gestures: gestureHints.actions["switch_mode"] ?? [], caption: "修改",
                                available: editAvailable, detail: "转换为编辑")
        polishHint.configure(gestures: gestureHints.actions["polish"] ?? [], caption: "润色",
                             available: polishAvailable, detail: polishDetail)
        finishButton.configure(gestures: gestureHints.actions["end"] ?? [], caption: "结束",
                               available: finishAvailable, detail: "结束语音并自动判断")
        finishButton.isHidden = !automatic || !finishAvailable
        if automatic { convertButton.isHidden = true; polishHint.isHidden = true }
    }

    func applyPresentation(_ presentation: ActionPanelPresentation) {
        status.stringValue = presentation.title
        status.setAccessibilityLabel(presentation.title)
        status.toolTip = presentation.detail ?? presentation.title
        status.textColor = VoiceHudStyle.text
        statusSymbol.image = presentation.isError ? VoiceHudAssets.image("status-error") : nil
        statusSymbol.isHidden = !presentation.isError
        statusSymbol.setAccessibilityLabel(presentation.isError ? "操作失败" : "")
    }

    func update(_ session: CompositionSession, historyDepth: Int = 0) {
        anchor.update(utteranceID: session.utteranceID,
                      editing: session.phase == .editing || session.phase == .polishing ||
                          (session.automaticMode && (session.phase == .finishing ||
                            (session.phase == .listening && !session.automaticStage.isEmpty))) ||
                          (session.phase == .finishing && session.editRequested) ||
                          (session.phase == .edited && session.awaitingReadback),
                      currentSize: panel.frame.size)
        dismissTimer?.invalidate()
        dismissTimer = nil
        let presentation = ActionPanelPresentation(phase: session.phase, error: session.error,
            empty: session.raw.isEmpty, editRequested: session.editRequested,
            readableRange: session.editScope == "readable_range", polished: session.polished,
            automatic: session.automaticMode, stage: session.automaticStage, tasks: session.automaticTasks,
            awaitingReadback: session.awaitingReadback)
        automatic = session.automaticMode
        finishAvailable = automatic && session.phase == .listening && session.automaticStage.isEmpty
        let pendingDictation = [.listening, .finishing].contains(session.phase) && !session.editRequested
        polishAvailable = !automatic && (pendingDictation || session.canPolish)
        polishDetail = pendingDictation ? "结束听写并润色本句" : "润色本句"
        let nextVisualState = "\(session.phase)|\(presentation.title)|\(presentation.isError)|\(session.canCancel)|\(session.canEdit)|\(polishAvailable)|\(historyDepth > 0)"
        let animateState = panel.isVisible && visualState != nextVisualState
        visualState = nextVisualState
        applyPresentation(presentation)
        busy = !presentation.isError && ([.editing, .polishing, .finishing].contains(session.phase) ||
            (automatic && session.phase == .listening && !session.automaticStage.isEmpty) || session.awaitingReadback)
        progress.setAnimating(busy)
        spinner.setAnimating(busy)
        if busy {
            statusSymbol.isHidden = true
        } else if !presentation.isError && session.phase == .listening {
            statusSymbol.image = VoiceHudAssets.image("listening-waveform")
            statusSymbol.isHidden = false
            status.textColor = VoiceHudStyle.blue
        }
        if !presentation.isError && !session.raw.isEmpty && [.dictated, .edited].contains(session.phase) {
            statusSymbol.image = VoiceHudAssets.image("status-done")
            statusSymbol.setAccessibilityLabel("已完成")
            statusSymbol.isHidden = false
        }
        cancelDetail = session.cancelLabel
        cancelAvailable = session.canCancel || (session.phase == .undone && historyDepth > 0)
        editAvailable = session.canEdit
        refreshHints()
        shouldShow = true
        fitVisibleActions()
        if animateState { VoiceHudMotion.transition(stack) }
        if let origin = anchor.heldOrigin, panel.frame.origin != origin { panel.setFrameOrigin(origin) }
        if [.dictated, .edited, .undone, .interrupted, .error].contains(session.phase) {
            dismissTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: false) { [weak self] _ in self?.hide() }
        }
    }

    func showSent() {
        finishAvailable = false
        dismissTimer?.invalidate()
        let animateState = panel.isVisible
        visualState = "sent"
        applyPresentation(ActionPanelPresentation(phase: .interrupted, error: "已发送"))
        statusSymbol.image = VoiceHudAssets.image("status-done")
        statusSymbol.setAccessibilityLabel("已发送")
        statusSymbol.isHidden = false
        busy = false
        progress.setAnimating(false)
        spinner.setAnimating(false)
        cancelAvailable = false
        editAvailable = false
        polishAvailable = false
        refreshHints()
        shouldShow = true
        fitVisibleActions()
        if animateState { VoiceHudMotion.transition(stack) }
        dismissTimer = Timer.scheduledTimer(withTimeInterval: 1.6, repeats: false) { [weak self] _ in self?.hide() }
    }

    func showNotice(_ text: String) {
        finishAvailable = false
        dismissTimer?.invalidate()
        visualState = "notice"
        applyPresentation(ActionPanelPresentation(phase: .error, error: text))
        busy = false
        progress.setAnimating(false)
        spinner.setAnimating(false)
        cancelAvailable = false
        editAvailable = false
        polishAvailable = false
        refreshHints()
        shouldShow = true
        fitVisibleActions()
        dismissTimer = Timer.scheduledTimer(withTimeInterval: 2.8, repeats: false) { [weak self] _ in self?.hide() }
    }

    func showLearnedHotwords(_ words: [String]) {
        let words = words.prefix(8).map { String($0.prefix(40)) }.filter { !$0.isEmpty }
        guard !words.isEmpty else { return }
        status.stringValue = words.count == 1 ? "已收录 · \(words[0])" : "已收录 \(words.count) 个热词"
        status.toolTip = "已收录热词：" + words.joined(separator: "、") + "。从下一句开始优先使用。"
        status.setAccessibilityLabel(status.toolTip)
        statusSymbol.image = VoiceHudAssets.image("status-done")
        statusSymbol.isHidden = false
        progress.setAnimating(false)
        spinner.setAnimating(false)
        shouldShow = true
        fitVisibleActions()
        VoiceHudMotion.transition(stack)
        dismissTimer?.invalidate()
        dismissTimer = Timer.scheduledTimer(withTimeInterval: 4, repeats: false) { [weak self] _ in self?.hide() }
    }

    func position(caret: NSRect?, clientLevel: Int32) {
        guard shouldShow else { return }
        // Some clients temporarily stop returning geometry while waiting for
        // a result. Keep this utterance's anchor even across temporary hides.
        guard let caret = anchor.observe(caret), let screen = NSScreen.screens.first(where: { $0.frame.intersects(caret.insetBy(dx: -1, dy: -1)) }) else {
            panel.orderOut(nil)
            return
        }
        let visible = screen.visibleFrame.insetBy(dx: 6, dy: 6)
        let size = panel.frame.size
        let x = min(max(caret.minX, visible.minX), max(visible.minX, visible.maxX - size.width))
        var y = caret.minY - size.height - InputPaletteStyle.caretGap
        if y < visible.minY { y = caret.maxY + InputPaletteStyle.caretGap }
        y = min(max(y, visible.minY), max(visible.minY, visible.maxY - size.height))
        let scale = screen.backingScaleFactor
        let origin = anchor.place(NSPoint(x: (x * scale).rounded() / scale, y: (y * scale).rounded() / scale), size: size)
        if panel.frame.origin != origin { panel.setFrameOrigin(origin) }
        panel.level = NSWindow.Level(rawValue: max(NSWindow.Level.floating.rawValue, Int(clientLevel) + 1))
        if !panel.isVisible {
            // A hidden accessory process cannot show even orderFrontRegardless.
            // Unhide without activation so the editor keeps keyboard focus.
            if NSApp.isHidden { NSApp.unhideWithoutActivation() }
            panel.orderFrontRegardless()
        }
    }

    func hide() {
        resizeTimer?.invalidate()
        resizeTimer = nil
        if let resizeTarget { panel.setContentSize(resizeTarget) }
        shouldShow = false
        // IMK may hide palettes while ending a composition or preparing an
        // edit. Its next state update must reuse this operation's anchor.
        // ActionPanelAnchor.update resets it when a new utterance starts.
        busy = false
        progress.setAnimating(false)
        spinner.setAnimating(false)
        stack.layer?.removeAnimation(forKey: kCATransition)
        visualState = ""
        dismissTimer?.invalidate()
        dismissTimer = nil
        panel.orderOut(nil)
    }

    deinit { dismissTimer?.invalidate() }
}
