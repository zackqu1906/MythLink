import AppKit

private final class StrokePalette: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

private class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

private extension NSColor {
    convenience init(hex: UInt32, alpha: CGFloat = 1) {
        self.init(srgbRed: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
                  blue: CGFloat(hex & 255) / 255, alpha: alpha)
    }
}

/// Handwriting ink over the menu. Like the in-app trial, the ink is centered
/// on its bounding box and a finished stroke fades out.
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
            ink = NSColor(hex: rgb)
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
        let xs = points.map(\.x), ys = points.map(\.y)
        let dx = 0.5 - (xs.min()! + xs.max()!) / 2, dy = 0.5 - (ys.min()! + ys.max()!) / 2
        let p = points.map { NSPoint(x: ($0.x + dx) * bounds.width, y: ($0.y + dy) * bounds.height) }
        context.setAlpha(0.42)
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

/// Hold-to-select path, projected onto the menu cells exactly as the trial
/// window does (stroke_trial_trace.selection_point).
private final class StrokeSelectionPathView: NSView {
    override var isFlipped: Bool { true }
    var cells: [String: NSRect] = [:]
    private var origin: [Double] = []
    private var points: [[Double]] = []
    private var direction = ""
    private var threshold = 3.0
    static let sectors = ["right", "down-right", "down", "down-left", "left", "up-left", "up", "up-right"]

    func update(origin: [Double], points: [[Double]], direction: String, threshold: Double) {
        self.origin = origin.count == 2 && origin.allSatisfy(\.isFinite) ? origin : []
        self.points = Array(points.prefix(201)).filter { $0.count == 2 && $0.allSatisfy(\.isFinite) }
        self.direction = direction
        self.threshold = threshold.isFinite && threshold > 0 ? threshold : 3
        needsDisplay = true
    }

    func project(_ point: [Double], direction fixed: String?) -> NSPoint? {
        guard let center = cells["center"] else { return nil }
        let dx = point[0] - origin[0], dy = point[1] - origin[1]
        let distance = hypot(dx, dy)
        if distance < threshold {
            let radius = Double(min(center.width, center.height)) * 0.36
            return NSPoint(x: Double(center.midX) + dx / threshold * radius, y: Double(center.midY) + dy / threshold * radius)
        }
        var name = fixed ?? ""
        if name.isEmpty {
            let degrees = atan2(dy, dx) * 180 / .pi + 22.5
            let wrapped = (degrees.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360)
            name = Self.sectors[Int(wrapped / 45) % 8]
        }
        guard let target = cells[name] else { return NSPoint(x: center.midX, y: center.midY) }
        let vx = Double(target.midX - center.midX), vy = Double(target.midY - center.midY)
        let gain = 0.72 + 0.24 * (1 - exp(-(distance / threshold - 1) / 4))
        let length = max(1e-6, hypot(vx, vy))
        let lateral = Double(min(target.width, target.height)) * 0.22
        let x = Double(center.midX) + vx * gain + (dx / distance - vx / length) * lateral
        let y = Double(center.midY) + vy * gain + (dy / distance - vy / length) * lateral
        let inset = min(10, Double(target.width) * 0.15, Double(target.height) * 0.15)
        return NSPoint(x: max(Double(target.minX) + inset, min(Double(target.maxX) - inset, x)),
                       y: max(Double(target.minY) + inset, min(Double(target.maxY) - inset, y)))
    }

    override func draw(_ dirtyRect: NSRect) {
        guard !origin.isEmpty, !points.isEmpty, let center = cells["center"],
              let context = NSGraphicsContext.current?.cgContext else { return }
        let path = points.enumerated().compactMap { index, point in
            project(point, direction: index == points.count - 1 && !direction.isEmpty ? direction : nil)
        }
        guard let end = path.last else { return }
        let start = NSPoint(x: center.midX, y: center.midY)
        context.setAlpha(0.42)
        context.setStrokeColor(NSColor(hex: 0x234CDB).cgColor); context.setLineWidth(3)
        context.setLineCap(.round); context.setLineJoin(.round)
        context.beginPath(); context.move(to: start)
        path.forEach { context.addLine(to: $0) }
        context.strokePath()
        // The stable origin and live endpoint stay distinct over the glyphs.
        context.setAlpha(0.9); context.setLineWidth(2)
        context.setFillColor(NSColor.white.cgColor); context.setStrokeColor(NSColor(hex: 0x21A675).cgColor)
        context.addEllipse(in: NSRect(x: start.x - 4, y: start.y - 4, width: 8, height: 8))
        context.drawPath(using: .fillStroke)
        context.setFillColor(NSColor(hex: 0x345EE5).cgColor); context.setStrokeColor(NSColor.white.cgColor)
        context.addEllipse(in: NSRect(x: end.x - 6, y: end.y - 6, width: 12, height: 12))
        context.drawPath(using: .fillStroke)
    }
}

