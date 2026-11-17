// Render only this component; no target application, keyboard or mouse events.
import AppKit

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let palette = StrokeCandidatePanel()
palette.update(code: "一 丨 𠃍", candidates: ["同", "回", "口", "国", "日", "田"], selected: 1)
palette.trace(points: [[0.5, 0.5], [0.62, 0.5], [0.56, 0.7]], finished: true,
              style: ["color": "#082ACB", "line_width": 3.2, "hold_ms": 400, "fade_ms": 280])
let panel = Mirror(reflecting: palette).children.first { $0.label == "panel" }!.value as! NSPanel
precondition(!panel.canBecomeKey && !panel.canBecomeMain && panel.ignoresMouseEvents)
precondition(!panel.isOpaque && panel.backgroundColor.alphaComponent == 0 && !panel.hasShadow)
precondition(palette.needsPosition)
palette.position(caret: nil, clientLevel: 0)
precondition(!palette.isVisible, "missing caret must not be reported as a visible palette")

let content = panel.contentView!
func find(_ name: String, in view: NSView) -> NSView? {
    if view.identifier?.rawValue == name { return view }
    for child in view.subviews { if let found = find(name, in: child) { return found } }
    return nil
}
let bar = find("strokeCandidateBar", in: content)!
for index in 0..<5 { precondition(find("strokeCandidate\(index)", in: bar) != nil, "missing candidate cell \(index)") }
for direction in ["up-left", "up", "up-right"] {
    precondition(find("strokePunctuation-\(direction)", in: bar) != nil, "missing punctuation cell \(direction)")
}
// Same 3×3 layout as the trial: the first candidate is the center cell.
let center = find("strokeCandidate0", in: bar)!, right = find("strokeCandidate1", in: bar)!
let delete = find("strokePunctuation-left", in: bar)!
let comma = find("strokePunctuation-up-left", in: bar)!
precondition(right.frame.minX > center.frame.maxX && abs(right.frame.minY - center.frame.minY) < 0.01)
precondition(delete.frame.maxX < center.frame.minX)
precondition(comma.frame.maxY < center.frame.minY, "punctuation must be the top row of the flipped grid")
precondition(right.layer?.borderColor == NSColor(srgbRed: 0x34 / 255, green: 0x5E / 255, blue: 0xE5 / 255, alpha: 1).cgColor,
             "keyboard/swipe choice must be outlined")

let screen = NSScreen.screens[0].visibleFrame.insetBy(dx: 6, dy: 6)
let size = StrokeCandidatePanel.menuSize
// Default: directly below the caret, centered on it.
let middle = NSRect(x: screen.midX.rounded(), y: screen.midY.rounded(), width: 2, height: 20)
palette.position(caret: middle, clientLevel: 0)
precondition(palette.isVisible && !palette.placedAboveCaret)
precondition(abs(panel.frame.maxY - (middle.minY - InputPaletteStyle.caretGap)) < 0.01, "menu must sit just below the caret")
precondition(abs(panel.frame.minX + bar.frame.midX - middle.midX) < 0.5, "grid must stay centered under the caret")
precondition(abs(bar.frame.minY) < 0.01, "grid must be the top part of the menu below the caret")
// Following: a moved caret moves the menu.
let moved = middle.offsetBy(dx: 40, dy: -30)
palette.position(caret: moved, clientLevel: 0)
precondition(abs(panel.frame.maxY - (moved.minY - InputPaletteStyle.caretGap)) < 0.01, "menu did not follow the caret")
// No room below: flip above the caret.
let low = NSRect(x: screen.midX, y: screen.minY + 20, width: 1, height: 20)
palette.position(caret: low, clientLevel: 0)
precondition(palette.placedAboveCaret && abs(panel.frame.minY - (low.maxY + InputPaletteStyle.caretGap)) < 0.01,
             "menu must flip above a caret near the bottom of the screen")
