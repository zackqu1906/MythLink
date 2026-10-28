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

/// Gesture artwork matches the settings page; actions use plain text captions.
/// Only the original Cancel and Convert controls dispatch mouse actions.
final class VoiceHudHint: NSButton {
    let actionID: String
    private(set) var gestures: [String] = []
    static let baseWidth: CGFloat = 84
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

    func configure(gestures: [String], caption: String, available: Bool, detail: String) {
        self.gestures = gestures
        title = caption
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
        // Multiple alternatives stay bounded; the tooltip retains every mapping.
        let gestureCell: CGFloat = (gestures.count > 1 ? 17 : 24) * uiScale
        let symbolsWidth = CGFloat(min(gestures.count, 2)) * gestureCell
        let caption = title as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: VoiceHudStyle.font(max(11, 14 * uiScale)),
            .foregroundColor: VoiceHudStyle.text.withAlphaComponent(opacity)
        ]
        let captionSize = caption.size(withAttributes: attributes)
        let gap: CGFloat = gestures.isEmpty ? 0 : 6 * uiScale
        let total = symbolsWidth + gap + captionSize.width
        var x = (bounds.width - total) / 2
        for gesture in gestures.prefix(gestures.count > 2 ? 1 : 2) {
            drawAsset(VoiceHudBindings.assetName(gesture), cell: NSRect(x: x, y: 0, width: gestureCell, height: bounds.height),
                      maximum: (gestures.count > 1 ? 16 : 20) * uiScale, opacity: opacity)
            if gesture.hasPrefix("double-") {
                let badgeAttributes: [NSAttributedString.Key: Any] = [
                    .font: NSFont.systemFont(ofSize: max(8, 9 * uiScale), weight: .semibold),
                    .foregroundColor: VoiceHudStyle.blue.withAlphaComponent(opacity)
                ]
                let badge = "2" as NSString
                let size = badge.size(withAttributes: badgeAttributes)
                badge.draw(at: NSPoint(x: x + gestureCell - size.width, y: bounds.height - size.height - uiScale),
                           withAttributes: badgeAttributes)
            }
            x += gestureCell
        }
        if gestures.count > 2 {
            let count = "+\(gestures.count - 1)" as NSString
            let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 9 * uiScale),
                                                           .foregroundColor: NSColor.labelColor.withAlphaComponent(opacity)]
            let size = count.size(withAttributes: attributes)
            count.draw(at: NSPoint(x: x + (gestureCell-size.width)/2, y: (bounds.height-size.height)/2), withAttributes: attributes)
            x += gestureCell
        }
        caption.draw(at: NSPoint(x: x + gap, y: (bounds.height - captionSize.height) / 2),
                     withAttributes: attributes)
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