/// One menu cell: punctuation on the top row, a candidate glyph below.
private final class StrokeMenuCell: FlippedView {
    let direction: String
    let punctuation: Bool
    private let arrow = NSTextField(labelWithString: "")
    private let rank = NSTextField(labelWithString: "")
    private let label = NSTextField(labelWithString: "")
    private let image = NSImageView()
    private let status = NSTextField(labelWithString: "")
    private(set) var character = ""
    private var chosen = false
    private var active = false

    init(direction: String, arrow value: String, rank number: Int?, symbol: String = "", title: String = "") {
        self.direction = direction
        punctuation = number == nil
        super.init(frame: .zero)
        identifier = NSUserInterfaceItemIdentifier(punctuation ? "strokePunctuation-\(direction)" : "strokeCandidate\(number! - 1)")
        wantsLayer = true
        layer?.cornerRadius = 10
        arrow.stringValue = value
        rank.stringValue = number.map { String($0) } ?? ""
        rank.alignment = .right
        label.alignment = .center
        label.font = punctuation ? .systemFont(ofSize: 30) : (NSFont(name: "Kaiti SC", size: 44) ?? .systemFont(ofSize: 44))
        label.stringValue = symbol
        status.alignment = .center
        status.lineBreakMode = .byTruncatingTail
        status.stringValue = title
        [arrow, rank, status].forEach { $0.font = .systemFont(ofSize: 10) }
        image.imageScaling = .scaleProportionallyUpOrDown
        ([arrow, rank, image, label, status] as [NSView]).forEach { addSubview($0) }
        refresh()
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func set(character value: String, glyph: [String: String]?, chosen isChosen: Bool) {
        character = value
        chosen = isChosen
        label.stringValue = value.isEmpty ? "—" : value
        image.image = nil
        label.isHidden = false
        if let glyph, glyph["character"] == value, let png = glyph["png"], png.count < 200_000,
           let data = Data(base64Encoded: png), let decoded = NSImage(data: data),
           decoded.size.width <= 512, decoded.size.height <= 512 {
            image.image = decoded
            label.isHidden = true
        }
        let next = glyph?["next"] ?? ""
        status.stringValue = value.isEmpty ? "等待输入"
            : String((glyph?["status"] ?? "候选").prefix(16)) + (next.isEmpty ? "" : " · 下一笔 " + next)
        setAccessibilityLabel(value.isEmpty ? "暂无候选" : "第\(rank.stringValue)候选 \(value)")
        refresh()
    }

    func set(active value: Bool) {
        guard value != active else { return }
        active = value
        refresh()
    }

    private func refresh() {
        let enabled = punctuation || !character.isEmpty
        let primary = !punctuation && rank.stringValue == "1" && enabled
        let background: NSColor = punctuation
            ? (active ? NSColor(hex: 0x234CDB) : NSColor(hex: 0xEDF2FB))
            : (active ? NSColor(hex: 0xEDF2FF) : primary ? NSColor(hex: 0xF0F4FF) : .white)
        let border: NSColor = active ? (punctuation ? NSColor(hex: 0x1739AC) : NSColor(hex: 0x345EE5))
            : chosen && enabled ? NSColor(hex: 0x345EE5) : primary ? NSColor(hex: 0xAABCF2) : NSColor(hex: 0xDCE4F0)
        layer?.backgroundColor = background.cgColor
        layer?.borderColor = border.cgColor
        layer?.borderWidth = active || chosen || primary ? 2 : 1
        let ink: NSColor = punctuation ? (active ? .white : NSColor(hex: 0x647895))
            : active ? NSColor(hex: 0x345EE5) : enabled ? NSColor(hex: 0x71809A) : NSColor(hex: 0xA7B0C0)
        [arrow, rank, status].forEach { $0.textColor = ink }
        label.textColor = punctuation ? ink : enabled ? NSColor(hex: 0x24334D) : NSColor(hex: 0xC3CBD7)
        needsLayout = true
    }

    override func layout() {
        super.layout()
        arrow.frame = NSRect(x: 7, y: 4, width: 30, height: 14)
        rank.frame = NSRect(x: bounds.width - 27, y: 4, width: 20, height: 14)
        status.frame = NSRect(x: 3, y: bounds.height - 17, width: bounds.width - 6, height: 14)
        let side = max(16, min(bounds.width - 16, bounds.height - 38))
        let glyph = NSRect(x: (bounds.width - side) / 2, y: 19 + (bounds.height - 38 - side) / 2, width: side, height: side)
        image.frame = glyph
        let height = label.intrinsicContentSize.height
        label.frame = NSRect(x: 2, y: glyph.midY - height / 2, width: bounds.width - 4, height: height)
    }
}

/// The in-app trial's 3×3 menu, shown next to the caret in other applications.
/// Pending strokes stay in the client's marked text; this palette only shows
/// choices and never takes focus or mouse input.
final class StrokeCandidatePanel {
    static let cellSize: CGFloat = 78
    static let cellGap: CGFloat = 6
    static let inset: CGFloat = 8
    static let hintHeight: CGFloat = 24
    static var gridSide: CGFloat { inset * 2 + cellSize * 3 + cellGap * 2 }
    static var menuSize: NSSize { NSSize(width: gridSide, height: gridSide + hintHeight) }
    // Same order as stroke_menu.CANDIDATE_CELLS / PUNCTUATION_CELLS.
    static let candidateCells: [(String, Int, Int, String)] = [
        ("center", 1, 1, "首选"), ("left", 1, 0, "←"), ("right", 1, 2, "→"),
        ("down-left", 2, 0, "↙"), ("down", 2, 1, "↓"), ("down-right", 2, 2, "↘")]
    static let punctuationCells: [(String, Int, String, String, String)] = [
        ("up-left", 0, "↖", "，", "逗号"), ("up", 1, "↑", "。", "句号"), ("up-right", 2, "↗", "？", "问号")]

