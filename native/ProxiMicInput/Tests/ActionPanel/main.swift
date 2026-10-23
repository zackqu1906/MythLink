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
for name in VoiceHudBindings.names.keys.map({ VoiceHudBindings.assetName($0) }) + ["listening-waveform", "processing-spinner", "processing-dots", "status-done", "status-error", "function-cancel", "function-edit", "function-polish"] {
    check(VoiceHudAssets.image(name)?.isValid == true, "missing/unreadable HUD asset: \(name)")
}
guard let cancelHint = find(content, "voiceHint_undo") as? VoiceHudHint,
      let editHint = find(content, "voiceHint_switch_mode") as? VoiceHudHint,
      let polishHint = find(content, "voiceHint_polish") as? VoiceHudHint,
      let dots = find(content, "voiceProcessingDots") as? VoiceHudProgress,
      let spinner = find(content, "voiceProcessingSpinner") as? VoiceHudSpinner,
      let hudContent = find(content, "voiceHudContent") else { fatalError("missing separate hint controls") }
check(content.bounds.height == 32, "default palette is not compact")
check(!window.hasShadow && content.layer?.backgroundColor?.alpha == 0.68,
      "voice palette does not share the translucent stroke surface")
check(cancelHint.gestures == ["swipe-left"] && editHint.gestures == ["swipe-right"], "wrong default mapping")
check(find(content, "voiceHint_confirm") == nil, "introduced a Tap/confirmation hint that was not in the existing panel")
check(cancelHint.title.isEmpty && editHint.title.isEmpty && polishHint.title.isEmpty, "function captions are still visible")
check(polishHint.gestures == ["swipe-down"] && polishHint.functionAsset == "function-polish" && polishHint.action == nil,
      "polish must remain a down-swipe hint routed by the host")

func checkActionFit(_ count: Int, scale: CGFloat = 0.8) {
    content.layoutSubtreeIfNeeded()
    let visible = [cancelHint, editHint, polishHint].filter { !$0.isHidden }
    check(visible.count == count, "fixture has the wrong visible action count")
    check(abs(content.bounds.height - 40 * scale) < 0.01, "action count changed the configured height")
    if let last = visible.last {
        let right = last.convert(last.bounds, to: content).maxX
        check(abs(content.bounds.maxX - right - 14 * scale) < 1,
              "hidden action left unused space at the right edge")
    } else {
        check(find(content, "voiceGestureHints")?.isHidden == true, "empty actions still occupy a slot")
        check(abs(content.bounds.width - 210 * scale) < 1, "zero actions kept the action gap")
    }
}

// Exercise the real panel/session transition: editRequested intentionally stays
// true after success and must not keep the result anchored to the instruction.
final class PanelTextClient: CompositionClient {
    let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 160))
    init() { text.string = "原文"; text.setSelectedRange(NSRange(location: 2, length: 0)) }
    func snapshot() throws -> EditorSnapshot {
        EditorSnapshot(text: text.string, selection: text.selectedRange(), markedRange: text.markedRange(),
            selectedText: (text.string as NSString).substring(with: text.selectedRange()), documentAccess: true)
    }
    func probe() -> CompositionProbe {
        let range = text.markedRange()
        return CompositionProbe(selection: text.selectedRange(), markedRange: range,
            markedText: isValidRange(range, length: (text.string as NSString).length) ? (text.string as NSString).substring(with: range) : nil)
    }
    func selection() -> NSRange { text.selectedRange() }
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
reordered.apply(["undo": ["swipe-right", "snap"], "confirm": ["swipe-left"], "switch_mode": ["tap"]])
palette.configureGestureHints(reordered)
check(cancelHint.gestures == ["swipe-right", "snap"] && cancelHint.functionAsset == "function-cancel" && editHint.functionAsset == "function-edit" && editHint.gestures == ["tap"], "gesture and function cannot be recombined")
check(polishHint.gestures == ["swipe-down"], "display remapping changed the reserved down-swipe hint")
check(session.phase == snapshot.0 && session.revision == snapshot.1 && session.raw == snapshot.2, "display configuration changed the transaction")
try capture("custom-bindings")
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
let learnedState = (session.phase, session.revision, session.raw, client.text.string)
palette.showLearnedHotwords(["张玮"])
check(label.stringValue == "已收录 · 张玮" && label.toolTip?.contains("从下一句") == true, "missing automatic vocabulary notice")
check(!cancelHint.isHidden && spinner.isHidden && !window.canBecomeKey, "vocabulary notice took focus or removed undo")
check(session.phase == learnedState.0 && session.revision == learnedState.1 && session.raw == learnedState.2 && client.text.string == learnedState.3,
      "vocabulary notice changed text or transaction")
try capture("hotwords-learned")
palette.showLearnedHotwords(["张玮", "Kubernetes"])
check(label.stringValue == "已收录 2 个热词" && label.toolTip?.contains("Kubernetes") == true, "multiple words overflowed notice or lost detail")
palette.update(session)
check(!label.stringValue.contains("收录"), "new presentation retained old vocabulary notice")
check(palette.needsPosition, "completed edit flag still blocks position refresh")
palette.position(caret: newCaret, clientLevel: 0)
check(window.frame.origin.x == newCaret.minX && window.frame.origin != oldOrigin,
      "result panel stayed at the old preedit caret")
session.cancel(); palette.update(session)
check(session.phase == .dictated && palette.needsPosition, "undo did not resume positioning")
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
print("PASS icon-only defaults, remapping, original hint visibility and mouse callbacks preserve composition")
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
