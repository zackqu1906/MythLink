import AppKit
import CoreText
import QuartzCore

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

/// Two-finger selection path, continuing through the external clear tab on long left pulls.
private final class StrokeSelectionPathView: NSView {
    override var isFlipped: Bool { true }
    var cells: [String: NSRect] = [:]
    var clearTarget = NSRect.zero
    var correctionTarget = NSRect.zero
    var correctionEnabled = false
    private var origin: [Double] = []
    private var points: [[Double]] = []
    private var direction = ""
    private var threshold = 3.0
    private var clearDistance = 30.0
    static let sectors = ["right", "down-right", "down", "down-left", "left", "up-left", "up", "up-right"]

    func update(origin: [Double], points: [[Double]], direction: String, threshold: Double, clearDistance: Double) {
        self.origin = origin.count == 2 && origin.allSatisfy(\.isFinite) ? origin : []
        self.points = Array(points.prefix(201)).filter { $0.count == 2 && $0.allSatisfy(\.isFinite) }
        self.direction = direction
        self.threshold = threshold.isFinite && threshold > 0 ? threshold : 3
        self.clearDistance = clearDistance.isFinite && clearDistance > self.threshold * 3 ? clearDistance : self.threshold * 5
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
        var boundedX = max(Double(target.minX) + inset, min(Double(target.maxX) - inset, x))
        var boundedY = max(Double(target.minY) + inset, min(Double(target.maxY) - inset, y))
        if name == "left" {
            let approach = max(0, min(1, (-dx - threshold) / (2 * threshold)))
            boundedX += (Double(target.minX) - boundedX) * approach
            let outward = max(0, min(1, (-dx - 3 * threshold) / (clearDistance - 3 * threshold)))
            boundedX += (Double(clearTarget.minX) + 10 - boundedX) * outward
            boundedY += (Double(clearTarget.midY) - boundedY) * outward
        } else if name == "right", correctionEnabled {
            let approach = max(0, min(1, (dx - threshold) / (2 * threshold)))
            boundedX += (Double(target.maxX) - boundedX) * approach
            let outward = max(0, min(1, (dx - 3 * threshold) / (clearDistance - 3 * threshold)))
            boundedX += (Double(correctionTarget.maxX) - 10 - boundedX) * outward
            boundedY += (Double(correctionTarget.midY) - boundedY) * outward
        }
        return NSPoint(x: boundedX, y: boundedY)
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

/// The same rounded, directional fill for each pull gesture.
private final class StrokePullView: FlippedView {
    private let surface = NSView()
    private let fill = NSView()
    private let arrow = NSImageView()
    private let label = NSTextField(labelWithString: "清空")
    enum Direction { case left, right, up, down }
    private let direction: Direction
    private var progress = 0.0
    private var shown = false
    private var destructive: Bool { direction == .left }

    init(direction: Direction, name: String) {
        self.direction = direction
        super.init(frame: .zero)
        identifier = NSUserInterfaceItemIdentifier(name)
        wantsLayer = true
        layer?.shadowColor = NSColor(hex: destructive ? 0x9C2F2A : 0x234CDB).cgColor
        layer?.shadowRadius = 5
        layer?.shadowOffset = .zero
        surface.wantsLayer = true
        surface.layer?.cornerRadius = 16
        surface.layer?.masksToBounds = true
        let background: UInt32 = destructive ? 0xFCE9E6 : direction == .right ? 0xE9EFFF : 0xEDF2FB
        surface.layer?.backgroundColor = NSColor(hex: background).cgColor
        surface.layer?.borderWidth = 1
        surface.layer?.borderColor = NSColor(hex: destructive ? 0xC2413B : 0x345EE5).cgColor
        fill.identifier = NSUserInterfaceItemIdentifier(name + "Fill")
        fill.wantsLayer = true
        surface.addSubview(fill)
        let icon: String
        switch direction {
        case .left:
            arrow.identifier = NSUserInterfaceItemIdentifier("strokeClearArrow")
            icon = "chevron.left.2"
        case .right:
            arrow.identifier = NSUserInterfaceItemIdentifier("strokeCorrectionArrow")
            icon = "chevron.right.2"
        case .up: icon = "chevron.up"
        case .down: icon = "chevron.down"
        }
        arrow.image = NSImage(systemSymbolName: icon, accessibilityDescription: nil)?
            .withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: 18, weight: .semibold))
        arrow.imageScaling = .scaleProportionallyUpOrDown
        label.alignment = .center
        label.font = .systemFont(ofSize: 12, weight: .medium)
        [surface, arrow, label].forEach { addSubview($0) }
        alphaValue = 0
        update(progress: 0, armed: false)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func show(_ value: Bool) {
        guard shown != value else { return }
        shown = value
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.12
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            animator().alphaValue = value ? 1 : 0
        }
    }

