import AppKit

func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}
_ = NSApplication.shared
NSApp.setActivationPolicy(.accessory)
let board = NSPasteboard(name: .init("mythlink.clipboard-suggestion.tests.\(UUID().uuidString)"))
defer { board.releaseGlobally() }
board.clearContents()
let long = String(repeating: "这是一段很长的剪贴板文字，包含团队新闻和下一步安排。👨‍👩‍👧‍👦\n", count: 30)
check(board.setString(long, forType: .string), "test pasteboard unavailable")
let revision = board.changeCount
let preview = ClipboardPreview.read(board, revision: revision)!
check(preview.text.count == 160 && preview.text.hasSuffix("…"), "text preview not bounded")
check(board.string(forType: .string) == long && board.changeCount == revision, "preview altered the clipboard")
check(ClipboardPreview.read(board, revision: revision - 1) == nil, "stale revision was displayed")

let palette = ClipboardSuggestionPanel()
var visibility: [(String, Bool)] = []
palette.onVisibility = { visibility.append(($0, $1)) }
let screen = NSScreen.screens[0].visibleFrame
let caret = NSRect(x: screen.midX, y: screen.midY, width: 1, height: 20)
check(palette.show(preview, token: "text", caret: caret), "text palette did not show")
check(!palette.panel.canBecomeKey && !palette.panel.canBecomeMain && palette.panel.ignoresMouseEvents,
      "clipboard panel steals typing or pointer focus")
check(palette.panel.frame.width <= 280 && palette.panel.frame.height <= 100, "text preview too large")
let content = palette.panel.contentView!
let label = content.subviews.first { $0.identifier?.rawValue == "clipboardPreviewText" } as! NSTextField
check(label.maximumNumberOfLines == 3 && label.cell?.truncatesLastVisibleLine == true, "text needs visual line limit")
let labels = content.subviews.compactMap { $0 as? NSTextField }.filter { !$0.isHidden }
check(labels.count == 2 && labels.last?.stringValue == "右滑接受 →", "palette has extra hints")
let directory = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MYTHLINK_SCREENSHOT_DIR"] ?? "/private/tmp/mythlink-clipboard-preview")
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
func capture(_ name: String) throws {
    content.layoutSubtreeIfNeeded()
    let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds)!
    content.cacheDisplay(in: content.bounds, to: bitmap)
    try bitmap.representation(using: .png, properties: [:])!.write(to: directory.appendingPathComponent(name + ".png"))
}
try capture("text")
let longSize = palette.panel.frame.size
palette.show(ClipboardPreview(text: "你好", image: nil), token: "short", caret: caret)
check(palette.panel.frame.width < longSize.width && palette.panel.frame.height < longSize.height,
      "short text kept empty space from long text")
try capture("short-text")
palette.show(preview, token: "text", caret: caret)
palette.hide(token: "stale")
check(palette.panel.isVisible, "old hide dismissed a new offer")
palette.hide(token: "text")
check(!palette.panel.isVisible && visibility.last?.1 == false, "dismissal lost acknowledgement")

let image = NSImage(size: NSSize(width: 1200, height: 800), flipped: false) { rect in
    NSColor(srgbRed: 0.12, green: 0.22, blue: 0.43, alpha: 1).setFill(); rect.fill()
    let heading = "AI 科技新闻"
    heading.draw(at: NSPoint(x: 80, y: 600), withAttributes: [.font: NSFont.boldSystemFont(ofSize: 76), .foregroundColor: NSColor.white])
    for row in 0..<3 {
        NSColor(srgbRed: 0.4, green: 0.7, blue: 0.95, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 80, y: 140 + row * 130, width: 1040 - row * 180, height: 70), xRadius: 16, yRadius: 16).fill()
    }
    return true
}
board.clearContents(); board.writeObjects([image])
let imagePreview = ClipboardPreview.read(board, revision: board.changeCount)!
check(imagePreview.image != nil && imagePreview.text.isEmpty, "missing image preview")
check(imagePreview.image!.size.width <= 240 && imagePreview.image!.size.height <= 144, "image preview not downsampled")
palette.show(imagePreview, token: "image", caret: caret)
check(palette.panel.frame.width <= 264 && palette.panel.frame.height <= 190, "image preview too large")
try capture("image")
let landscape = NSImage(size: NSSize(width: 240, height: 40))
palette.show(ClipboardPreview(text: "", image: landscape), token: "wide", caret: caret)
let wideSize = palette.panel.frame.size
check(wideSize.height == 86, "wide image kept excess vertical space")
let portrait = NSImage(size: NSSize(width: 40, height: 144))
palette.show(ClipboardPreview(text: "", image: portrait), token: "tall", caret: caret)
check(palette.panel.frame.width < wideSize.width && palette.panel.frame.height > wideSize.height,
      "portrait did not adapt its window dimensions")
palette.show(imagePreview, token: "timeout", caret: caret, duration: 0.03)
RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
check(!palette.panel.isVisible && visibility.last?.0 == "timeout" && visibility.last?.1 == false,
      "automatic expiry failed")
board.clearContents(); board.setString("temporary internal text", forType: .string)
board.setString("1", forType: .init("application/x-mythlink-internal-clipboard"))
check(ClipboardPreview.read(board, revision: board.changeCount) == nil, "internal clipboard leaked into suggestions")
print("PASS clipboard preview limits, native palette, image thumbnail, focus, revision, dismissal and expiry")

// Both a normal reply and a document taller than the display must be readable to their last character.
for count in [1200, 1, 8] {
    let candidate = String(repeating: "这是需要完整显示的一句文字，包含长串EnglishWithoutBreaks和👨‍👩‍👧‍👦。", count: count) + "末字"
    check(palette.show(ClipboardPreview(text: candidate, image: nil), token: "replacement", caret: caret,
                       duration: nil, replacement: true), "full replacement preview was dropped")
    guard let scroll = content.subviews.compactMap({ $0 as? NSScrollView }).first,
          let body = scroll.documentView as? NSTextView, let manager = body.layoutManager,
          let container = body.textContainer else { fatalError("missing complete replacement text layout") }
    manager.ensureLayout(for: container)
    let last = manager.glyphIndexForCharacter(at: (candidate as NSString).length - 1)
    let lastLine = manager.lineFragmentUsedRect(forGlyphAt: last, effectiveRange: nil)
    check(!palette.panel.canBecomeKey && screen.contains(palette.panel.frame), "replacement left screen or took focus")
    check(body.string == candidate && lastLine.maxY <= body.bounds.height + 1, "last line clipped")
    if count < 1200 {
        check(scroll.documentVisibleRect.contains(lastLine), "screen-fitting replacement concealed its last line")
    } else {
        check(!palette.panel.ignoresMouseEvents && scroll.hasVerticalScroller, "overflow preview cannot scroll: screen=\(screen.size) viewport=\(scroll.contentSize) document=\(body.frame.size) last=\(lastLine)")
        body.scrollRangeToVisible(NSRange(location: (candidate as NSString).length - 1, length: 1))
        check(scroll.documentVisibleRect.intersects(lastLine), "last line unreachable")
    }
}
print("PASS complete wrapped replacement and scrollable overflow through final glyph")
