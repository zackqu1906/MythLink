import AppKit
import QuartzCore

func check(_ ok: @autoclosure () -> Bool, _ message: String) {
    if !ok() { fputs("FAIL: \(message)\n", stderr); exit(1) }
}

let cases: [(String, String)] = [
    ("辅助功能权限尚未生效。若已授权，请完全退出主程序后重新打开", "辅助功能权限未生效"),
    ("按键控制权限尚未生效。若已授权，请完全退出主程序后重新打开", "按键控制权限未生效"),
    ("输入框未确认本次文字更新，已停止后续写入", "输入框未确认文字更新"),
    ("输入框未确认编辑结果或末尾光标，已停止本次操作", "编辑结果或光标未确认"),
    ("输入框未确认本句选取或撤销结果，已停止本次操作", "选取或撤销结果未确认"),
    ("输入框尚未确认本句撤销", "输入框未确认撤销结果"),
    ("光标或选区已变化，本句已结束", "光标或选区已改变"),
    ("输入法连接已断开，已停止本句", "输入法连接已断开"),
    ("语音识别服务连接超时", "语音识别服务连接超时")
]
for (reason, title) in cases {
    let result = ActionPanelPresentation(phase: .error, error: reason)
    check(result.title == title && result.isError && result.detail == reason, "lost failure cause: \(reason)")
}
print("PASS distinct failures display short causes with complete details")
let unsuccessful = ActionPanelPresentation(phase: .dictated, error: "编辑模型返回结果为空，已保留听写")
check(unsuccessful.isError && unsuccessful.title.contains("结果为空"), "retained dictation hid the edit failure")
print("PASS failed edit after dictation remains an error")
for phase in [CompositionSession.Phase.listening, .editing, .dictated, .edited, .undone] {
    check(!ActionPanelPresentation(phase: phase, error: "").isError, "normal phase is red")
}
let normal = ActionPanelPresentation(phase: .interrupted, error: "鼠标操作已接管本句")
check(!normal.isError && normal.title == "鼠标操作已接管本句", "intentional takeover is misreported")
print("PASS normal completion, cancellation and user takeover are not errors")
let unknown = ActionPanelPresentation(phase: .error, error: String(repeating: "未知服务错误", count: 20))
check(unknown.title.count <= 24 && unknown.detail!.count > 24 && unknown.isError, "unknown error grew the palette or lost details")
check(ActionPanelPresentation(phase: .error, error: "").title.contains("未返回具体原因"), "invented a cause for missing error")
print("PASS unknown long errors stay bounded without inventing a cause")

_ = NSApplication.shared
NSApp.setActivationPolicy(.accessory)
let palette = ActionPanel()
func find(_ root: NSView, _ id: String) -> NSView? {
    if root.identifier?.rawValue == id { return root }
    return root.subviews.lazy.compactMap { find($0, id) }.first
}
guard let window = NSApp.windows.first(where: { $0.contentView.map { find($0, "status") != nil } ?? false }),
      let content = window.contentView, let errorSymbol = find(content, "voiceStatusSymbol") as? NSImageView,
      let label = find(content, "status") as? NSTextField else { fatalError("missing palette") }
palette.applyPresentation(ActionPanelPresentation(phase: .error, error: cases[0].0))
check(!errorSymbol.isHidden && errorSymbol.image === VoiceHudAssets.image("status-error"), "failure lost the original Figma error icon")
check(label.textColor == VoiceHudStyle.text && label.toolTip == cases[0].0, "error text or complete cause was lost")
palette.applyPresentation(ActionPanelPresentation(phase: .listening, error: "", empty: true))
check(errorSymbol.isHidden && label.textColor == VoiceHudStyle.text && label.toolTip == label.stringValue,
      "next utterance kept stale error styling or tooltip")
palette.hide()
print("PASS Figma error indicator preserves the complete cause and resets on the next utterance")

