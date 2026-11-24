import AppKit

/// Preview only. Paste always uses the untouched system pasteboard in the host.
struct ClipboardPreview {
    let text: String
    let image: NSImage?

    static func read(_ board: NSPasteboard, revision: Int) -> ClipboardPreview? {
        guard board.changeCount == revision,
              board.string(forType: .init("application/x-mythlink-internal-clipboard")) == nil else { return nil }
        let result: ClipboardPreview?
        if let image = NSImage(pasteboard: board), image.isValid {
            // Downsample the display object; never transfer the full image over IPC.
            let bounds = NSSize(width: 240, height: 144)
            let factor = min(1, bounds.width / max(1, image.size.width), bounds.height / max(1, image.size.height))
            let size = NSSize(width: max(1, image.size.width * factor), height: max(1, image.size.height * factor))
            let thumbnail = NSImage(size: size, flipped: false) { rect in
                image.draw(in: rect, from: .zero, operation: .copy, fraction: 1)
                return true
            }
            // Materialize now so the lazy drawing closure does not retain a full screenshot.
            let raster = thumbnail.tiffRepresentation.flatMap(NSImage.init(data:))
            result = raster.map { ClipboardPreview(text: "", image: $0) }
        } else if let text = board.string(forType: .string), !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            result = ClipboardPreview(text: bounded(text), image: nil)
        } else if let urls = board.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL],
                  let first = urls.first {
            let name = first.lastPathComponent + (urls.count > 1 ? " 等 \(urls.count) 项" : "")
            result = ClipboardPreview(text: bounded(name), image: NSWorkspace.shared.icon(forFile: first.path))
        } else if let data = board.data(forType: .rtf), data.count <= 1_048_576,
                  let attributed = NSAttributedString(rtf: data, documentAttributes: nil), !attributed.string.isEmpty {
            result = ClipboardPreview(text: bounded(attributed.string), image: nil)
        } else { result = nil }
        return board.changeCount == revision ? result : nil
    }

    static func bounded(_ text: String) -> String {
        let prefix = String(text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(161))
        // Three visual lines are also enforced by the native text field.
        return prefix.count > 160 ? String(prefix.prefix(159)) + "…" : prefix
    }
}

/// A second mode of the IME palette, also usable while ABC/Pinyin owns input.
/// Shares the voice palette surface, metrics and passive window behavior.
final class ClipboardSuggestionPanel {
    let panel: NSPanel
    private let surface = InputPaletteSurface()
    private let text = NSTextField(wrappingLabelWithString: "")
    private let replacementScroll = NSScrollView()
    private let replacementBody = NSTextView(frame: .zero)
    private let image = NSImageView()
    private let hint = NSTextField(labelWithString: "右滑接受 →")
    private var token = ""
    private var timer: Timer?
    var onVisibility: ((String, Bool) -> Void)?