    var exitGestureLabel = "Touchpad 双击"
    private let panel: NSPanel
    private let content = FlippedView()
    private let hint = NSTextField(labelWithString: "")
    private let choices = InputPaletteSurface()
    private let grid = FlippedView()
    private var cells: [StrokeMenuCell] = []
    private let trail = StrokeTraceView()
    private let selectionPath = StrokeSelectionPathView()
    private var anchor = ActionPanelAnchor()
    private var showing = false
    private var code = ""
    private var hasCandidates = false
    private var phase = "idle"
    private var progress = 0.0
    private(set) var placedAboveCaret = false
    var isVisible: Bool { showing && panel.isVisible }
    var needsPosition: Bool { showing && (anchor.needsCaret || !panel.isVisible) }

    init() {
        panel = StrokePalette(contentRect: NSRect(origin: .zero, size: Self.menuSize),
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
        choices.identifier = NSUserInterfaceItemIdentifier("strokeCandidateBar")
        choices.cornerRadius = 14
        hint.font = .systemFont(ofSize: 11)
        hint.textColor = .secondaryLabelColor
        hint.lineBreakMode = .byTruncatingTail
        hint.identifier = NSUserInterfaceItemIdentifier("strokeSelectionHint")
        for (direction, column, arrow, symbol, title) in Self.punctuationCells {
            let cell = StrokeMenuCell(direction: direction, arrow: arrow, rank: nil, symbol: symbol, title: title)
            cell.frame = Self.cellFrame(row: 0, column: column)
            cells.append(cell)
        }
        for (index, (direction, row, column, arrow)) in Self.candidateCells.enumerated() {
            let cell = StrokeMenuCell(direction: direction, arrow: arrow, rank: index + 1)
            cell.frame = Self.cellFrame(row: row, column: column)
            cells.append(cell)
        }
        cells.forEach { grid.addSubview($0) }
        let gridFrame = NSRect(x: 0, y: 0, width: Self.gridSide, height: Self.gridSide)
        grid.frame = gridFrame
        trail.frame = gridFrame
        selectionPath.frame = gridFrame
        selectionPath.cells = Dictionary(uniqueKeysWithValues: cells.map { ($0.direction, $0.frame) })
        choices.frame = gridFrame
        choices.addSubview(grid)
        choices.addSubview(trail)
        choices.addSubview(selectionPath)
        content.frame = NSRect(origin: .zero, size: Self.menuSize)
        content.addSubview(choices)
        content.addSubview(hint)
        panel.contentView = content
        layoutHint()
    }

    private static func cellFrame(row: Int, column: Int) -> NSRect {
        NSRect(x: inset + CGFloat(column) * (cellSize + cellGap), y: inset + CGFloat(row) * (cellSize + cellGap),
               width: cellSize, height: cellSize)
    }

    /// Candidates of the current page (up to six); `selected` is the keyboard/swipe choice.
    func update(code: String, candidates: [String], selected: Int, glyphs: [[String: String]] = []) {
        self.code = code
        hasCandidates = !candidates.isEmpty
        let page = Array(candidates.prefix(Self.candidateCells.count))
        for (index, cell) in cells.filter({ !$0.punctuation }).enumerated() {
            let value = index < page.count ? page[index] : ""
            let glyph = glyphs.first { $0["character"] == value }
            cell.set(character: value, glyph: glyph, chosen: index == selected)
        }
        refreshHint()
        anchor.update(utteranceID: "stroke", editing: false, currentSize: panel.frame.size)
        showing = true
    }

    /// Live hold-to-select feedback; phase is idle/writing/holding/selecting.
    func selection(phase value: String, progress: Double, direction: String,
                   origin: [Double], points: [[Double]], selectDistance: Double) {
        phase = value
        self.progress = progress.isFinite ? min(100, max(0, progress)) : 0
        let selecting = value == "selecting"
        let target = selecting ? (direction.isEmpty ? "center" : direction) : ""
        cells.forEach { $0.set(active: $0.direction == target) }
        trail.isHidden = selecting
        selectionPath.isHidden = !selecting
        selectionPath.update(origin: origin, points: selecting ? points : [], direction: direction,
                             threshold: selectDistance)
        refreshHint()
    }

    private func refreshHint() {
        switch phase {
        case "selecting": hint.stringValue = "选字模式 · 抬手确认"
        case "holding": hint.stringValue = "保持不动 · \(Int(progress.rounded()))%"
        case "writing": hint.stringValue = "书写中 · 抬手结束"
        default:
            hint.stringValue = !hasCandidates && code.isEmpty ? "笔画输入 · \(exitGestureLabel)退出"
                : "停留 0.333 秒后滑向候选，抬手确认 · \(exitGestureLabel)退出"
        }
        hint.textColor = phase == "selecting" ? NSColor(hex: 0x234CDB) : .secondaryLabelColor
        hint.font = phase == "selecting" ? .boldSystemFont(ofSize: 11) : .systemFont(ofSize: 11)
    }

    private func layoutHint() {
        // The hint sits on the side away from the caret so the grid stays adjacent to it.
        // Flipped content: y = 0 is the top edge.
        let gridY: CGFloat = placedAboveCaret ? Self.hintHeight : 0
        choices.frame.origin = NSPoint(x: 0, y: gridY)
        hint.frame = NSRect(x: 6, y: placedAboveCaret ? 4 : Self.gridSide + 4,
                            width: Self.gridSide - 12, height: Self.hintHeight - 8)
    }

    func trace(points: [[Double]], finished: Bool, style: [String: Any]) {
        trail.update(points: points, finished: finished, style: style)
    }

    /// Default: directly below the caret, centered on it. Flip above when the
    /// screen has no room below; clamp inside the visible frame either way.
    static func placement(caret: NSRect, bounds: NSRect, size: NSSize, gap: CGFloat) -> (NSPoint, Bool) {
        let below = caret.minY - gap - size.height
        let above = caret.maxY + gap
        let fitsBelow = below >= bounds.minY
        let fitsAbove = above + size.height <= bounds.maxY
        let useAbove = !fitsBelow && (fitsAbove || bounds.maxY - caret.maxY > caret.minY - bounds.minY)
        let y = min(max(useAbove ? above : below, bounds.minY), max(bounds.minY, bounds.maxY - size.height))
        let x = min(max(caret.midX - size.width / 2, bounds.minX), max(bounds.minX, bounds.maxX - size.width))
        return (NSPoint(x: x, y: y), useAbove)
    }

    func position(caret: NSRect?, clientLevel: Int32) {
        guard showing, let caret = anchor.observe(caret),
              let screen = NSScreen.screens.first(where: { $0.frame.intersects(caret.insetBy(dx: -1, dy: -1)) }) else {
            panel.orderOut(nil)
            return
        }
        let bounds = screen.visibleFrame.insetBy(dx: 6, dy: 6)
        let size = Self.menuSize
        let (origin, above) = Self.placement(caret: caret, bounds: bounds, size: size, gap: InputPaletteStyle.caretGap)
        if above != placedAboveCaret {
            placedAboveCaret = above
            layoutHint()
        }
        panel.setContentSize(size)
        panel.setFrameOrigin(anchor.place(origin, size: size))
        panel.level = NSWindow.Level(rawValue: max(Int(clientLevel) + 1, NSWindow.Level.popUpMenu.rawValue))
        panel.orderFrontRegardless()
    }

    func hide() {
        showing = false
        panel.orderOut(nil)
        anchor = ActionPanelAnchor()
        trail.update(points: [], finished: false, style: [:])
        selection(phase: "idle", progress: 0, direction: "", origin: [], points: [], selectDistance: 3)
    }
}
