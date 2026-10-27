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
for index in 0..<6 { precondition(find("strokeCandidate\(index)", in: bar) != nil, "missing candidate cell \(index)") }
for direction in ["up-left", "up", "up-right"] {
    precondition(find("strokePunctuation-\(direction)", in: bar) != nil, "missing punctuation cell \(direction)")
}
// Same 3×3 layout as the trial: the first candidate is the center cell.
let center = find("strokeCandidate0", in: bar)!, left = find("strokeCandidate1", in: bar)!
let comma = find("strokePunctuation-up-left", in: bar)!
precondition(left.frame.maxX < center.frame.minX && abs(left.frame.minY - center.frame.minY) < 0.01)
precondition(comma.frame.maxY < center.frame.minY, "punctuation must be the top row of the flipped grid")
precondition(left.layer?.borderColor == NSColor(srgbRed: 0x34 / 255, green: 0x5E / 255, blue: 0xE5 / 255, alpha: 1).cgColor,
             "keyboard/swipe choice must be outlined")

let screen = NSScreen.screens[0].visibleFrame.insetBy(dx: 6, dy: 6)
let size = StrokeCandidatePanel.menuSize
// Default: directly below the caret, centered on it.
let middle = NSRect(x: screen.midX, y: screen.midY, width: 1, height: 20)
palette.position(caret: middle, clientLevel: 0)
precondition(palette.isVisible && !palette.placedAboveCaret)
precondition(abs(panel.frame.maxY - (middle.minY - InputPaletteStyle.caretGap)) < 0.01, "menu must sit just below the caret")
precondition(abs(panel.frame.midX - middle.midX) < 0.5, "menu must be centered under the caret")
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

palette.selection(phase: "selecting", progress: 100, direction: "left", origin: [0, 0],
                  points: [[0, 0], [-2, 0], [-4, 0], [-6, 0]], selectDistance: 3)
precondition(left.layer?.backgroundColor == NSColor(srgbRed: 0xED / 255, green: 0xF2 / 255, blue: 1, alpha: 1).cgColor,
             "aimed candidate must be highlighted")
let hint = find("strokeSelectionHint", in: content) as! NSTextField
precondition(hint.stringValue == "选字模式 · 抬手确认")
content.layoutSubtreeIfNeeded()
let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds)!
content.cacheDisplay(in: content.bounds, to: bitmap)
if CommandLine.arguments.count > 1 {
    try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
}
palette.selection(phase: "idle", progress: 0, direction: "", origin: [], points: [], selectDistance: 3)
let trace = Mirror(reflecting: palette).children.first { $0.label == "trail" }!.value as! NSView
RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.8))
precondition(trace.alphaValue == 0, "completed stroke failed to fade")
palette.trace(points: [[0.5, 0.5], [0.65, 0.58]], finished: false, style: [:])
precondition(trace.alphaValue == 1, "new ink failed to replace faded stroke")
palette.hide()
precondition(!palette.needsPosition && !panel.isVisible)
print("PASS transparent stroke palette, focus behavior, ink fade")
print("PASS 3x3 menu below the caret, follows it, flips above and clamps at screen edges")
