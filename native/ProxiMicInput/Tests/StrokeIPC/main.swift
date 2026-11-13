// Real controller, IPC and palette; the editor exists only in this process.
// Never select a system input source, post keys, or write into another app.
import AppKit
import InputMethodKit
import Carbon

class IMKInputController: NSObject {
    func activateServer(_ sender: Any!) {}
    func deactivateServer(_ sender: Any!) {}
    func inputControllerWillClose() {}
    func hidePalettes() {}
    func mark(forStyle style: Int, at range: NSRange) -> [AnyHashable: Any]! { [:] }
    func recognizedEvents(_ sender: Any!) -> Int { 0 }
    func client() -> IMKTextInput? { nil }
}

final class LocalEditor: NSObject, IMKTextInput {
    var text = ProcessInfo.processInfo.environment["STROKE_TEST_TEXT"] ?? ""
    var selection = NSRange(location: Int(ProcessInfo.processInfo.environment["STROKE_TEST_CARET"] ?? "0")!, length: 0)
    var marked = unspecifiedRange
    var caret: NSRect {
        let screen = NSScreen.screens[0].visibleFrame
        return NSRect(x: screen.midX, y: screen.midY, width: 1, height: 20)
    }
    func bundleIdentifier() -> String! { "test.editor" }
    func selectedRange() -> NSRange { selection }
    func markedRange() -> NSRange { marked }
    func length() -> Int { (text as NSString).length }
    func string(from range: NSRange, actualRange: NSRangePointer!) -> String! {
        guard isValidRange(range, length: length()) else { return nil }
        actualRange?.pointee = range
        return (text as NSString).substring(with: range)
    }
    func insertText(_ value: Any!, replacementRange: NSRange) {
        let range = isValidRange(replacementRange) ? replacementRange : (isValidRange(marked) ? marked : selection)
        let string = value as! String
        text = (text as NSString).replacingCharacters(in: range, with: string)
        selection = NSRange(location: range.location + (string as NSString).length, length: 0)
        marked = unspecifiedRange
    }
    func setMarkedText(_ value: Any!, selectionRange: NSRange, replacementRange: NSRange) {
        let range = isValidRange(replacementRange) ? replacementRange : (isValidRange(marked) ? marked : selection)
        let string = (value as? NSAttributedString)?.string ?? (value as! String)
        text = (text as NSString).replacingCharacters(in: range, with: string)
        marked = string.isEmpty ? unspecifiedRange : NSRange(location: range.location, length: (string as NSString).length)
        selection = NSRange(location: range.location + selectionRange.location, length: selectionRange.length)
    }
    func attributedSubstring(from range: NSRange) -> NSAttributedString! { nil }
    func characterIndex(for point: NSPoint, tracking mode: IMKLocationToOffsetMappingMode,
                        inMarkedRange: UnsafeMutablePointer<ObjCBool>!) -> Int { NSNotFound }
    func attributes(forCharacterIndex index: Int, lineHeightRectangle: UnsafeMutablePointer<NSRect>!) -> [AnyHashable: Any]! {
        lineHeightRectangle?.pointee = caret
        return [:]
    }
    func validAttributesForMarkedText() -> [Any]! { [] }
    func overrideKeyboard(withKeyboardNamed name: String!) { fatalError("unexpected input source switch") }
    func selectMode(_ identifier: String!) { fatalError("unexpected input source switch") }
    func supportsUnicode() -> Bool { true }
    func windowLevel() -> Int32 { 0 }
    func supportsProperty(_ property: TSMDocumentPropertyTag) -> Bool { true }
    func uniqueClientIdentifierString() -> String! { "stroke-ipc-editor" }
    func firstRect(forCharacterRange range: NSRange, actualRange: NSRangePointer!) -> NSRect {
        actualRange?.pointee = range
        return caret
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let editor = LocalEditor()
let controller = ProxiMicInputController()
let service = IMEService.shared
let receive = service.transport.onMessage
func palette() -> StrokeCandidatePanel? {
    Mirror(reflecting: controller).children.first { $0.label == "strokePanel" }?.value as? StrokeCandidatePanel
}
service.transport.onMessage = { message in
    let previousText = isValidRange(editor.marked)
        ? (editor.text as NSString).replacingCharacters(in: editor.marked, with: "") : editor.text
    receive?(message)
    if message["type"] as? String == "stroke_update",
       let glyphs = message["glyphs"] as? [[String: String]],
       glyphs.contains(where: { !($0["png"] ?? "").isEmpty }), let palette = palette() {
        let panel = Mirror(reflecting: palette).children.first { $0.label == "panel" }?.value as? NSPanel
        func images(in view: NSView) -> Int {
            if view.identifier?.rawValue == "strokeClearArrow" { return 0 }
            let own = (view as? NSImageView)?.image == nil ? 0 : 1
            return own + view.subviews.reduce(0) { $0 + images(in: $1) }
        }
        let expected = glyphs.filter { !($0["png"] ?? "").isEmpty }.count
        let actual = panel.flatMap { $0.contentView }.map { images(in: $0) } ?? 0
        precondition(actual == expected,
                     "candidate glyph PNGs did not render in the AppKit palette")
        print("STROKE_GLYPHS_RENDERED")
    }
    switch message["type"] as? String {
    case "stroke_selection":
        if let palette = palette(), let panel = Mirror(reflecting: palette).children.first(where: { $0.label == "panel" })?.value as? NSPanel,
           let content = panel.contentView {
            func find(_ name: String, in view: NSView) -> NSView? {
                if view.identifier?.rawValue == name { return view }
                return view.subviews.lazy.compactMap { find(name, in: $0) }.first
            }
            content.layoutSubtreeIfNeeded()
            let pull = find("strokeClearPull", in: content)!, fill = find("strokeClearPullFill", in: content)!
            service.send(["type": "pong", "event": "test_pull_feedback", "visible": !pull.isHidden,
                          "progress": fill.frame.width / pull.frame.width])
        }
    case "test_select_to_start", "test_backspace":
        let selecting = message["type"] as? String == "test_select_to_start"
        let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: selecting ? [.command, .shift] : [],
            timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: 0, context: nil,
            characters: "", charactersIgnoringModifiers: "", isARepeat: false, keyCode: selecting ? 126 : 51)!
        precondition(!controller.handle(event, client: editor), "stroke mode must pass editor keys")
        let textView = NSTextView()
        textView.string = editor.text
        textView.setSelectedRange(editor.selection)
        if selecting { textView.moveToBeginningOfDocumentAndModifySelection(nil) }
        else { textView.deleteBackward(nil) }
        editor.text = textView.string
        editor.selection = textView.selectedRange()
        precondition(palette()?.isVisible == true, "Backspace must keep the stroke palette open")
        service.send(["type": "pong", "event": selecting ? "test_select_to_start_done" : "test_backspace_done", "text": editor.text])
    case "stroke_begin":
        precondition(palette()?.isVisible == true, "actual stroke palette did not appear")
        print("STROKE_PALETTE_VISIBLE")
    case "stroke_commit":
        precondition(!editor.text.isEmpty && palette()?.isVisible == true, "commit lost the palette or text")
        print("STROKE_COMMIT_ACKNOWLEDGED")
    case "stroke_update" where message["prediction"] as? Bool == true:
        precondition(editor.text == previousText && !isValidRange(editor.marked), "prediction changed editor text before confirmation")
        precondition(palette()?.isVisible == true, "prediction candidate palette did not appear")
        print("STROKE_PREDICTIONS_VISIBLE")
    case "stroke_end":
        precondition(palette()?.isVisible != true, "stroke palette did not close")
        print("STROKE_PALETTE_HIDDEN")
    case "test_exit":
        print("STROKE_FINAL_TEXT=" + editor.text)
        controller.deactivateServer(editor)
        service.transport.stop()
        exit(0)
    default: break
    }
    fflush(stdout)
}
controller.activateServer(editor)
service.start()
RunLoop.main.run(until: Date().addingTimeInterval(20))
service.transport.stop()
fputs("stroke IPC test timed out\n", stderr)
exit(1)
