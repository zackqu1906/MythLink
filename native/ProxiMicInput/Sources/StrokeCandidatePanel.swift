import AppKit

private final class StrokePalette: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

private final class StrokeTraceView: NSView {
    override var isFlipped: Bool { true }
    private var points: [NSPoint] = []
    private var standard = false
    private var ink = NSColor(red: 8 / 255, green: 42 / 255, blue: 203 / 255, alpha: 1)
    private var lineWidth: CGFloat = 3.2
    private var fade: Timer?

    func update(points values: [[Double]], finished: Bool, style: [String: Any]) {
        fade?.invalidate(); fade = nil
        points = Array(values.prefix(201)).compactMap { p in
            guard p.count == 2, p.allSatisfy({ $0.isFinite && (0...1).contains($0) }) else { return nil }
            return NSPoint(x: p[0], y: p[1])
        }
        standard = finished
        if let width = style["line_width"] as? Double, width.isFinite { lineWidth = min(8, max(1, width)) }
        if let color = style["color"] as? String, color.hasPrefix("#"), color.count == 7,
           let rgb = UInt32(color.dropFirst(), radix: 16) {
            ink = NSColor(red: CGFloat((rgb >> 16) & 255) / 255, green: CGFloat((rgb >> 8) & 255) / 255,
                          blue: CGFloat(rgb & 255) / 255, alpha: 1)
        }
        alphaValue = 1; needsDisplay = true
        guard finished, points.count > 1 else { return }
        let hold = min(2, max(0, (style["hold_ms"] as? Double ?? 400) / 1000))
        let duration = min(2, max(0.05, (style["fade_ms"] as? Double ?? 280) / 1000))
        let start = ProcessInfo.processInfo.systemUptime + hold
        fade = Timer.scheduledTimer(withTimeInterval: 1 / 60, repeats: true) { [weak self] timer in
            guard let self else { timer.invalidate(); return }
            let progress = min(1, max(0, (ProcessInfo.processInfo.systemUptime - start) / duration))
            self.alphaValue = pow(1 - progress, 3)
            if progress >= 1 { timer.invalidate(); self.fade = nil }
        }
    }

    override func draw(_ dirtyRect: NSRect) {
        guard points.count > 1, let context = NSGraphicsContext.current?.cgContext else { return }
        let p = points.map { NSPoint(x: $0.x * bounds.width, y: $0.y * bounds.height) }
        context.setStrokeColor(ink.cgColor); context.setLineWidth(lineWidth)
        context.setLineCap(.round); context.setLineJoin(.round)
        context.beginPath(); context.move(to: p[0])
        for i in 1..<(p.count - 1) {
            if standard { context.addLine(to: p[i]) }
            else { context.addQuadCurve(to: NSPoint(x: (p[i].x + p[i + 1].x) / 2,
                                                     y: (p[i].y + p[i + 1].y) / 2), control: p[i]) }
        }
        context.addLine(to: p[p.count - 1]); context.strokePath()
    }
}

/// Separate candidate cells provide the same blue selection as a native IME.
private final class StrokeCandidateCell: NSView {
    private let label = NSTextField(labelWithString: "")
    init(value: String, selected: Bool) {
        super.init(frame: .zero)
        label.stringValue = value
        label.font = .systemFont(ofSize: 16)
        label.textColor = selected ? .white : .labelColor
        label.alignment = .center
        label.setAccessibilityLabel(value)
        label.setAccessibilityValue(selected ? "已选中" : "")
        addSubview(label)
        wantsLayer = true
        layer?.cornerRadius = 4
        layer?.backgroundColor = selected ? InputPaletteStyle.selection.cgColor : NSColor.clear.cgColor
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override var intrinsicContentSize: NSSize {
        let size = label.intrinsicContentSize
        return NSSize(width: max(30, ceil(size.width) + 14), height: size.height + 6)
    }
    override func layout() {
        super.layout()
        let height = label.intrinsicContentSize.height
        label.frame = NSRect(x: 7, y: (bounds.height - height) / 2, width: bounds.width - 14, height: height)
    }
}

final class StrokeCandidatePanel {
    var exitGestureLabel = "Touchpad 双击"
    private let panel: NSPanel
    private let label = NSTextField(labelWithString: "")
    private let choices = InputPaletteSurface()
    private var candidateCells: [StrokeCandidateCell] = []
    private let trail = StrokeTraceView()
    private var anchor = ActionPanelAnchor()
    private var showing = false
    private var rowWidth: CGFloat = 200
    private let traceSize: CGFloat = 140
    private let traceLift: CGFloat = 24
    var isVisible: Bool { showing && panel.isVisible }
    var needsPosition: Bool { showing && (anchor.needsCaret || !panel.isVisible) }