precondition(abs(bar.frame.minY - StrokeCandidatePanel.hintHeight) < 0.01, "grid must stay next to the caret when flipped")
// Horizontal edges are clamped inside the visible frame.
let edge = NSRect(x: screen.maxX - 2, y: screen.midY, width: 1, height: 20)
palette.position(caret: edge, clientLevel: 0)
precondition(panel.frame.maxX <= screen.maxX + 0.01 && panel.frame.minX >= screen.minX - 0.01)
let (origin, above) = StrokeCandidatePanel.placement(caret: NSRect(x: 0, y: 10, width: 1, height: 20),
    bounds: NSRect(x: 0, y: 0, width: 800, height: 200), size: size, gap: 4)
precondition(origin.y == 0 && origin.x == 0 && above, "a screen too short for either side must clamp to its edge")

let hint = find("strokeSelectionHint", in: content) as! NSTextField
let exitHint = find("strokeExitHint", in: content) as! NSTextField
let clearPull = find("strokeClearPull", in: content)!
let clearFill = find("strokeClearPullFill", in: content)!
precondition(clearPull.isHidden && clearPull.frame.minX >= 6)
precondition(abs(clearPull.frame.maxX - (bar.frame.minX + delete.frame.minX)) < 0.01)
precondition(exitHint.stringValue == "双指双击退出" && exitHint.font!.pointSize < hint.font!.pointSize)
precondition(!exitHint.isHidden && !exitHint.frame.intersects(bar.frame), "exit reminder must stay outside the writing grid")
palette.selection(phase: "selecting", progress: 0, direction: "", origin: [], points: [], selectDistance: 6)
precondition(bar.layer?.borderWidth == 3 && hint.stringValue == "已识别双指 · 滑动选字")
let selectionBorder = bar.layer?.borderColor
func screenshot(_ suffix: String) throws {
    guard CommandLine.arguments.count > 1 else { return }
    content.layoutSubtreeIfNeeded()
    let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds)!
    content.cacheDisplay(in: content.bounds, to: bitmap)
    // Preview on an editor's light surface so transparent gray footer text is visible.
    let preview = NSImage(size: content.bounds.size)
    preview.lockFocus()
    NSColor.white.setFill(); content.bounds.fill()
    let captured = NSImage(size: content.bounds.size)
    captured.addRepresentation(bitmap)
    captured.draw(in: content.bounds, from: .zero, operation: .sourceOver, fraction: 1)
    preview.unlockFocus()
    let rendered = NSBitmapImageRep(data: preview.tiffRepresentation!)!
    try rendered.representation(using: .png, properties: [:])!.write(to:
        URL(fileURLWithPath: CommandLine.arguments[1] + "-" + suffix + ".png"))
}
try screenshot("two-finger")
palette.selection(phase: "selecting", progress: 100, direction: "left", origin: [0, 0],
                  points: [[0, 0], [-2, 0], [-4, 0], [-6, 0]], selectDistance: 6)
let stationaryWindow = panel.frame, stationaryGrid = bar.frame
content.layoutSubtreeIfNeeded()
precondition(!clearPull.isHidden && clearFill.frame.width == 0 && clearPull.layer?.shadowOpacity == 0)
precondition(delete.layer?.backgroundColor == NSColor(srgbRed: 0xC2 / 255, green: 0x41 / 255, blue: 0x3B / 255, alpha: 1).cgColor,
             "delete must turn red")
precondition(bar.layer?.borderWidth == 3 && bar.layer?.borderColor != selectionBorder)
precondition(hint.stringValue == "抬手删除 · 继续外拉可清空")
try screenshot("delete")
let deleteBorder = bar.layer?.borderColor
palette.selection(phase: "selecting", progress: 100, direction: "left", origin: [0, 0],
                  points: [[0, 0], [-6, 0], [-18, 0], [-24, 0]], selectDistance: 6, clearProgress: 0.5)