let screenshotDirectory = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MYTHLINK_SCREENSHOT_DIR"] ?? "/private/tmp/mythlink-voice-hud")
try FileManager.default.createDirectory(at: screenshotDirectory, withIntermediateDirectories: true)
func capture(_ name: String) throws {
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.08))
    content.layoutSubtreeIfNeeded()
    let titleRect = label.convert(label.bounds, to: content)
    check(abs(titleRect.midY - content.bounds.midY) < 1, "status jumped when indicators changed")
    guard let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds),
          let layer = content.layer, let context = NSGraphicsContext(bitmapImageRep: bitmap) else { fatalError("missing render surface") }
    content.cacheDisplay(in: content.bounds, to: bitmap)
    context.cgContext.clear(content.bounds)
    layer.render(in: context.cgContext)
    try bitmap.representation(using: .png, properties: [:])!.write(to: screenshotDirectory.appendingPathComponent(name + ".png"))
}
for name in VoiceHudBindings.names.keys.map({ VoiceHudBindings.assetName($0) }) + ["listening-waveform", "processing-spinner", "processing-dots", "status-done", "status-error"] {
    check(VoiceHudAssets.image(name)?.isValid == true, "missing/unreadable HUD asset: \(name)")
}
guard let cancelHint = find(content, "voiceHint_undo") as? VoiceHudHint,
      let editHint = find(content, "voiceHint_switch_mode") as? VoiceHudHint,
      let polishHint = find(content, "voiceHint_polish") as? VoiceHudHint,
      let dots = find(content, "voiceProcessingDots") as? VoiceHudProgress,
      let spinner = find(content, "voiceProcessingSpinner") as? VoiceHudSpinner,
      let undoCheck = find(content, "voiceUndoCheck") as? VoiceHudUndoCheck,
      let hudContent = find(content, "voiceHudContent") else { fatalError("missing separate hint controls") }
check(content.bounds.height == 32, "default palette is not compact")
check(!window.hasShadow && content.layer?.backgroundColor?.alpha == 0.68,
      "voice palette does not share the translucent stroke surface")
check(cancelHint.gestures == ["swipe-left"] && editHint.gestures == ["swipe-right"], "wrong default mapping")
check(find(content, "voiceHint_confirm") == nil, "introduced a Tap/confirmation hint that was not in the existing panel")
check(cancelHint.title == "取消" && editHint.title == "修改" && polishHint.title == "润色", "missing action captions")
check(polishHint.gestures == ["swipe-down"] && polishHint.action == nil,
      "polish must remain a down-swipe hint routed by the host")

func checkActionFit(_ count: Int, scale: CGFloat = 0.8) {
    content.layoutSubtreeIfNeeded()
    let visible = [cancelHint, editHint, polishHint].filter { !$0.isHidden }
    check(visible.count == count, "fixture has the wrong visible action count")
    check(abs(content.bounds.height - 40 * scale) < 0.01, "action count changed the configured height")
    for hint in visible {
        let textWidth = (hint.title as NSString).size(withAttributes: [.font: VoiceHudStyle.font(max(11, 14 * scale))]).width
        let gesturesWidth = CGFloat(min(2, hint.gestures.count)) * (hint.gestures.count > 1 ? 17 : 24) * scale
        let gap: CGFloat = hint.gestures.isEmpty ? 0 : 6 * scale
        check(textWidth + gesturesWidth + gap <= hint.bounds.width - 8 * scale, "gesture or function caption is clipped")
    }
    if let last = visible.last {
        let right = last.convert(last.bounds, to: content).maxX
        check(abs(content.bounds.maxX - right - 14 * scale) < 1,
              "hidden action left unused space at the right edge")
    } else {
        check(find(content, "voiceGestureHints")?.isHidden == true, "empty actions still occupy a slot")
        let requiredText = ceil((label.stringValue as NSString).size(withAttributes: [.font: label.font!]).width)
        let expectedWidth = 30 * scale + max(180 * scale, requiredText + 82 * scale)
        check(abs(content.bounds.width - expectedWidth) < 1, "zero actions kept the action gap")
    }
}