    init() {
        panel = StrokePalette(contentRect: NSRect(x: 0, y: 0, width: 280, height: 44),
                              styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.ignoresMouseEvents = true
        let surface = NSView()
        choices.identifier = NSUserInterfaceItemIdentifier("strokeCandidateBar")
        label.font = .systemFont(ofSize: 15)
        label.textColor = .labelColor
        label.lineBreakMode = .byTruncatingTail
        choices.addSubview(label)
        surface.addSubview(choices)
        surface.addSubview(trail)
        panel.contentView = surface
    }

    func update(code: String, candidates: [String], selected: Int) {
        candidateCells.forEach { $0.removeFromSuperview() }
        candidateCells = candidates.prefix(5).enumerated().map { index, value in
            let cell = StrokeCandidateCell(value: value, selected: index == selected)
            cell.identifier = NSUserInterfaceItemIdentifier("strokeCandidate\(index)")
            choices.addSubview(cell)
            return cell
        }
        label.stringValue = code.isEmpty ? (candidates.isEmpty ? "笔画输入 · \(exitGestureLabel)退出" : "")
            : code.replacingOccurrences(of: "𠃍", with: "乛")
        label.isHidden = label.stringValue.isEmpty
        let labelWidth = label.isHidden ? 0 : min(240, ceil(label.intrinsicContentSize.width) + 8)
        label.frame = NSRect(x: 12, y: (InputPaletteStyle.rowHeight - label.intrinsicContentSize.height) / 2,
                             width: labelWidth, height: label.intrinsicContentSize.height)
        var x = label.isHidden ? 12 : label.frame.maxX + (candidateCells.isEmpty ? 0 : 12)
        for cell in candidateCells {
            let size = cell.intrinsicContentSize
            cell.frame = NSRect(x: x, y: (InputPaletteStyle.rowHeight - size.height) / 2,
                                width: size.width, height: size.height)
            x += size.width + 4
        }
        rowWidth = max(180, x + 8)
        anchor.update(utteranceID: "stroke", editing: false, currentSize: panel.frame.size)
        showing = true
    }

    func trace(points: [[Double]], finished: Bool, style: [String: Any]) {
        trail.update(points: points, finished: finished, style: style)
    }

    func position(caret: NSRect?, clientLevel: Int32) {
        guard showing, let caret = anchor.observe(caret),
              let screen = NSScreen.screens.first(where: { $0.frame.intersects(caret.insetBy(dx: -1, dy: -1)) }) else {
            panel.orderOut(nil)
            return
        }
        let bounds = screen.visibleFrame.insetBy(dx: 6, dy: 6)
        let gap = InputPaletteStyle.caretGap
        let below = max(0, caret.minY - gap - bounds.minY)
        let above = max(0, bounds.maxY - caret.maxY - gap)
        let aboveCaret = below < InputPaletteStyle.rowHeight && above >= below
        let available = aboveCaret ? above : below
        let height = max(InputPaletteStyle.rowHeight, min(traceSize, available))
        // Anchor the row independently, then lift the right-hand canvas. Using
        // their union keeps the entire trace inside the window without moving
        // the candidate row away from the caret.
        let rowY = aboveCaret ? caret.maxY + gap : caret.minY - InputPaletteStyle.rowHeight - gap
        let row = NSRect(x: 0, y: min(max(rowY, bounds.minY), bounds.maxY - InputPaletteStyle.rowHeight),
                         width: rowWidth, height: InputPaletteStyle.rowHeight)
        let traceY = (aboveCaret ? row.minY : row.maxY - height) + traceLift
        let canvas = NSRect(x: rowWidth + 6, y: min(max(traceY, bounds.minY), bounds.maxY - height),
                            width: height, height: height)
        let union = row.union(canvas)
        panel.setContentSize(union.size)
        choices.frame = row.offsetBy(dx: 0, dy: -union.minY)
        trail.frame = canvas.offsetBy(dx: 0, dy: -union.minY)
        let x = min(max(caret.minX, bounds.minX), max(bounds.minX, bounds.maxX - union.width))
        panel.setFrameOrigin(anchor.place(NSPoint(x: x, y: union.minY), size: union.size))
        panel.level = NSWindow.Level(rawValue: max(Int(clientLevel) + 1, NSWindow.Level.popUpMenu.rawValue))
        panel.orderFrontRegardless()
    }

    func hide() {
        showing = false
        panel.orderOut(nil)
        anchor = ActionPanelAnchor()
        trail.update(points: [], finished: false, style: [:])
    }
}
