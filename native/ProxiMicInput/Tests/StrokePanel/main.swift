// Render only this component; no target application, keyboard or mouse events.
import AppKit

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let palette = StrokeCandidatePanel()
palette.update(code: "一 丨 𠃍", candidates: ["同", "回", "口", "国", "日"], selected: 1)
palette.trace(points: [[0.2, 0.28], [0.8, 0.28], [0.51, 0.76]], finished: true,
              style: ["color": "#082ACB", "line_width": 3.2, "hold_ms": 400, "fade_ms": 280])
let panel = Mirror(reflecting: palette).children.first { $0.label == "panel" }!.value as! NSPanel
precondition(!panel.canBecomeKey && !panel.canBecomeMain && panel.ignoresMouseEvents)
precondition(!panel.isOpaque && panel.backgroundColor.alphaComponent == 0 && !panel.hasShadow)
precondition(palette.needsPosition)
palette.position(caret: nil, clientLevel: 0)
precondition(!palette.isVisible, "missing caret must not be reported as a visible palette")
let content = panel.contentView!
let bar = content.subviews.first { $0.identifier?.rawValue == "strokeCandidateBar" }!
let labels = bar.subviews.flatMap { view -> [NSTextField] in
    (view as? NSTextField).map { [$0] } ?? view.subviews.compactMap { $0 as? NSTextField }
}
precondition(labels.allSatisfy { !$0.stringValue.contains("【") && !$0.stringValue.contains("】") })
let selected = bar.subviews.first { $0.identifier?.rawValue == "strokeCandidate1" }!
precondition(selected.layer?.backgroundColor == InputPaletteStyle.selection.cgColor)
precondition((selected.subviews.first as? NSTextField)?.textColor == .white)
let screen = NSScreen.screens[0].visibleFrame
let lowCaret = NSRect(x: screen.midX - 120, y: screen.minY + 10, width: 1, height: 20)
palette.position(caret: lowCaret, clientLevel: 0)
precondition(bar.frame.height == 32)
let trace = Mirror(reflecting: palette).children.first { $0.label == "trail" }!.value as! NSView
precondition(abs(trace.frame.minY - bar.frame.minY - 24) < 0.01)
precondition(abs(panel.frame.minY + bar.frame.minY - lowCaret.maxY - 4) < 0.01,
             "above-caret candidate row was pushed away by the transparent ink canvas")
let nearBottom = NSRect(x: lowCaret.minX, y: screen.minY + 75, width: 1, height: 20)
palette.position(caret: nearBottom, clientLevel: 0)
precondition(abs(panel.frame.minY + bar.frame.maxY - nearBottom.minY + 4) < 0.01,
             "candidate row moved away when only the ink canvas ran out of screen space")
precondition(panel.frame.minY >= screen.minY && panel.frame.height < 140)
palette.position(caret: NSRect(x: lowCaret.minX, y: screen.midY, width: 1, height: 20), clientLevel: 0)
precondition(abs(trace.frame.maxY - bar.frame.maxY - 24) < 0.01,
             "the right-hand trace must begin 24 pt higher without moving the candidate row")
content.layoutSubtreeIfNeeded()
let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds)!
content.cacheDisplay(in: content.bounds, to: bitmap)
if CommandLine.arguments.count > 1 {
    try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
}
RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.8))
precondition(trace.alphaValue == 0, "completed canonical stroke failed to fade")
palette.trace(points: [[0.5, 0.5], [0.65, 0.58]], finished: false, style: [:])
precondition(trace.alphaValue == 1, "new ink failed to replace faded stroke")
palette.hide()
precondition(!palette.needsPosition && !panel.isVisible)
print("PASS transparent stroke palette, focus behavior, shared shape and fade")
print("PASS compact blue selection and candidate-row anchoring above/below caret")