// Exercise the real panel/session transition: editRequested intentionally stays
// true after success and must not keep the result anchored to the instruction.
final class PanelTextClient: CompositionClient {
    let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 160))
    var exposesRanges = true
    init() { text.string = "原文"; text.setSelectedRange(NSRange(location: 2, length: 0)) }
    func snapshot() throws -> EditorSnapshot {
        if !exposesRanges {
            return EditorSnapshot(text: nil, selection: unspecifiedRange, markedRange: unspecifiedRange,
                                  selectedText: nil, documentAccess: false)
        }
        return EditorSnapshot(text: text.string, selection: text.selectedRange(), markedRange: text.markedRange(),
            selectedText: (text.string as NSString).substring(with: text.selectedRange()), documentAccess: true)
    }
    func probe() -> CompositionProbe {
        if !exposesRanges { return CompositionProbe(selection: unspecifiedRange, markedRange: unspecifiedRange, markedText: nil) }
        let range = text.markedRange()
        return CompositionProbe(selection: text.selectedRange(), markedRange: range,
            markedText: isValidRange(range, length: (text.string as NSString).length) ? (text.string as NSString).substring(with: range) : nil)
    }
    func selection() -> NSRange { exposesRanges ? text.selectedRange() : unspecifiedRange }
    func mark(_ value: String) {
        text.setMarkedText(value, selectedRange: NSRange(location: (value as NSString).length, length: 0), replacementRange: unspecifiedRange)
    }
    func commit(_ value: String) { text.insertText(value, replacementRange: unspecifiedRange) }
    func replace(_ range: NSRange, with value: String) { text.insertText(value, replacementRange: range) }
}
let client = PanelTextClient()
let session = try CompositionSession(client: client, utteranceID: "panel-edit", sequence: 1)
let screen = NSScreen.screens[0].visibleFrame
let oldCaret = NSRect(x: screen.midX - 150, y: screen.midY, width: 1, height: 20)
let newCaret = NSRect(x: screen.midX + 40, y: screen.midY - 70, width: 1, height: 20)
session.update("扩写", final: false)
palette.update(session)
checkActionFit(3)
try capture("listening")
check(cancelHint.toolTip?.contains("取消") == true && !cancelHint.isHidden && !editHint.isHidden, "listening hints do not follow existing availability")
check(!polishHint.isHidden && polishHint.toolTip == "下滑 · 结束听写并润色本句", "listening is missing its polish hint")
let snapshot = (session.phase, session.revision, session.raw)
var reordered = VoiceHudBindings()
reordered.apply(["undo": ["swipe-right", "snap", "double-index-pinch"], "confirm": [], "end": [],
                 "switch_mode": ["tap"], "send": ["swipe-down"], "polish": ["middle-pinch", "clench", "swipe-up"]])
palette.configureGestureHints(reordered)
check(cancelHint.gestures == ["swipe-right", "snap", "double-index-pinch"] && cancelHint.title == "取消" && editHint.title == "修改" && editHint.gestures == ["tap"], "gesture and caption cannot be recombined")
check(polishHint.gestures == ["middle-pinch", "clench", "swipe-up"], "polish hint did not follow configured gestures")
check(polishHint.toolTip?.contains("上滑") == true, "extra alternatives are missing from the accessible hint")
check(session.phase == snapshot.0 && session.revision == snapshot.1 && session.raw == snapshot.2, "display configuration changed the transaction")
try capture("custom-bindings")
var doubleHints = reordered
doubleHints.apply(["undo": ["double-tap"], "confirm": [], "end": [],
                   "switch_mode": ["double-index-pinch", "double-middle-pinch"], "send": [], "polish": ["circle-clockwise"]])
