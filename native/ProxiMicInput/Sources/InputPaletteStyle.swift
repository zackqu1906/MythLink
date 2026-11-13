import AppKit

/// Shared presentation for voice and stroke input; never changes focus or input.
enum InputPaletteStyle {
    static let rowHeight: CGFloat = 32
    static let cornerRadius: CGFloat = 10
    static let caretGap: CGFloat = 4
    static let selection = NSColor(srgbRed: 0, green: 92/255, blue: 211/255, alpha: 1)
}

final class InputPaletteSurface: NSView {
    var cornerRadius = InputPaletteStyle.cornerRadius { didSet { refreshSurface() } }
    var interactionColor: NSColor? { didSet { refreshSurface() } }

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        refreshSurface()
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        refreshSurface()
    }

    private func refreshSurface() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            layer?.cornerRadius = cornerRadius
            layer?.masksToBounds = true
            layer?.backgroundColor = interactionColor?.withAlphaComponent(0.18).cgColor
                ?? NSColor.windowBackgroundColor.withAlphaComponent(0.68).cgColor
            layer?.borderWidth = interactionColor == nil ? 0.5 : 3
            layer?.borderColor = interactionColor?.cgColor ?? NSColor.separatorColor.withAlphaComponent(0.25).cgColor
        }
    }
}
