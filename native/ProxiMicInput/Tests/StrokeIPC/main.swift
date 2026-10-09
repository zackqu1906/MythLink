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
    switch message["type"] as? String {
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