palette.configureGestureHints(doubleHints)
checkActionFit(3)
try capture("double-gestures")
var noUndoGesture = reordered
noUndoGesture.apply(["undo": ["", ""], "confirm": ["tap", ""], "switch_mode": ["swipe-right", ""]])
palette.configureGestureHints(noUndoGesture)
var cancelClicks = 0, editClicks = 0
palette.onCancel = { cancelClicks += 1 }; palette.onConvert = { editClicks += 1 }
cancelHint.performClick(nil); editHint.performClick(nil)
check(cancelClicks == 1 && editClicks == 1, "unbound hint disabled the existing mouse controls")
let validHints = noUndoGesture
noUndoGesture.apply(["confirm": ["not-a-gesture"], "undo": [], "switch_mode": []])
check(noUndoGesture == validHints, "invalid presentation metadata replaced valid hints")
palette.configureGestureHints(VoiceHudBindings())
palette.position(caret: oldCaret, clientLevel: 0)
let oldOrigin = window.frame.origin
let listeningSize = window.frame.size
session.convert(); session.update("扩写", final: true)
palette.update(session)
let processingSize = window.frame.size
check(processingSize.width < listeningSize.width && processingSize.height == listeningSize.height,
      "one action did not make the panel narrower")
checkActionFit(1)
check(!spinner.isHidden, "processing circle is not visible")
spinner.setAnimating(true, reducedMotion: false)
guard let rotation = spinner.rotor.animation(forKey: "rotation") as? CABasicAnimation else { fatalError("spinner is static") }
check(rotation.keyPath == "transform.rotation.z" && rotation.repeatCount == .infinity && rotation.duration == 0.8,
      "Figma arc does not rotate continuously")
spinner.setAnimating(true, reducedMotion: false)
check(spinner.rotor.animation(forKey: "rotation")?.beginTime == rotation.beginTime, "loading update restarted rotation")
if !VoiceHudMotion.reduced {
    check(hudContent.layer?.animation(forKey: kCATransition)?.duration == 0.10, "missing short content transition")
    let transitionStart = hudContent.layer?.animation(forKey: kCATransition)?.beginTime
    palette.update(session)
    check(hudContent.layer?.animation(forKey: kCATransition)?.beginTime == transitionStart, "unchanged state replayed transition")
}
var large = VoiceHudAppearance()
large.apply(140)
palette.configureAppearance(large)
checkActionFit(1, scale: 1.4)
check(abs(window.frame.height - 56) < 0.01 && window.frame.width > listeningSize.width && window.frame.origin == oldOrigin,
      "live resize moved the held anchor or ignored the configured size")
try capture("processing-large")
palette.configureAppearance(VoiceHudAppearance())
check(window.frame.size == processingSize, "reset did not restore compact one-action size")
checkActionFit(1)
check(!dots.isHidden && dots.image === VoiceHudAssets.image("processing-dots"), "loading is not the original Figma artwork")
check(editHint.isHidden && !cancelHint.isHidden, "processing hint visibility changed")
try capture("processing")
check(session.phase == .editing && !palette.needsPosition, "model waiting did not freeze the panel")
palette.position(caret: newCaret, clientLevel: 0)
check(window.frame.origin == oldOrigin, "pending model adopted a transient selection")
session.applyEdit(text: "扩写后的原文\n第二行", revision: session.revision, error: nil)
check(session.phase == .edited && session.editRequested, "fixture did not retain the edit flag")
palette.update(session)
check(spinner.isHidden && spinner.rotor.animationKeys()?.isEmpty != false, "completion left spinner running")
check(window.frame.size == processingSize, "unchanged action count resized the window")
checkActionFit(1)
check(cancelHint.toolTip?.contains("还原听写") == true && editHint.isHidden, "settled hints imply unavailable editing")
try capture("edited")
var reportedFrames: [[Double]] = []
palette.onPosition = { reportedFrames.append($0) }
check(palette.needsPosition, "completed edit flag still blocks position refresh")
palette.position(caret: newCaret, clientLevel: 0)
check(window.frame.origin.x == newCaret.minX && window.frame.origin != oldOrigin,
      "result panel stayed at the old preedit caret")
let desktopTop = NSScreen.screens[0].frame.maxY - window.frame.maxY
check(reportedFrames.last == [window.frame.minX, desktopTop, window.frame.width, window.frame.height],
      "host notice anchor does not match the visible voice panel")