content.layoutSubtreeIfNeeded()
precondition(abs(clearFill.frame.width - clearPull.frame.width / 2) < 0.01)
precondition(abs(clearFill.frame.maxX - clearPull.frame.width) < 0.01, "fill must advance from right to left")
try screenshot("pulling")
palette.selection(phase: "selecting", progress: 100, direction: "left", origin: [0, 0],
                  points: [[0, 0], [-6, 0], [-18, 0], [-30, 0]], selectDistance: 6,
                  clearArmed: true, clearDistance: 30)
precondition(hint.stringValue == "抬手清空 · 拉回取消" && delete.accessibilityLabel() == "清空笔画")
precondition(exitHint.stringValue == "双指双击退出" && !exitHint.isHidden)
precondition(bar.layer?.borderColor != deleteBorder)
precondition(hint.intrinsicContentSize.width <= hint.frame.width, "clear hint must fit")
content.layoutSubtreeIfNeeded()
precondition(clearFill.frame.width == clearPull.frame.width && clearPull.layer!.shadowOpacity > 0)
precondition(panel.frame == stationaryWindow && bar.frame == stationaryGrid, "clear affordance moved the grid")
try screenshot("clear")
palette.selection(phase: "selecting", progress: 100, direction: "left", origin: [0, 0],
                  points: [[0, 0], [-30, 0], [-18, 0]], selectDistance: 6)
precondition(hint.stringValue == "抬手删除 · 继续外拉可清空" && delete.accessibilityLabel() == "删除一笔或退格")
content.layoutSubtreeIfNeeded()
precondition(clearFill.frame.width == 0 && clearPull.layer?.shadowOpacity == 0)
// Leave space for the pull tab even at the left screen edge, without a gesture-time jump.
palette.position(caret: NSRect(x: screen.minX, y: screen.midY, width: 1, height: 20), clientLevel: 0)
precondition(panel.frame.minX + clearPull.frame.minX >= screen.minX)
precondition(panel.frame.maxX <= screen.maxX)
palette.selection(phase: "idle", progress: 0, direction: "", origin: [], points: [], selectDistance: 3)
precondition(bar.layer?.borderWidth == 0.5 && exitHint.stringValue == "双指双击退出" && clearPull.isHidden)
try screenshot("writing")
palette.update(code: "", candidates: ["好"], selected: 0)
palette.selection(phase: "selecting", progress: 100, direction: "left", origin: [0, 0],
                  points: [[0, 0], [-24, 0]], selectDistance: 6, clearProgress: 0.5)
precondition(hint.stringValue == "抬手退格 · 外拉删除前文")
precondition(clearPull.accessibilityLabel() == "继续向左外拉删除前文")
try screenshot("pull-delete-before")
palette.selection(phase: "selecting", progress: 100, direction: "left", origin: [0, 0],
                  points: [[0, 0], [-30, 0]], selectDistance: 6, clearArmed: true)
precondition(hint.stringValue == "抬手删除前文 · 拉回取消" && delete.accessibilityLabel() == "删除前文")
precondition(clearPull.accessibilityLabel() == "抬手删除前文")
precondition(hint.intrinsicContentSize.width <= hint.frame.width, "delete-before hint must fit")
precondition(panel.frame.size == stationaryWindow.size && bar.frame == stationaryGrid)
try screenshot("delete-before")
palette.correction(enabled: true, text: "清华大学人机交互研究实验室", message: "", busy: false, canUndo: false)
palette.position(caret: middle, clientLevel: 0)
let correctionWindow = panel.frame, correctionGrid = bar.frame
let correctionPull = find("strokeCorrectionPull", in: content)!
let correctionFill = find("strokeCorrectionPullFill", in: content)!
let scope = find("strokeCorrectionScope", in: content) as! NSTextField
precondition(scope.stringValue == "修正用词「清华大学人机交互研究实验室」" && !scope.isHidden)
palette.selection(phase: "selecting", progress: 100, direction: "right", origin: [0, 0],
                  points: [[0, 0], [18, 0], [24, 0]], selectDistance: 6, correctionProgress: 0.5)
