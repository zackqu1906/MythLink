import AppKit
import QuartzCore

enum VoiceHudStyle {
    static let blue = NSColor(srgbRed: 2/255, green: 37/255, blue: 186/255, alpha: 1)
    static let text = NSColor.labelColor
    static func font(_ size: CGFloat, bold: Bool = false) -> NSFont {
        NSFont(name: bold ? "SourceHanSansSC-Bold" : "SourceHanSansSC-Regular", size: size)
            ?? .systemFont(ofSize: size, weight: bold ? .bold : .regular)
    }
}

enum VoiceHudAssets {
    static let directory: URL = {
        let bundled = Bundle.main.resourceURL?.appendingPathComponent("VoiceHud")
        if let bundled, FileManager.default.fileExists(atPath: bundled.path) { return bundled }
        // Source-only native tests use the exact same resources as the signed bundle.
        return URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("Resources/VoiceHud")
    }()
    private static var cache: [String: NSImage] = [:]
    static func image(_ name: String) -> NSImage? {
        if let image = cache[name] { return image }
        guard let image = NSImage(contentsOf: directory.appendingPathComponent(name + ".svg")) else { return nil }
        cache[name] = image
        return image
    }
}

/// Figma function and gesture SVGs are separate, freely recombinable layers.
/// Only the original Cancel and Convert controls dispatch mouse actions.
final class VoiceHudHint: NSButton {
    let actionID: String
    private(set) var gestures: [String] = []
    private(set) var functionAsset = ""
    var uiScale: CGFloat = 0.8 { didSet { needsDisplay = true } }
    override var acceptsFirstResponder: Bool { false }
    override var isFlipped: Bool { true }

    init(action: String) {
        self.actionID = action
        super.init(frame: .zero)
        identifier = NSUserInterfaceItemIdentifier("voiceHint_" + action)
        isBordered = false
        title = ""
        focusRingType = .none
        setButtonType(.momentaryChange)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func configure(gestures: [String], functionAsset: String, available: Bool, detail: String) {
        self.gestures = gestures
        self.functionAsset = functionAsset
        isEnabled = available
        isHidden = !available
        let names = gestures.compactMap { VoiceHudBindings.names[$0] }.joined(separator: " 或 ")
        toolTip = (names.isEmpty ? "未绑定手势" : names) + " · " + detail
        setAccessibilityLabel(toolTip)
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        let opacity: CGFloat = isEnabled ? 1 : 0.38
        NSColor.labelColor.withAlphaComponent(0.05).setFill()
        NSBezierPath(roundedRect: bounds, xRadius: 4 * uiScale, yRadius: 4 * uiScale).fill()
        // Keep Figma's 24 pt gesture/function cells. Two alternatives use smaller
        // gesture cells, preserving the panel's size and its held caret anchor.
        let gestureCell: CGFloat = (gestures.count > 1 ? 17 : 24) * uiScale
        let symbolsWidth = CGFloat(gestures.count) * gestureCell
        let total = symbolsWidth + (gestures.isEmpty ? 0 : 1 * uiScale) + 24 * uiScale
        var x = (bounds.width - total) / 2
        for gesture in gestures {
            drawAsset("gesture-" + gesture, cell: NSRect(x: x, y: 0, width: gestureCell, height: bounds.height),
                      maximum: (gestures.count > 1 ? 16 : 20) * uiScale, opacity: opacity)
            x += gestureCell
        }
        if !gestures.isEmpty { x += uiScale }
        drawAsset(functionAsset, cell: NSRect(x: x, y: 0, width: 24 * uiScale, height: bounds.height),
                  maximum: 20 * uiScale, opacity: opacity)
    }

    private func drawAsset(_ name: String, cell: NSRect, maximum: CGFloat, opacity: CGFloat) {
        guard let image = VoiceHudAssets.image(name) else { return }
        let scale = min(uiScale, maximum / max(image.size.width, image.size.height))
        let size = NSSize(width: image.size.width * scale, height: image.size.height * scale)
        image.draw(in: NSRect(x: cell.midX-size.width/2, y: cell.midY-size.height/2,
                             width: size.width, height: size.height), from: .zero,
                   operation: .sourceOver, fraction: opacity, respectFlipped: true, hints: nil)
    }
}

enum VoiceHudMotion {
    static var reduced: Bool { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }
    static let transitionDuration = 0.10

    static func transition(_ view: NSView) {
        guard !reduced else { view.layer?.removeAnimation(forKey: kCATransition); return }
        let fade = CATransition()
        fade.type = .fade
        fade.duration = transitionDuration
        fade.beginTime = CACurrentMediaTime()
        fade.timingFunction = CAMediaTimingFunction(name: .easeOut)
        // A single key replaces an in-flight transition. There is no completion
        // callback, queue or minimum display time on the input path.
        view.layer?.add(fade, forKey: kCATransition)
    }
}

/// Rotate the original Figma arc on its own centered layer, never the label or
/// panel. Core Animation drives frames independently of transcription updates.
final class VoiceHudSpinner: NSView {
    let rotor = CALayer()
    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        rotor.contentsGravity = .resizeAspect
        if let image = VoiceHudAssets.image("processing-spinner") {
            let raster = NSImage(size: NSSize(width: 64, height: 64), flipped: false) { rect in
                image.draw(in: rect); return true
            }
            rotor.contents = raster.cgImage(forProposedRect: nil, context: nil, hints: nil)
        }
        layer?.addSublayer(rotor)
        isHidden = true
        setAccessibilityLabel("正在处理")
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func layout() {
        super.layout()
        CATransaction.begin(); CATransaction.setDisableActions(true)
        let side = min(bounds.width, bounds.height) * 20 / 24
        rotor.bounds = NSRect(x: 0, y: 0, width: side, height: side)
        rotor.position = NSPoint(x: bounds.midX, y: bounds.midY)
        CATransaction.commit()
    }

    func setAnimating(_ active: Bool, reducedMotion: Bool = VoiceHudMotion.reduced) {
        isHidden = !active
        guard active else { rotor.removeAllAnimations(); return }
        let key = reducedMotion ? "pulse" : "rotation"
        rotor.removeAnimation(forKey: reducedMotion ? "rotation" : "pulse")
        guard rotor.animation(forKey: key) == nil else { return }
        let animation = CABasicAnimation(keyPath: reducedMotion ? "opacity" : "transform.rotation.z")
        animation.fromValue = reducedMotion ? 0.45 : 0
        animation.toValue = reducedMotion ? 1 : -Double.pi * 2
        animation.duration = reducedMotion ? 0.65 : 0.8
        animation.autoreverses = reducedMotion
        animation.repeatCount = .infinity
        animation.beginTime = CACurrentMediaTime()
        animation.timingFunction = CAMediaTimingFunction(name: .linear)
        rotor.add(animation, forKey: key)
    }
}

final class VoiceHudProgress: NSImageView {
    func setAnimating(_ active: Bool) {
        isHidden = !active
        wantsLayer = true
        if active && layer?.animation(forKey: "processing") == nil {
            let pulse = CABasicAnimation(keyPath: "opacity")
            pulse.fromValue = 0.4; pulse.toValue = 1
            pulse.duration = 0.65; pulse.autoreverses = true; pulse.repeatCount = .infinity
            layer?.add(pulse, forKey: "processing")
        } else if !active { layer?.removeAnimation(forKey: "processing") }
    }
}