palette.position(caret: newCaret, clientLevel: 0)
check(reportedFrames.count == 1, "unchanged voice panel repeatedly published its position")
session.cancel(); palette.update(session)
check(session.phase == .dictated && palette.needsPosition, "undo did not resume positioning")
check(!undoCheck.isHidden && errorSymbol.isHidden, "restoring a committed edit lost its undo confirmation")
palette.position(caret: oldCaret, clientLevel: 0)
check(window.frame.origin.x == oldCaret.minX, "undo panel did not follow restored dictation")
session.interrupt("鼠标操作已接管本句")
palette.update(session)
checkActionFit(0)
check(window.frame.width < processingSize.width, "no-action state kept the button region")
try capture("no-actions")
palette.hide()
check(!window.isVisible && hudContent.layer?.animation(forKey: kCATransition) == nil, "hide waited for an animation")
print("PASS native panel freezes during editing, follows the result caret and follows undo")
palette.applyPresentation(ActionPanelPresentation(phase: .error, error: cases[0].0))
try capture("error")
print("PASS gesture icons with text captions, remapping, hint visibility and mouse callbacks preserve composition")
spinner.setAnimating(true, reducedMotion: true)
check(spinner.rotor.animation(forKey: "rotation") == nil && spinner.rotor.animation(forKey: "pulse") != nil,
      "Reduce Motion kept spatial animation")
spinner.setAnimating(false)
large.apply(Double.nan); large.apply(500); large.apply("huge")
check(large.scalePercent == 140, "invalid size metadata replaced a valid size")
print("PASS compact default, live scaling, continuous spinner and interruptible transitions")


// Background prewarming must not permanently hide the ready-to-speak palette.
let readySession = try CompositionSession(client: PanelTextClient(), utteranceID: "warm-hidden", sequence: 1)
palette.update(readySession)
NSApp.hide(nil)
RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05))
check(NSApp.isHidden, "fixture did not hide the accessory process")
let front = NSWorkspace.shared.frontmostApplication?.processIdentifier
palette.position(caret: oldCaret, clientLevel: 0)
RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05))
check(!NSApp.isHidden && window.isVisible, "background prewarming hid the listening palette")
check(!window.isKeyWindow && !window.isMainWindow, "palette took keyboard focus")
check(NSWorkspace.shared.frontmostApplication?.processIdentifier == front, "unhiding activated the IME")
readySession.update("下一句", final: false)
palette.update(readySession)
checkActionFit(3)
check(window.frame.size == listeningSize, "new available action did not restore panel width")
palette.configureAppearance(large)
checkActionFit(3, scale: 1.4)
try capture("listening-large")
palette.configureAppearance(VoiceHudAppearance())
readySession.finish()
palette.update(readySession)
check(!polishHint.isHidden && polishHint.toolTip == "下滑 · 结束听写并润色本句", "final recognition wait lost the polish hint")
readySession.update("下一句", final: true)
palette.update(readySession)
checkActionFit(2)
check(cancelHint.title == "撤销" && polishHint.title == "润色", "settled actions lost their text captions")
check(!polishHint.isHidden && editHint.isHidden && polishHint.toolTip == "下滑 · 润色本句", "dictated text is missing its polish hint")
try capture("dictated")
readySession.polish(restoringRawOnUndo: true)
palette.update(readySession)
checkActionFit(1)
check(polishHint.isHidden && !spinner.isHidden, "polishing still advertises another polish request")
try capture("polishing")
readySession.applyPolish(text: "下一句", revision: readySession.revision, error: nil)
palette.update(readySession)
checkActionFit(1)
check(polishHint.isHidden && spinner.isHidden && label.stringValue == "已润色", "polished completion kept an unavailable hint")
try capture("polished")
palette.showSent()
checkActionFit(0)
check(label.stringValue == "已发送" && spinner.isHidden && !errorSymbol.isHidden,
      "sent feedback retained an operation or error state")