content.layoutSubtreeIfNeeded()
precondition(!correctionPull.isHidden && correctionFill.frame.width == correctionPull.frame.width / 2)
precondition(correctionFill.frame.minX == 0, "right pull must fill from left to right")
precondition(panel.frame == correctionWindow && bar.frame == correctionGrid, "right pull moved the grid")
precondition(!scope.frame.intersects(bar.frame), "word scope overlapped the candidate grid")
precondition(!palette.placedAboveCaret && scope.frame.minY == 0 && scope.frame.maxY <= bar.frame.minY,
             "below the caret, the handwriting scope must precede the grid")
precondition(scope.backgroundColor == NSColor(srgbRed: 233 / 255, green: 239 / 255, blue: 1, alpha: 1), "word highlighted before reaching correction")
try screenshot("correction-pulling")
palette.selection(phase: "selecting", progress: 100, direction: "right", origin: [0, 0],
                  points: [[0, 0], [18, 0], [30, 0]], selectDistance: 6, correctionArmed: true)
content.layoutSubtreeIfNeeded()
precondition(correctionFill.frame.width == correctionPull.frame.width && correctionPull.layer!.shadowOpacity > 0)
precondition(hint.stringValue == "抬手修正前文 · 拉回取消" && correctionPull.accessibilityLabel() == "抬手修正前文")
precondition(scope.textColor == .white && scope.backgroundColor == NSColor(srgbRed: 35 / 255, green: 76 / 255, blue: 219 / 255, alpha: 1),
             "the entire confirmed handwriting must highlight at the right endpoint")
try screenshot("correction-armed")
palette.selection(phase: "selecting", progress: 100, direction: "right", origin: [0, 0],
                  points: [[0, 0], [30, 0], [24, 0]], selectDistance: 6, correctionProgress: 0.5)
precondition(scope.textColor != .white && scope.backgroundColor == NSColor(srgbRed: 233 / 255, green: 239 / 255, blue: 1, alpha: 1),
             "pulling back must remove the handwriting highlight")
palette.selection(phase: "selecting", progress: 100, direction: "right", origin: [0, 0],
                  points: [[0, 0], [30, 0]], selectDistance: 6, correctionArmed: true)
palette.correction(enabled: true, text: String(repeating: "这是需要完整显示的手写词", count: 5), message: "", busy: false, canUndo: false)
palette.position(caret: low, clientLevel: 0)
content.layoutSubtreeIfNeeded()
precondition(palette.placedAboveCaret && scope.frame.height > 60 && scope.frame.minY >= bar.frame.maxY
             && scope.frame.maxY == content.bounds.height, "above the caret, long scope must wrap below the grid next to the input")
try screenshot("correction-long-scope")
palette.selection(phase: "idle", progress: 0, direction: "", origin: [], points: [], selectDistance: 6)
palette.correction(enabled: true, text: "", message: "青华 → 清华 · 左滑撤销", busy: false, canUndo: true)
palette.position(caret: middle, clientLevel: 0)
precondition(scope.stringValue == "青华 → 清华 · 左滑撤销" && !scope.isHidden && hint.stringValue.isEmpty,
             "correction result and gesture undo hint must appear once")
try screenshot("correction-undo")
palette.update(code: "一", candidates: ["一"], selected: 0)
precondition(scope.isHidden && !hint.stringValue.contains("撤销"), "new ink must not advertise correction undo")
palette.correction(enabled: false, text: "", message: "", busy: false, canUndo: false)
precondition(correctionPull.isHidden && scope.isHidden, "disabled correction retained an affordance")
let trace = Mirror(reflecting: palette).children.first { $0.label == "trail" }!.value as! NSView
RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.8))
precondition(trace.alphaValue == 0, "completed stroke failed to fade")
palette.trace(points: [[0.5, 0.5], [0.65, 0.58]], finished: false, style: [:])
precondition(trace.alphaValue == 1, "new ink failed to replace faded stroke")
palette.hide()
precondition(!palette.needsPosition && !panel.isVisible)
print("PASS transparent stroke palette, focus behavior, ink fade")
print("PASS 3x3 menu below the caret, follows it, flips above and clamps at screen edges")