    init() {
        panel = InputPaletteWindow(contentRect: NSRect(x: 0, y: 0, width: 280, height: 88),
                                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.ignoresMouseEvents = true
        panel.animationBehavior = .none
        panel.contentView = surface
        text.identifier = .init("clipboardPreviewText")
        text.maximumNumberOfLines = 3
        text.lineBreakMode = .byWordWrapping
        text.cell?.truncatesLastVisibleLine = true
        text.font = VoiceHudStyle.font(12)
        text.textColor = VoiceHudStyle.text
        text.isSelectable = false
        image.identifier = .init("clipboardPreviewImage")
        image.imageScaling = .scaleProportionallyDown
        image.setAccessibilityLabel("剪贴板图片预览")
        hint.identifier = .init("clipboardAcceptHint")
        hint.font = VoiceHudStyle.font(11)
        hint.textColor = .secondaryLabelColor
        hint.alignment = .right
        replacementBody.isEditable = false
        replacementBody.isSelectable = false
        replacementBody.drawsBackground = false
        replacementBody.font = text.font
        replacementBody.textColor = text.textColor
        replacementBody.textContainerInset = .zero
        replacementBody.textContainer?.lineFragmentPadding = 0
        replacementBody.textContainer?.widthTracksTextView = false
        replacementBody.textContainer?.heightTracksTextView = false
        replacementScroll.drawsBackground = false
        replacementScroll.borderType = .noBorder
        replacementScroll.documentView = replacementBody
        replacementScroll.isHidden = true
        for view in [text, image, replacementScroll, hint] { surface.addSubview(view) }
    }

    @discardableResult
    func show(_ preview: ClipboardPreview, token: String, caret: NSRect, duration: TimeInterval? = 5, replacement: Bool = false) -> Bool {
        hide()
        guard let screen = NSScreen.screens.first(where: { $0.frame.intersects(caret.insetBy(dx: -1, dy: -1)) }) else { return false }
        hint.stringValue = replacement ? "下滑替换 ↓" : "右滑接受 →"
        if replacement {
            layoutReplacement(preview.text, visible: screen.visibleFrame)
            present(token: token, caret: caret, screen: screen, duration: duration)
            return true
        }
        let font = text.font!
        let hintHeight: CGFloat = 16
        let hintWidth = ceil((hint.stringValue as NSString).size(withAttributes: [.font: hint.font!]).width) + 2
        let maxTextWidth: CGFloat = 256
        let naturalTextWidth = preview.text.components(separatedBy: .newlines).map {
            ($0 as NSString).size(withAttributes: [.font: font]).width
        }.max() ?? 0
        let textWidth = preview.text.isEmpty ? 0 : min(maxTextWidth, max(hintWidth, ceil(naturalTextWidth) + 2))
        let textHeight = preview.text.isEmpty ? 0 : min(ceil(font.ascender - font.descender + font.leading) * 3,
            ceil((preview.text as NSString).boundingRect(with: NSSize(width: textWidth, height: 1000),
                 options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: [.font: font]).height) + 2)
        let imageSize: NSSize
        if let source = preview.image {
            let limit = preview.text.isEmpty ? NSSize(width: 240, height: 144) : NSSize(width: 32, height: 32)
            let factor = min(1, limit.width / max(1, source.size.width), limit.height / max(1, source.size.height))
            imageSize = NSSize(width: ceil(source.size.width * factor), height: ceil(source.size.height * factor))
        } else { imageSize = .zero }
        let gap: CGFloat = imageSize.width > 0 && textWidth > 0 ? 8 : 0
        let bodyWidth = imageSize.width + gap + textWidth
        let bodyHeight = max(imageSize.height, textHeight)
        let width = max(hintWidth, bodyWidth) + 24
        let height = bodyHeight + 12 + 8 + hintHeight + 10
        panel.setContentSize(NSSize(width: width, height: height))
        text.stringValue = preview.text
        text.isHidden = preview.text.isEmpty
        image.image = preview.image
        image.isHidden = preview.image == nil
        hint.frame = NSRect(x: 12, y: 10, width: width - 24, height: 16)
        let bodyX = (width - bodyWidth) / 2
        text.frame = NSRect(x: bodyX + imageSize.width + gap, y: 34 + (bodyHeight - textHeight) / 2,
                            width: textWidth, height: textHeight)
        image.frame = NSRect(x: bodyX, y: 34 + (bodyHeight - imageSize.height) / 2,
                             width: imageSize.width, height: imageSize.height)
        present(token: token, caret: caret, screen: screen, duration: duration)
        return true
    }

    private func layoutReplacement(_ candidate: String, visible: NSRect) {
        text.isHidden = true
        image.isHidden = true
        replacementScroll.isHidden = false
        replacementBody.string = candidate
        let container = replacementBody.textContainer!
        let manager = replacementBody.layoutManager!
        // Measure the same TextKit glyphs we display, including fallback fonts,
        // emoji, wrapping and the last line. Coordinates are in screen points.
        func bodyHeight(_ width: CGFloat) -> CGFloat {
            container.containerSize = NSSize(width: width, height: .greatestFiniteMagnitude)
            manager.ensureLayout(for: container)
            return ceil(manager.usedRect(for: container).maxY) + 2
        }
        let maxWidth = max(1, visible.width - 36)
        let maxHeight = max(1, visible.height - 58)
        let hintWidth = ceil(hint.intrinsicContentSize.width)
        let naturalWidth = ceil((candidate as NSString).size(withAttributes: [.font: replacementBody.font!]).width) + 4
        var width = min(maxWidth, max(hintWidth, min(440, naturalWidth)))
        var height = bodyHeight(width)
        if height > maxHeight && width < maxWidth {
            width = maxWidth
            height = bodyHeight(width)
        }
        let overflow = height > maxHeight
        let viewport = min(height, maxHeight)
        panel.setContentSize(NSSize(width: width + 24, height: viewport + 46))
        hint.frame = NSRect(x: 12, y: 10, width: width, height: 16)
        hint.stringValue = overflow ? "滚动看全文 · 下滑替换 ↓" : "下滑替换 ↓"
        replacementScroll.hasVerticalScroller = overflow
        replacementScroll.frame = NSRect(x: 12, y: 34, width: width, height: viewport)
        replacementScroll.tile()
        let documentWidth = max(1, replacementScroll.contentSize.width -
            (overflow && replacementScroll.scrollerStyle == .overlay ? 16 : 0))
        height = bodyHeight(documentWidth)
        replacementBody.setFrameSize(NSSize(width: documentWidth, height: height))
        replacementScroll.contentView.scroll(to: .zero)
        replacementScroll.reflectScrolledClipView(replacementScroll.contentView)
        panel.ignoresMouseEvents = !overflow
    }

    func handlesPreviewClick(_ event: NSEvent) -> Bool {
        guard panel.isVisible, !panel.ignoresMouseEvents, event.type != .keyDown else { return false }
        let point = event.window?.convertPoint(toScreen: event.locationInWindow) ?? event.locationInWindow
        return panel.frame.contains(point)
    }

    private func present(token: String, caret: NSRect, screen: NSScreen, duration: TimeInterval?) {
        let width = panel.frame.width, height = panel.frame.height
        let visible = screen.visibleFrame.insetBy(dx: 6, dy: 6)
        var y = caret.minY - height - InputPaletteStyle.caretGap
        if y < visible.minY { y = caret.maxY + InputPaletteStyle.caretGap }
        panel.setFrameOrigin(NSPoint(x: min(max(caret.minX, visible.minX), visible.maxX - width),
                                    y: min(max(y, visible.minY), visible.maxY - height)))
        panel.level = .popUpMenu
        self.token = token
        if NSApp.isHidden { NSApp.unhideWithoutActivation() }
        panel.orderFrontRegardless()
        onVisibility?(token, true)
        if let duration {
            timer = Timer.scheduledTimer(withTimeInterval: duration, repeats: false) { [weak self] _ in self?.hide() }
        }
    }

    func hide(token expected: String? = nil) {
        if let expected, expected != token { return }
        timer?.invalidate(); timer = nil
        panel.orderOut(nil)
        panel.ignoresMouseEvents = true
        replacementScroll.isHidden = true
        replacementBody.string = ""
        text.stringValue = ""
        image.image = nil
        let previous = token; token = ""
        if !previous.isEmpty { onVisibility?(previous, false) }
    }
}