check(cancelHint.isHidden && editHint.isHidden && polishHint.isHidden, "sent feedback advertised stale actions")
try capture("sent")
readySession.cancel()
palette.update(readySession)
check(!readySession.polished && !undoCheck.isHidden, "restoring a polished sentence lost its undo confirmation")
readySession.cancel()
palette.update(readySession)
check(readySession.phase == .undone && !undoCheck.isHidden, "ordinary dictation undo lost its confirmation")
let nextSession = try CompositionSession(client: PanelTextClient(), utteranceID: "after-sent", sequence: 1)
palette.update(nextSession)
check(label.stringValue == "听写 · 可以说话", "new session kept sent feedback")
palette.hide()
print("PASS hidden background process shows listening palette without taking focus")
let noticeFront = NSWorkspace.shared.frontmostApplication?.processIdentifier
palette.showNotice("当前没有可以润色的文本")
palette.position(caret: oldCaret, clientLevel: 0)
check(label.stringValue == "当前没有可以润色的文本", "input notice was not displayed")
checkActionFit(0)
check(window.isVisible && !window.isKeyWindow && !window.isMainWindow, "notice took keyboard focus")
check(NSWorkspace.shared.frontmostApplication?.processIdentifier == noticeFront, "notice activated the IME")
palette.update(nextSession)
check(label.stringValue == "听写 · 可以说话", "notice replaced the next input state")
palette.hide()
print("PASS input notices are passive and leave composition state intact")
print("PASS listening and dictated palettes show the down-swipe polish hint until polishing")
print("PASS zero through three actions fit without reserved space at every configured scale")

let redoSession = try CompositionSession(client: PanelTextClient(), utteranceID: "redo-hud", sequence: 1)
redoSession.update("本句", final: true)
palette.configureGestureHints(VoiceHudBindings())
palette.update(redoSession, redoAvailable: true)
check(editHint.title == "下一步" && !editHint.isHidden, "settled history did not advertise redo")
try capture("redo-available")
palette.update(redoSession, redoAvailable: false, redoEnabled: false)
check(editHint.title == "修改" && editHint.isHidden, "disabled redo changed the original hint")
redoSession.automaticMode = true
palette.update(redoSession, redoAvailable: true)
check(editHint.title == "下一步" && !editHint.isHidden, "automatic mode hid redo")
palette.update(redoSession, redoAvailable: false)
check(editHint.isHidden, "empty automatic redo remained available")
print("PASS stable manual/automatic redo hints respect availability and the setting")

// Automatic routing has one end action and never advertises manual conversion.
let automaticClient = PanelTextClient()
let automaticSession = try CompositionSession(client: automaticClient, utteranceID: "automatic-hud", sequence: 1)
automaticSession.automaticMode = true
automaticSession.update("改得客气一些", final: false)
palette.configureGestureHints(VoiceHudBindings())
palette.update(automaticSession)
palette.position(caret: oldCaret, clientLevel: 0)
guard let finishHint = find(content, "voiceHint_end") as? VoiceHudHint else { fatalError("missing finish hint") }
check(editHint.isHidden && polishHint.isHidden && !finishHint.isHidden, "automatic mode exposed manual actions")
check(finishHint.gestures == ["tap"] && label.stringValue == "请说话", "wrong automatic start/end presentation")
try capture("automatic-listening")
var finishes = 0
automaticSession.onFinishAudio = { finishes += 1 }
automaticSession.convert(); automaticSession.finish()
check(finishes == 1 && !automaticSession.editRequested, "old conversion ending must only finish once")
let beforeProgress = (automaticSession.revision, automaticClient.text.string)
for (stage, tasks, title) in [("routing", [String](), "正在判断"),
                               ("routed", ["polish", "translate"], "正在润色、翻译"),
                               ("generating", ["polish", "translate"], "正在编辑 · 润色、翻译")] {
    automaticSession.setAutomaticProgress(stage, tasks: tasks)
    palette.update(automaticSession)
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.25))
    check(label.stringValue.hasPrefix(title), "automatic stage was lost: \(stage)")
    check(editHint.isHidden && polishHint.isHidden && finishHint.isHidden, "processing exposed end or manual choices")
    try capture("automatic-" + stage)
}
automaticSession.setAutomaticProgress("generating", tasks: ["modify", "insert", "polish", "format", "translate", "compose"])
let shortWidth = window.frame.width
palette.update(automaticSession)
if !VoiceHudMotion.reduced {
    check(abs(window.frame.width - shortWidth) < 1, "resize jumped instead of starting at the displayed width")
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.06))
    check(window.frame.width > shortWidth, "resize did not grow smoothly")
}
RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.25))
content.layoutSubtreeIfNeeded()
let fullTextWidth = (label.stringValue as NSString).size(withAttributes: [.font: label.font!]).width
check(label.bounds.width + 1 >= fullTextWidth && label.lineBreakMode == .byClipping, "full task status was truncated")
try capture("automatic-all-tasks")
let longWidth = window.frame.width
automaticSession.setAutomaticProgress("routing", tasks: [])
palette.update(automaticSession)
if !VoiceHudMotion.reduced {
    check(abs(window.frame.width - longWidth) < 1, "shorter state snapped the panel width")
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.06))
    let interruptedWidth = window.frame.width
    automaticSession.setAutomaticProgress("generating", tasks: ["polish", "translate"])
    palette.update(automaticSession)
    check(abs(window.frame.width - interruptedWidth) < 1, "new state did not continue from the current animation width")
}
automaticSession.setAutomaticProgress("generating", tasks: ["polish", "translate"])
RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.25))
check(beforeProgress.0 == automaticSession.revision && beforeProgress.1 == automaticClient.text.string,
      "presentation changed text or transaction revision")