    func update(progress: Double, armed: Bool, deleteBefore: Bool = false) {
        self.progress = armed ? 1 : progress.isFinite ? min(1, max(0, progress)) : 0
        fill.layer?.backgroundColor = NSColor(hex: destructive ? (armed ? 0x9C2F2A : 0xEDABA5) : (armed ? 0x234CDB : 0xAABCF2)).cgColor
        layer?.shadowOpacity = armed ? 0.28 : 0
        let ink = armed ? NSColor.white : NSColor(hex: destructive ? 0xA6322C : 0x234CDB)
        arrow.contentTintColor = ink
        label.textColor = ink
        let action: String
        switch direction {
        case .left:
            label.stringValue = deleteBefore ? "删除前文" : "清空"
            action = (armed ? "抬手" : "继续向左外拉") + (deleteBefore ? "删除前文" : "清空笔画")
        case .right:
            label.stringValue = "修正"
            action = (armed ? "抬手" : "继续向右外拉") + "修正前文"
        case .up:
            label.stringValue = "更多"
            action = label.stringValue
        case .down:
            label.stringValue = "取消"
            action = label.stringValue
        }
        setAccessibilityLabel(action)
        needsLayout = true
    }

    override func layout() {
        super.layout()
        CATransaction.begin(); CATransaction.setDisableActions(true)
        surface.frame = bounds
        if destructive || direction == .right {
            fill.frame = NSRect(x: destructive ? bounds.width * (1 - progress) : 0, y: 0,
                                width: bounds.width * progress, height: bounds.height)
        } else {
            fill.frame = NSRect(x: 0, y: direction == .up ? bounds.height * (1 - progress) : 0,
                                width: bounds.width, height: bounds.height * progress)
        }
        layer?.shadowPath = CGPath(roundedRect: bounds, cornerWidth: 16, cornerHeight: 16, transform: nil)
        CATransaction.commit()
        arrow.frame = NSRect(x: (bounds.width - 24) / 2, y: 9, width: 24, height: 20)
        label.frame = NSRect(x: 0, y: 32, width: bounds.width, height: 17)
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
    private var clearing = false

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

    func setClearing(_ clearing: Bool, deleteBefore: Bool) {
        guard direction == "left" else { return }
        let action = deleteBefore ? "删除前文" : "清空笔画"
        status.stringValue = clearing ? action : "删除"
        setAccessibilityLabel(clearing ? action : "删除一笔或退格")
        if self.clearing != clearing { self.clearing = clearing; refresh() }
    }

    func set(character value: String, glyph: [String: String]?, chosen isChosen: Bool) {
        character = value
        chosen = isChosen
        label.stringValue = value.isEmpty ? "—" : value
        let size = value.count > 1 ? max(14, min(23, 100 / CGFloat(value.count))) : 44
        label.font = NSFont(name: "Kaiti SC", size: size) ?? .systemFont(ofSize: size)
        label.lineBreakMode = .byTruncatingTail
        label.toolTip = value.count > 1 ? value : nil
        needsLayout = true
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
        let actionColor = direction == "left" ? NSColor(hex: clearing ? 0x9C2F2A : 0xC2413B) : NSColor(hex: 0x234CDB)
        let background: NSColor = punctuation
            ? (active ? actionColor : NSColor(hex: 0xEDF2FB))
            : (active ? NSColor(hex: 0xEDF2FF) : primary ? NSColor(hex: 0xF0F4FF) : .white)
        let border: NSColor = active ? (punctuation ? actionColor : NSColor(hex: 0x345EE5))
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
private final class StrokePunctuationGlyph: NSView {
    let symbol: String
    private let font: NSFont
    var textColor = NSColor.labelColor { didSet { needsDisplay = true } }

    init(_ symbol: String, size: CGFloat) {
        self.symbol = symbol
        font = .systemFont(ofSize: size)
        super.init(frame: .zero)
        identifier = NSUserInterfaceItemIdentifier("strokeMorePunctuation-\(symbol)")
        setAccessibilityLabel(symbol)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func draw(_ dirtyRect: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        let text = NSAttributedString(string: symbol, attributes: [.font: font, .foregroundColor: textColor])
        let line = CTLineCreateWithAttributedString(text)
        context.saveGState()
        context.textMatrix = .identity
        context.textPosition = .zero
        let ink = CTLineGetImageBounds(line, context)
        context.textPosition = CGPoint(x: bounds.midX - ink.midX, y: bounds.midY - ink.midY)
        CTLineDraw(line, context)
        context.restoreGState()
    }
}

/// Pending strokes stay in the client's marked text; this palette only shows
/// choices and never takes focus or mouse input.
private final class StrokePunctuationOverlay: FlippedView {
    static let symbols = [
        ["、"], ["“", "”"], ["；"],
        ["《", "》"], ["！"], ["（", "）"],
        ["……"], ["——"], ["："]
    ]
    static let side: CGFloat = 188
    static let cancelSpace: CGFloat = 62
    private let surface = FlippedView()
    private let slider = StrokePullView(direction: .up, name: "strokePunctuationPull")
    private let cancelSlider = StrokePullView(direction: .down, name: "strokeCancelPunctuation")
    private var buttons: [(FlippedView, StrokePunctuationGlyph)] = []
    private var opened = false

    override init(frame: NSRect) {
        super.init(frame: frame)
        surface.frame = NSRect(x: 0, y: 0, width: Self.side, height: Self.side)
        surface.wantsLayer = true
        surface.layer?.cornerRadius = 14
        surface.layer?.borderWidth = 1
        surface.layer?.borderColor = NSColor(hex: 0xAABCF2).cgColor
        surface.layer?.backgroundColor = NSColor(hex: 0xF3F6FC, alpha: 0.93).cgColor
        surface.layer?.shadowColor = NSColor(hex: 0x24334D).cgColor
        surface.layer?.shadowOpacity = 0.2
        surface.layer?.shadowRadius = 5
        surface.layer?.shadowOffset = CGSize(width: 0, height: -3)
        surface.alphaValue = 0
        addSubview(surface)

        let cellSide = (Self.side - 24) / 3
        for (index, symbols) in Self.symbols.enumerated() {
            let cell = FlippedView(frame: NSRect(x: 8 + CGFloat(index % 3) * (cellSide + 4),
                                                y: 8 + CGFloat(index / 3) * (cellSide + 4),
                                                width: cellSide, height: cellSide))
            cell.wantsLayer = true
            cell.layer?.backgroundColor = NSColor.white.withAlphaComponent(0.8).cgColor
            cell.layer?.cornerRadius = 10
            cell.layer?.borderWidth = 1
            cell.layer?.borderColor = NSColor(hex: 0xDCE4F0).cgColor
            surface.addSubview(cell)
            let width = (cellSide - 4) / CGFloat(symbols.count)
            for (part, symbol) in symbols.enumerated() {
                let button = FlippedView(frame: NSRect(x: 2 + CGFloat(part) * width, y: 2,
                                                       width: width, height: cellSide - 4))
                button.wantsLayer = true
                button.layer?.cornerRadius = 8
                let label = StrokePunctuationGlyph(symbol, size: symbols.count == 2 ? 20 : 24)
                label.frame = button.bounds
                button.addSubview(label)
                cell.addSubview(button)
                buttons.append((button, label))
            }
            if symbols.count == 2 {
                let divider = NSView(frame: NSRect(x: cellSide / 2, y: 6, width: 1, height: cellSide - 12))
                divider.wantsLayer = true
                divider.layer?.backgroundColor = NSColor(hex: 0xDCE4F0).cgColor
                cell.addSubview(divider)
            }
        }
        slider.frame.size = NSSize(width: 60, height: 58)
        cancelSlider.frame = NSRect(x: (Self.side - 60) / 2, y: Self.side + 4, width: 60, height: 58)
        [slider, cancelSlider].forEach { addSubview($0) }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(start: NSPoint?, progress: CGFloat, open: Bool, symbol: String, cancelProgress: CGFloat = 0) {
        if let start {
            let center = Self.side / 2
            slider.frame.origin = NSPoint(x: start.x + (center - start.x) * progress - slider.frame.width / 2,
                                          y: start.y + (center - start.y) * progress - slider.frame.height / 2)
            slider.update(progress: Double(progress), armed: progress >= 1)
        }
        slider.show(start != nil && !open)
        cancelSlider.update(progress: Double(cancelProgress), armed: cancelProgress >= 1)
        cancelSlider.show(open)
        if opened != open {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = open ? 0.18 : 0.14
                surface.animator().alphaValue = open ? 1 : 0
            }
        }
        if opened != open, let layer = surface.layer {
            let scale = CAKeyframeAnimation(keyPath: "transform.scale")
            let currentScale = (layer.presentation()?.value(forKeyPath: "transform.scale") as? NSNumber)?.doubleValue
            scale.values = open ? [currentScale ?? 0.64, 1.04, 1] : [currentScale ?? 1, 0.75]
            scale.duration = open ? 0.22 : 0.14
            scale.timingFunctions = open ? [CAMediaTimingFunction(name: .easeOut), CAMediaTimingFunction(name: .easeInEaseOut)]
                : [CAMediaTimingFunction(name: .easeIn)]
            layer.add(scale, forKey: "punctuationScale")
        }
        opened = open
        for (button, label) in buttons {
            let selected = open && label.symbol == symbol
            label.textColor = selected ? .white : NSColor(hex: 0x24334D)
            button.layer?.backgroundColor = selected ? NSColor(hex: 0x234CDB).cgColor : NSColor.clear.cgColor
        }
    }
}

final class StrokeCandidatePanel {
    static let cellSize: CGFloat = 78
    static let cellGap: CGFloat = 6
    static let inset: CGFloat = 8
    static let hintHeight: CGFloat = 38
    static let clearGutter: CGFloat = 60
    static let punctuationSymbols = punctuationCells.filter { $0.0 != "left" }.map { $0.4 }
        + StrokePunctuationOverlay.symbols.flatMap { $0 }
    static var gridSide: CGFloat { inset * 2 + cellSize * 3 + cellGap * 2 }
    static var menuSize: NSSize { NSSize(width: gridSide + clearGutter, height: gridSide + hintHeight) }
    // Same order as stroke_menu.CANDIDATE_CELLS / PUNCTUATION_CELLS / ACTION_CELLS.
    static let candidateCells: [(String, Int, Int, String)] = [
        ("center", 1, 1, "首选"), ("right", 1, 2, "→"),
        ("down-left", 2, 0, "↙"), ("down", 2, 1, "↓"), ("down-right", 2, 2, "↘")]
    // Fixed action cells: punctuation on top, two-finger-left delete.
    static let punctuationCells: [(String, Int, Int, String, String, String)] = [
        ("up-left", 0, 0, "↖", "，", "逗号"), ("up", 0, 1, "↑", "。", "句号"), ("up-right", 0, 2, "↗", "？", "问号"),
        ("left", 1, 0, "←", "⌫", "删除")]

    var exitGestureLabel = "双指双击" { didSet { exitHint.stringValue = exitGestureLabel + "退出" } }
    var tapHint = "单指空格 · 双指首选" { didSet { refreshHint() } }
    private let panel: NSPanel
    private let punctuationPanel = StrokePalette(
        contentRect: NSRect(x: 0, y: 0, width: StrokePunctuationOverlay.side, height: StrokePunctuationOverlay.side),
        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
    private let punctuationOverlay = StrokePunctuationOverlay()
    private var punctuationPull: [String: Any] = [:]
    private var punctuationScreenBounds: NSRect?
    private let content = FlippedView()
    private let hint = NSTextField(labelWithString: "")
    private let exitHint = NSTextField(labelWithString: "双指双击退出")
    private let choices = InputPaletteSurface()
    private let grid = FlippedView()
    private var cells: [StrokeMenuCell] = []
    private let trail = StrokeTraceView()
    private let selectionPath = StrokeSelectionPathView()
    private let clearPull = StrokePullView(direction: .left, name: "strokeClearPull")
    private let correctionPull = StrokePullView(direction: .right, name: "strokeCorrectionPull")
    private let correctionScope = NSTextField(wrappingLabelWithString: "")
    private var correctionEnabled = false
    private var correctionText = ""
    private var correctionMessage = ""
    private var correctionBusy = false
    private var correctionCanUndo = false
    private var correcting = false
    private var anchor = ActionPanelAnchor()
    private var showing = false
    private var code = ""
    private var hasCandidates = false
    private var phase = "idle"
    private var progress = 0.0
    private var selectionDirection = ""
    private var clearing = false
    private(set) var placedAboveCaret = false
    var isVisible: Bool { showing && panel.isVisible }
    private var scopeVisible: Bool {
        correctionEnabled && ((phase == "selecting" && selectionDirection == "right") || correctionBusy
            || (correctionCanUndo && code.isEmpty) || !correctionText.isEmpty)
    }
    private var scopeHeight: CGFloat {
        guard correctionEnabled else { return 0 }
        let text = correctionScope.stringValue as NSString
        let height = text.boundingRect(with: NSSize(width: Self.gridSide - 20, height: 1000),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: NSFont.systemFont(ofSize: 14, weight: .semibold)]).height
        return max(38, ceil(height) + 16)
    }
    private var displaySize: NSSize {
        NSSize(width: Self.menuSize.width + (correctionEnabled ? Self.clearGutter : 0),
               height: Self.menuSize.height + scopeHeight)
    }
    var needsPosition: Bool { showing && (anchor.needsCaret || !panel.isVisible || panel.frame.size != displaySize) }

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
        punctuationPanel.isOpaque = false
        punctuationPanel.backgroundColor = .clear
        punctuationPanel.hasShadow = false
        punctuationPanel.ignoresMouseEvents = true
        punctuationPanel.hidesOnDeactivate = false
        punctuationPanel.collectionBehavior = panel.collectionBehavior
        punctuationPanel.contentView = punctuationOverlay
        panel.addChildWindow(punctuationPanel, ordered: .above)
        punctuationPanel.orderOut(nil)
        choices.identifier = NSUserInterfaceItemIdentifier("strokeCandidateBar")
        choices.cornerRadius = 14
        hint.font = .systemFont(ofSize: 11)
        hint.textColor = .secondaryLabelColor
        hint.lineBreakMode = .byTruncatingTail
        hint.identifier = NSUserInterfaceItemIdentifier("strokeSelectionHint")
        exitHint.font = .systemFont(ofSize: 10)
        exitHint.textColor = .secondaryLabelColor
        exitHint.alignment = .right
        exitHint.identifier = NSUserInterfaceItemIdentifier("strokeExitHint")
        for (direction, row, column, arrow, symbol, title) in Self.punctuationCells {
            let cell = StrokeMenuCell(direction: direction, arrow: arrow, rank: nil, symbol: symbol, title: title)
            cell.frame = Self.cellFrame(row: row, column: column)
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
        selectionPath.cells = Dictionary(uniqueKeysWithValues: cells.map { ($0.direction, $0.frame.offsetBy(dx: Self.clearGutter, dy: 0)) })
        selectionPath.clearTarget = NSRect(x: 8, y: Self.cellFrame(row: 1, column: 0).midY - 29,
                                          width: Self.clearGutter, height: 58)
        choices.frame = gridFrame
        choices.addSubview(grid)
        choices.addSubview(trail)
        content.frame = NSRect(origin: .zero, size: Self.menuSize)
        content.addSubview(choices)
        content.addSubview(clearPull)
        correctionPull.alphaValue = 1
        correctionPull.isHidden = true
        content.addSubview(correctionPull)
        content.addSubview(selectionPath)
        content.addSubview(hint)
        content.addSubview(exitHint)
        correctionScope.identifier = NSUserInterfaceItemIdentifier("strokeCorrectionScope")
        correctionScope.font = .systemFont(ofSize: 14, weight: .semibold)
        correctionScope.textColor = NSColor(hex: 0x234CDB)
        correctionScope.drawsBackground = true
        correctionScope.backgroundColor = NSColor(hex: 0xE9EFFF)
        correctionScope.isSelectable = false
        content.addSubview(correctionScope)
        selectionPath.correctionTarget = NSRect(x: Self.clearGutter + Self.gridSide - 8,
            y: Self.cellFrame(row: 1, column: 2).midY - 29, width: Self.clearGutter, height: 58)
        panel.contentView = content
        layoutHint()
    }

    private static func cellFrame(row: Int, column: Int) -> NSRect {
        NSRect(x: inset + CGFloat(column) * (cellSize + cellGap), y: inset + CGFloat(row) * (cellSize + cellGap),
               width: cellSize, height: cellSize)
    }

    /// Candidates of the current page (up to five); `selected` is the keyboard/swipe choice.
    func update(code: String, candidates: [String], selected: Int, glyphs: [[String: String]] = []) {
        self.code = code
        hasCandidates = !candidates.isEmpty
        let page = Array(candidates.prefix(Self.candidateCells.count))
        for (index, cell) in cells.filter({ !$0.punctuation }).enumerated() {
            let value = index < page.count ? page[index] : ""
            let glyph = glyphs.first { $0["character"] == value }
            cell.set(character: value, glyph: glyph, chosen: index == selected)
        }
        refreshHint(); layoutHint()
        anchor.update(utteranceID: "stroke", editing: false, currentSize: panel.frame.size)
        showing = true
    }

    /// Live two-finger selection feedback; phase is idle/writing/holding/selecting.
    func selection(phase value: String, progress: Double, direction: String,
                   origin: [Double], points: [[Double]], selectDistance: Double,
                   clearArmed: Bool = false, clearDistance: Double = 30, clearProgress: Double = 0,
                   correctionArmed: Bool = false, correctionProgress: Double = 0,
                   punctuationPull: [String: Any] = [:]) {
        phase = value
        selectionDirection = direction
        self.progress = progress.isFinite ? min(100, max(0, progress)) : 0
        let selecting = value == "selecting"
        self.punctuationPull = selecting ? punctuationPull : [:]
        clearing = selecting && direction == "left" && clearArmed
        correcting = selecting && direction == "right" && correctionEnabled && correctionArmed
        choices.interactionColor = selecting ? NSColor(hex: clearing ? 0x9C2F2A : direction == "left" ? 0xC2413B : 0x345EE5) : nil
        let target = selecting ? (direction.isEmpty ? "center" : direction) : ""
        cells.forEach { $0.set(active: $0.direction == target); $0.setClearing(clearing, deleteBefore: code.isEmpty) }
        clearPull.show(selecting && direction == "left")
        clearPull.update(progress: clearProgress, armed: clearing, deleteBefore: code.isEmpty)
        correctionPull.isHidden = !selecting || direction != "right" || !correctionEnabled
        correctionPull.update(progress: correctionProgress, armed: correcting)
        trail.isHidden = selecting
        selectionPath.isHidden = !selecting || !self.punctuationPull.isEmpty
        selectionPath.update(origin: origin, points: selecting ? points : [], direction: direction,
                             threshold: selectDistance, clearDistance: clearDistance)
        refreshHint()
        layoutHint()
        updatePunctuationOverlay()
    }

    func correction(enabled: Bool, text: String, message: String, busy: Bool, canUndo: Bool) {
        correctionEnabled = enabled; correctionText = text; correctionMessage = message
        correctionBusy = busy; correctionCanUndo = canUndo
        selectionPath.correctionEnabled = enabled
        if !enabled { correctionPull.isHidden = true; correcting = false }
        refreshHint(); layoutHint()
    }

    private func refreshHint() {
        switch phase {
        case "selecting":
            hint.stringValue = clearing ? (code.isEmpty ? "抬手删除前文 · 拉回取消" : "抬手清空 · 拉回取消")
                : selectionDirection == "left" ? (code.isEmpty ? "抬手退格 · 外拉删除前文" : "抬手删除 · 继续外拉可清空")
                : punctuationPull["opened"] as? Bool == true
                    ? ((punctuationPull["cancel_progress"] as? Double ?? 0) > 0 ? "继续下拉取消 · " : "")
                        + "抬手输入 \(punctuationPull["symbol"] as? String ?? "")"
                : !punctuationPull.isEmpty ? "抬手输入 · 继续上滑展开标点"
                : selectionDirection.isEmpty ? "已识别双指 · 滑动选字" : "双指选字 · 抬手确认"
            if correctionEnabled && selectionDirection == "right" {
                hint.stringValue = !code.isEmpty ? "请先确认正在书写的字" : correcting
                    ? "抬手修正前文 · 拉回取消" : "继续向右外拉可修正前文"
            }
        case "holding": hint.stringValue = "保持不动 · \(Int(progress.rounded()))%"
        case "writing": hint.stringValue = "书写中 · 抬手结束"
        default:
            hint.stringValue = correctionEnabled && code.isEmpty && !correctionMessage.isEmpty ? correctionMessage
                : !hasCandidates && code.isEmpty ? "笔画输入" : tapHint
        }
        correctionScope.stringValue = !correctionText.isEmpty ? "修正用词「\(correctionText)」"
            : !code.isEmpty ? "请先确认正在书写的字"
            : correctionMessage.isEmpty ? "尚未确认手写词" : correctionMessage
        let highlight = correcting && !correctionText.isEmpty
        correctionScope.backgroundColor = NSColor(hex: highlight ? 0x234CDB : 0xE9EFFF)
        correctionScope.textColor = highlight ? .white : NSColor(hex: 0x234CDB)
        correctionScope.setAccessibilityLabel(correctionScope.stringValue)
        if scopeVisible && hint.stringValue == correctionScope.stringValue { hint.stringValue = "" }
        hint.textColor = phase == "selecting" ? NSColor(hex: selectionDirection == "left" ? 0xC2413B : 0x234CDB) : .secondaryLabelColor
        hint.font = phase == "selecting" ? .boldSystemFont(ofSize: 11) : .systemFont(ofSize: 11)
    }

    private func layoutHint() {
        // Put the correction scope next to the caret, on either side of the grid.
        // Flipped content: y = 0 is the top edge.
        let gridY: CGFloat = placedAboveCaret ? Self.hintHeight : scopeHeight
        choices.frame.origin = NSPoint(x: Self.clearGutter, y: gridY)
        selectionPath.frame = NSRect(x: 0, y: gridY, width: displaySize.width, height: Self.gridSide)
        clearPull.frame = selectionPath.clearTarget.offsetBy(dx: 0, dy: gridY)
        correctionPull.frame = selectionPath.correctionTarget.offsetBy(dx: 0, dy: gridY)
        correctionScope.isHidden = !scopeVisible
        correctionScope.frame = NSRect(x: Self.clearGutter + 8, y: placedAboveCaret ? Self.hintHeight + Self.gridSide + 6 : 0,
                                      width: Self.gridSide - 16, height: max(0, scopeHeight - 6))
        let hintY: CGFloat = placedAboveCaret ? 4 : Self.gridSide + scopeHeight + 4
        hint.frame = NSRect(x: Self.clearGutter + 6, y: hintY, width: Self.gridSide - 12, height: 16)
        exitHint.frame = NSRect(x: Self.clearGutter + 6, y: hintY + 16, width: Self.gridSide - 12, height: 14)
    }

    func trace(points: [[Double]], finished: Bool, style: [String: Any]) {
        trail.update(points: points, finished: finished, style: style)
    }

    private func updatePunctuationOverlay() {
        guard panel.isVisible, let bounds = punctuationScreenBounds,
              let direction = punctuationPull["direction"] as? String,
              let cell = cells.first(where: { $0.direction == direction }) else {
            punctuationOverlay.update(start: nil, progress: 0, open: false, symbol: "")
            return
        }
        let side = StrokePunctuationOverlay.side
        let cellX = panel.frame.minX + choices.frame.minX + cell.frame.midX
        let cellY = panel.frame.maxY - choices.frame.minY - cell.frame.midY
        let gridTop = panel.frame.maxY - choices.frame.minY
        let left = min(max(cellX - side / 2, bounds.minX), max(bounds.minX, bounds.maxX - side))
        let totalHeight = side + StrokePunctuationOverlay.cancelSpace
        let top = min(max(gridTop - 8 + side / 2, bounds.minY + totalHeight), bounds.maxY)
        let bottom = min(top - totalHeight, cellY - 29)
        punctuationPanel.setFrame(NSRect(x: left, y: bottom, width: side, height: top - bottom), display: false)
        if !punctuationPanel.isVisible { punctuationPanel.orderFront(nil) }
        let progress = punctuationPull["progress"] as? Double ?? 0
        let cancelProgress = punctuationPull["cancel_progress"] as? Double ?? 0
        punctuationOverlay.update(start: NSPoint(x: cellX - left, y: top - cellY),
                                  progress: CGFloat(progress.isFinite ? min(1, max(0, progress)) : 0),
                                  open: punctuationPull["opened"] as? Bool == true,
                                  symbol: punctuationPull["symbol"] as? String ?? "",
                                  cancelProgress: CGFloat(cancelProgress.isFinite ? min(1, max(0, cancelProgress)) : 0))
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
        let x = min(max(caret.midX - (size.width + Self.clearGutter) / 2, bounds.minX), max(bounds.minX, bounds.maxX - size.width))
        return (NSPoint(x: x, y: y), useAbove)
    }

    func position(caret: NSRect?, clientLevel: Int32) {
        guard showing, let caret = anchor.observe(caret),
              let screen = NSScreen.screens.first(where: { $0.frame.intersects(caret.insetBy(dx: -1, dy: -1)) }) else {
            punctuationPanel.orderOut(nil)
            panel.orderOut(nil)
            return
        }
        let bounds = screen.visibleFrame.insetBy(dx: 6, dy: 6)
        punctuationScreenBounds = bounds
        let size = displaySize
        let (origin, above) = Self.placement(caret: caret.offsetBy(dx: correctionEnabled ? Self.clearGutter / 2 : 0, dy: 0),
                                           bounds: bounds, size: size, gap: InputPaletteStyle.caretGap)
        if above != placedAboveCaret {
            placedAboveCaret = above
            layoutHint()
        }
        panel.setContentSize(size)
        layoutHint()
        panel.setFrameOrigin(anchor.place(origin, size: size))
        panel.level = NSWindow.Level(rawValue: max(Int(clientLevel) + 1, NSWindow.Level.popUpMenu.rawValue))
        panel.orderFrontRegardless()
        if !punctuationPull.isEmpty { updatePunctuationOverlay() }
    }

    func hide() {
        showing = false
        punctuationScreenBounds = nil
        punctuationPanel.orderOut(nil)
        panel.orderOut(nil)
        anchor = ActionPanelAnchor()
        trail.update(points: [], finished: false, style: [:])
        selection(phase: "idle", progress: 0, direction: "", origin: [], points: [], selectDistance: 3)
    }
}