automaticSession.cancel()
automaticSession.setAutomaticProgress("routing", tasks: [])
check(automaticSession.automaticStage == "generating", "cancelled sentence accepted late progress")
palette.hide()
print("PASS automatic end, routing/task/generation states, cancellation and hidden manual hints")

palette.update(redoSession, redoEnabled: false, retryAvailable: true, historyActionMode: "retry")
check(editHint.title == "重做" && !editHint.isHidden, "retry did not advertise a fresh model request")
palette.update(redoSession, redoEnabled: false, retryAvailable: false, historyActionMode: "retry")
check(editHint.isHidden, "retry was offered without an edit instruction")
redoSession.systemUndoTrial = true
redoSession.onChanged = { palette.update(redoSession) }
palette.position(caret: oldCaret, clientLevel: 0)
var deliveryReceipt: ((String?) -> Void)?
redoSession.systemUndo { deliveryReceipt = $0 }
check(redoSession.systemUndoPending && undoCheck.isHidden, "undo animated before the delivery receipt")
deliveryReceipt?("系统撤销未返回发送回执")
check(undoCheck.isHidden && !errorSymbol.isHidden, "failed receipt displayed a successful undo")
redoSession.systemUndo { $0(nil) }
check(redoSession.phase == .undone && label.stringValue == "已撤销" && label.toolTip == "已撤销",
      "system undo should show the same completion message as ordinary undo")
if !VoiceHudMotion.reduced {
    palette.hide()
    redoSession.systemUndo { $0(nil) }
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 1.1))
    check(!window.isVisible, "fixture unexpectedly presented the panel")
    palette.position(caret: oldCaret, clientLevel: 0)
    func displayedStroke(after seconds: TimeInterval) -> CGFloat {
        RunLoop.current.run(until: Date(timeIntervalSinceNow: seconds))
        return undoCheck.stroke.presentation()?.strokeEnd ?? -1
    }
    let start = displayedStroke(after: 0.12)
    palette.update(redoSession)
    let middle = displayedStroke(after: 0.38)
    let end = displayedStroke(after: 0.37)
    let done = displayedStroke(after: 0.22)
    print("Undo presentation stroke at 0.12/0.50/0.87/1.09 s:", start, middle, end, done)
    check(start >= 0 && start < 0.35 && middle > start && middle < 0.8 &&
          end > middle && end < 1 && done == 1,
          "the visible panel must draw its check over a full second even when positioning was late")
    redoSession.systemUndo { $0(nil) }
    let inFlight = displayedStroke(after: 0.45)
    redoSession.systemUndo { $0(nil) }
    let restarted = displayedStroke(after: 0.12)
    check(inFlight > 0.2 && restarted >= 0 && restarted < inFlight,
          "a second successful undo should immediately restart the visible stroke")
}
check(!undoCheck.isHidden && errorSymbol.isHidden && spinner.isHidden &&
      undoCheck.superview === errorSymbol.superview && undoCheck.frame == errorSymbol.frame &&
      undoCheck.convert(undoCheck.bounds, to: content).maxX <= label.convert(label.bounds, to: content).minX,
      "undo should use the reserved icon slot to the left of the label")
let burstStart = CACurrentMediaTime()
let firstRevision = redoSession.revision
for _ in 0..<8 {
    palette.update(redoSession)
    check(redoSession.canSystemUndo && cancelHint.isEnabled, "animation blocked the next undo")
    redoSession.systemUndo { $0(nil) }
    if !VoiceHudMotion.reduced {
        guard let draw = undoCheck.stroke.animation(forKey: "draw") as? CABasicAnimation else { fatalError("missing undo animation") }
        check(draw.keyPath == "strokeEnd" && draw.duration == 1 &&
              undoCheck.stroke.animationKeys() == ["draw"], "successive undos did not replace one bounded draw animation")
    }
}
check(redoSession.revision == firstRevision + 8, "burst lost an undo")
print("PASS eight consecutive undo receipts in \(Int((CACurrentMediaTime() - burstStart) * 1000)) ms")
try capture("undo-check")
undoCheck.show(utterance: "reduced-motion", revision: 1)
undoCheck.playIfVisible(reducedMotion: true)
check(!undoCheck.isHidden && undoCheck.stroke.strokeEnd == 1 && undoCheck.stroke.animation(forKey: "draw") == nil,
      "Reduce Motion should show a static complete check")
redoSession.systemUndo { $0(nil) }
palette.showNotice("操作未完成")
check(undoCheck.isHidden && undoCheck.stroke.animation(forKey: "draw") == nil, "notice retained undo feedback")
palette.update(redoSession)
check(!undoCheck.isHidden && undoCheck.stroke.animation(forKey: "draw") == nil, "returning to old state replayed a receipt")
redoSession.systemUndo { $0("撤销失败") }
check(undoCheck.isHidden && !errorSymbol.isHidden, "failure after a successful undo kept the check")
redoSession.onChanged = nil
print("PASS success-only undo check, stable icon slot, interruptible replay and reduced motion")

// An IMK client can accept dictation without exposing ranges or geometry.
// Its recording status must remain visible without taking keyboard focus.
palette.hide()
let unreadableClient = PanelTextClient()
unreadableClient.exposesRanges = false
let unreadableSession = try CompositionSession(client: unreadableClient, utteranceID: "no-ranges-or-caret", sequence: 1)
let foregroundBeforeFallback = NSWorkspace.shared.frontmostApplication?.processIdentifier
palette.update(unreadableSession)
palette.position(caret: nil, clientLevel: 0)
check(window.isVisible, "missing selection and caret silently hid active dictation")
check(!window.canBecomeKey && !window.isKeyWindow &&
      NSWorkspace.shared.frontmostApplication?.processIdentifier == foregroundBeforeFallback,
      "fallback recording panel stole editor focus")
check(NSScreen.screens.contains { $0.visibleFrame.contains(window.frame) }, "fallback panel is outside visible screens")
let fallbackOrigin = window.frame.origin
palette.position(caret: nil, clientLevel: 0)
check(window.frame.origin == fallbackOrigin, "missing geometry moved the fallback recording panel")
unreadableSession.update("兼容", final: false)
unreadableSession.convert()
check(unreadableSession.phase == .listening && !unreadableSession.error.isEmpty,
      "range-dependent edit failure stopped dictation")
unreadableSession.update("兼容听写", final: true)
palette.update(unreadableSession)
palette.position(caret: nil, clientLevel: 0)
check(unreadableClient.text.string == "原文兼容听写" && unreadableSession.phase == .dictated && window.isVisible,
      "missing ranges/geometry prevented dictation delivery or completion feedback")
palette.position(caret: newCaret, clientLevel: 0)
check(window.frame.origin.x == newCaret.minX, "restored caret did not replace fallback position")
palette.hide()
print("PASS no-selection/no-geometry dictation stays visible, retains focus and survives edit failure")
