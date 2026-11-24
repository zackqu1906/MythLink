import AppKit
import InputMethodKit
import Carbon

// A local IMK protocol endpoint backed by an actual, windowless NSTextView.
// It reproduces the two observed bridge behaviours: length()==0 and strict
// range reads, plus dropped empty insertText. No external application is used.
final class LocalUndoTextView: NSTextView {
    private let history = UndoManager()
    override var undoManager: UndoManager? { allowsUndo ? history : super.undoManager }
}

final class StrictIMKClient: NSObject, IMKTextInput {
    let view = LocalUndoTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 100))
    var reads = 0
    var markedReads = 0
    var compositionCoordinateOffset = 0
    var emptyInserts = 0
    var explicitEmptyMarks = 0
    var ignoreEmptyMark = false
    var ignoreReplacementRange = false
    var bundle = "test.strict.imk"
    var staleCompositionRead = false
    var wrongMarkedRange = false
    var relativeCompositionSelection = false
    var cachedSelectionOrigin = 0
    var acceptsEmptyInsert = true
    var preserveCommittedCaret = false
    var ignoreMarkedReplacementRange = false
    var staleDocumentRead = false
    var unavailableDocumentRead = false
    var reportedLength = 0
    var unselectedCachedSuffix = ""
    var dropNonemptyInsert = false
    var trailingParagraphSentinel = false
    var cachedParagraphSuffix = 0
    var fixedReportedSelection: NSRange?
    var fixedReportedMarkedRange: NSRange?
    var documentAccess = true
    var reportedSelections: [NSRange] = []
    var reportedMarkedRanges: [NSRange] = []
    var markCount = 0
    var explicitNonemptyMarks = 0
    var insertCount = 0
    var lastMarkedPayload: NSAttributedString?
    var onInsert: (() -> Void)?
    var onTextRead: ((NSRange) -> Void)?
    var textReadOverride: ((NSRange) -> (String?, NSRange)?)?
    var candidateRectAtIndex: ((Int) -> NSRect)?
    var candidateRect = NSRect.zero
    var candidateQueries: [Int] = []
    var rectQueries: [NSRange] = []
    var rectangleForRange: ((NSRange) -> (NSRect, NSRange))?
    init(_ text: String, selection: NSRange? = nil) {
        super.init()
        view.string = text
        view.setSelectedRange(selection ?? NSRange(location: (text as NSString).length, length: 0))
    }
    func insertText(_ string: Any!, replacementRange range: NSRange) {
        let text = string as! String
        insertCount += 1
        if dropNonemptyInsert && !text.isEmpty { return }
        if text.isEmpty {
            emptyInserts += 1
            if !acceptsEmptyInsert { return }
        }
        let oldCaret = view.selectedRange()
        let preserve = preserveCommittedCaret && isValidRange(range) && !view.hasMarkedText()
        view.insertText(text, replacementRange: ignoreReplacementRange ? unspecifiedRange : range)
        if preserve { view.setSelectedRange(NSRange(location: min(oldCaret.location, (view.string as NSString).length), length: 0)) }
        onInsert?()
    }
    func setMarkedText(_ string: Any!, selectionRange: NSRange, replacementRange range: NSRange) {
        markCount += 1
        lastMarkedPayload = string as? NSAttributedString
        let text = (string as? NSAttributedString)?.string ?? (string as! String)
        if !text.isEmpty && isValidRange(range) { explicitNonemptyMarks += 1 }
        if text.isEmpty && isValidRange(range) {
            explicitEmptyMarks += 1
            if ignoreEmptyMark { return }
        }
        view.setMarkedText(string!, selectedRange: selectionRange,
                           replacementRange: ignoreReplacementRange || ignoreMarkedReplacementRange ? unspecifiedRange : range)
    }
    var selectionReadOverride: (() -> NSRange)?
    func selectedRange() -> NSRange {
        if let override = selectionReadOverride { return override() }
        if let selected = fixedReportedSelection { return selected }
        if !reportedSelections.isEmpty { return reportedSelections.removeFirst() }
        if compositionCoordinateOffset != 0 && (view.hasMarkedText() || insertCount == 0) {
            let selected = view.selectedRange()
            return NSRange(location: selected.location + compositionCoordinateOffset, length: selected.length)
        }
        if relativeCompositionSelection && view.hasMarkedText() {
            return NSRange(location: cachedSelectionOrigin, length: view.markedRange().length)
        }
        return staleCompositionRead && view.hasMarkedText() ? view.markedRange() : view.selectedRange()
    }
    func markedRange() -> NSRange {
        markedReads += 1
        if let fixed = fixedReportedMarkedRange { return fixed }
        if !reportedMarkedRanges.isEmpty { return reportedMarkedRanges.removeFirst() }
        let range = view.markedRange()
        if compositionCoordinateOffset != 0 && view.hasMarkedText() {
            return NSRange(location: range.location + compositionCoordinateOffset, length: range.length)
        }
        return wrongMarkedRange && view.hasMarkedText() ? NSRange(location: range.location + 1, length: range.length) : range
    }
    func length() -> Int { reportedLength }
    func string(from range: NSRange, actualRange: NSRangePointer!) -> String! {
        reads += 1
        onTextRead?(range)
        if let overridden = textReadOverride?(range) {
            actualRange?.pointee = overridden.1
            return overridden.0
        }
        if unavailableDocumentRead { actualRange?.pointee = NSRange(location: 0, length: 0); return nil }
        if !unselectedCachedSuffix.isEmpty && view.selectedRange().length == 0 && !view.hasMarkedText() {
            let cached = (view.string + unselectedCachedSuffix) as NSString
            if range.length > 0 && isValidRange(range, length: cached.length) {
                actualRange?.pointee = range
                return cached.substring(with: range)
            }
        }
        if cachedParagraphSuffix > 0, range.length > 0 {
            let cached = (view.string + String(repeating: "\n", count: cachedParagraphSuffix)) as NSString
            if isValidRange(range, length: cached.length) {
                actualRange?.pointee = range
                return cached.substring(with: range)
            }
        }
        if trailingParagraphSentinel && range.location == (view.string as NSString).length && (range.length == 1 || range.length == 2) {
            actualRange?.pointee = range
            return String(repeating: "\n", count: range.length)
        }
        guard range.length > 0, isValidRange(range, length: (view.string as NSString).length) else {
            actualRange?.pointee = NSRange(location: 0, length: 0)
            return nil
        }
        actualRange?.pointee = range
        if staleDocumentRead || (staleCompositionRead && view.hasMarkedText()) {
            return String(repeating: "旧", count: range.length)
        }
        return (view.string as NSString).substring(with: range)
    }
    func attributedSubstring(from range: NSRange) -> NSAttributedString! {
        var actual = unspecifiedRange
        return string(from: range, actualRange: &actual).map { NSAttributedString(string: $0) }
    }
    func characterIndex(for point: NSPoint, tracking mappingMode: IMKLocationToOffsetMappingMode,
                        inMarkedRange: UnsafeMutablePointer<ObjCBool>!) -> Int { NSNotFound }
    func attributes(forCharacterIndex index: Int, lineHeightRectangle: UnsafeMutablePointer<NSRect>!) -> [AnyHashable: Any]! {
        candidateQueries.append(index)
        lineHeightRectangle?.pointee = candidateRectAtIndex?(index) ?? candidateRect
        return [:]
    }
    func validAttributesForMarkedText() -> [Any]! { [] }
    func overrideKeyboard(withKeyboardNamed name: String!) { fatalError("must not select a keyboard") }
    func selectMode(_ identifier: String!) { fatalError("must not select an input source") }
    func supportsUnicode() -> Bool { true }
    func bundleIdentifier() -> String! { bundle }
    func windowLevel() -> Int32 { 0 }
    func supportsProperty(_ property: TSMDocumentPropertyTag) -> Bool { documentAccess }
    func uniqueClientIdentifierString() -> String! { "test-local" }
    func firstRect(forCharacterRange range: NSRange, actualRange: NSRangePointer!) -> NSRect {
        rectQueries.append(range)
        let result = rectangleForRange?(range) ?? (.zero, unspecifiedRange)
        actualRange?.pointee = result.1
        return result.0
    }
}

var passed = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}
func test(_ name: String, _ work: () throws -> Void) {
    if let filter = ProcessInfo.processInfo.environment["TEST_FILTER"], !name.contains(filter) { return }
    do { try work(); passed += 1; print("PASS \(name)") }
    catch { fatalError("\(name): \(error)") }
}

// IMK's candidate rectangle can remain valid at the composition start while
// document-range geometry follows the actual insertion point on later lines.
let geometryScreen = NSScreen.screens[0].visibleFrame
let candidatePoint = NSPoint(x: geometryScreen.midX - 200, y: geometryScreen.midY)
let candidateRect = NSRect(origin: candidatePoint, size: NSSize(width: 1, height: 20))
test("Unavailable document selection permits repeated dictation after unsupported edit and undo") {
    for hiddenMarkedRange in [false, true] {
        let proxy = StrictIMKClient("原文")
        proxy.fixedReportedSelection = unspecifiedRange
        proxy.fixedReportedMarkedRange = hiddenMarkedRange ? unspecifiedRange : nil
        proxy.documentAccess = false
        proxy.unavailableDocumentRead = true
        proxy.reportedLength = Int(Int32.max)
        let adapter = NativeInputClient(proxy)
        adapter.configureKeyDelivery { _, _, _, _ in fatalError("unreadable selection dispatched an edit/undo key") }
        proxy.candidateRect = candidateRect
        check(adapter.caret(compositionLength: 0) == candidateRect,
              "candidate positioning incorrectly required document selection")
        let session = try CompositionSession(client: adapter, utteranceID: "unreadable-1", sequence: 1)
        var editRequests = 0
        session.onEdit = { _, _, _ in editRequests += 1 }
        session.update("第一", final: false)
        check(proxy.view.string == "原文第一" && session.phase == .listening && !session.awaitingReadback,
              "unavailable selection blocked streaming dictation")
        session.convert()
        check(!session.error.isEmpty && session.phase == .listening && editRequests == 0,
              "unsupported edit stopped dictation or called the model")
        session.cancel()
        check(!session.error.isEmpty && session.phase == .listening && !session.awaitingReadback,
              "unsupported preedit undo stopped dictation")
        session.update("第一句", final: false)
        session.finish()
        session.update("第一句。", final: true)
        check(proxy.view.string == "原文第一句。" && !proxy.view.hasMarkedText() && proxy.insertCount == 1,
              "dictation was lost, duplicated or left uncommitted")
        check(session.phase == .dictated && !session.awaitingReadback && !session.hasComposition && session.error.isEmpty,
              "unavailable final caret poisoned the completed dictation")
        check(session.undoRecord() == nil && !session.committedRangeVerified && !session.canReplace,
              "unknown selection granted document mutation authority")
        session.cancel()
        check(!session.error.isEmpty && session.historyReady && proxy.view.string == "原文第一句。",
              "unsupported undo changed the document or blocked the next sentence")
        let next = try CompositionSession(client: adapter, utteranceID: "unreadable-2", sequence: 1)
        next.update("第二", final: false)
        next.update("第二句。", final: true)
        check(next.phase == .dictated && !next.awaitingReadback && next.error.isEmpty && proxy.insertCount == 2 &&
              proxy.view.string == "原文第一句。第二句。", "subsequent dictation failed after an unsupported operation")
        let automatic = try CompositionSession(client: adapter, utteranceID: "unreadable-auto", sequence: 1)
        automatic.automaticMode = true
        automatic.update("编辑", final: false)
        automatic.setAutomaticTask("edit")
        automatic.update("编辑指令", final: true)
        check(automatic.phase == .dictated && !automatic.error.isEmpty && !automatic.hasComposition &&
              !automatic.awaitingReadback && proxy.view.string == "原文第一句。第二句。编辑指令",
              "automatic edit failure did not preserve usable dictation")
        let afterFailure = try CompositionSession(client: adapter, utteranceID: "unreadable-after-error", sequence: 1)
        afterFailure.update("继续", final: false)
        afterFailure.update("继续听写", final: true)
        check(afterFailure.phase == .dictated && afterFailure.error.isEmpty && proxy.insertCount == 4 &&
              proxy.view.string == "原文第一句。第二句。编辑指令继续听写", "automatic edit failure poisoned the next sentence")
    }
}
test("Every preedit revision uses the system raw-text marking for Chinese and digits") {
    let proxy = StrictIMKClient("原文")
    let markingController = IMKInputController()
    var requestedRanges: [NSRange] = []
    var generatedAttributes: [NSAttributedString.Key: Any] = [:]
    let adapter = NativeInputClient(proxy, preeditAttributes: { range in
        requestedRanges.append(range)
        generatedAttributes = markingController.mark(forStyle: kTSMHiliteSelectedRawText, at: range)! as! [NSAttributedString.Key: Any]
        return generatedAttributes
    })
    for text in ["中文", "中文123", "修改后的中文123\n第二行😀", "中"] {
        adapter.mark(text)
        guard let payload = proxy.lastMarkedPayload else { fatalError("preedit styling was delegated to the client's inconsistent defaults") }
        check(payload.string == text && payload.length == (text as NSString).length, "styled preedit changed content")
        let expectedRange = NSRange(location: 0, length: payload.length)
        check(requestedRanges.last == expectedRange, "system marking did not receive this full revision")
        // IMK allocates a new clause segment for each call. Compare with the
        // dictionary produced for this exact revision, not a second request.
        let expected = generatedAttributes
        for i in 0..<payload.length {
            let actual = payload.attributes(at: i, effectiveRange: nil)
            check(NSDictionary(dictionary: actual).isEqual(to: expected), "preedit changed the system marking dictionary")
            check(((payload.attribute(.underlineStyle, at: i, effectiveRange: nil) as? NSNumber)?.intValue ?? 0) != 0,
                  "a Chinese/Latin/UTF-16 character lost its preedit underline")
        }
        check(payload.attribute(.font, at: 0, effectiveRange: nil) == nil && payload.attribute(.foregroundColor, at: 0, effectiveRange: nil) == nil,
              "preedit overrode client text appearance")
        check(proxy.view.hasMarkedText(), "styling committed the composition")
    }
    adapter.commit("中")
    check(proxy.view.string == "原文中" && !proxy.view.hasMarkedText(), "styled preedit did not commit normally")
    let style = proxy.view.textStorage?.attribute(.underlineStyle, at: 2, effectiveRange: nil) as? NSNumber
    check(style == nil || style?.intValue == 0, "preedit underline leaked into committed text")
    adapter.mark("再次听写")
    adapter.commit("")
    check(proxy.view.string == "原文中" && !proxy.view.hasMarkedText(), "styled cancellation left text or a composition")
}

test("Real bridge trace: one-past-end is empty, last character follows growth and wrap") {
    let proxy = StrictIMKClient("")
    let adapter = NativeInputClient(proxy)
    // Capture reproduced in WeChat: document geometry is malformed across
    // IMK, and attributes(length) is zero while attributes(length-1) moves.
    proxy.rectangleForRange = { _ in (NSRect(x: 1.6e-314, y: 6.2e10, width: 1.6e-314, height: 1), NSRange(location: 0, length: 0)) }
    var previous: NSRect?
    for (length, dx, dy) in [(5, 41, 0), (10, 137, 0), (18, 207, 0), (28, 310, 0), (36, 407, 0), (42, 484, 0), (49, 55, -17)] {
        adapter.mark(String(repeating: "字", count: length))
        let live = candidateRect.offsetBy(dx: Double(dx), dy: Double(dy))
        proxy.candidateRectAtIndex = { $0 == length - 1 ? live : .zero }
        let result = adapter.caret(compositionLength: length)
        check(result == live, "queried one-past-end and retained the old palette position")
        if let previous { check(result != previous, "bridge fixture stopped following") }
        previous = result
    }
    check(proxy.candidateQueries == [4, 9, 17, 27, 35, 41, 48], "inline character index is not zero-based")
    check(proxy.reads == 0, "positioning unexpectedly read document text")
}
test("Late layout does not stop later caret updates") {
    let proxy = StrictIMKClient("")
    let adapter = NativeInputClient(proxy)
    adapter.mark("正在换行")
    proxy.candidateRectAtIndex = { _ in .zero }
    check(adapter.caret(compositionLength: 4) == nil, "missing layout fabricated a caret")
    let next = candidateRect.offsetBy(dx: -20, dy: -17)
    proxy.candidateRectAtIndex = { $0 == 3 ? next : .zero }
    check(adapter.caret(compositionLength: 4) == next, "later layout never refreshed caret")
}

test("Caret follows preedit tail instead of stationary candidate anchor") {
    let proxy = StrictIMKClient("已有原文")
    proxy.candidateRect = candidateRect
    let adapter = NativeInputClient(proxy)
    var lastRect: NSRect?
    for (step, partial) in ["今天", "今天下午", "今天下午开会换行继续说", "今天下午开会换行继续说😀"].enumerated() {
        adapter.mark(partial)
        let expectedRange = NSRange(location: NSMaxRange(proxy.view.markedRange()), length: 0)
        let expectedRect = NSRect(x: candidatePoint.x + Double(step % 2) * 90,
                                  y: candidatePoint.y - Double(step / 2) * 24,
                                  width: 0, height: 20)
        proxy.rectangleForRange = { requested in
            check(requested == expectedRange, "caret query used a relative or cached selection offset")
            return (expectedRect, requested)
        }
        let result = adapter.caret(compositionLength: (partial as NSString).length)
        check(result == expectedRect, "valid but stationary candidate rectangle hid the live caret")
        if let lastRect { check(result != lastRect, "caret stopped on growth or wrap") }
        lastRect = result
    }
    check(proxy.candidateQueries.isEmpty && proxy.reads == 0, "caret tracking read text or queried unnecessary fallback")
}
test("Composition geometry uses live marked range with cached relative selection") {
    let proxy = StrictIMKClient("已有原文")
    proxy.relativeCompositionSelection = true
    let adapter = NativeInputClient(proxy)
    adapter.mark("正在听写")
    let expected = NSRange(location: NSMaxRange(proxy.view.markedRange()), length: 0)
    proxy.rectangleForRange = { requested in
        check(requested == expected, "cached full-composition selection displaced caret")
        return (candidateRect, requested)
    }
    check(adapter.caret(compositionLength: 4) == candidateRect, "relative-selection client lost caret")
}
test("No composition queries current insertion point") {
    let proxy = StrictIMKClient("已有原文")
    proxy.candidateRect = candidateRect.offsetBy(dx: -80, dy: 0)
    proxy.rectangleForRange = { (candidateRect, $0) }
    check(NativeInputClient(proxy).caret(compositionLength: 0) == candidateRect, "idle caret used candidate start")
    check(proxy.rectQueries == [NSRange(location: 4, length: 0)], "idle caret queried wrong range")
}
test("Unavailable or mismatched caret geometry uses native candidate fallback") {
    for wrongRange in [false, true] {
        let proxy = StrictIMKClient("")
        proxy.candidateRect = candidateRect
        let adapter = NativeInputClient(proxy)
        adapter.mark("正在听写")
        proxy.rectangleForRange = { requested in
            wrongRange ? (candidateRect.offsetBy(dx: 99, dy: -40), NSRange(location: 0, length: requested.location)) : (.zero, unspecifiedRange)
        }
        check(adapter.caret(compositionLength: 4) == candidateRect, "invalid range geometry was treated as current caret")
        check(proxy.candidateQueries == [3], "native inline fallback used wrong index")
    }
}
test("Caret geometry rejects off-screen and nonfinite rectangles") {
    for rectangle in [NSRect(x: Double.nan, y: 0, width: 0, height: 20), NSRect(x: 1e9, y: 1e9, width: 0, height: 20)] {
        let proxy = StrictIMKClient("")
        proxy.rectangleForRange = { (rectangle, $0) }
        check(NativeInputClient(proxy).caret(compositionLength: 0) == nil, "invalid rectangle leaked to palette")
    }
}

test("Real NSTextView follows growing marked text across a wrapped line") {
    let proxy = StrictIMKClient("已有原文：")
    let window = NSWindow(contentRect: NSRect(x: candidatePoint.x, y: candidatePoint.y - 150, width: 180, height: 150),
                          styleMask: [.borderless], backing: .buffered, defer: false)
    window.isReleasedWhenClosed = false
    window.contentView = proxy.view
    proxy.view.font = .systemFont(ofSize: 16)
    proxy.view.isHorizontallyResizable = false
    proxy.view.textContainer?.widthTracksTextView = true
    proxy.view.textContainer?.containerSize = NSSize(width: 180, height: 10000)
    proxy.candidateRect = candidateRect
    proxy.rectangleForRange = { requested in
        proxy.view.layoutManager?.ensureLayout(for: proxy.view.textContainer!)
        var actual = unspecifiedRange
        let rectangle = proxy.view.firstRect(forCharacterRange: requested, actualRange: &actual)
        return (rectangle, actual)
    }
    let adapter = NativeInputClient(proxy)
    var rectangles: [NSRect] = []
    for text in ["今天", "今天下午", "今天下午我们继续听写这一段内容，直到它超过一行并且自动换行。"] {
        adapter.mark(text)
        guard let rectangle = adapter.caret(compositionLength: (text as NSString).length) else {
            fatalError("native text layout supplied no caret geometry")
        }
        rectangles.append(rectangle)
    }
    check(rectangles[0].minX != rectangles[1].minX, "native caret did not move horizontally")
    check(rectangles[2].minY < rectangles[1].minY, "native caret did not follow line wrap")
    check(proxy.candidateQueries.isEmpty, "native text layout fell back to stationary anchor")
    window.close()
}

test("Negative controls reproduce logged nil context and swallowed committed undo") {
    let proxy = StrictIMKClient("原文听写")
    proxy.acceptsEmptyInsert = false
    var actual = unspecifiedRange
    check(proxy.string(from: NSRange(location: 0, length: 32768), actualRange: &actual) == nil,
          "oversized read did not reproduce bridge failure")
    proxy.insertText("", replacementRange: NSRange(location: 2, length: 2))
    check(proxy.view.string == "原文听写", "empty insertion did not reproduce bridge failure")
}

test("Production adapter reads original when oversized query is rejected") {
    for caret in [0, 3, 7] {
        let proxy = StrictIMKClient("前😀中间尾巴", selection: NSRange(location: caret, length: 0))
        let adapter = NativeInputClient(proxy)
        let snapshot = try adapter.snapshot()
        check(!snapshot.complete && snapshot.readableContext?.text == proxy.view.string,
              "readable original was lost or labeled complete")
        check(snapshot.readableContext?.range == NSRange(location: 0, length: 7), "wrong UTF16 context range")
        check(proxy.reads <= 12, "short input performed excessive queries")
    }
}

test("IMK zero length and origin caret provide an empty source despite nil substrings") {
    let proxy = StrictIMKClient("")
    let adapter = NativeInputClient(proxy)
    let snapshot = try adapter.snapshotForBeginning()
    check(snapshot.readableContext?.text == "" && snapshot.readableContext?.range == NSRange(location: 0, length: 0),
          "an empty IMK input field was treated as missing source")
    check(!snapshot.complete && snapshot.selectedText == "", "empty prefix claimed full-document access")
    check(adapter.lastReadDiagnostics["empty_context_from_imk"] as? Bool == true, "empty context evidence was not recorded")
}

test("Missing text with a nonzero or unknown IMK length or caret does not become empty") {
    for (length, selection) in [(NSNotFound, NSRange(location: 0, length: 0)),
                                (2, NSRange(location: 0, length: 0)),
                                (0, NSRange(location: 2, length: 0)),
                                (0, NSRange(location: 0, length: 2))] {
        let proxy = StrictIMKClient("原文", selection: selection)
        proxy.reportedLength = length
        proxy.unavailableDocumentRead = true
        let snapshot = try NativeInputClient(proxy).snapshotForBeginning()
        check(snapshot.text == nil && snapshot.readableContext == nil, "unreadable nonempty context was discarded")
    }
    let proxy = StrictIMKClient("")
    let adapter = NativeInputClient(proxy)
    adapter.mark("已有组合文本")
    proxy.fixedReportedSelection = NSRange(location: 0, length: 0)
    proxy.unavailableDocumentRead = true
    let snapshot = try adapter.snapshot()
    check(snapshot.readableContext == nil, "a positive marked range was treated as an empty input field")
}

test("Codex stale substring cache does not block partials, final or live cancellation") {
    for cancel in [false, true] {
        let proxy = StrictIMKClient("原文")
        proxy.bundle = "com.openai.codex"
        proxy.staleCompositionRead = true
        let adapter = NativeInputClient(proxy)
        let session = try CompositionSession(client: adapter, utteranceID: "codex", sequence: 1)
        let reads = proxy.reads
        for text in ["今", "今天", "今天下午😀\n开会"] {
            // Real Codex switched from document [141,1] to relative [0,4].
            proxy.relativeCompositionSelection = text != "今"
            proxy.cachedSelectionOrigin = text == "今天" ? 0 : 6
            session.update(text, final: false)
            session.checkSelection()
            check(session.phase == .listening && !session.awaitingReadback && proxy.view.string == "原文" + text,
                  "Codex partial blocked on stale text cache")
        }
        check(proxy.reads == reads, "streaming still queries cached document text")
        if cancel { session.cancel() }
        else { session.update("今天下午😀\n开会。", final: true) }
        check(!session.awaitingReadback && !proxy.view.hasMarkedText(), "Codex failed to end marked text")
        check(cancel ? session.phase == .undone && proxy.view.string == "原文" : session.phase == .dictated && proxy.view.string == "原文今天下午😀\n开会。", "Codex result wrong")
    }
}

test("Codex only normalizes the full relative composition selection") {
    let proxy = StrictIMKClient(String(repeating: "原", count: 141))
    proxy.bundle = "com.openai.codex"
    let adapter = NativeInputClient(proxy)
    proxy.view.setMarkedText("今天下午", selectedRange: NSRange(location: 4, length: 0), replacementRange: unspecifiedRange)
    proxy.relativeCompositionSelection = true
    check(adapter.probe().selection == NSRange(location: 141, length: 4), "relative range not normalized")
    check(adapter.selection() == NSRange(location: 141, length: 4), "poll used unnormalized range")
    proxy.cachedSelectionOrigin = 6
    check(adapter.probe().selection == NSRange(location: 141, length: 4), "cached selection origin was not normalized")
    proxy.relativeCompositionSelection = false
    proxy.view.setSelectedRange(NSRange(location: 1, length: 0))
    check(adapter.selection() == NSRange(location: 1, length: 0), "arbitrary caret was reinterpreted")
}

test("Codex committed undo uses empty insertText, then the next sentence streams normally") {
    let proxy = StrictIMKClient("原文")
    proxy.bundle = "com.openai.codex"
    proxy.acceptsEmptyInsert = true
    proxy.ignoreEmptyMark = true
    proxy.staleCompositionRead = true
    let adapter = NativeInputClient(proxy)
    let s = try CompositionSession(client: adapter, utteranceID: "codex-undo", sequence: 1)
    s.update("听写", final: true); s.cancel()
    check(s.phase == .undone && proxy.view.string == "原文" && proxy.explicitEmptyMarks == 0,
          "Codex committed deletion used composition cancellation")
    let next = try CompositionSession(client: adapter, utteranceID: "codex-next", sequence: 1)
    next.update("继续", final: false); next.update("继续听写", final: true)
    check(next.phase == .dictated && proxy.view.string == "原文继续听写", "next sentence blocked after undo")
}

test("Negative control reproduces VS Code cancelling composition without deleting committed text") {
    let proxy = StrictIMKClient("原文" + String(repeating: "字", count: 8))
    proxy.acceptsEmptyInsert = true
    proxy.ignoreMarkedReplacementRange = true
    proxy.setMarkedText("", selectionRange: NSRange(location: 0, length: 0),
                        replacementRange: NSRange(location: 2, length: 8))
    check((proxy.view.string as NSString).length == 10 && proxy.view.selectedRange() == NSRange(location: 10, length: 0),
          "negative control did not retain the logged pre-undo caret and text")
}

test("VS Code committed undo deletes only its sentence and permits the next dictation") {
    for bundle in ["com.microsoft.VSCode", "com.microsoft.VSCodeInsiders"] {
        for prefix in ["", "已有文字", "前😀\n"] {
            let suffix = "后面的原文"
            let proxy = StrictIMKClient(prefix + suffix, selection: NSRange(location: (prefix as NSString).length, length: 0))
            proxy.bundle = bundle
            proxy.acceptsEmptyInsert = true
            proxy.ignoreMarkedReplacementRange = true
            let adapter = NativeInputClient(proxy)
            let s = try CompositionSession(client: adapter, utteranceID: "vscode-undo", sequence: 1)
            s.update("听写😀", final: false); s.update("听写😀。", final: true)
            s.cancel()
            check(s.phase == .undone && !s.awaitingReadback && proxy.view.string == prefix + suffix,
                  "VS Code committed undo still used composition cancellation")
            check(proxy.explicitEmptyMarks == 0 && proxy.emptyInserts == 1 && !proxy.view.hasMarkedText(),
                  "VS Code undo left a mark or issued a second deletion")
            let next = try CompositionSession(client: adapter, utteranceID: "vscode-next", sequence: 2)
            next.update("下一句", final: true)
            check(next.phase == .dictated && proxy.view.string == prefix + "下一句" + suffix,
                  "VS Code undo moved the next dictation out of its original range")
        }
    }
}

test("VS Code can undo past an edited history entry without abandoning earlier dictation") {
    let proxy = StrictIMKClient("原文")
    proxy.bundle = "com.microsoft.VSCode"
    proxy.acceptsEmptyInsert = true
    proxy.ignoreMarkedReplacementRange = true
    let adapter = NativeInputClient(proxy)
    let history = CompositionUndoHistory()
    history.configure(enabled: true)
    let first = try CompositionSession(client: adapter, utteranceID: "first", sequence: 1)
    first.update("甲😀", final: true); history.settled(first)
    let edit = try CompositionSession(client: adapter, utteranceID: "edit", sequence: 2)
    history.begin(edit.original)
    edit.update("扩写", final: false); edit.convert(); edit.update("扩写", final: true)
    edit.applyEdit(text: "扩写后的完整内容。", revision: edit.revision, error: nil)
    check(edit.phase == .edited, "historical edit precondition failed")
    history.settled(edit)
    let last = try CompositionSession(client: adapter, utteranceID: "last", sequence: 3)
    history.begin(last.original)
    last.update("下一句", final: true); last.cancel()
    check(last.phase == .undone && proxy.view.string == "扩写后的完整内容。", "last dictation undo failed")
    guard let editRecord = history.pop() else { fatalError("edited record was lost") }
    let restoredEdit = CompositionSession(client: adapter, undo: editRecord, utteranceID: "undo-edit", sequence: 4, revision: 10)
    restoredEdit.cancel()
    check(restoredEdit.phase == .dictated && proxy.view.string == "原文甲😀扩写", "edit did not restore its dictated instruction")
    restoredEdit.cancel()
    check(restoredEdit.phase == .undone && proxy.view.string == "原文甲😀", "restored instruction could not be undone")
    guard let firstRecord = history.pop() else { fatalError("earlier dictation was lost") }
    let restoredFirst = CompositionSession(client: adapter, undo: firstRecord, utteranceID: "undo-first", sequence: 5, revision: 20)
    restoredFirst.cancel()
    check(restoredFirst.phase == .undone && proxy.view.string == "原文" && proxy.explicitEmptyMarks == 0,
          "undo stalled when crossing the edit record")
}

test("VS Code ignores a failed committed deletion only once and never marks undo successful") {
    let proxy = StrictIMKClient("原文")
    proxy.bundle = "com.microsoft.VSCode"
    proxy.ignoreMarkedReplacementRange = true
    let adapter = NativeInputClient(proxy)
    var now: TimeInterval = 0
    proxy.acceptsEmptyInsert = false
    let s = try CompositionSession(client: adapter, utteranceID: "vscode-dropped-undo", sequence: 1, clock: { now })
    s.update("听写", final: true); s.cancel()
    check(s.awaitingReadback, "dropped deletion was falsely acknowledged")
    now = 0.6; s.advanceReadback(); s.cancel(); s.advanceReadback()
    check(s.phase == .error && proxy.view.string == "原文听写" && proxy.emptyInserts == 1 && proxy.explicitEmptyMarks == 0,
          "failed undo was retried, relabeled as success, or used a marked-text fallback")
}

test("Codex editing starts despite stale composing context, then verifies original after model response") {
    for cancel in [true, false] {
        let proxy = StrictIMKClient("明天三点开会")
        proxy.bundle = "com.openai.codex"
        proxy.staleCompositionRead = true
        proxy.relativeCompositionSelection = true
        let adapter = NativeInputClient(proxy)
        let s = try CompositionSession(client: adapter, utteranceID: "codex-edit", sequence: 1)
        var calls = 0
        s.onEdit = { original, instruction, _ in
            check(original == "明天三点开会" && instruction == "改成四点", "model input included stale composition cache")
            check(proxy.view.string == "明天三点开会改成四点" && proxy.view.hasMarkedText(), "instruction removed before model")
            calls += 1
        }
        s.update("改成", final: false); s.convert(); s.update("改成四点", final: true)
        check(calls == 1 && s.phase == .editing, "Codex model call was blocked by stale live readback")
        if cancel {
            s.cancel()
            check(s.phase == .dictated && proxy.view.string == "明天三点开会改成四点", "waiting cancel lost dictation")
        } else {
            s.applyEdit(text: "明天四点开会", revision: s.revision, error: nil)
            check(s.phase == .edited && proxy.view.string == "明天四点开会", "Codex edit not applied")
        }
    }
}

test("Negative control reproduces an expanded committed replacement retaining the old caret") {
    let original = String(repeating: "原", count: 14)
    let result = String(repeating: "新", count: 32)
    let proxy = StrictIMKClient(original)
    proxy.preserveCommittedCaret = true
    proxy.insertText(result, replacementRange: NSRange(location: 0, length: 14))
    check(proxy.view.string == result && proxy.view.selectedRange() == NSRange(location: 14, length: 0),
          "logged text-success/caret-failure was not reproduced")
}

test("Geometry-only composition still rejects wrong ranges and manual caret takeover") {
    for wrongRange in [true, false] {
        let proxy = StrictIMKClient("原文")
        proxy.bundle = "com.openai.codex"
        let adapter = NativeInputClient(proxy)
        var now: TimeInterval = 0
        let session = try CompositionSession(client: adapter, utteranceID: "codex-guard", sequence: 1, clock: { now })
        proxy.wrongMarkedRange = wrongRange
        session.update("今天", final: false)
        if wrongRange {
            now = 1.1; session.advanceReadback()
            check(session.phase == .error, "wrong composition range granted ownership")
        } else {
            proxy.view.setSelectedRange(NSRange(location: 0, length: 0))
            // Production InputController receives mouse/key events directly;
            // a polled IMK coordinate alone is no longer a takeover signal.
            session.interrupt("鼠标操作已接管本句", commitIfOwned: false)
            session.checkSelection()
            check(!session.awaitingReadback && session.phase == .interrupted, "explicit mouse takeover ignored")
        }
    }
}

test("Every client streams past stale text and relative selection, including both logged VS Code failures") {
    let original = String(repeating: "原", count: 141)
    for bundle in ["com.microsoft.VSCode", "com.microsoft.VSCodeInsiders", "com.openai.codex", "com.tencent.xinWeChat", "test.strict.imk", "unknown.editor"] {
        for caret in [0, 22, 141] {
            for cancel in [false, true] {
                let proxy = StrictIMKClient(original, selection: NSRange(location: caret, length: 0))
                proxy.bundle = bundle
                proxy.staleCompositionRead = true
                proxy.relativeCompositionSelection = true
                let adapter = NativeInputClient(proxy)
                let session = try CompositionSession(client: adapter, utteranceID: "default-stream", sequence: 1)
                let reads = proxy.reads
                for (index, text) in ["今", "今天", "今天下午😀\n开会", "明天下午😀\n开会"].enumerated() {
                    proxy.cachedSelectionOrigin = index < 2 ? 0 : 6
                    session.update(text, final: false)
                    session.checkSelection()
                    let expected = (original as NSString).replacingCharacters(in: NSRange(location: caret, length: 0), with: text)
                    check(session.phase == .listening && !session.awaitingReadback && proxy.view.string == expected,
                          "\(bundle) stalled at caret \(caret): \(session.error)")
                }
                check(proxy.reads == reads, "\(bundle) queried the document cache for a partial ACK")
                if cancel { session.cancel() }
                else { session.update("明天下午😀\n开会。", final: true) }
                let expected = cancel ? original : (original as NSString).replacingCharacters(in: NSRange(location: caret, length: 0), with: "明天下午😀\n开会。")
                check(!session.awaitingReadback && !proxy.view.hasMarkedText() && proxy.view.string == expected,
                      "\(bundle) failed to finish or cancel")
                check(cancel ? session.phase == .undone : session.phase == .dictated, "wrong final phase")
            }
        }
    }
}

test("Default geometry ACK still rejects shifted marks and a real caret move") {
    for wrongRange in [true, false] {
        let proxy = StrictIMKClient("原文")
        proxy.staleCompositionRead = true
        let adapter = NativeInputClient(proxy)
        var now: TimeInterval = 0
        let session = try CompositionSession(client: adapter, utteranceID: "default-guard", sequence: 1, clock: { now })
        proxy.wrongMarkedRange = wrongRange
        session.update("今天", final: false)
        if wrongRange {
            now = 1.1; session.advanceReadback()
            check(session.phase == .error, "wrong marked range granted ownership")
        } else {
            proxy.view.setSelectedRange(NSRange(location: 0, length: 0))
            // Production InputController receives mouse/key events directly;
            // a polled IMK coordinate alone is no longer a takeover signal.
            session.interrupt("鼠标操作已接管本句", commitIfOwned: false)
            session.checkSelection()
            check(!session.awaitingReadback && session.phase == .interrupted, "explicit mouse takeover ignored")
        }
    }
}

test("Stale or unavailable selection never delays otherwise confirmed partials") {
    for selection in [NSRange(location: 2, length: 0), unspecifiedRange] {
        let proxy = StrictIMKClient("原文")
        let adapter = NativeInputClient(proxy)
        var now: TimeInterval = 0
        let session = try CompositionSession(client: adapter, utteranceID: "selection-jitter", sequence: 1, clock: { now })
        let reads = proxy.reads
        session.update("今天", final: false)
        session.checkSelection()
        check(!session.awaitingReadback && proxy.markCount == 1, "healthy polling added a wait")
        proxy.reportedSelections = [selection, selection]
        session.checkSelection()
        check(session.active && !session.awaitingReadback, "old caret blocked live composition")
        session.update("今天下午", final: false)
        session.update("今天下午开会", final: false)
        now = 0.75; session.advanceReadback()
        check(session.phase == .listening && !session.awaitingReadback && proxy.markCount == 3,
              "caret-only uncertainty delayed a partial")
        check(proxy.view.string == "原文今天下午开会" && proxy.reads == reads,
              "resumption duplicated text or queried cached text for polling")
        session.update("今天下午开会。", final: true)
        check(session.phase == .dictated && !proxy.view.hasMarkedText(), "final blocked after transient polling mismatch")
    }
}

test("Delayed marked range is retried beyond half a second without repeating a write") {
    let proxy = StrictIMKClient("原文")
    let adapter = NativeInputClient(proxy)
    var now: TimeInterval = 0
    let session = try CompositionSession(client: adapter, utteranceID: "marked-jitter", sequence: 1, clock: { now })
    session.update("今", final: false)
    proxy.reportedMarkedRanges = Array(repeating: NSRange(location: 2, length: 1), count: 3)
    session.update("今天", final: false)
    check(session.awaitingReadback && proxy.markCount == 2, "old marked range did not defer confirmation")
    session.update("今天下午😀", final: true)
    now = 0.75; session.advanceReadback()
    check(session.active && session.awaitingReadback && proxy.markCount == 2, "mark was replayed or timed out too soon")
    now = 0.9; session.advanceReadback()
    check(session.phase == .dictated && !session.awaitingReadback && proxy.markCount == 3 && proxy.insertCount == 1,
          "final did not resume once after range recovery")
    check(proxy.view.string == "原文今天下午😀", "range recovery lost or duplicated content")
}

test("A marked range regression after confirmation is transient even with an unchanged caret") {
    let proxy = StrictIMKClient("原文")
    let adapter = NativeInputClient(proxy)
    var now: TimeInterval = 0
    let session = try CompositionSession(client: adapter, utteranceID: "range-poll", sequence: 1, clock: { now })
    session.update("今天", final: false)
    proxy.reportedMarkedRanges = [unspecifiedRange]
    session.checkSelection()
    check(session.active && session.awaitingReadback, "missing mark was ignored or ended the session immediately")
    now = 0.03; session.advanceReadback()
    check(session.phase == .listening && !session.awaitingReadback && proxy.markCount == 1,
          "one-frame range regression caused a write or a stop")
}

test("Explicit user or focus takeover still ends immediately during coordinate recovery") {
    for reason in ["鼠标操作已接管本句", "键盘输入已接管本句", "已切换输入框"] {
        let proxy = StrictIMKClient("原文")
        let adapter = NativeInputClient(proxy)
        var now: TimeInterval = 0
        let session = try CompositionSession(client: adapter, utteranceID: "explicit-takeover", sequence: 1, clock: { now })
        session.update("今天", final: false)
        proxy.reportedSelections = [NSRange(location: 0, length: 0)]
        session.checkSelection()
        session.update("今天继续听写", final: false)
        session.interrupt(reason)
        check(session.phase == .interrupted && !session.awaitingReadback, "explicit takeover waited for a timeout")
        let body = proxy.view.string
        let writes = proxy.markCount + proxy.insertCount
        now = 0.1; session.advanceReadback()
        session.update("不应出现的迟到结果", final: true)
        check(proxy.view.string == body && proxy.markCount + proxy.insertCount == writes,
              "late coordinates or ASR revived a user-ended session")
    }
}

test("Default geometry ACK does not bypass committed text checks during undo") {
    let proxy = StrictIMKClient("原文")
    let adapter = NativeInputClient(proxy)
    var now: TimeInterval = 0
    let session = try CompositionSession(client: adapter, utteranceID: "committed-guard", sequence: 1, clock: { now })
    session.update("听写", final: true)
    let writes = proxy.insertCount
    proxy.staleDocumentRead = true
    session.cancel()
    now = 0.6; session.advanceReadback()
    check(proxy.view.string == "原文听写" && proxy.insertCount == writes && proxy.explicitEmptyMarks == 0,
          "committed undo wrote without verifying the sentence")
    check(session.phase != .undone, "unverified undo was reported successful")
}

test("Default geometry ACK does not apply an edit against contradictory committed context") {
    let proxy = StrictIMKClient("明天三点开会")
    proxy.staleCompositionRead = true
    proxy.relativeCompositionSelection = true
    let adapter = NativeInputClient(proxy)
    var now: TimeInterval = 0
    let session = try CompositionSession(client: adapter, utteranceID: "edit-guard", sequence: 1, clock: { now })
    session.update("改成四点", final: false)
    session.convert()
    session.update("改成四点", final: true)
    check(session.phase == .editing, "stale live cache still blocked editing")
    proxy.staleDocumentRead = true
    session.applyEdit(text: "明天四点开会", revision: session.revision, error: nil)
    now = 0.6; session.advanceReadback()
    check(session.phase != .edited && proxy.explicitNonemptyMarks == 0,
          "model result bypassed committed-context verification")
    check(proxy.view.string == "明天三点开会" || proxy.view.string == "明天三点开会改成四点",
          "unverified model result replaced the original")
}

test("Production adapter undo replaces the exact committed range without creating preedit") {
    let proxy = StrictIMKClient("前😀后", selection: NSRange(location: 3, length: 0))
    let adapter = NativeInputClient(proxy)
    let session = try CompositionSession(client: adapter, utteranceID: "undo", sequence: 1)
    session.update("听写", final: true)
    check(session.canCancel && session.phase == .dictated, "local undo was disabled")
    session.cancel()
    check(proxy.view.string == "前😀后" && session.phase == .undone && !proxy.view.hasMarkedText(),
          "committed undo still failed through production adapter")
    check(proxy.explicitEmptyMarks == 0 && proxy.emptyInserts == 1, "undo used marked-text cancellation")
    let next = try CompositionSession(client: adapter, utteranceID: "next", sequence: 1)
    next.update("下一句", final: true)
    check(proxy.view.string == "前😀下一句后" && next.phase == .dictated, "undo left a stale composition")
}

test("Production adapter conversion removes instruction, edits original and cancels back to dictation") {
    for apply in [false, true] {
        let proxy = StrictIMKClient("明天三点开会")
        let adapter = NativeInputClient(proxy)
        let session = try CompositionSession(client: adapter, utteranceID: "edit", sequence: 1)
        var calls = 0
        session.onEdit = { original, instruction, _ in
            check(original == "明天三点开会" && instruction == "把三点改成四点", "wrong model inputs")
            check(proxy.view.string == original + instruction && proxy.view.hasMarkedText(), "instruction disappeared before model result")
            calls += 1
        }
        session.update("把三点改", final: false)
        session.convert()
        session.update("把三点改成四点", final: true)
        check(calls == 1 && session.phase == .editing, "strict IMK bridge could not start editing")
        let revision = session.revision
        if apply { session.applyEdit(text: "明天四点开会", revision: revision, error: nil) }
        session.cancel()
        check(proxy.view.string == "明天三点开会把三点改成四点" && session.phase == .dictated, "cancel lost dictated sentence")
        session.applyEdit(text: "迟到", revision: revision, error: nil)
        session.cancel()
        check(proxy.view.string == "明天三点开会" && session.phase == .undone, "second cancel did not restore original")
    }
}

test("A swallowed explicit deletion fails once and records the real desired caret") {
    let proxy = StrictIMKClient("已有文字")
    proxy.acceptsEmptyInsert = false
    proxy.ignoreEmptyMark = true
    let adapter = NativeInputClient(proxy)
    var now: TimeInterval = 0
    let session = try CompositionSession(client: adapter, utteranceID: "failure", sequence: 1, clock: { now })
    session.update("听写", final: true)
    session.cancel()
    check(session.awaitingReadback, "ignored deletion was reported successful")
    now = 0.6
    session.advanceReadback()
    check(session.phase == .error && proxy.emptyInserts == 1 && proxy.explicitEmptyMarks == 0 && proxy.view.string == "已有文字听写", "failed deletion repeated or lost text")
    check(session.readbackDiagnostics["expected_selection"] as? [Int] == [4, 0] &&
          session.readbackDiagnostics["observed_selection"] as? [Int] == [6, 0], "diagnostics hid failed caret movement")
}

test("Context reader accepts bounded windows and rejects inconsistent range evidence") {
    let selection = NSRange(location: 100, length: 0)
    var queries = 0
    let context = EditorContextReader.read(around: selection) { range in
        queries += 1
        let actual = NSIntersectionRange(range, NSRange(location: 90, length: 20))
        guard actual.length > 0 else { return nil }
        return EditorTextRange(text: String(repeating: "字", count: actual.length), range: actual)
    }
    check(context?.range == NSRange(location: 90, length: 20) && queries == 1, "clipped window lost its actual range")
    for bad in [EditorTextRange(text: "x", range: NSRange(location: 99, length: 5)),
                EditorTextRange(text: "xx", range: NSRange(location: 0, length: 2)),
                EditorTextRange(text: "", range: unspecifiedRange)] {
        queries = 0
        let invalid = EditorContextReader.read(around: selection) { _ in queries += 1; return bad }
        check(invalid == nil && queries <= 20, "bad metadata granted context or caused excessive reads")
    }
}

test("Unknown or invalid context never becomes an empty document") {
    var queries = 0
    for selection in [NSRange(location: 0, length: 0), NSRange(location: 200, length: 0)] {
        let missing = EditorContextReader.read(around: selection) { _ in queries += 1; return nil }
        check(missing == nil, "nil was treated as an empty document")
    }
    let previous = queries
    for selection in [unspecifiedRange, NSRange(location: Int.max - 1, length: 1), NSRange(location: 0, length: 100_000)] {
        check(EditorContextReader.read(around: selection) { _ in queries += 1; return nil } == nil,
              "invalid range was accepted")
    }
    check(previous == queries, "invalid ranges reached the client")
}

test("Deletion delivery never falls back to an unspecified range or repeats a write") {
    let range = NSRange(location: 89, length: 6)
    var insertedRanges: [NSRange] = []
    CommittedReplacementDelivery.send("", range: range, insert: { _, actual in insertedRanges.append(actual) })
    CommittedReplacementDelivery.send("", range: NSRange(location: 89, length: 0), insert: { _, actual in insertedRanges.append(actual) })
    CommittedReplacementDelivery.send("", range: unspecifiedRange, insert: { _, actual in insertedRanges.append(actual) })
    check(insertedRanges == [range], "empty deletion wrote at an unknown range or repeated an operation")
}

final class WeChatFixture {
    let proxy: StrictIMKClient
    let adapter: NativeInputClient
    var now: TimeInterval = 0
    var keys: [String] = []
    var undoText = ""
    var keyError: String?
    var dropSelectAll = false
    var dropSelectToStart = false
    var dropSentenceSelection = false
    var dropCaretMovement = false
    var reportedEndOffset = 0
    var keyCounts: [Int] = []
    var afterKey: ((String, Int) -> Void)?
    var deferKeyAck = false
    var pendingKeyAck: ((String?) -> Void)?
    init(_ text: String, caret: Int? = nil, codex: Bool = false, application: String? = nil, directRange: Bool = true, prefixOnly: Bool = false, selectionDriver: PrefixSelectionDriver? = nil, isCurrent: @escaping () -> Bool = { true }) {
        proxy = StrictIMKClient(text, selection: caret.map { NSRange(location: $0, length: 0) })
        proxy.ignoreReplacementRange = !codex
        if codex {
            proxy.bundle = "com.openai.codex"
            proxy.preserveCommittedCaret = true
            proxy.ignoreMarkedReplacementRange = true
            proxy.acceptsEmptyInsert = true
        }
        proxy.bundle = application ?? (codex ? "com.openai.codex" : "com.tencent.xinWeChat")
        adapter = NativeInputClient(proxy, isCurrent: isCurrent)
        let deliver: WeChatCompatibility.SendKey = { [unowned self] command, count, _, done in
            keys.append(command); keyCounts.append(count)
            if let keyError { done(keyError); return }
            switch command {
            case "select_to_start":
                if !dropSelectToStart { proxy.view.moveToBeginningOfDocumentAndModifySelection(nil) }
            case "select_all":
                if !dropSelectAll { proxy.view.setSelectedRange(NSRange(location: 0, length: (proxy.view.string as NSString).length)) }
            case "undo":
                proxy.view.string = undoText
                proxy.view.setSelectedRange(NSRange(location: (undoText as NSString).length, length: 0))
            case "select_previous":
                if !dropSentenceSelection {
                    for _ in 0..<count { proxy.view.moveLeftAndModifySelection(nil) }
                }
            case "delete":
                proxy.view.deleteBackward(nil)
            case "caret_from_end":
                let prefix = String(proxy.view.string.dropLast(count))
                proxy.view.setSelectedRange(NSRange(location: (prefix as NSString).length, length: 0))
            case "caret_to_end":
                if !dropCaretMovement { proxy.view.moveToEndOfDocument(nil) }
                if reportedEndOffset != 0 {
                    proxy.reportedSelections = [NSRange(location: proxy.view.selectedRange().location + reportedEndOffset, length: 0)]
                }
            case "caret_backward":
                if !dropCaretMovement { for _ in 0..<count { proxy.view.moveLeft(nil) } }
            case "caret_forward":
                if !dropCaretMovement { for _ in 0..<count { proxy.view.moveRight(nil) } }
            default: fatalError("unexpected command")
            }
            afterKey?(command, count)
            if deferKeyAck { pendingKeyAck = done } else { done(nil) }
        }
        adapter.configureKeyDelivery(deliver, clock: { [unowned self] in now }, selectionDriver: selectionDriver, prefixOnly: prefixOnly)
        // Exercise the former direct-range strategy independently. Production
        // factory cases below use directRange:false and native selection.
        if codex && directRange {
            adapter.nativeCompatibility = WeChatCompatibility(client: adapter, sendKey: deliver, clock: { [unowned self] in now })
        }
    }
    func session() throws -> CompositionSession {
        try CompositionSession(client: adapter, utteranceID: "wechat", sequence: 1, clock: { [unowned self] in now })
    }
    func drain(_ session: CompositionSession) {
        for _ in 0..<60 {
            now += 0.06
            adapter.advanceClientOperation()
            session.advanceReadback()
            if !adapter.clientOperationPending && !session.awaitingReadback { return }
        }
        fatalError("operation hung")
    }
}

test("Production adapters undo and redo dictation and edit steps without live composition or another model call") {
    for app in ["com.tencent.xinWeChat", "com.openai.codex", "com.microsoft.VSCode", "test.editor"] {
        let f = WeChatFixture("前文", codex: app != "com.tencent.xinWeChat", application: app, directRange: false)
        let history = CompositionUndoHistory()
        var session = try f.session()
        func wire() {
            session.onSettled = { _, _ in history.settled(session) }
            session.onInterrupted = { _ in history.clear() }
        }
        func undo() { history.undo(session); f.drain(session) }
        func redo() {
            guard let step = history.prepareRedo(session) else { fatalError("missing redo: \(app)") }
            session = CompositionSession(client: f.adapter, undo: step.result,
                utteranceID: session.utteranceID, sequence: session.lastSequence, revision: session.revision + 1,
                clock: { f.now })
            wire()
            session.onEdit = { _, _, _ in fatalError("redo called the model") }
            session.onPolish = { _, _ in fatalError("redo called the model") }
            session.restoreRedo(step); f.drain(session)
        }
        wire()
        session.update("😀本句", final: true)
        let before = f.proxy.view.string, caret = f.proxy.view.selectedRange()
        undo(); check(f.proxy.view.string == "前文", "adapter undo failed: \(app)")
        redo(); check(f.proxy.view.string == before && f.proxy.view.selectedRange() == caret,
                      "adapter redo changed text or caret: \(app) \(session.error)")
        undo(); redo(); undo()
        check(f.proxy.view.string == "前文" && history.count == 0, "adapter alternation corrupted history")

        history.clear()
        session = try f.session(); wire()
        session.update("改成新文", final: false); session.convert(); session.update("改成新文", final: true)
        session.applyEdit(text: "新文", revision: session.revision, error: nil); f.drain(session)
        check(session.phase == .edited && f.proxy.view.string == "新文", "edit setup failed: \(app)")
        undo(); check(f.proxy.view.string == "前文改成新文", "edit restore failed: \(app)")
        undo(); check(f.proxy.view.string == "前文", "instruction undo failed: \(app)")
        redo(); check(f.proxy.view.string == "前文改成新文", "instruction redo failed: \(app)")
        redo(); check(f.proxy.view.string == "新文" && !f.proxy.view.hasMarkedText(), "edit redo failed: \(app)")
    }
}

for partial in [false, true] {
    for (instruction, result) in [("帮我回复一下", "好的，我会处理。"), ("加一句谢谢", "谢谢")] {
        test("WeChat empty-source generation, replacement and undo: \(instruction), partial=\(partial)") {
            let f = WeChatFixture("")
            let s = try f.session()
            s.automaticMode = true
            var requests = 0
            s.onEdit = { source, spoken, _ in
                requests += 1
                check(source.isEmpty && spoken == instruction, "empty-source request changed its inputs")
            }
            check(s.editContextAvailable && s.editRequestText == "", "empty field cannot participate in routing")
            if partial { s.update("帮我", final: false) }
            s.setAutomaticTask("edit")
            s.update(instruction, final: true); f.drain(s)
            check(requests == 1 && s.phase == .editing && s.error.isEmpty, "empty source blocked generation")
            check(f.keys.isEmpty && f.proxy.view.string == instruction && f.proxy.view.hasMarkedText(),
                  "instruction was committed or removed before generation completed")
            s.applyEdit(text: result, revision: s.revision, error: nil); f.drain(s)
            check(s.phase == .edited && s.error.isEmpty && f.proxy.view.string == result && !f.proxy.view.hasMarkedText(),
                  "generated text did not replace the owned instruction")
            s.cancel(); f.drain(s)
            check(s.phase == .dictated && f.proxy.view.string == instruction, "undo lost the spoken instruction")
            s.cancel(); f.drain(s)
            check(s.phase == .undone && f.proxy.view.string.isEmpty, "second undo did not restore the empty field")
            check(requests == 1, "replacement or undo issued another model request")
        }
    }
}

test("WeChat automatic editing trusts its route while manual editing still needs text") {
    for automatic in [false, true] {
        let f = WeChatFixture("")
        let s = try f.session()
        s.automaticMode = automatic
        var requests = 0
        s.onEdit = { _, _, _ in requests += 1 }
        s.update("把三点改成四点", final: false)
        if automatic { s.setAutomaticTask("edit") } else { s.convert() }
        s.update("把三点改成四点", final: true); f.drain(s)
        check(requests == (automatic ? 1 : 0) && s.phase == (automatic ? .editing : .dictated),
              "automatic routing was revalidated or manual editing changed")
        check(f.proxy.view.string == "把三点改成四点", "source validation changed the instruction")
    }
}

test("Default client configuration selects before replacement in every application") {
    for bundle in ["com.openai.codex", "com.microsoft.VSCode", "com.apple.TextEdit", "com.apple.Safari", "org.example.Editor"] {
        let f = WeChatFixture("前😀原文后缀", caret: 3, codex: true, application: bundle, directRange: false)
        check(f.adapter.nativeCompatibility != nil && f.adapter.compatibility == nil,
              "default client did not receive native key operations")
        let s = try f.session()
        s.onEdit = { source, _, _ in check(source == "前😀", "default model received text after the caret") }
        s.update("扩写", final: false); s.convert(); s.update("扩写", final: true)
        s.applyEdit(text: "扩写后的完整内容😀\n末尾。", revision: s.revision, error: nil); f.drain(s)
        check(f.proxy.view.string == "扩写后的完整内容😀\n末尾。原文后缀", "default edit changed the suffix")
        check(s.phase == .edited && f.proxy.view.selectedRange() == NSRange(location: ("扩写后的完整内容😀\n末尾。" as NSString).length, length: 0),
              "default replacement did not restore the boundary before the unchanged suffix")
        s.cancel(); f.drain(s)
        check(s.phase == .dictated && f.proxy.view.string == "前😀扩写原文后缀" && f.proxy.view.selectedRange().location == 5,
              "default edit undo did not restore the middle insertion point")
        s.cancel(); f.drain(s)
        check(s.phase == .undone && f.proxy.view.string == "前😀原文后缀", "default sentence undo failed")
        check(f.keys.contains("select_previous") && f.keys.contains("delete") && f.keys.contains("select_all") &&
              !f.keys.contains("copy") && f.proxy.explicitEmptyMarks == 0,
              "default flow regressed to whole-field selection or empty marked replacement")
        let next = try f.session(); next.update("继续", final: true)
        check(next.phase == .dictated && f.proxy.view.string == "前😀继续原文后缀", "next sentence did not resume after undo")
    }
}

test("A default client's dropped sentence-selection key never deletes neighbouring text") {
    let f = WeChatFixture("原文", codex: true, application: "org.example.Editor")
    let s = try f.session(); s.update("听写", final: true)
    f.dropSentenceSelection = true
    s.cancel(); f.drain(s)
    check(s.phase == .error && f.proxy.view.string == "原文听写" && f.keys == ["select_previous"],
          "default undo deleted an unconfirmed range or repeated keys")
}

test("Production edit paths preserve selected text and suffix when editing before the selection") {
    for bundle in ["com.tencent.xinWeChat", "com.microsoft.VSCode", "com.openai.codex"] {
        for candidate in ["新的前文😀", ""] {
            let prefix = "前文😀\n"
            let suffix = "选中后文\n保留😀"
            let selection = NSRange(location: (prefix as NSString).length, length: 2)
            let f = WeChatFixture(prefix + suffix, codex: bundle != "com.tencent.xinWeChat", application: bundle, directRange: false)
            f.proxy.view.setSelectedRange(selection)
            let s = try f.session()
            var requests = 0
            s.onEdit = { source, instruction, _ in
                check(source == prefix && instruction == "修改前文", "model received selected text or suffix")
                requests += 1
            }
            s.update("修改", final: false); s.convert(); s.update("修改前文", final: true)
            s.applyEdit(text: candidate, revision: s.revision, error: nil); f.drain(s)
            check(requests == 1 && s.phase == .edited && f.proxy.view.string == candidate + suffix,
                  "native edit lost selected text or suffix: \(bundle), \(s.error)")
            check(f.proxy.view.selectedRange() == NSRange(location: (candidate as NSString).length, length: 0),
                  "edit caret did not return before preserved selection: \(bundle)")
            s.cancel(); f.drain(s)
            check(s.phase == .dictated && f.proxy.view.string == prefix + "修改前文后文\n保留😀", "native edit undo lost instruction")
            s.cancel(); f.drain(s)
            check(s.phase == .undone && f.proxy.view.string == prefix + suffix,
                  "native sentence undo lost original selection: bundle=\(bundle), candidate=\(candidate), phase=\(s.phase), error=\(s.error), body=\(f.proxy.view.string.debugDescription), keys=\(f.keys), selection=\(f.proxy.view.selectedRange()), diagnostics=\(s.readbackDiagnostics)")
        }
    }
}

test("Negative control reproduces Codex appending an explicit marked-text replacement") {
    let f = WeChatFixture(String(repeating: "原", count: 67), caret: 65, codex: true)
    f.proxy.setMarkedText(String(repeating: "新", count: 55), selectionRange: NSRange(location: 55, length: 0),
                          replacementRange: NSRange(location: 0, length: 67))
    check((f.proxy.view.string as NSString).length == 122 && f.proxy.view.markedRange() == NSRange(location: 65, length: 55),
          "logged Codex marked replacement failure was not reproduced")
}

test("Codex edits write once without marked replacement and continue at the new end") {
    let cases: [(Int, Int, String)] = [
        (14, 14, String(repeating: "扩", count: 32)), (14, 14, "短"),
        (14, 14, "第一行😀\n第二行👨‍👩‍👧‍👦结束"),
        (67, 65, String(repeating: "新", count: 55)),
        (67, 65, String(repeating: "扩", count: 131)),
        (14, 7, String(repeating: "长", count: 2000))]
    for (size, caret, result) in cases {
        let original = String(repeating: "原", count: size)
        let document = result + (original as NSString).substring(from: caret)
        let f = WeChatFixture(original, caret: caret, codex: true)
        let s = try f.session()
        s.update("修改原文", final: false); s.convert(); s.update("修改原文", final: true)
        let writes = f.proxy.insertCount
        s.applyEdit(text: result, revision: s.revision, error: nil); f.drain(s)
        check(s.phase == .edited && f.proxy.view.string == document,
              "Codex edit failed: size=\(size) caret=\(caret) result=\((result as NSString).length) phase=\(s.phase) error=\(s.error) selection=\(f.proxy.view.selectedRange()) keys=\(f.keys) body=\((f.proxy.view.string as NSString).length) diagnostics=\(s.readbackDiagnostics)")
        check(f.proxy.insertCount == writes + 2 && f.proxy.explicitNonemptyMarks == 0 && !f.proxy.view.hasMarkedText(),
              "result was rewritten or left in composition")
        check(f.proxy.view.selectedRange() == NSRange(location: (result as NSString).length, length: 0), "caret not at edited prefix end")
        check(f.keys == (caret == size ? ["caret_to_end"] : ["caret_to_end", "caret_backward"]), "caret positioning used full selection or repeated keys")
        let record = s.undoRecord()!
        let next = try f.session()
        next.update("下一句", final: true)
        check(f.proxy.view.string == result + "下一句" + (original as NSString).substring(from: caret), "next dictation did not continue before preserved suffix")
        next.cancel(); f.drain(next)
        check(next.phase == .undone && f.proxy.view.string == document, "next dictation undo failed")
        let restored = CompositionSession(client: f.adapter, undo: record, utteranceID: "history", sequence: 2, revision: 2,
                                          clock: { f.now })
        restored.cancel(); f.drain(restored)
        let dictated = (original as NSString).replacingCharacters(in: NSRange(location: caret, length: 0), with: "修改原文")
        check(restored.phase == .dictated && f.proxy.view.string == dictated, "historical edit restore failed")
        check(f.proxy.explicitNonemptyMarks == 0 && !f.keys.contains("select_all"), "restore used marked/full-selection replacement")
        restored.cancel(); f.drain(restored)
        check(restored.phase == .undone && f.proxy.view.string == original, "middle instruction could not be undone after restore")
    }
}

test("Codex mixed history continues after an edit restore receives the stale pre-replacement caret") {
    let f = WeChatFixture("\n\n", caret: 0, codex: true)
    let history = CompositionUndoHistory(); history.configure(enabled: true)
    for raw in ["一二三四五六", "一二三四五六七", "一二三四五六七八"] {
        let s = try f.session(); history.begin(s.original)
        s.update(raw, final: true); history.settled(s)
    }
    let edit = try f.session(); history.begin(edit.original)
    edit.update("一二三四五六", final: false); edit.convert(); edit.update("一二三四五六", final: true)
    edit.applyEdit(text: String(repeating: "新", count: 31), revision: edit.revision, error: nil); f.drain(edit)
    history.settled(edit)
    let last = try f.session(); history.begin(last.original)
    last.update("下一句话", final: true); last.cancel(); f.drain(last)
    let restored = CompositionSession(client: f.adapter, undo: history.pop()!, utteranceID: "history",
                                      sequence: 2, revision: 2, clock: { f.now })
    // Real log: restored text is 29 UTF-16 units, target 27, cached caret 31.
    // Cmd+Down actually reaches 29; the stale 31 causes four Left keys -> 25.
    f.reportedEndOffset = 2; f.proxy.cachedParagraphSuffix = 2
    let writes = f.proxy.insertCount
    let started = f.now
    restored.cancel(); f.drain(restored)
    check(restored.phase == .dictated && f.proxy.view.selectedRange() == NSRange(location: 27, length: 0),
          "stale caret broke historical edit restore: \(restored.error), \(f.keys)")
    check(f.proxy.insertCount == writes + 1 && f.keys.suffix(3) == ["caret_to_end", "caret_backward", "caret_forward"],
          "caret recovery rewrote text or did not correct the overshoot")
    check(f.now - started < 0.3, "corrected restore waited for the error timeout")
    f.reportedEndOffset = 0; f.proxy.cachedParagraphSuffix = 0
    restored.cancel(); f.drain(restored)
    check(restored.phase == .undone, "restored edit instruction could not be removed")
    while let record = history.pop() {
        let prior = CompositionSession(client: f.adapter, undo: record, utteranceID: "history",
                                       sequence: 2, revision: 4, clock: { f.now })
        prior.cancel(); f.drain(prior)
        check(prior.phase == .undone, "older dictation failed after edit undo")
    }
    check(f.proxy.view.string == "\n\n" && f.proxy.view.selectedRange() == NSRange(location: 0, length: 0),
          "mixed history did not return to its original body and caret")
    check(!f.keys.contains("select_all"), "history recovery selected the whole field")
}

test("Codex restore does not repeat a move while selection readback is unchanged") {
    let f = WeChatFixture("旧结果", codex: true)
    f.adapter.replaceEditing(NSRange(location: 0, length: 3), with: "原文指令后缀", restoringCaret: 4)
    f.adapter.advanceClientOperation() // Native end at 6.
    f.now += 0.06; f.adapter.advanceClientOperation() // Two Left keys -> 4.
    f.proxy.reportedSelections = Array(repeating: NSRange(location: 6, length: 0), count: 3)
    for _ in 0..<3 { f.now += 0.06; f.adapter.advanceClientOperation() }
    check(f.keys == ["caret_to_end", "caret_backward"] && f.proxy.view.selectedRange().location == 4,
          "stale selection repeated an already-delivered motion")
    f.now += 0.06; f.adapter.advanceClientOperation()
    check(!f.adapter.clientOperationPending && f.adapter.clientOperationFailure == nil,
          "fresh selection did not finish the restore")
}

test("Codex caret waits for result text and never resends a replacement") {
    let f = WeChatFixture("原文", codex: true)
    f.adapter.replace(NSRange(location: 0, length: 2), with: "确认后的编辑结果")
    f.proxy.staleDocumentRead = true
    f.now += 0.1; f.adapter.advanceClientOperation()
    check(f.keys.isEmpty && f.adapter.clientOperationPending, "moved caret before result text was confirmed")
    f.proxy.staleDocumentRead = false
    f.adapter.advanceClientOperation(); f.now += 0.1; f.adapter.advanceClientOperation()
    check(f.keys == ["caret_to_end"] && f.proxy.insertCount == 1 && !f.adapter.clientOperationPending,
          "delayed acknowledgement repeated text or caret motion")
}

test("Codex bounded replacement also puts the caret at the full document end without changing its suffix") {
    let f = WeChatFixture("前原文后文", caret: 1, codex: true)
    f.adapter.replace(NSRange(location: 1, length: 2), with: "扩😀写")
    f.adapter.advanceClientOperation(); f.now += 0.1; f.adapter.advanceClientOperation()
    check(f.proxy.view.string == "前扩😀写后文" && f.proxy.view.selectedRange() == NSRange(location: 7, length: 0),
          "bounded edit did not reach full document end or changed the suffix")
    check(f.keys == ["caret_to_end"] && !f.adapter.clientOperationPending, "bounded edit did not use a single end command")
}

test("Codex independently delayed caret acknowledgement is observed without extra writes or keys") {
    let f = WeChatFixture("原文", codex: true)
    f.dropCaretMovement = true
    f.adapter.replace(NSRange(location: 0, length: 2), with: "恢复后的听写结果")
    f.proxy.view.setSelectedRange(NSRange(location: 1, length: 0))
    f.adapter.advanceClientOperation()
    check(f.adapter.clientOperationPending && f.adapter.clientOperationFailure == nil && f.keys == ["caret_to_end"],
          "interim selection blocked the end command")
    f.now += 0.1
    f.proxy.view.setSelectedRange(NSRange(location: 8, length: 0))
    f.adapter.advanceClientOperation()
    check(!f.adapter.clientOperationPending && f.adapter.clientOperationFailure == nil && f.keys == ["caret_to_end"] && f.proxy.insertCount == 1,
          "late correct caret did not finish without another write")
}

test("Codex visual end is accepted when IMK exposes a trailing paragraph sentinel") {
    let f = WeChatFixture("原文", codex: true)
    f.proxy.trailingParagraphSentinel = true
    f.adapter.replace(NSRange(location: 0, length: 2), with: "确认的结果")
    f.adapter.advanceClientOperation(); f.now += 0.1; f.adapter.advanceClientOperation()
    check(f.keys == ["caret_to_end"] && !f.adapter.clientOperationPending && f.adapter.clientOperationFailure == nil,
          "already-correct caret waited on an irrelevant EOF probe")
    check(f.adapter.verifiedReplacementCaret == NSRange(location: 5, length: 0) && f.proxy.insertCount == 1,
          "visual end was not acknowledged or result was rewritten")
}

test("Codex leaves editing immediately after caret delivery without waiting for EOF timeout") {
    let f = WeChatFixture("原文", codex: true)
    let s = try f.session()
    s.update("扩写", final: false); s.convert(); s.update("扩写", final: true)
    f.proxy.trailingParagraphSentinel = true
    s.applyEdit(text: "已经替换的结果", revision: s.revision, error: nil)
    f.drain(s)
    check(s.phase == .edited && !s.awaitingReadback && s.error.isEmpty && f.now <= 0.15,
          "loading state persisted after text and caret were confirmed")
}

test("Codex dropped caret motion fails without duplicate text or a pending composition") {
    let f = WeChatFixture("原文", codex: true)
    f.dropCaretMovement = true
    f.adapter.replace(NSRange(location: 0, length: 2), with: "编辑后的完整结果")
    f.adapter.advanceClientOperation()
    for _ in 0..<30 { f.now += 0.1; f.adapter.advanceClientOperation() }
    check(f.proxy.view.string == "编辑后的完整结果" && f.proxy.insertCount == 1 && f.proxy.explicitNonemptyMarks == 0,
          "failed caret movement rewrote text")
    check(f.keys == ["caret_to_end"] && f.adapter.clientOperationFailure != nil && !f.adapter.clientOperationPending,
          "failed movement was accepted or retried")
}

test("Codex controller takeover or cancellation prevents late movement") {
    for cancel in [true, false] {
        let f = WeChatFixture("原文", codex: true)
        if cancel { f.proxy.onInsert = { f.adapter.cancelClientOperation() } }
        f.adapter.replace(NSRange(location: 0, length: 2), with: "扩写结果")
        if !cancel {
            f.proxy.view.setSelectedRange(NSRange(location: 1, length: 0))
            f.adapter.cancelClientOperation()
        }
        f.adapter.advanceClientOperation(); f.now += 3; f.adapter.advanceClientOperation()
        check(f.keys.isEmpty && f.proxy.insertCount == 1 && !f.adapter.clientOperationPending, "takeover moved caret or wrote again")
    }
}

test("Codex caret commands are admitted only while the exact native motion is pending") {
    let f = WeChatFixture("原文", codex: true)
    f.dropCaretMovement = true
    f.adapter.replace(NSRange(location: 0, length: 2), with: "扩写的结果")
    f.adapter.advanceClientOperation()
    let helper = f.adapter.nativeCompatibility!
    check(!helper.acceptsKey(code: 0, command: true, otherModifiers: false, tag: 0), "accepted select-all")
    check(helper.acceptsKey(code: 125, command: true, otherModifiers: false, tag: 0), "native end key was mistaken for manual takeover")
    check(!helper.acceptsKey(code: 125, command: true, otherModifiers: false, tag: 0), "admitted repeated key")
}

test("Negative control reproduces Qt ignoring a nonempty replacement range") {
    let f = WeChatFixture("今天三点开会")
    f.proxy.insertText("今天四点开会", replacementRange: NSRange(location: 0, length: 6))
    check(f.proxy.view.string == "今天三点开会今天四点开会", "ignored replacement was not reproduced")
}

test("WeChat edits original full text and cancel restores dictation then undoes only that sentence") {
    let f = WeChatFixture("今天三点开会")
    let s = try f.session()
    var modelOriginal = ""
    s.onEdit = { original, instruction, _ in
        modelOriginal = original
        check(instruction == "把三点改成四点", "instruction changed")
    }
    s.update("把三点改成四点", final: false); s.convert(); s.update("把三点改成四点", final: true)
    f.drain(s)
    check(s.phase == .editing && modelOriginal == "今天三点开会", "full original was not captured")
    check(f.keys.isEmpty && f.proxy.view.hasMarkedText() && f.proxy.view.string == "今天三点开会把三点改成四点", "WeChat selected or removed text before model result")
    s.applyEdit(text: "今天四点开会", revision: s.revision, error: nil); f.drain(s)
    check(s.phase == .edited && f.proxy.view.string == "今天四点开会", "edit appended or failed")
    check(!f.keys.contains("caret_from_end"), "already correct caret caused an unnecessary key")
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "今天三点开会把三点改成四点", "restore did not recover dictation")
    s.cancel(); f.drain(s)
    check(s.phase == .undone && f.proxy.view.string == "今天三点开会", "second cancel lost original")
    check(!f.keys.contains("undo"), "restored edit must not trust native undo history")
}

test("WeChat committed undo selects only the sentence and uses one Backspace regardless of undo history") {
    for nativeResult in ["原文", "原文听", ""] {
        let f = WeChatFixture("原文")
        f.undoText = nativeResult
        let s = try f.session()
        s.update("听写", final: true)
        s.cancel(); f.drain(s)
        check(s.phase == .undone && f.proxy.view.string == "原文", "native undo grouping damaged original")
        check(f.keys == ["select_previous", "delete"], "ordinary undo selected all or depended on native undo")
    }
}

test("WeChat live cancellation clears only the IME composition without any shortcut") {
    let f = WeChatFixture("原文")
    let s = try f.session()
    s.update("未定稿", final: false); s.cancel(); f.drain(s)
    check(s.phase == .undone && f.proxy.view.string == "原文" && f.keys.isEmpty,
          "live undo selected or deleted committed text")
}

test("WeChat sentence undo handles emoji, suffix and original replaced selection") {
    let f = WeChatFixture("前原文后", caret: 3)
    f.proxy.view.setSelectedRange(NSRange(location: 1, length: 2))
    let s = try f.session()
    s.update("👨‍👩‍👧‍👦听写", final: true); s.cancel(); f.drain(s)
    check(s.phase == .undone && f.proxy.view.string == "前原文后", "selection restore lost text")
    check(f.keys == ["select_previous"], "restoring a selection used whole-field commands")
}

test("A dropped sentence selection never sends Backspace") {
    let f = WeChatFixture("原文")
    let s = try f.session()
    s.update("听写", final: true)
    f.dropSentenceSelection = true
    s.cancel(); f.drain(s)
    check(s.phase == .error && f.proxy.view.string == "原文听写" && f.keys == ["select_previous"],
          "unconfirmed sentence selection deleted text")
}

test("WeChat undo preserves suffix and UTF16 caret with emoji") {
    let f = WeChatFixture("前😀后文", caret: 3)
    f.undoText = "前😀后文"
    let s = try f.session()
    s.update("新增", final: true); s.cancel(); f.drain(s)
    check(s.phase == .undone && f.proxy.view.string == "前😀后文", "suffix lost")
    check(f.proxy.view.selectedRange() == NSRange(location: 3, length: 0), "UTF16 caret changed")
}

test("WeChat empty result and original empty document do not leave a composition") {
    let f = WeChatFixture("原文")
    let s = try f.session()
    s.update("删除全文", final: false); s.convert(); s.update("删除全文", final: true); f.drain(s)
    s.applyEdit(text: "", revision: s.revision, error: nil); f.drain(s)
    check(s.phase == .edited && f.proxy.view.string.isEmpty && !f.proxy.view.hasMarkedText(), "empty result failed")
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "原文删除全文", "empty result cannot restore")
}

test("WeChat denied keys do not append model result") {
    let f = WeChatFixture("原文")
    let s = try f.session()
    s.update("修改", final: false); s.convert(); s.update("修改", final: true); f.drain(s)
    f.keyError = "permission denied"
    s.applyEdit(text: "新文", revision: s.revision, error: nil); f.drain(s)
    check(s.phase == .error && f.proxy.view.string == "原文", "permission failure wrote result")
}

test("WeChat takeover invalidates pending compatibility work") {
    let f = WeChatFixture("原文")
    let s = try f.session()
    s.update("修改", final: false); s.convert(); s.update("修改", final: true); f.drain(s)
    s.applyEdit(text: "新文", revision: s.revision, error: nil)
    s.interrupt("user typed", commitIfOwned: false)
    f.proxy.view.string = "用户新输入"
    f.drain(s)
    check(s.phase == .interrupted && f.proxy.view.string == "用户新输入", "late replacement overwrote takeover")
}

test("Dropped select-all cannot mistake a prefix for the full document") {
    let f = WeChatFixture("原文后文")
    f.dropSelectAll = true
    f.proxy.view.setSelectedRange(NSRange(location: 0, length: 2))
    f.adapter.compatibility!.replace(NSRange(location: 0, length: 2), with: "新文", undo: false)
    for _ in 0..<50 { f.now += 0.06; f.adapter.compatibility!.advance() }
    check(f.adapter.clientOperationFailure != nil && f.proxy.view.string == "原文后文", "prefix selected was overwritten")
}

test("Preview replacement and undo survive delayed host key delivery") {
    for stripped in [false, true] {
        let f = WeChatFixture("", prefixOnly: true)
        let session = try f.session()
        session.update("野餐我带", final: true)
        let helper = f.adapter.compatibility!
        func deliverSelection() {
            check(f.keys.last == "select_to_start", "missing sentence selection")
            let tag = helper.eventTag
            f.now += 0.502
            if !stripped {
                check(helper.acceptsKey(code: 126, command: true, shift: true, otherModifiers: false, tag: tag),
                      "tagged key in flight mistaken for external typing")
            }
            f.pendingKeyAck?(nil)
            f.pendingKeyAck = nil
            f.now += 0.006
            if stripped {
                check(helper.acceptsKey(code: 126, command: true, shift: true, otherModifiers: false, tag: 0),
                      "host-confirmed selection mistaken for external typing")
            }
            check(!helper.acceptsKey(code: 126, command: true, shift: true, otherModifiers: false, tag: tag),
                  "replayed key admitted")
            f.drain(session)
        }
        f.deferKeyAck = true
        session.acceptSuggestion("野餐垫我带。", originalText: session.raw, revision: session.revision)
        deliverSelection()
        check(session.polished && f.proxy.view.string == "野餐垫我带。", "late selection failed to replace")
        session.cancel()
        deliverSelection()
        check(!session.polished && f.proxy.view.string == "野餐我带", "late selection failed to undo")
    }
}

test("Preview replacement and undo select only a middle sentence without AX") {
    let f = WeChatFixture("前😀后缀", caret: 3, prefixOnly: true)
    let session = try f.session()
    session.update("明天带🍎", final: true)
    session.acceptSuggestion("我明天带苹果。", originalText: session.raw, revision: session.revision)
    f.drain(session)
    check(f.proxy.view.string == "前😀我明天带苹果。后缀" && session.polished, "middle replacement failed: \(session.error)")
    session.cancel()
    f.drain(session)
    check(f.proxy.view.string == "前😀明天带🍎后缀" && !session.polished, "middle undo failed: \(session.error)")
}

test("System undo of accepted preview returns a collapsed sentence-end caret") {
    for (original, insertion) in [("", 0), ("前😀后缀", 3)] {
        let f = WeChatFixture(original, caret: insertion, codex: true, directRange: false, prefixOnly: true)
        let view = f.proxy.view
        view.allowsUndo = true
        let history = view.undoManager!
        history.groupsByEvent = false
        let session = try f.session()
        session.systemUndoTrial = true
        session.hostUndoTag = 25
        let raw = "明天带🍎"
        history.beginUndoGrouping()
        session.update(raw, final: true)
        history.endUndoGrouping()
        view.breakUndoCoalescing()
        let dictated = view.string
        let caret = view.selectedRange()
        history.beginUndoGrouping()
        session.acceptSuggestion("明天我带苹果。", originalText: raw, revision: session.revision)
        f.drain(session)
        history.endUndoGrouping()
        check(session.polished && view.string != dictated, "preview did not replace the sentence: \(session.error)")
        history.undo()
        session.systemUndo { $0(nil) }
        check(view.string == dictated, "undo did not restore the dictated text")
        check(view.selectedRange() == caret, "undo left \(view.selectedRange()), expected \(caret)")
        check(session.takeRestoredReplacementSelection() == nil, "moved the caret again in an AppKit editor")
        let restored = NSRange(location: insertion, length: raw.utf16.count)
        // Web editors can complete undo after the delivery receipt.
        view.setSelectedRange(restored)
        check(session.takeRestoredReplacementSelection() == restored, "missed delayed selection in the original sentence")
        check(session.takeRestoredReplacementSelection() == nil, "replayed caret movement")
    }
}

test("Ordinary dictation undo recognizes a late WeChat Shift-Left batch") {
    let f = WeChatFixture("已有", prefixOnly: true)
    let session = try f.session()
    session.update("今天有空", final: true)
    let helper = f.adapter.compatibility!
    f.deferKeyAck = true
    session.cancel()
    check(f.keys.last == "select_previous" && f.keyCounts.last == 4, "undo did not select its sentence")
    f.now += 0.54
    f.pendingKeyAck?(nil)
    for _ in 0..<4 {
        check(helper.acceptsKey(code: 123, command: false, shift: true, otherModifiers: false, tag: 0),
              "undo interrupted by its own delayed selection")
    }
    check(!helper.acceptsKey(code: 123, command: false, shift: true, otherModifiers: false, tag: 0),
          "extra Shift-Left bypassed manual takeover")
    f.deferKeyAck = false
    f.drain(session)
    check(session.phase == .undone && f.proxy.view.string == "已有", "ordinary undo changed the prefix")
}

test("Qt missing key tags admit only the pending exact native command once") {
    let f = WeChatFixture("原文")
    let c = f.adapter.compatibility!
    c.replace(NSRange(location: 0, length: 2), with: "新文", undo: false)
    check(!c.acceptsKey(code: 6, command: true, otherModifiers: false, tag: 0), "unrequested undo admitted")
    check(!c.acceptsKey(code: 0, command: true, otherModifiers: true, tag: 0), "different modifier admitted")
    check(!c.acceptsKey(code: 0, command: true, otherModifiers: false, tag: c.eventTag + 1), "wrong nonzero tag admitted")
    check(c.acceptsKey(code: 0, command: true, otherModifiers: false, tag: 0), "stripped native select-all rejected")
    check(!c.acceptsKey(code: 0, command: true, otherModifiers: false, tag: 0), "replayed select-all admitted")
    c.replace(NSRange(location: 0, length: 2), with: "新文", undo: false)
    f.now += 0.6
    check(!c.acceptsKey(code: 0, command: true, otherModifiers: false, tag: 0), "late manual command admitted")
    c.cancel()
    check(!c.acceptsKey(code: 0, command: true, otherModifiers: false, tag: c.eventTag), "cancelled key admitted")
}

test("WeChat and Codex historical dictation undo selects only each sentence then Backspace") {
    for codex in [false, true] {
        let f = WeChatFixture("原文")
        if codex {
            f.proxy.bundle = "com.openai.codex"
            f.proxy.ignoreReplacementRange = false
            f.adapter.nativeCompatibility = f.adapter.compatibility
            f.adapter.compatibility = nil
        }
        let history = CompositionUndoHistory()
        history.configure(enabled: true)
        let first = try f.session(); first.update("甲😀", final: true); history.settled(first)
        let second = try f.session(); history.begin(second.original); second.update("乙", final: true)
        second.cancel(); f.drain(second)
        check(second.phase == .undone && f.proxy.view.string == "原文甲😀", "current undo failed")
        let restored = CompositionSession(client: f.adapter, undo: history.pop()!, utteranceID: "history",
                                         sequence: 2, revision: 3, clock: { f.now })
        restored.cancel(); f.drain(restored)
        check(restored.phase == .undone && f.proxy.view.string == "原文", "historical undo failed")
        check(f.keys == ["select_previous", "delete", "select_previous", "delete"], "ordinary undo selected whole field")
    }
}

test("Historical edit restores dictation and supports deleting it with both adapters") {
    for codex in [false, true] {
        let f = WeChatFixture("原文")
        if codex {
            f.proxy.bundle = "com.openai.codex"
            f.proxy.ignoreReplacementRange = false
            f.adapter.nativeCompatibility = f.adapter.compatibility
            f.adapter.compatibility = nil
        }
        let s = try f.session()
        s.update("改为新文", final: false); s.convert(); s.update("改为新文", final: true)
        s.applyEdit(text: "新文", revision: s.revision, error: nil); f.drain(s)
        check(s.phase == .edited, "edit precondition failed")
        let record = s.undoRecord()!
        let later = try f.session(); later.update("后一句", final: true)
        later.cancel(); f.drain(later)
        let restored = CompositionSession(client: f.adapter, undo: record, utteranceID: "history-edit",
                                         sequence: 1, revision: 10, clock: { f.now })
        restored.cancel(); f.drain(restored)
        check(restored.phase == .dictated && f.proxy.view.string == "原文改为新文", "edit restore failed")
        restored.cancel(); f.drain(restored)
        check(restored.phase == .undone && f.proxy.view.string == "原文", "restored instruction undo failed")
        if codex { check(!f.keys.contains("select_all"), "Codex edit used WeChat full-field keys") }
    }
}

test("Default selected replacement cannot leave the ignored explicit-range suffix behind") {
    for bundle in ["com.microsoft.VSCode", "com.openai.codex", "org.example.Editor"] {
        let f = WeChatFixture("第一句。最后一句。", codex: true, application: bundle, directRange: false)
        f.proxy.ignoreReplacementRange = true
        for (instruction, result) in [("扩写", "第一句扩写后的内容。第二句是补充。最后一句。"),
                                      ("删掉最后一句话", "第一句扩写后的内容。第二句是补充。"),
                                      ("只保留第一句", "第一句扩写后的内容。") ] {
            let s = try f.session()
            let keysBefore = f.keys
            s.update(instruction, final: false); s.convert(); s.update(instruction, final: true); f.drain(s)
            check(f.keys == keysBefore && f.proxy.view.hasMarkedText(), "model wait changed the visible selection")
            s.applyEdit(text: result, revision: s.revision, error: nil); f.drain(s)
            check(s.phase == .edited && f.proxy.view.string == result && !s.awaitingReadback,
                  "editing retained or reintroduced a previous result")
            check((f.adapter.clientOperationDiagnostics["post_write_ms"] as? Double ?? 999) <= 61,
                  "selected replacement delayed the loading indicator after writing")
            check(!f.proxy.view.hasMarkedText(), "edit left a composition behind")
        }
        check(f.keys == Array(repeating: "select_all", count: 3), "edits copied text or repeated native selection")
    }
}

test("A missing post-edit document cache does not keep the successful edit loading") {
    let f = WeChatFixture("原文", codex: true, application: "com.microsoft.VSCode", directRange: false)
    let s = try f.session()
    s.update("扩写", final: false); s.convert(); s.update("扩写", final: true); f.drain(s)
    let result = String(repeating: "扩写的结果。", count: 20)
    var readsAtWrite = 0
    f.proxy.onInsert = {
        if f.proxy.view.string == result {
            readsAtWrite = f.proxy.reads
            f.proxy.unavailableDocumentRead = true
        }
    }
    s.applyEdit(text: result, revision: s.revision, error: nil); f.drain(s)
    check(s.phase == .edited && s.error.isEmpty && f.now <= 0.4, "successful result waited for a cache timeout")
    check(f.proxy.reads - readsAtWrite == 1, "adapter and transaction both read back the full result")
    check(f.adapter.verifiedReplacement?.text == result, "missing scoped write receipt")
}

test("Selected replacement still recovers an old caret without re-reading the result") {
    let f = WeChatFixture("原文", codex: true, directRange: false)
    let s = try f.session()
    s.update("扩写", final: false); s.convert(); s.update("扩写", final: true); f.drain(s)
    let result = "已经替换完毕的新内容"
    var readsAtWrite = 0
    f.proxy.onInsert = {
        if f.proxy.view.string == result {
            readsAtWrite = f.proxy.reads
            f.proxy.view.setSelectedRange(NSRange(location: 1, length: 0))
        }
    }
    s.applyEdit(text: result, revision: s.revision, error: nil); f.drain(s)
    check(s.phase == .edited && f.proxy.view.selectedRange().location == (result as NSString).length,
          "selected replacement retained the old caret")
    check(f.proxy.reads - readsAtWrite == 1 && f.keys.suffix(2) == ["select_all", "caret_from_end"],
          "caret recovery repeated document queries or selection")
}

test("Default selection or insertion loss cannot apply or duplicate a model result") {
    for dropSelection in [true, false] {
        let f = WeChatFixture("完整的原文", caret: 2, codex: true, directRange: false)
        let s = try f.session()
        s.update("修改", final: false); s.convert(); s.update("修改", final: true); f.drain(s)
        f.dropSelectAll = dropSelection
        f.proxy.onInsert = { if !dropSelection { f.proxy.dropNonemptyInsert = true } }
        s.applyEdit(text: "模型的结果", revision: s.revision, error: nil); f.drain(s)
        check(s.phase != .edited && f.proxy.view.string == "完整修改的原文", "a dropped operation overwrote original text")
        check(f.keys.filter { $0 == "select_all" }.count == 1, "a dropped operation was blindly retried")
    }
}

test("Default mixed undo history crosses edits and then continues deleting older dictation") {
    let f = WeChatFixture("原文", codex: true, directRange: false)
    let history = CompositionUndoHistory(); history.configure(enabled: true)
    for raw in ["第一句。", "第二句😀。"] {
        let s = try f.session(); history.begin(s.original)
        s.update(raw, final: true); history.settled(s)
    }
    let s = try f.session(); history.begin(s.original)
    s.update("扩写", final: false); s.convert(); s.update("扩写", final: true); f.drain(s)
    s.applyEdit(text: "完整的扩写结果。", revision: s.revision, error: nil); f.drain(s); history.settled(s)
    let next = try f.session(); history.begin(next.original)
    while let record = history.pop() {
        let restored = CompositionSession(client: f.adapter, undo: record, utteranceID: "history",
                                          sequence: 2, revision: 4, clock: { f.now })
        restored.cancel(); f.drain(restored)
        if restored.phase == .dictated { restored.cancel(); f.drain(restored) }
        check(restored.phase == .undone, "undo stack stopped: raw=\(record.raw), phase=\(restored.phase), error=\(restored.error), body=\(f.proxy.view.string), keys=\(f.keys), diag=\(restored.readbackDiagnostics)")
    }
    check(f.proxy.view.string == "原文" && f.keys.filter { $0 == "select_all" }.count == 2,
          "sentence undo selected the full field or old text was duplicated")
}

test("Default selection replacement preserves a bounded context's prefix and suffix") {
    let f = WeChatFixture("前文目标后文", codex: true, directRange: false)
    f.adapter.replaceEditing(NSRange(location: 2, length: 2), with: "修改后的目标", restoringCaret: nil)
    for _ in 0..<3 { f.now += 0.06; f.adapter.advanceClientOperation() }
    check(!f.adapter.clientOperationPending && f.adapter.clientOperationFailure == nil &&
          f.proxy.view.string == "前文修改后的目标后文", "bounded context overwrote the unedited suffix")
    check(f.adapter.verifiedReplacement?.range == NSRange(location: 2, length: 6), "receipt does not match the requested scope")
}

test("Mail diagnostics distinguish bounded reads from post-write caret failure without changing behavior") {
    for tailLength in [512, 513, 32769] {
        let suffix = String(repeating: "乙", count: tailLength)
        let f = WeChatFixture("甲" + suffix, caret: 1, codex: true,
                              application: "com.google.Chrome", directRange: false)
        let s = try f.session()
        check(f.adapter.lastReadDiagnostics["context_origin"] as? String == "bounded", "missing read provenance")
        s.update("改写", final: false); s.convert(); s.update("改写", final: true)
        let reads = f.proxy.reads
        check(s.editDiagnostics["source_complete"] as? Bool == false &&
              s.editDiagnostics["model_source_characters"] as? Int == 1 &&
              s.editDiagnostics["original_selection"] as? [Int] == [1, 0], "request boundary was not captured")
        _ = s.editDiagnostics; _ = f.adapter.clientOperationDiagnostics
        check(f.proxy.reads == reads, "diagnostics queried the editor")
        s.applyEdit(text: "丙", revision: s.revision, error: nil); f.drain(s)
        let d = s.readbackDiagnostics
        check(d["write_sent"] as? Bool == (tailLength <= 32768), "lost whether the candidate was written")
        check(d["tail_characters"] as? Int == tailLength && d["caret_restore_limit"] as? Int == 512,
              "lost the caret-distance evidence")
        check(d["source_relation"] as? String == (tailLength <= 32768 ? "equal" : "snapshot_prefix"),
              "missing bounded-source mismatch explanation")
        if tailLength <= 512 {
            check(s.phase == .edited && f.proxy.view.string == "丙" + suffix &&
                  f.proxy.view.selectedRange() == NSRange(location: 1, length: 0), "logging changed the successful path")
        } else {
            // These are existing failures. Instrumentation must expose them,
            // not change their write/selection policy in this diagnostic patch.
            check(s.phase == .error, "logging unexpectedly changed the failed operation")
            check(d["failure_code"] as? String == (tailLength == 513 ? "caret_restore_limit_or_boundary" : "source_mismatch_restore_limit"),
                  "failure stage was lost after cancellation")
            check(f.proxy.view.string == (tailLength == 513 ? "丙" + suffix : "甲改写" + suffix),
                  "logging changed the existing write behavior")
            check(f.keys == ["select_all"], "logging added selection or caret commands")
        }
        let data = try JSONSerialization.data(withJSONObject: [d, s.editDiagnostics])
        let encoded = String(data: data, encoding: .utf8)!
        check(!encoded.contains("甲") && !encoded.contains("乙") && !encoded.contains("改写"), "diagnostics leaked text")
    }
}

test("Source disagreement diagnostics describe rich-text boundary classes without text") {
    for (expected, observed, expectedKind, observedKind) in [
        ("甲\n乙", "甲乙", "lf", "other"),
        ("甲\u{00a0}乙", "甲 乙", "nbsp", "space"),
        ("甲\u{fffc}乙", "甲乙", "object", "other"),
        ("甲乙", "甲丙", "other", "other")
    ] {
        let d = EditSourceComparison.diagnostics(EditorTextRange(text: expected,
            range: NSRange(location: 0, length: expected.utf16.count)), with: observed)
        check(d["source_common_prefix"] as? Int == 1 &&
              d["source_first_expected_kind"] as? String == expectedKind &&
              d["source_first_observed_kind"] as? String == observedKind, "difference classification lost")
    }
}

test("Source comparison distinguishes terminal paragraph metadata from changed content") {
    func compare(_ cached: String, _ selected: String, at location: Int = 0) -> EditSourceComparison {
        EditSourceComparison.compare(EditorTextRange(text: cached,
            range: NSRange(location: location, length: (cached as NSString).length)), with: selected)
    }
    check(compare("正文\n\n", "正文") == .terminalParagraphs, "logged two paragraph sentinels rejected")
    check(compare("正文\r\n", "正文") == .terminalParagraphs, "CRLF boundary rejected")
    check(compare("正文\u{2029}", "正文") == .terminalParagraphs, "paragraph separator rejected")
    check(compare("前\n后\n\n", "前\n后\n\n") == .exact, "real blank lines were normalized away")
    for pair in [("前\n后\n\n", "前后"), ("前 后\n\n", "前后"),
                 ("原文\n\n", "变文"), ("正文旧尾文\n\n", "正文"), ("正文 \n\n", "正文"),
                 ("正文", "正文\n"), ("正文\u{200b}\n\n", "正文")] {
        check(compare(pair.0, pair.1) == .different, "real content difference was ignored")
    }
    check(compare("正文\n\n", "正文", at: 4) == .different, "bounded context was treated as whole document")
    check(EditSourceComparison.compare(nil, with: "正文") == .different, "missing source was accepted")
}

test("Logged extra IMK paragraph separators do not reject valid edits in any default client") {
    for bundle in ["com.openai.codex", "com.microsoft.VSCode", "test.generic.editor"] {
        for original in ["12345。12345。", "12345已完成，前期第1点，适配测试通过。", "甲😀\n乙\n\n"] {
            for caret in [1, (original as NSString).length] {
                let f = WeChatFixture(original, caret: caret, codex: true, directRange: false)
                f.proxy.bundle = bundle
                f.proxy.unselectedCachedSuffix = "\n\n"
                let s = try f.session()
                var requests = 0
                s.onEdit = { source, _, _ in
                    requests += 1
                    check(source == (original as NSString).substring(to: caret), "model received suffix or cached paragraph markers")
                }
                s.update("润色一下。", final: false); s.convert(); s.update("润色一下。", final: true)
                let dictated = f.proxy.view.string
                for _ in 0..<120 { f.now += 0.06; f.adapter.advanceClientOperation(); s.advanceReadback() }
                check(requests == 1 && f.keys.isEmpty && f.proxy.view.hasMarkedText(), "waiting selected the field or lost underline")
                let candidate = "修改后的正文😀\n\n"
                s.applyEdit(text: candidate, revision: s.revision, error: nil); f.drain(s)
                check(s.phase == .edited && s.error.isEmpty && f.proxy.view.string == candidate + (original as NSString).substring(from: caret) && requests == 1,
                      "terminal paragraph metadata blocked editing or trimmed model output: \(s.error)")
                check(f.keys.filter { $0 == "select_all" }.count == 1, "representation match caused another selection")
                check(f.proxy.view.selectedRange() == NSRange(location: (candidate as NSString).length, length: 0), "paragraph metadata shifted the restored caret")
                // The undo source must come from the actual native selection,
                // not the longer cached IMK substring window.
                s.cancel(); f.drain(s)
                check(s.phase == .dictated && f.proxy.view.string == dictated, "edit undo reintroduced cached terminal paragraphs")
                s.cancel(); f.drain(s)
                check(s.phase == .undone && f.proxy.view.string == original, "second undo lost original blank lines")
            }
        }
    }
}

test("Post-model source mismatch preserves dictation without a second model request") {
    for caret in [4, 9] {
        let original = "第一句。最后一句。"
        let f = WeChatFixture(original, caret: caret, codex: true, directRange: false)
        f.proxy.unselectedCachedSuffix = "旧扩写结果。这些文字已不在编辑器内。"
        let s = try f.session()
        check(s.editOriginal?.text != original, "fixture lacks stale context")
        var requests = 0
        s.onEdit = { _, _, _ in requests += 1 }
        s.update("删掉最后一句话", final: false); s.convert(); s.update("删掉最后一句话", final: true)
        let oldRevision = s.revision
        let dictated = f.proxy.view.string
        for _ in 0..<120 { f.now += 0.06; f.adapter.advanceClientOperation(); s.advanceReadback() }
        check(requests == 1 && s.phase == .editing && f.keys.isEmpty && f.proxy.view.hasMarkedText(),
              "model wait selected the document, cleared preedit or requested another model")
        s.applyEdit(text: "错误结果带回旧尾文", revision: oldRevision, error: nil); f.drain(s)
        check(requests == 1 && s.phase == .dictated && !s.error.isEmpty &&
              f.proxy.view.string == dictated && f.proxy.view.selectedRange().length == 0,
              "mismatch retried the model, overwrote text or left selection")
        let keys = f.keys
        s.applyEdit(text: "迟到结果", revision: oldRevision, error: nil); f.drain(s)
        check(f.keys == keys && f.proxy.view.string == dictated, "late model result wrote text")
        f.proxy.unselectedCachedSuffix = ""
        s.cancel(); f.drain(s)
        check(s.phase == .undone && f.proxy.view.string == original, "mismatch lost instruction undo")
    }
}

test("Long model wait retains marked instruction with no selection until one replacement") {
    for caret in [2, 4] {
        let f = WeChatFixture("前文后文", caret: caret, codex: true, directRange: false)
        let s = try f.session()
        var requests = 0
        s.onEdit = { source, instruction, _ in
            requests += 1
            check(source == ("前文后文" as NSString).substring(to: caret) && instruction == "扩写", "model context included suffix")
        }
        s.update("扩写", final: false); s.convert(); s.update("扩写", final: true)
        let dictated = f.proxy.view.string
        let mark = f.proxy.view.markedRange()
        for _ in 0..<500 { f.now += 0.06; f.adapter.advanceClientOperation(); s.advanceReadback() }
        check(requests == 1 && s.phase == .editing && f.keys.isEmpty &&
              f.proxy.view.markedRange() == mark && f.proxy.view.string == dictated,
              "long model wait changed the field or repeated a request")
        s.applyEdit(text: "扩写后的正文", revision: s.revision, error: nil); f.drain(s)
        check(s.phase == .edited && requests == 1 && f.proxy.view.string == "扩写后的正文" + ("前文后文" as NSString).substring(from: caret) &&
              !f.proxy.view.hasMarkedText() && f.keys.filter { $0 == "select_all" }.count == 1,
              "result did not apply in a single selection")
        s.cancel(); f.drain(s)
        check(s.phase == .dictated && f.proxy.view.string == dictated, "edit undo lost dictation")
        s.cancel(); f.drain(s)
        check(s.phase == .undone && f.proxy.view.string == "前文后文", "instruction undo lost source")
    }
}

test("Cancelling during model wait retains dictation without another full selection") {
    let f = WeChatFixture("前文后文", caret: 2, codex: true, directRange: false)
    let s = try f.session()
    s.update("修改", final: false); s.convert(); s.update("修改", final: true); f.drain(s)
    let keys = f.keys
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文修改后文" && f.keys == keys,
          "cancellation selected/replaced the full field or lost dictation")
    s.cancel(); f.drain(s)
    check(s.phase == .undone && f.proxy.view.string == "前文后文", "cancelled model instruction cannot be undone")
}

test("Cancelling after the model but before selected replacement never writes its candidate") {
    // One selection now replaces the old prefix + whole-document pair.
    // Tick 2 performs the write; cancellation-before-write is ticks 0 and 1.
    for ticks in [0, 1] {
        let f = WeChatFixture("前文后文", caret: 2, codex: true, directRange: false)
        let s = try f.session()
        s.update("修改", final: false); s.convert(); s.update("修改", final: true)
        let revision = s.revision
        s.applyEdit(text: "不应写入的候选", revision: revision, error: nil)
        for _ in 0..<ticks { f.now += 0.06; f.adapter.advanceClientOperation(); s.advanceReadback() }
        s.cancel(); f.drain(s)
        check(s.phase == .dictated && f.proxy.view.string == "前文修改后文" &&
              f.proxy.view.selectedRange() == NSRange(location: 4, length: 0), "pre-write cancellation applied a candidate or left selection")
        check(f.proxy.insertCount == 1, "cancelled candidate was written and then restored")
        s.applyEdit(text: "迟到", revision: revision, error: nil); f.drain(s)
        s.cancel(); f.drain(s)
        check(s.phase == .undone && f.proxy.view.string == "前文后文", "cancelled instruction could not be undone")
    }
}

test("Actual source change is rejected at replacement without retrying the model") {
    let f = WeChatFixture("原文", codex: true, directRange: false)
    let s = try f.session()
    var requests = 0
    s.onEdit = { _, _, _ in requests += 1 }
    s.update("修改", final: false); s.convert(); s.update("修改", final: true)
    let revision = s.revision
    // Simulate an application-side source change with no input event.
    f.proxy.onInsert = {
        f.proxy.onInsert = nil
        f.proxy.view.textStorage?.replaceCharacters(in: NSRange(location: 0, length: 2), with: "变文")
    }
    s.applyEdit(text: "旧原文的结果", revision: revision, error: nil); f.drain(s)
    check(requests == 1 && s.phase == .dictated && !s.error.isEmpty && f.proxy.view.string == "变文修改",
          "source change retried a model or overwrote new text: requests=\(requests), phase=\(s.phase), error=\(s.error), text=\(f.proxy.view.string), keys=\(f.keys)")
    check(f.proxy.view.selectedRange() == NSRange(location: 4, length: 0), "failure left a full selection")
}

test("Model failure keeps the visible instruction and never selects the document") {
    let f = WeChatFixture("已有正文", codex: true, directRange: false)
    let s = try f.session()
    s.update("扩写", final: false); s.convert(); s.update("扩写", final: true)
    check(f.keys.isEmpty && f.proxy.view.hasMarkedText(), "waiting for model changed the input field")
    s.applyEdit(text: "", revision: s.revision, error: "模型服务暂时不可用"); f.drain(s)
    check(f.keys.isEmpty && s.phase == .dictated && f.proxy.view.string == "已有正文扩写",
          "model error selected the document or removed dictation")
}

test("Empty model result replaces the selected document once and undo restores dictation") {
    let f = WeChatFixture("全部删除", codex: true, directRange: false)
    let s = try f.session()
    s.update("清空", final: false); s.convert(); s.update("清空", final: true)
    s.applyEdit(text: "", revision: s.revision, error: nil); f.drain(s)
    check(s.phase == .edited && f.proxy.view.string.isEmpty && f.keys == ["select_all", "delete"],
          "empty result failed or deleted twice")
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "全部删除清空", "empty edit could not restore dictation")
}

test("Confirmed context follows own dictation and undo without reusing a stale suffix") {
    let f = WeChatFixture("第一句。", codex: true, directRange: false)
    let first = try f.session()
    first.update("扩写", final: false); first.convert(); first.update("扩写", final: true)
    first.applyEdit(text: "第一句扩写。", revision: first.revision, error: nil); f.drain(first)
    f.proxy.unselectedCachedSuffix = "已删除的旧段落。"
    let spoken = try f.session()
    spoken.update("下一", final: false); spoken.update("下一句。", final: true)
    spoken.cancel(); f.drain(spoken)
    check(spoken.phase == .undone && f.proxy.view.string == "第一句扩写。", "hint tracking disturbed sentence undo")
    let next = try f.session()
    var requests = 0
    next.onEdit = { source, _, _ in
        requests += 1
        check(source == "第一句扩写。", "confirmed context reused phantom text after dictation undo")
    }
    let keys = f.keys
    next.update("精简", final: false); next.convert(); next.update("精简", final: true)
    check(f.keys == keys, "hint reading selected the document")
    next.applyEdit(text: "简文。", revision: next.revision, error: nil); f.drain(next)
    check(next.phase == .edited && requests == 1 && f.proxy.view.string == "简文。", "known document caused another model round")
    next.cancel(); f.drain(next); next.cancel(); f.drain(next)
    let afterUndo = try f.session()
    check(afterUndo.editOriginal?.text == "第一句扩写。", "edit undo left a stale context hint")
    f.adapter.discardContextHint()
    let uncached = try f.adapter.snapshot()
    check(uncached.readableContext?.text != "第一句扩写。", "manual takeover did not discard the context hint")
}

test("Committed dictation rebases cached composition coordinates and remains undoable") {
    for bundle in ["com.openai.codex", "com.microsoft.VSCode", "test.generic.editor"] {
        for prefix in ["", "原文😀\n"] {
            let suffix = "。后文保留"
            let proxy = StrictIMKClient(prefix + suffix, selection: NSRange(location: (prefix as NSString).length, length: 0))
            proxy.bundle = bundle
            proxy.compositionCoordinateOffset = 100
            let adapter = NativeInputClient(proxy)
            let session = try CompositionSession(client: adapter, utteranceID: "commit-rebase", sequence: 1)
            check(session.cancelLabel == "取消", "live dictation label is not cancel")
            session.update("今天", final: false)
            session.update("今天下午开会😀", final: false)
            session.finish()
            check(session.cancelLabel == "取消", "pending final label is not cancel")
            session.update("今天下午开会😀。", final: true)
            check(session.phase == .dictated && !session.awaitingReadback && session.canCancel,
                  "coordinate switch after commit removed undo")
            check(session.cancelLabel == "撤销", "committed label is not undo")
            check(session.readbackDiagnostics["ack_source"] as? String == "committed_range", "commit was not locally verified")
            check(!session.canReplace && session.expectedSelection == proxy.view.selectedRange(), "rebase gained full-document rights or kept the old caret")
            session.checkSelection()
            check(!session.awaitingReadback, "postcommit polling rejected the recovered coordinates")
            check(session.undoRecord()?.committedRange?.location == (prefix as NSString).length, "history retained the obsolete origin")
            session.cancel()
            check(session.phase == .undone && proxy.view.string == prefix + suffix, "undo damaged the surrounding text")
            check(proxy.insertCount == 2, "recovery replayed the native commit")
        }
    }
}

test("Commit settles without granting undo when local coordinates are unconfirmed") {
    for readFailure in ["unavailable", "wrong-text"] {
        let proxy = StrictIMKClient("")
        proxy.compositionCoordinateOffset = 100
        let adapter = NativeInputClient(proxy)
        var now: TimeInterval = 0
        let session = try CompositionSession(client: adapter, utteranceID: "commit-no-proof", sequence: 1, clock: { now })
        session.update("今天", final: false)
        proxy.onInsert = { if readFailure == "unavailable" { proxy.unavailableDocumentRead = true } else { proxy.staleDocumentRead = true } }
        session.update("今天", final: true)
        check(!session.awaitingReadback && !session.committedRangeVerified, "unreadable caret range blocked commit or enabled undo")
        now = 0.6; session.advanceReadback()
        check(session.phase == .dictated && !session.canCancel && proxy.insertCount == 1, "uncertain undo range failed dictation or replayed a write")
    }
}

test("Commit coordinate recovery does not follow a moved caret to an earlier identical sentence") {
    let proxy = StrictIMKClient("今天中间", selection: NSRange(location: 4, length: 0))
    let adapter = NativeInputClient(proxy)
    var now: TimeInterval = 0
    let session = try CompositionSession(client: adapter, utteranceID: "commit-moved", sequence: 1, clock: { now })
    session.update("今天", final: false)
    proxy.onInsert = { proxy.view.setSelectedRange(NSRange(location: 2, length: 0)) }
    session.update("今天", final: true)
    check(!session.awaitingReadback && !session.committedRangeVerified, "ambiguous caret was mistaken for an undo range")
    now = 0.6; session.advanceReadback()
    check(session.phase == .dictated && !session.canCancel && proxy.view.string == "今天中间今天", "uncertain caret failed dictation or changed an earlier sentence")
}

test("Live composition range follows coherent client coordinates across partials and polling") {
    let proxy = StrictIMKClient("前文")
    proxy.compositionCoordinateOffset = 100
    let adapter = NativeInputClient(proxy)
    let session = try CompositionSession(client: adapter, utteranceID: "live-coordinates", sequence: 1)
    session.update("一", final: false)
    for offset in [60, 0, 30] {
        proxy.compositionCoordinateOffset = offset
        session.checkSelection()
        check(!session.awaitingReadback && session.active, "coherent range change was mistaken for user movement")
        session.update("一二三", final: false)
        check(!session.awaitingReadback && session.phase == .listening, "partial retained an obsolete origin")
    }
    session.update("一二三。", final: true)
    check(session.phase == .dictated && session.committedRange == NSRange(location: 2, length: 4), "commit used a draft coordinate")
    session.cancel()
    check(session.phase == .undone && proxy.view.string == "前文", "undo used original snapshot coordinates")
}

test("Cancelling a live draft ends the owned composition after its coordinate origin changes") {
    let proxy = StrictIMKClient("前文")
    proxy.compositionCoordinateOffset = 100
    let session = try CompositionSession(client: NativeInputClient(proxy), utteranceID: "cancel-coordinates", sequence: 1)
    session.update("取消本句", final: false)
    proxy.compositionCoordinateOffset = 60
    session.cancel()
    check(session.phase == .undone && !session.awaitingReadback && proxy.view.string == "前文", "cancel required a stale draft caret")
}

test("Undo history retains committed spans without rewriting original context snapshots") {
    let proxy = StrictIMKClient("")
    proxy.compositionCoordinateOffset = 100
    let adapter = NativeInputClient(proxy)
    let history = CompositionUndoHistory()
    history.configure(enabled: true)
    let first = try CompositionSession(client: adapter, utteranceID: "history-first", sequence: 1)
    first.update("第一句。", final: true)
    check(first.original.selection.location == 100 && first.committedRange?.location == 0, "original context and committed range were conflated")
    history.settled(first)
    let second = try CompositionSession(client: adapter, utteranceID: "history-second", sequence: 1)
    history.begin(second.original)
    second.update("第二句。", final: true)
    check(history.count == 1 && second.phase == .dictated, "history rejected a confirmed committed range")
    second.cancel()
    check(proxy.view.string == "第一句。", "current undo damaged an older sentence")
    let record = history.pop()!
    let restored = CompositionSession(client: adapter, undo: record, utteranceID: "history-undo", sequence: 1, revision: 1)
    restored.cancel()
    check(restored.phase == .undone && proxy.view.string.isEmpty, "older undo used the draft origin")
}


test("Every native app ignores cached caret positions through streaming and commit") {
    for bundle in ["com.openai.codex", "com.tencent.xinWeChat", "com.microsoft.VSCode", "test.generic.editor"] {
        for reported in [NSRange(location: 0, length: 0), NSRange(location: 6, length: 0),
                         NSRange(location: 1000, length: 0), unspecifiedRange] {
            let proxy = StrictIMKClient("前文")
            proxy.bundle = bundle
            let session = try CompositionSession(client: NativeInputClient(proxy), utteranceID: "cached-caret", sequence: 1)
            proxy.fixedReportedSelection = reported
            for text in ["一", "一二三", "一二三四五六七八"] {
                session.update(text, final: false)
                session.checkSelection()
                check(session.phase == .listening && !session.awaitingReadback, "cached caret paused live ASR in \(bundle)")
            }
            session.update("一二三四五六七八。", final: true)
            check(session.phase == .dictated && !session.awaitingReadback && proxy.insertCount == 1,
                  "cached or unavailable caret blocked final in \(bundle): \(session.error)")
            check(proxy.view.string == "前文一二三四五六七八。" && !proxy.view.hasMarkedText(), "commit lost, duplicated, or retained marked text")
            session.checkSelection()
            check(session.phase == .dictated && !session.awaitingReadback, "postcommit polling stopped completed dictation")
            proxy.fixedReportedSelection = nil
            let next = try CompositionSession(client: NativeInputClient(proxy), utteranceID: "next-caret", sequence: 1)
            next.update("下一句", final: true)
            check(next.phase == .dictated && proxy.view.string.hasSuffix("。下一句"), "cached caret stranded next sentence")
        }
    }
}

test("Starting snapshot skips old marks and preserves selected text for undo") {
    let proxy = StrictIMKClient("甲乙丙", selection: NSRange(location: 1, length: 1))
    proxy.reportedMarkedRanges = [NSRange(location: 0, length: 900)]
    let adapter = NativeInputClient(proxy)
    let session = try CompositionSession(client: adapter, utteranceID: "selected-start", sequence: 1)
    check(proxy.markedReads == 0 && proxy.reportedMarkedRanges.count == 1, "begin consulted stale marked state")
    check(session.original.selectedText == "乙" && session.original.selection == NSRange(location: 1, length: 1), "selected original was lost")
    proxy.reportedMarkedRanges = []
    session.update("新字", final: false)
    check(proxy.view.string == "甲新字丙", "native input failed to replace selection")
    session.cancel()
    check(proxy.view.string == "甲乙丙", "cancel failed to restore selected original")
}



test("Editing does not depend on a web editor accepting Cmd Shift Up") {
    for caret in [2, 5] {
        let f = WeChatFixture("前文原文后", caret: caret, codex: true, application: "com.apple.Safari", directRange: false)
        f.dropSelectToStart = true
        let s = try f.session()
        s.update("改写", final: false); s.convert(); s.update("改写", final: true)
        s.applyEdit(text: "确认后的新原文", revision: s.revision, error: nil); f.drain(s)
        check(s.phase == .edited && f.proxy.view.string == "确认后的新原文" + ("前文原文后" as NSString).substring(from: caret),
              "a swallowed selection arrow prevents editing")
        check(f.keys == (caret == 5 ? ["select_all"] : ["select_all", "caret_from_end"]), "edit selected the prefix or repeated caret movement")
    }
}

test("A swallowed select all still cannot authorize an edit") {
    let f = WeChatFixture("原文后缀", codex: true, application: "com.apple.Safari", directRange: false)
    f.dropSelectAll = true
    let s = try f.session()
    s.update("改写", final: false); s.convert(); s.update("改写", final: true)
    s.applyEdit(text: "不应写入", revision: s.revision, error: nil); f.drain(s)
    check(s.phase == .error && f.proxy.view.string == "原文后缀改写", "missing native selection authorized replacement")
}


test("Stale committed caret cannot authorize replacing a different instruction") {
    for caret in [0, 500] {
        let f = WeChatFixture("原文后缀", codex: true, application: "com.apple.Safari", directRange: false)
        let s = try f.session()
        s.update("改写", final: false); s.convert(); s.update("改写", final: true)
        s.applyEdit(text: "不应写入", revision: s.revision, error: nil)
        f.proxy.reportedSelections = [NSRange(location: caret, length: 0)]
        f.drain(s)
        check(s.phase == .error && f.proxy.view.string == "原文后缀改写", "stale caret authorized replacement")
        check(f.proxy.insertCount == 1, "invalid context wrote more than the committed instruction")
    }
}

test("Production adapters retry restored instructions with original prefix and preserved suffix") {
    for bundle in ["com.tencent.xinWeChat", "com.openai.codex", "com.microsoft.VSCode", "test.generic.editor"] {
        let f = WeChatFixture("原文后缀", caret: 2, codex: bundle != "com.tencent.xinWeChat", application: bundle, directRange: false)
        let s = try f.session()
        var requests = 0
        s.onEdit = { source, instruction, _ in
            check(source == "原文" && instruction == "扩写", "retry changed source in \(bundle)")
            requests += 1
        }
        s.update("扩写", final: false); s.convert(); s.update("扩写", final: true); f.drain(s)
        s.applyEdit(text: "结果", revision: s.revision, error: nil); f.drain(s)
        check(s.phase == .edited, "initial edit failed: \(s.error)")
        s.cancel(); f.drain(s)
        check(s.canRetryEdit && f.proxy.view.string == "原文扩写后缀", "instruction not restored in \(bundle)")
        let writes = f.proxy.insertCount
        s.retryEdit(); f.drain(s)
        check(requests == 2 && f.proxy.insertCount == writes, "retry wrote before model in \(bundle): \(s.error)")
        s.applyEdit(text: "新结果", revision: s.revision, error: nil); f.drain(s)
        check(s.phase == .edited && f.proxy.view.string == "新结果后缀", "retry failed in \(bundle): \(s.error)")
        s.cancel(); f.drain(s); s.cancel(); f.drain(s)
        check(s.phase == .undone && f.proxy.view.string == "原文后缀", "retry broke undo in \(bundle)")
    }
}

final class MainPrefixDriver: PrefixSelectionDriver {
    weak var proxy: StrictIMKClient?
    var metadata: [String: Any] = [:]
    var attempt: PrefixSelectionAttempt = .submitted
    var drop = false
    var wrong = false
    var valid = true
    var calls: [NSRange] = []
    var events: [[String: Any]] = []
    var afterSelect: (() -> Void)?
    var axOverride: NSRange?
    var caretMoves: [NSRange] = []
    var dropCaretMove = false
    var caretMoveAttempt: PrefixSelectionAttempt = .submitted
    var onCaretMove: (() -> Void)?
    func moveCaret(to target: NSRange, from before: NSRange, current: () -> Bool) -> PrefixSelectionAttempt {
        caretMoves.append(target)
        if case .submitted = caretMoveAttempt, !dropCaretMove { axOverride = target }
        onCaretMove?()
        return caretMoveAttempt
    }
    var readCount = 0
    var caretSamples = 0
    func sampleCaret(current: () -> Bool) -> NSRange? {
        caretSamples += 1
        return readSelection(current: current)
    }
    var readUnavailable = false
    var onRead: ((Int) -> Void)?
    func readSelection(current: () -> Bool) -> NSRange? {
        readCount += 1; onRead?(readCount)
        return valid && current() && !readUnavailable ? (axOverride ?? proxy?.view.selectedRange()) : nil
    }
    var textEnabled = false
    var textReadCount = 0
    var textOverride: ((NSRange) -> String?)?
    var onTextRead: (() -> Void)?
    func readBody(_ scope: EditorReadScope, current: () -> Bool) -> EditorTextRange? {
        textReadCount += 1
        guard textEnabled, valid, current(), !readUnavailable,
              let before = axOverride ?? proxy?.view.selectedRange(),
              let proxy, let range = scope.range(at: before, documentLength: proxy.view.string.utf16.count),
              isValidRange(range, length: proxy.view.string.utf16.count) else { return nil }
        let text = textOverride.map { $0(range) } ?? (proxy.view.string as NSString).substring(with: range)
        onTextRead?()
        guard valid, current(), (axOverride ?? proxy.view.selectedRange()) == before,
              let text, text.utf16.count == range.length else { return nil }
        return EditorTextRange(text: text, range: range, source: .ax, selection: before, readScope: scope)
    }
    var validationCount = 0
    var onValidate: ((Int) -> Void)?
    var onValidateStep: ((String) -> Void)?
    func validateTarget(current: () -> Bool, step: String) -> Bool {
        onValidateStep?(step)
        return validateTarget(current: current)
    }
    func begin(_ operation: String) {}
    func validateTarget(current: () -> Bool) -> Bool {
        validationCount += 1; onValidate?(validationCount)
        return valid && current()
    }
    func record(_ event: [String: Any]) { events.append(event) }
    func select(_ range: NSRange, from caret: NSRange, current: () -> Bool) -> PrefixSelectionAttempt {
        calls.append(range)
        if case .submitted = attempt, !drop {
            axOverride = nil
            proxy?.view.setSelectedRange(wrong ? NSRange(location: 1, length: max(0, range.length - 1)) : range)
        }
        afterSelect?()
        return attempt
    }
}
func prefixFixture(_ app: String = "com.openai.codex", text: String = "前文后文", caret: Int = 2,
                   keyboard: Bool = false) -> (WeChatFixture, MainPrefixDriver) {
    let driver = MainPrefixDriver()
    if keyboard { driver.attempt = .unavailable("ax_test_unavailable") }
    let f = WeChatFixture(text, caret: caret, codex: app != "com.tencent.xinWeChat",
                          application: app, directRange: false, prefixOnly: true, selectionDriver: driver)
    driver.proxy = f.proxy
    return (f, driver)
}
func prefixEdit(_ f: WeChatFixture, candidate: String = "新文", instruction: String = "改写",
                systemUndoTrial: Bool = false) throws -> CompositionSession {
    let s = try f.session()
    s.systemUndoTrial = systemUndoTrial
    f.adapter.pendingCompatibility?.systemUndoTrial = systemUndoTrial
    s.update(instruction, final: false); s.convert(); s.update(instruction, final: true)
    s.applyEdit(text: candidate, revision: s.revision, error: nil); f.drain(s)
    return s
}

test("System undo edit waits for the committed text boundary in both B108 Codex traces") {
    for (length, first, second) in [(219, 22, 26), (1041, 156, 158)] {
        let (f, d) = prefixFixture()
        let output = String(repeating: "字", count: length)
        var samples = Set<Int>()
        f.proxy.onInsert = {
            guard f.proxy.view.string == output + "后文" else { return }
            let written = f.now
            d.axOverride = NSRange(location: 0, length: 7)
            f.proxy.selectionReadOverride = {
                let elapsed = f.now - written
                let position = elapsed < 0.10 ? first : (elapsed < 0.16 ? second : length)
                samples.insert(position)
                return NSRange(location: position, length: 0)
            }
        }
        let s = try prefixEdit(f, candidate: output, systemUndoTrial: true)
        check(s.phase == .edited && samples == Set([first, second, length]) && f.keys.isEmpty &&
              f.adapter.verifiedReplacement?.text == output && f.proxy.insertCount == 2,
              "transient IMK or stale AX drove motion or rejected the write: \(s.error), \(samples)")
    }
}

test("System undo edit requires text even when both caret offsets agree") {
    let (f, d) = prefixFixture()
    f.proxy.onInsert = {
        guard f.proxy.view.string == "新文后文" else { return }
        f.proxy.view.string = "错文后文"
        f.proxy.view.setSelectedRange(NSRange(location: 2, length: 0))
        d.textEnabled = true
    }
    let s = try prefixEdit(f, systemUndoTrial: true)
    check(s.phase == .error && s.error.contains("postwrite_boundary_unconfirmed") &&
          f.keys.isEmpty && f.proxy.insertCount == 2 && f.adapter.verifiedReplacement == nil,
          "matching offsets accepted different text or rewrote the edit: \(s.error)")
}

test("System undo edit accepts an AX text boundary in AX coordinates while IMK is stale") {
    let (f, d) = prefixFixture()
    f.proxy.onInsert = {
        guard f.proxy.view.string == "新文后文" else { return }
        f.proxy.fixedReportedSelection = NSRange(location: 1, length: 0)
        d.textEnabled = true
    }
    let s = try prefixEdit(f, systemUndoTrial: true)
    check(s.phase == .edited && f.keys.isEmpty && f.adapter.verifiedReplacement?.source == .ax &&
          f.adapter.verifiedReplacement?.text == "新文" && f.proxy.view.string == "新文后文",
          "AX body evidence was rejected or used to move the cursor: \(s.error)")
}

test("System undo edit restores a verified selected tail once in grapheme steps") {
    let (f, _) = prefixFixture(text: "前文选😀后文")
    f.proxy.view.setSelectedRange(NSRange(location: 2, length: 3))
    let s = try prefixEdit(f, candidate: "新文", systemUndoTrial: true)
    check(s.phase == .edited && f.proxy.view.string == "新文选😀后文" &&
          f.keys == ["caret_backward"] && f.keyCounts == [2] &&
          f.proxy.view.selectedRange() == NSRange(location: 2, length: 0),
          "did not restore only the verified preserved tail: \(s.error), \(f.keys)")
}

test("System undo edit accepts native LF for submitted CRLF and leaves following text intact") {
    let (f, _) = prefixFixture()
    f.proxy.onInsert = {
        if f.proxy.view.string == "新\r\n文后文" {
            f.proxy.view.textStorage!.replaceCharacters(in: NSRange(location: 1, length: 2), with: "\n")
            f.proxy.view.setSelectedRange(NSRange(location: 3, length: 0))
        }
    }
    let s = try prefixEdit(f, candidate: "新\r\n文", systemUndoTrial: true)
    check(s.phase == .edited && f.adapter.verifiedReplacement?.text == "新\n文" &&
          f.keys.isEmpty && f.proxy.view.string == "新\n文后文",
          "line ending normalization used model geometry as native truth: \(s.error)")
}

test("Right context measures B109 paragraph formats without moving the caret") {
    for (length, blankLines) in [(227, 1), (398, 2), (659, 2), (592, 4)] {
        let paragraphs = String(repeating: "段\n\n", count: blankLines)
        let output = paragraphs + String(repeating: "字", count: length - paragraphs.utf16.count)
        for distance in [0, 3 * blankLines] {
            let (f, d) = prefixFixture()
            f.proxy.onInsert = {
                guard f.proxy.view.string == output + "后文" else { return }
                d.textEnabled = true
                f.proxy.unavailableDocumentRead = true
                f.proxy.fixedReportedSelection = NSRange(location: length, length: 0)
                f.proxy.view.setSelectedRange(NSRange(location: length - distance, length: 0))
            }
            let s = try prefixEdit(f, candidate: output, systemUndoTrial: true)
            let terminal = d.events.last { ["confirmed", "failed"].contains($0["event"] as? String ?? "") }
            check(terminal?["right_check"] as? String == (distance == 0 ? "aligned_offsets" : "before_boundary") &&
                  terminal?["format_line_breaks"] as? Int == 2 * blankLines &&
                  terminal?["format_blank_lines"] as? Int == blankLines &&
                  terminal?["right_available_utf16"] as? Int == (distance == 0 ? nil : distance + 2) &&
                  terminal?["right_move_graphemes"] as? Int == distance &&
                  terminal?["right_move_utf16"] as? Int == distance && f.keys.isEmpty &&
                  s.phase == (distance == 0 ? .edited : .error) && f.proxy.insertCount == 2,
                  "paragraph format predicted movement or changed the original outcome: \(length)/\(distance)")
        }
    }
}

test("Right context counts composed characters and never counts the old suffix as movement") {
    let (f, d) = prefixFixture()
    let output = "开头\n\n完成👩‍💻。"
    let remaining = "👩‍💻。"
    f.proxy.onInsert = {
        guard f.proxy.view.string == output + "后文" else { return }
        d.textEnabled = true
        f.proxy.fixedReportedSelection = NSRange(location: output.utf16.count, length: 0)
        f.proxy.view.setSelectedRange(NSRange(location: output.utf16.count - remaining.utf16.count, length: 0))
    }
    let s = try prefixEdit(f, candidate: output, systemUndoTrial: true)
    let terminal = d.events.last { $0["event"] as? String == "confirmed" }
    check(s.phase == .edited && f.keys.isEmpty && terminal?["right_move_graphemes"] as? Int == 2 &&
          terminal?["right_move_utf16"] as? Int == 6 && terminal?["right_available_utf16"] as? Int == 8,
          "right-side measurement mixed the old suffix, UTF-16, or grapheme count")
}

test("A changed AX rendering at document end records empty right context and unknown distance") {
    let (f, d) = prefixFixture(text: "前文", caret: 2)
    f.proxy.onInsert = {
        guard f.proxy.view.string == "首段\n\n末段" else { return }
        f.proxy.view.string = "首段末段"
        f.proxy.view.setSelectedRange(NSRange(location: 4, length: 0))
        d.textEnabled = true
    }
    _ = try prefixEdit(f, candidate: "首段\n\n末段", systemUndoTrial: true)
    let terminal = d.events.last { $0["event"] as? String == "failed" }
    check(terminal?["right_check"] as? String == "text_mismatch" &&
          terminal?["right_available_utf16"] as? Int == 0 && terminal?["right_move_graphemes"] == nil &&
          f.keys.isEmpty, "missing text evidence was silently converted to a movement distance")
}

test("Right context can use AX ranges without AXValue and rejects conflicting right-hand text") {
    for rangesOnly in [true, false] {
        let (f, d) = prefixFixture()
        let output = "今天确认"
        f.proxy.onInsert = {
            guard f.proxy.view.string == output + "后文" else { return }
            d.textEnabled = true
            f.proxy.fixedReportedSelection = NSRange(location: 4, length: 0)
            f.proxy.view.setSelectedRange(NSRange(location: 2, length: 0))
            d.textOverride = { range in
                if rangesOnly && range.length == 6 { return nil }
                if !rangesOnly && range.location == 2 { return "重复" }
                return ((output + "后文") as NSString).substring(with: range)
            }
        }
        let s = try prefixEdit(f, candidate: output, systemUndoTrial: true)
        let terminal = d.events.last { $0["event"] as? String == "confirmed" }
        check(s.phase == .edited && f.keys.isEmpty &&
              terminal?["right_check"] as? String == (rangesOnly ? "before_boundary" : "right_range_unconfirmed") &&
              terminal?["right_move_graphemes"] as? Int == (rangesOnly ? 2 : nil),
              "missing AXValue or conflicting range produced the wrong distance")
    }
}

test("Codex paragraph trial replays the three B110 geometries with one right-key batch") {
    for (length, blanks) in [(454, 2), (571, 3), (616, 5)] {
        let (f, d) = prefixFixture(text: "前文", caret: 2)
        d.textEnabled = true
        let lead = String(repeating: "段\n\n", count: blanks)
        let output = lead + String(repeating: "字", count: length - lead.utf16.count)
        let rendered = output.replacingOccurrences(of: "\n\n", with: "\n")
        f.proxy.onInsert = {
            guard f.proxy.view.string == output else { return }
            f.proxy.view.string = rendered
            f.proxy.fixedReportedSelection = NSRange(location: length, length: 0)
            f.proxy.view.setSelectedRange(NSRange(location: length - 3 * blanks, length: 0))
        }
        let s = try prefixEdit(f, candidate: output, systemUndoTrial: true)
        let end = d.events.last { $0["event"] as? String == "confirmed" }
        check(s.phase == .edited && f.keys == ["caret_forward"] && f.keyCounts == [2 * blanks] &&
              f.proxy.view.selectedRange().location == rendered.utf16.count &&
              f.adapter.verifiedReplacement?.text == rendered && f.proxy.insertCount == 2 &&
              end?["paragraph_after_right_utf16"] as? Int == 0 &&
              end?["paragraph_move_status"] as? String == "at_end",
              "Codex replay failed for \(length)/\(blanks): \(s.error), \(f.keys)")
    }
}

test("Paragraph repair detects other apps, skips wrong text and old suffixes, and never resends a swallowed key") {
    for scenario in ["old_suffix", "other_app", "swallowed", "emoji", "unverified_body", "wrong_offset"] {
        let suffix = scenario == "old_suffix" ? "尾" : ""
        let (f, d) = prefixFixture(scenario == "other_app" ? "com.tencent.xinWeChat" : "com.openai.codex",
                                   text: "前文" + suffix, caret: 2)
        d.textEnabled = true
        f.dropCaretMovement = scenario == "swallowed"
        let output = "甲\n\n乙\n\n丙\n\n末行👩‍💻。"
        let length = output.utf16.count
        f.proxy.onInsert = {
            guard f.proxy.view.string == output + suffix else { return }
            var rendered = output.replacingOccurrences(of: "\n\n", with: "\n")
            if scenario == "old_suffix" { rendered.removeFirst() }
            if scenario == "unverified_body" { rendered = "错" + String(rendered.dropFirst()) }
            f.proxy.view.string = rendered + suffix
            f.proxy.fixedReportedSelection = NSRange(location: length, length: 0)
            let location = length - 9 + (scenario == "wrong_offset" ? -1 : 0)
            f.proxy.view.setSelectedRange(NSRange(location: location, length: 0))
        }
        let s = try prefixEdit(f, candidate: output, systemUndoTrial: true)
        if ["swallowed", "emoji", "other_app"].contains(scenario) {
            check(f.keys == ["caret_forward"] && f.keyCounts == [2] && f.proxy.insertCount == 2,
                  "one 6-unit suffix must produce two grapheme steps only: \(scenario), \(f.keys)")
            let terminal = d.events.last { ["confirmed", "failed"].contains($0["event"] as? String ?? "") }
            check((s.phase == .edited) == (scenario != "swallowed") &&
                  terminal?["paragraph_move_status"] as? String == (scenario == "swallowed" ? "end_unconfirmed" : "at_end"),
                  "movement and body verification conflated: \(scenario), \(s.error)")
        } else {
            check(f.keys.isEmpty && f.adapter.verifiedReplacement == nil, "unexpected trial in \(scenario)")
        }
    }
}

test("Healthy editors complete on one quick sample with no postwrite AX body read or repair delay") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat", "another.editor"] {
        for candidate in ["正常正文", "段落\n\n正常"] {
            let (f, d) = prefixFixture(app, text: "前文", caret: 2)
            d.textEnabled = true
            var readsAfterWrite = 0
            d.onTextRead = { if f.proxy.view.string == candidate { readsAfterWrite += 1 } }
            let s = try prefixEdit(f, candidate: candidate, systemUndoTrial: true)
            check(s.phase == .edited && d.caretSamples == 1 && readsAfterWrite == 0 && f.keys.isEmpty,
                  "extra repair probes on a healthy edit: \(app) \(s.error), \(d.caretSamples)/\(readsAfterWrite)")
        }
    }
}

test("An unknown editor must repeat the same exact-body offset before receiving a single key batch") {
    for transient in [false, true] {
        let (f, d) = prefixFixture("unknown.editor", text: "前文", caret: 2)
        let candidate = "新文👩‍💻。"
        d.textEnabled = true
        f.proxy.onInsert = {
            guard f.proxy.view.string == candidate else { return }
            f.proxy.fixedReportedSelection = NSRange(location: candidate.utf16.count, length: 0)
            f.proxy.view.setSelectedRange(NSRange(location: 2, length: 0))
        }
        d.onRead = { _ in
            if transient && d.caretSamples == 2 {
                f.proxy.view.setSelectedRange(NSRange(location: candidate.utf16.count, length: 0))
            }
        }
        let s = try prefixEdit(f, candidate: candidate, systemUndoTrial: true)
        check(s.phase == .edited && f.keyCounts == (transient ? [] : [2]) &&
              f.keys == (transient ? [] : ["caret_forward"]) && f.proxy.view.selectedRange().location == candidate.utf16.count,
              "transient or stable caret misclassified: \(s.error)")
    }
}

test("Unavailable optional caret sample does not delay a verified write or start a repair") {
    let (f, d) = prefixFixture("other.editor", text: "原文", caret: 2)
    var readsAfterWrite = 0
    f.proxy.onInsert = {
        if f.proxy.view.string == "新文" { d.readUnavailable = true }
    }
    d.onTextRead = { if f.proxy.view.string == "新文" { readsAfterWrite += 1 } }
    let s = try prefixEdit(f, systemUndoTrial: true)
    check(s.phase == .edited && d.caretSamples == 1 && f.keys.isEmpty && readsAfterWrite == 0 &&
          d.events.last?["right_check"] as? String == "unavailable",
          "unavailable optional sample overrode the verified write: \(s.error)")
}

test("B70 main prefix selects AX or host shortcut and keeps rich suffix in place") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat", "com.microsoft.VSCode", "test.editor"] {
        for keyboard in [false, true] {
            let (f, driver) = prefixFixture(app, keyboard: keyboard)
            let attributes: [NSAttributedString.Key: Any] = [.foregroundColor: NSColor.red, .link: URL(string: "https://example.org")!]
            f.proxy.view.textStorage!.addAttributes(attributes, range: NSRange(location: 2, length: 2))
            var payloads: [String] = []
            f.proxy.onInsert = { payloads.append(f.proxy.view.string) }
            let s = try prefixEdit(f, candidate: "新文😀\n第二行")
            check(s.phase == .edited && f.proxy.view.string == "新文😀\n第二行后文", "B70 edit failed in \(app): \(s.error)")
            let end = "新文😀\n第二行".utf16.count
            check(f.proxy.view.selectedRange() == NSRange(location: end, length: 0), "caret crossed suffix")
            let attrs = f.proxy.view.textStorage!.attributes(at: end, effectiveRange: nil)
            check((attrs[.foregroundColor] as? NSColor) == .red && attrs[.link] != nil, "suffix formatting was replaced")
            check(driver.calls == [NSRange(location: 0, length: app == "com.tencent.xinWeChat" ? 2 : 4)], "AX did not select prefix+instruction")
            check(f.keys == (keyboard ? ["select_to_start"] : []), "unexpected full selection/caret key")
            check(f.proxy.insertCount == 2, "candidate not a single commit")
        }
    }
}
test("B70 ordinary dictation undo retains main sentence selection and delete") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat"] {
        let (f, driver) = prefixFixture(app)
        f.proxy.acceptsEmptyInsert = false
        let s = try f.session(); s.update("听写", final: true); s.cancel(); f.drain(s)
        check(s.phase == .undone && f.proxy.view.string == "前文后文", "main dictation undo regressed")
        check(driver.calls.isEmpty && f.keys == ["select_previous", "delete"], "dictation undo entered AX edit path")
    }
}
test("B70 edit undo redo retry preserve suffix and require only prefix source") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat"] {
        for keyboard in [false, true] {
            let (f, _) = prefixFixture(app, keyboard: keyboard)
            let s = try prefixEdit(f, candidate: "扩写新文")
            check(s.phase == .edited, "edit failed")
            s.cancel(); f.drain(s)
            check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "edit undo failed: \(s.error)")
            s.retryEdit(); f.drain(s)
            check(s.phase == .editing && s.editRequestText == "前文", "retry included suffix")
            s.applyEdit(text: "再改", revision: s.revision, error: nil); f.drain(s)
            check(s.phase == .edited && f.proxy.view.string == "再改后文", "retry failed: \(s.error)")
            s.cancel(); f.drain(s); s.cancel(); f.drain(s)
            check(s.phase == .undone && f.proxy.view.string == "前文后文", "double undo failed: \(s.error)")
            check(!f.keys.contains("select_all") && !f.keys.contains("caret_from_end"), "undo rewrote suffix")
        }
    }
}
test("B70 prefix is independent of a very long trailing document") {
    for n in [513, 32769] {
        let suffix = String(repeating: "尾", count: n)
        let (f, _) = prefixFixture(text: "前文" + suffix)
        let s = try prefixEdit(f)
        check(s.phase == .edited && f.proxy.view.string == "新文" + suffix && f.keys.isEmpty, "long suffix blocked or moved edit")
    }
}
test("B70 swallowed shortcut never writes or retries") {
    let (f, _) = prefixFixture(keyboard: true); f.dropSelectToStart = true
    let s = try prefixEdit(f)
    check(s.phase == .error && f.proxy.view.string == "前文改写后文" && f.proxy.insertCount == 1, "swallowed shortcut authorized write")
    check(f.keys == ["select_to_start"] && s.error.contains("\(InputMethodDiagnostics.buildLabel)/"), "missing error provenance or repeated key")
}
test("B70 uncertain AX outcome does not stack a keyboard fallback") {
    let (f, d) = prefixFixture(); d.attempt = .uncertain("ax_set_outcome_unknown")
    let s = try prefixEdit(f)
    check(s.phase == .error && f.proxy.insertCount == 1 && f.keys.isEmpty && s.error.contains("ax_set_outcome_unknown"), "uncertain AX retried")
}
test("B70 AX wrong or absent range cannot authorize a candidate") {
    for wrong in [false, true] {
        let (f, d) = prefixFixture(); d.wrong = wrong; d.drop = !wrong
        let s = try prefixEdit(f)
        check(s.phase == .error && f.proxy.insertCount == 1 && f.keys.isEmpty, "bad AX selection authorized write")
    }
}
test("B70 stale prefix and unavailable selected text stop before candidate write") {
    for missing in [false, true] {
        let (f, d) = prefixFixture()
        d.afterSelect = { if missing { f.proxy.unavailableDocumentRead = true } else { f.proxy.staleDocumentRead = true } }
        let s = try prefixEdit(f)
        check(s.phase == .error && f.proxy.insertCount == 1, "unverified source authorized replacement")
    }
}
test("B71 missing postwrite body cannot create a model-backed undo record") {
    let (f, d) = prefixFixture()
    f.proxy.onInsert = { if f.proxy.view.string == "新文后文" { f.proxy.unavailableDocumentRead = true } }
    let s = try prefixEdit(f)
    check(s.phase == .error && f.proxy.view.string == "新文后文" && s.undoRecord() == nil, "missing body created history")
    check(s.error.contains("postwrite_baseline_unavailable") && d.events.last?["native_text_verified"] as? Bool == false, "missing-body failure mislabeled")
}
test("B71 dropped commit cannot create a baseline from the old selection") {
    let (f, _) = prefixFixture()
    f.proxy.onInsert = { if f.proxy.view.string == "前文改写后文" { f.proxy.dropNonemptyInsert = true } }
    let s = try prefixEdit(f)
    check(s.phase == .error && s.undoRecord() == nil && f.proxy.insertCount == 2, "dropped write accepted or retried")
}

test("B70 empty edit deletes selected prefix with main key path and preserves suffix") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat"] {
        let (f, _) = prefixFixture(app); f.proxy.acceptsEmptyInsert = false
        let s = try prefixEdit(f, candidate: "")
        check(s.phase == .edited && f.proxy.view.string == "后文", "empty edit failed: \(s.error)")
        check(f.keys == ["delete"] && f.proxy.emptyInserts == (app == "com.tencent.xinWeChat" ? 1 : 0), "empty insert used instead of delete")
        s.cancel(); f.drain(s)
        check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "empty edit undo failed: \(s.error)")
    }
}
test("B70 explicit original selection is restored without rewriting following suffix") {
    let (f, _) = prefixFixture(text: "前文选中后文")
    f.proxy.view.setSelectedRange(NSRange(location: 2, length: 2))
    let s = try prefixEdit(f)
    check(s.phase == .edited && f.proxy.view.string == "新文选中后文", "original selected text lost: \(s.error)")
    s.cancel(); f.drain(s); s.cancel(); f.drain(s)
    check(s.phase == .undone && f.proxy.view.string == "前文选中后文", "selection undo failed: \(s.error)")
}
test("B70 arbitrary range uses exact AX or rejects an overbroad shortcut") {
    for keyboard in [false, true] {
        let (f, _) = prefixFixture(text: "前文目标后文", caret: 4, keyboard: keyboard)
        f.adapter.replaceEditing(NSRange(location: 2, length: 2), with: "新目标", restoringCaret: nil, expected: "目标")
        for _ in 0..<50 { f.now += 0.06; f.adapter.advanceClientOperation() }
        check(f.proxy.view.string == (keyboard ? "前文目标后文" : "前文新目标后文"), "range replacement damaged suffix")
        check(f.keys.isEmpty && (f.adapter.clientOperationFailure != nil) == keyboard, "unsupported fallback sent keys")
    }
}
test("B70 changed target stops after selection before candidate commit") {
    let (f, d) = prefixFixture(); d.afterSelect = { d.valid = false }
    let s = try prefixEdit(f)
    check(s.phase == .error && f.proxy.insertCount == 1 && f.keys.isEmpty, "changed target was written")
}
test("B70 cancellation ignores a late selection acknowledgement") {
    let (f, d) = prefixFixture()
    d.afterSelect = { f.adapter.cancelClientOperation() }
    let s = try prefixEdit(f)
    check(s.phase != .edited && f.proxy.insertCount == 1, "cancelled selection wrote a result")
}
test("B70 complete snapshots keep prefix ownership through edit undo and retry") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat"] {
        let (f, _) = prefixFixture(app)
        f.proxy.reportedLength = f.proxy.view.string.utf16.count
        f.proxy.onInsert = { f.proxy.reportedLength = f.proxy.view.string.utf16.count }
        let s = try prefixEdit(f)
        check(s.phase == .edited && f.proxy.view.string == "新文后文", "full snapshot edit failed: \(s.error)")
        s.cancel(); f.drain(s)
        check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "full snapshot undo failed: \(s.error)")
        s.retryEdit(); f.drain(s)
        check(s.phase == .editing, "full snapshot retry failed: \(s.error)")
        s.applyEdit(text: "再次", revision: s.revision, error: nil); f.drain(s)
        check(s.phase == .edited && f.proxy.view.string == "再次后文", "full snapshot retry write failed: \(s.error)")
    }
}
test("B70 history undo and redo preserve prefix scope and rich suffix") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat"] {
        let (f, _) = prefixFixture(app)
        let history = CompositionUndoHistory(); history.configure(enabled: true)
        let s = try f.session(); history.begin(s.original)
        s.onSettled = { _, _ in history.settled(s) }
        s.update("改写", final: false); s.convert(); s.update("改写", final: true)
        s.applyEdit(text: "新的前文", revision: s.revision, error: nil); f.drain(s)
        history.undo(s); f.drain(s)
        check(f.proxy.view.string == "前文改写后文", "history edit undo lost suffix: \(s.error)")
        guard let redo = history.prepareRedo(s) else { fatalError("missing redo") }
        let next = CompositionSession(client: f.adapter, undo: redo.result,
            utteranceID: s.utteranceID, sequence: s.lastSequence, revision: s.revision + 1, clock: { f.now })
        next.onSettled = { _, _ in history.settled(next) }
        next.restoreRedo(redo); f.drain(next)
        check(f.proxy.view.string == "新的前文后文", "history redo lost suffix: \(next.error)")
    }
}
test("B70 cancelled model result and duplicate result cannot write twice") {
    let (f, _) = prefixFixture(); let s = try f.session()
    s.update("改写", final: false); s.convert(); s.update("改写", final: true)
    let revision = s.revision
    s.cancel(); f.drain(s); let writes = f.proxy.insertCount
    s.applyEdit(text: "迟到", revision: revision, error: nil); f.drain(s)
    check(f.proxy.insertCount == writes && f.proxy.view.string == "前文改写后文", "late candidate wrote")
    let (f2, _) = prefixFixture(); let s2 = try prefixEdit(f2); let written = f2.proxy.insertCount
    s2.applyEdit(text: "再写", revision: s2.revision, error: nil); f2.drain(s2)
    check(f2.proxy.insertCount == written && f2.proxy.view.string == "新文后文", "duplicate candidate wrote")
}

test("B70 cancellation during final AX target validation cannot write") {
    let (f, d) = prefixFixture()
    d.onValidate = { if $0 == 2 { f.adapter.cancelClientOperation() } }
    let s = try prefixEdit(f)
    check(d.validationCount >= 2 && s.phase != .edited && f.proxy.insertCount == 1 &&
          f.proxy.view.string == "前文改写后文", "AX reentry cancellation still wrote a result")
}

test("B70 AX readback accepts the selected range while IMK still reports old caret") {
    let (f, d) = prefixFixture()
    d.afterSelect = { f.proxy.fixedReportedSelection = NSRange(location: 4, length: 0) }
    f.proxy.onInsert = { if f.proxy.view.string == "新文后文" { f.proxy.fixedReportedSelection = nil } }
    let s = try prefixEdit(f)
    check(s.phase == .edited && f.proxy.view.string == "新文后文" && f.keys.isEmpty,
          "AX selection was blocked by stale IMK geometry: \(s.error)")
}
test("B70 empty WeChat prefix inserts without AX selection or shortcut") {
    let (f, d) = prefixFixture("com.tencent.xinWeChat", text: "后文", caret: 0, keyboard: true)
    f.adapter.replaceEditing(NSRange(location: 0, length: 0), with: "新文", restoringCaret: nil, expected: "")
    for _ in 0..<50 { f.now += 0.06; f.adapter.advanceClientOperation() }
    check(f.adapter.clientOperationFailure == nil && f.proxy.view.string == "新文后文", "empty prefix insert failed")
    check(d.calls.isEmpty && f.keys.isEmpty, "empty caret unnecessarily selected through AX or shortcut")
}

test("B70 AX readback unavailable never trusts IMK selection or stacks fallback") {
    let (f, d) = prefixFixture(); d.readUnavailable = true
    let s = try prefixEdit(f)
    check(s.phase == .error && f.proxy.insertCount == 1 && f.keys.isEmpty, "missing AX readback was accepted")
    check(d.events.last?["selection_read_available"] as? Bool == false, "missing AX evidence not logged")
}
test("B70 prewrite AX changes and readback cancellation cannot commit") {
    for cancel in [false, true] {
        let (f, d) = prefixFixture()
        d.onRead = { if $0 == 2 {
            if cancel { f.adapter.cancelClientOperation() }
            else { f.proxy.view.setSelectedRange(NSRange(location: 4, length: 0)) }
        } }
        let s = try prefixEdit(f)
        check(d.readCount == 2 && s.phase != .edited && f.proxy.insertCount == 1,
              "AX change or cancellation during readback still wrote")
    }
}
test("B70 empty replacement at empty range never deletes outside ownership") {
    let (f, d) = prefixFixture("com.tencent.xinWeChat")
    f.adapter.replaceEditing(NSRange(location: 2, length: 0), with: "", restoringCaret: nil, expected: "")
    for _ in 0..<50 { f.now += 0.06; f.adapter.advanceClientOperation() }
    check(f.adapter.clientOperationFailure == nil && f.proxy.view.string == "前文后文" &&
          f.proxy.insertCount == 0 && f.keys.isEmpty && d.calls.isEmpty, "empty no-op deleted neighboring content")
    check(d.events.last?["write_sent"] as? Bool == false, "no-op claimed write delivery")
}
test("B70 WeChat fallback cannot write when shortcut delivery is rejected or swallowed") {
    for reject in [false, true] {
        let (f, _) = prefixFixture("com.tencent.xinWeChat", keyboard: true)
        if reject { f.keyError = "host delivery rejected" } else { f.dropSelectToStart = true }
        let s = try prefixEdit(f)
        check(s.phase == .error && f.proxy.view.string == "前文后文" &&
              f.keys == ["select_to_start"] && f.proxy.insertCount == 1, "failed WeChat key authorized a write")
    }
}

// The bridge values are replayed; key movement uses an actual local NSTextView.
func prepareRightRepair(_ f: WeChatFixture, _ d: MainPrefixDriver,
                        candidate: String, before: Int) {
    f.proxy.onTextRead = { _ in
        if !f.keys.isEmpty { f.proxy.unavailableDocumentRead = false }
    }
    f.proxy.onInsert = {
        if f.proxy.view.string.hasPrefix(candidate) {
            f.proxy.view.setSelectedRange(NSRange(location: before, length: 0))
            f.proxy.fixedReportedSelection = NSRange(location: candidate.utf16.count, length: 0)
            f.proxy.unavailableDocumentRead = true
        }
    }
}

test("B73 replay IMK 271 AX 268 to IMK 273 AX 270 continues to body baseline") {
    let (f, d) = prefixFixture()
    let candidate = String(repeating: "字", count: 273)
    prepareRightRepair(f, d, candidate: candidate, before: 268)
    f.proxy.onInsert = {
        if f.proxy.view.string.hasPrefix(candidate) {
            f.proxy.view.setSelectedRange(NSRange(location: 268, length: 0))
            f.proxy.fixedReportedSelection = NSRange(location: 271, length: 0)
            f.proxy.unavailableDocumentRead = true
        }
    }
    f.afterKey = { _, _ in
        f.proxy.view.setSelectedRange(NSRange(location: 270, length: 0))
        f.proxy.fixedReportedSelection = NSRange(location: 273, length: 0)
    }
    let s = try prefixEdit(f, candidate: candidate)
    check(s.phase == .edited && f.keys == ["caret_forward"] && f.keyCounts == [3],
          "post-move coordinate difference still blocked baseline: \(s.error)")
    check(s.undoRecord()?.editedText == candidate && f.proxy.insertCount == 2 &&
          f.proxy.view.string == candidate + "后文", "body baseline or single-write behavior changed")
    check(d.events.last?["postwrite_imk_after_move"] as? [Int] == [273, 0] &&
          d.events.last?["postwrite_ax_after_move"] as? [Int] == [270, 0] &&
          d.events.last?["baseline_range"] as? [Int] == [0, 273], "missing continued baseline geometry")
}

test("B74 unequal post-move coordinates do not bypass failed IMK and AX body reads") {
    let (f, d) = prefixFixture(); d.textEnabled = true
    d.textOverride = { _ in nil }
    prepareRightRepair(f, d, candidate: "新文", before: 1)
    f.proxy.onTextRead = nil
    f.afterKey = { _, _ in f.proxy.fixedReportedSelection = NSRange(location: 3, length: 0) }
    let s = try prefixEdit(f)
    check(s.phase == .error && s.error.contains("postwrite_baseline_unavailable") &&
          f.keys == ["caret_forward"] && f.proxy.insertCount == 2 && d.textReadCount > 0,
          "alignment relaxation bypassed actual body read failure: \(s.error)")
    check(s.undoRecord() == nil &&
          d.events.last?["postwrite_caret_status"] as? String == "moved_without_alignment_check",
          "unreadable baseline became undoable or failed at the old alignment gate")
}

test("B78 AX baseline uses own 229 prefix while IMK caret remains 235") {
    let (f, d) = prefixFixture(); d.textEnabled = true
    let candidate = String(repeating: "字", count: 235)
    prepareRightRepair(f, d, candidate: candidate, before: 225)
    f.proxy.onTextRead = nil
    f.proxy.onInsert = {
        if f.proxy.view.string.hasPrefix(candidate) {
            f.proxy.view.setSelectedRange(NSRange(location: 225, length: 0))
            f.proxy.fixedReportedSelection = NSRange(location: 231, length: 0)
            f.proxy.unavailableDocumentRead = true
        }
    }
    f.afterKey = { _, _ in
        f.proxy.view.setSelectedRange(NSRange(location: 229, length: 0))
        f.proxy.fixedReportedSelection = NSRange(location: 235, length: 0)
    }
    let s = try prefixEdit(f, candidate: candidate)
    check(s.phase == .edited && f.keys == ["caret_forward"] && f.keyCounts == [6] &&
          f.proxy.insertCount == 2 && d.textReadCount == 1,
          "stable AX offset still rejected body fallback: \(s.error)")
    check(s.undoRecord()?.editedText == String(candidate.prefix(229)) &&
          f.adapter.verifiedReplacement?.range == NSRange(location: 0, length: 229) &&
          f.adapter.verifiedReplacementCaret == NSRange(location: 235, length: 0) &&
          d.events.last?["baseline_read_method"] as? String == "ax" &&
          d.events.last?["baseline_range"] as? [Int] == [0, 235],
          "AX body coordinates were mixed with IMK caret or model text")
}

test("B73 noncollapsed AX selection after movement still stops before body baseline") {
    let (f, d) = prefixFixture()
    prepareRightRepair(f, d, candidate: "新文", before: 1)
    f.afterKey = { _, _ in d.axOverride = NSRange(location: 0, length: 1) }
    let s = try prefixEdit(f)
    check(s.phase == .error && s.error.contains("postwrite_ax_selection_changed") &&
          f.keys == ["caret_forward"] && f.proxy.insertCount == 2 && s.undoRecord() == nil,
          "position-only relaxation also accepted a text selection: \(s.error)")
}

test("B70 replay 363 IMK 357 AX and 361 reported length sends six right keys") {
    let (f, d) = prefixFixture()
    let candidate = String(repeating: "字", count: 363)
    d.metadata["postwrite_ax_text_length"] = 361
    d.caretMoveAttempt = .unavailable("postwrite_caret_out_of_bounds")
    prepareRightRepair(f, d, candidate: candidate, before: 357)
    let s = try prefixEdit(f, candidate: candidate)
    check(s.phase == .edited && f.keys == ["caret_forward"] && f.keyCounts == [6],
          "363/357 replay did not send exactly six right keys: \(s.error), keys=\(f.keys)")
    check(d.caretMoves.isEmpty && f.proxy.view.selectedRange() == NSRange(location: 363, length: 0),
          "relative repair used AX setter or failed to reach the fixed target")
    check(f.proxy.insertCount == 2 && f.proxy.view.string == candidate + "后文", "repair rewrote candidate")
    check(d.events.last?["native_text_verified"] as? Bool == true &&
          d.events.last?["undo_baseline_source"] as? String == "post_caret_imk" &&
          d.events.last?["postwrite_caret_status"] as? String == "moved_without_alignment_check",
          "missing post-key body baseline")
}

test("B70 matching or unavailable AX retains the existing write without keys") {
    for unavailable in [false, true] {
        let (f, d) = prefixFixture()
        if unavailable { f.proxy.onInsert = { if f.proxy.view.string == "新文后文" { d.readUnavailable = true } } }
        let s = try prefixEdit(f)
        check(s.phase == .edited && f.keys.isEmpty && d.caretMoves.isEmpty, "unneeded repair")
    }
}

test("B73 swallowed right movement continues to body check without repeating or rewriting") {
    let (f, d) = prefixFixture(); f.dropCaretMovement = true
    prepareRightRepair(f, d, candidate: "新文", before: 1)
    let s = try prefixEdit(f)
    check(s.phase == .edited &&
          f.keys == ["caret_forward"] && f.keyCounts == [1] && f.proxy.insertCount == 2,
          "body check blocked by alignment or movement retried: \(s.error)")
    check(d.events.last?["postwrite_ax_after_move"] as? [Int] == [1, 0], "missing post-key AX read")
}

test("B73 a changed IMK position after right movement enters body check without chasing") {
    let (f, d) = prefixFixture()
    prepareRightRepair(f, d, candidate: "新文", before: 1)
    f.afterKey = { _, _ in f.proxy.fixedReportedSelection = NSRange(location: 3, length: 0) }
    let s = try prefixEdit(f)
    check(s.phase == .edited && f.keys == ["caret_forward"],
          "changed position blocked body check or caused additional movement: \(s.error)")
    check(d.events.last?["postwrite_imk_after_move"] as? [Int] == [3, 0], "missing post-key IMK position")
}

test("B70 right delivery rejection and missing acknowledgement have separate errors") {
    for missing in [false, true] {
        let (f, d) = prefixFixture()
        prepareRightRepair(f, d, candidate: "新文", before: 1)
        if missing { f.deferKeyAck = true } else { f.keyError = "test delivery rejected" }
        let s = try prefixEdit(f)
        check(s.phase == .error && s.error.contains(missing ? "postwrite_key_ack_timeout" : "key_delivery_failed") &&
              f.keys == ["caret_forward"] && f.proxy.insertCount == 2, "delivery failure was lost: \(s.error)")
        f.pendingKeyAck?(nil)
        check(s.phase == .error, "late acknowledgement settled failed edit")
    }
}

test("B70 cancellation during AX read or right dispatch cannot confirm an edit") {
    for duringKey in [false, true] {
        let (f, d) = prefixFixture()
        prepareRightRepair(f, d, candidate: "新文", before: 1)
        if duringKey { f.afterKey = { _, _ in f.adapter.cancelClientOperation() } }
        else { d.onRead = { if $0 == 3 { f.adapter.cancelClientOperation() } } }
        let s = try prefixEdit(f)
        check(s.phase != .edited && f.keys.count == (duringKey ? 1 : 0) && f.proxy.insertCount == 2,
              "cancelled repair completed or sent extra keys")
    }
}

test("B70 nonempty AX selection waits and never receives unshifted right keys") {
    let (f, d) = prefixFixture()
    f.proxy.onInsert = { if f.proxy.view.string == "新文后文" { d.axOverride = NSRange(location: 0, length: 2) } }
    d.onRead = { if $0 == 5 { d.axOverride = NSRange(location: 2, length: 0) } }
    let s = try prefixEdit(f)
    check(s.phase == .edited && f.keys.isEmpty, "nonempty selection received movement")
}

test("B70 stable 300 to 312 relative repair preserves rich suffix and edit undo") {
    let (f, d) = prefixFixture(); let candidate = String(repeating: "字", count: 312)
    f.proxy.view.textStorage!.addAttribute(.foregroundColor, value: NSColor.red, range: NSRange(location: 2, length: 2))
    prepareRightRepair(f, d, candidate: candidate, before: 300)
    let s = try prefixEdit(f, candidate: candidate)
    check(s.phase == .edited && f.keys == ["caret_forward"] && f.keyCounts == [12], "300 to 312 failed")
    check(f.proxy.view.string == candidate + "后文" &&
          f.proxy.view.textStorage!.attribute(.foregroundColor, at: 312, effectiveRange: nil) as? NSColor == .red,
          "relative repair changed suffix formatting")
    f.proxy.onInsert = nil; f.proxy.fixedReportedSelection = nil; f.proxy.unavailableDocumentRead = false
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "repair broke undo: \(s.error)")
}

test("B70 invalid right displacement never produces an unbounded key burst") {
    for delta in [-513, 513] {
        let (f, d) = prefixFixture(text: "前文" + String(repeating: "尾", count: 600))
        let candidate = String(repeating: "字", count: 514)
        prepareRightRepair(f, d, candidate: candidate, before: candidate.utf16.count - delta)
        let s = try prefixEdit(f, candidate: candidate)
        check(s.phase == .error && s.error.contains("postwrite_caret_delta_invalid") && f.keys.isEmpty,
              "invalid right displacement generated keys")
    }
}

test("B70 target change before right dispatch stops without keys") {
    let (f, d) = prefixFixture()
    prepareRightRepair(f, d, candidate: "新文", before: 1)
    d.onRead = { if $0 == 3 { d.valid = false } }
    let s = try prefixEdit(f)
    check(s.phase != .edited && f.keys.isEmpty, "changed target received right keys")
}

test("B70 AX disappearing after right dispatch is not a successful skip") {
    let (f, d) = prefixFixture()
    prepareRightRepair(f, d, candidate: "新文", before: 1)
    f.afterKey = { _, _ in d.readUnavailable = true }
    let s = try prefixEdit(f)
    check(s.phase == .error && s.error.contains("postwrite_caret_read_unavailable") && f.keys == ["caret_forward"],
          "unreadable post-key AX accepted or repeated")
}

test("B73 character steps differing from UTF16 offsets proceed without claiming alignment") {
    let (f, d) = prefixFixture()
    prepareRightRepair(f, d, candidate: "字😀", before: 0)
    let s = try prefixEdit(f, candidate: "字😀")
    check(s.phase == .edited &&
          f.keyCounts == [3] && f.proxy.insertCount == 2 &&
          d.events.last?["postwrite_caret_status"] as? String == "moved_without_alignment_check",
          "alignment still required or movement repeated: \(s.error)")
}


test("B70 AX ahead of IMK sends one left batch and confirms agreement") {
    let (f, d) = prefixFixture()
    prepareRightRepair(f, d, candidate: "新文", before: 3)
    let s = try prefixEdit(f)
    check(s.phase == .edited && f.keys == ["caret_backward"] && f.keyCounts == [1],
          "AX ahead did not move left once: \(s.error)")
    check(f.proxy.view.selectedRange() == NSRange(location: 2, length: 0), "left movement missed target")
}

test("B70 agreement on a refreshed position completes without requiring old IMK target") {
    let (f, d) = prefixFixture()
    prepareRightRepair(f, d, candidate: "新文", before: 1)
    f.afterKey = { _, _ in
        f.proxy.view.setSelectedRange(NSRange(location: 3, length: 0))
        f.proxy.fixedReportedSelection = NSRange(location: 3, length: 0)
    }
    let s = try prefixEdit(f)
    check(s.phase == .edited && f.keys == ["caret_forward"] && f.keyCounts == [1],
          "agreed refreshed position rejected: \(s.error)")
    check(d.events.last?["postwrite_imk_target"] as? [Int] == [2, 0] &&
          d.events.last?["postwrite_imk_after_move"] as? [Int] == [3, 0] &&
          d.events.last?["postwrite_ax_after_move"] as? [Int] == [3, 0], "before/after positions not distinct")
}

test("B71 native-normalized result becomes undo baseline after caret confirmation") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat"] {
        let (f, _) = prefixFixture(app)
        f.proxy.onInsert = {
            if f.proxy.view.string == "新\n文后文" {
                f.proxy.view.textStorage!.replaceCharacters(in: NSRange(location: 0, length: 3), with: "新文")
                f.proxy.view.setSelectedRange(NSRange(location: 2, length: 0))
            }
        }
        let s = try prefixEdit(f, candidate: "新\n文")
        check(s.phase == .edited, "normalized edit rejected: \(s.error)")
        check(s.undoRecord()?.editedText == "新文", "undo saved model output instead of IMK body")
        s.cancel(); f.drain(s)
        check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "normalized undo failed: \(s.error)")
    }
}


test("B71 normalized native text preserves rich suffix and repeated undo redo") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat"] {
        let (f, _) = prefixFixture(app)
        f.proxy.view.textStorage!.addAttribute(.foregroundColor, value: NSColor.red, range: NSRange(location: 2, length: 2))
        f.proxy.onInsert = {
            if f.proxy.view.string == "新\n文后文" {
                f.proxy.view.textStorage!.replaceCharacters(in: NSRange(location: 0, length: 3), with: "新文")
                f.proxy.view.setSelectedRange(NSRange(location: 2, length: 0))
            }
        }
        let h = CompositionUndoHistory()
        var s = try f.session()
        func wire() { s.onSettled = { _, _ in h.settled(s) }; s.onInterrupted = { _ in h.clear() } }
        wire(); s.update("改写", final: false); s.convert(); s.update("改写", final: true)
        s.applyEdit(text: "新\n文", revision: s.revision, error: nil); f.drain(s)
        for _ in 0..<3 {
            check(s.phase == .edited && s.undoRecord()?.editedText == "新文", "model-backed edit record")
            h.undo(s); f.drain(s)
            check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "undo failed: \(s.error)")
            guard let redo = h.prepareRedo(s) else { fatalError("missing normalized redo") }
            s = CompositionSession(client: f.adapter, undo: redo.result, utteranceID: "redo", sequence: 1,
                                   revision: s.revision + 1, clock: { f.now })
            wire(); s.restoreRedo(redo); f.drain(s)
            check(f.proxy.view.string == "新文后文", "redo replayed pre-normalized output")
            check(f.proxy.view.textStorage!.attribute(.foregroundColor, at: 2, effectiveRange: nil) as? NSColor == .red,
                  "redo replaced suffix formatting")
        }
    }
}

test("B71 same-length control conversion is accepted without model equality") {
    let (f, d) = prefixFixture()
    f.proxy.onInsert = {
        if f.proxy.view.string == "新\r文后文" {
            f.proxy.view.textStorage!.replaceCharacters(in: NSRange(location: 1, length: 1), with: "\n")
            f.proxy.view.setSelectedRange(NSRange(location: 3, length: 0))
        }
    }
    let s = try prefixEdit(f, candidate: "新\r文")
    check(s.phase == .edited && s.undoRecord()?.editedText == "新\n文", "same-length conversion rejected")
    check(d.events.last?["replacement_text_matches"] as? Bool == false, "model difference not logged")
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "converted body undo failed")
}

test("B71 explicit restored selection uses actual prefix length and keeps suffix") {
    let (f, _) = prefixFixture(text: "前文选😀后文")
    f.proxy.view.setSelectedRange(NSRange(location: 2, length: 3))
    f.proxy.onInsert = {
        if f.proxy.view.string == "新\n文选😀后文" {
            f.proxy.view.textStorage!.replaceCharacters(in: NSRange(location: 0, length: 3), with: "新文")
            f.proxy.view.setSelectedRange(NSRange(location: 5, length: 0))
        }
    }
    let s = try prefixEdit(f, candidate: "新\n文")
    check(s.phase == .edited && s.undoRecord()?.editedText == "新文选😀", "explicit selection baseline wrong: \(s.error)")
    check(f.proxy.view.selectedRange() == NSRange(location: 2, length: 0), "caret did not restore before selection")
    s.cancel(); f.drain(s); s.cancel(); f.drain(s)
    check(s.phase == .undone && f.proxy.view.string == "前文选😀后文", "explicit selection double undo failed")
}

test("B71 baseline reads only after relative keys and uses refreshed IMK coordinate") {
    let (f, d) = prefixFixture()
    prepareRightRepair(f, d, candidate: "新文", before: 1)
    var postwriteReads = 0
    f.proxy.onTextRead = { _ in
        if f.proxy.insertCount >= 2 {
            check(f.keys == ["caret_forward"], "body read before caret correction")
            postwriteReads += 1; f.proxy.unavailableDocumentRead = false
        }
    }
    f.afterKey = { _, _ in
        // Reproduce an editor whose IMK representation expands when refreshed.
        f.proxy.view.textStorage!.replaceCharacters(in: NSRange(location: 0, length: 2), with: "新\n文")
        f.proxy.view.setSelectedRange(NSRange(location: 3, length: 0))
        f.proxy.fixedReportedSelection = NSRange(location: 3, length: 0)
    }
    let s = try prefixEdit(f)
    check(s.phase == .edited && s.undoRecord()?.editedText == "新\n文" && postwriteReads == 1,
          "baseline used old target or model: \(s.error)")
    check(d.events.last?["baseline_range"] as? [Int] == [0, 3] &&
          d.events.last?["postwrite_imk_target"] as? [Int] == [2, 0], "final baseline lost refreshed range")
    f.afterKey = nil; f.proxy.onInsert = nil; f.proxy.onTextRead = nil; f.proxy.fixedReportedSelection = nil
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "refreshed baseline could not undo")
}

test("B71 partial or wrong-range postwrite reads never create an undo record") {
    for wrongRange in [false, true] {
        let (f, _) = prefixFixture()
        f.proxy.textReadOverride = { range in
            guard f.proxy.insertCount >= 2 else { return nil }
            return wrongRange ? ("新文", NSRange(location: 1, length: 2)) : ("新", NSRange(location: 0, length: 1))
        }
        let s = try prefixEdit(f)
        check(s.phase == .error && s.error.contains("postwrite_baseline_unavailable") && s.undoRecord() == nil,
              "partial read became baseline")
        check(f.proxy.view.string == "新文后文" && f.proxy.insertCount == 2, "read failure retried write")
    }
}

test("B80 first complete IMK sample becomes baseline and later mismatch refuses undo") {
    let (f, _) = prefixFixture(); var reads = 0
    f.proxy.textReadOverride = { range in
        guard f.proxy.insertCount >= 2 else { return nil }
        reads += 1
        return (reads % 2 == 0 ? "新文" : "另一", range)
    }
    let s = try prefixEdit(f)
    check(s.phase == .edited && reads == 1 && s.undoRecord()?.editedText == "另一",
          "single complete IMK sample was not saved")
    let writes = f.proxy.insertCount
    s.cancel(); f.drain(s)
    check(s.phase == .error && s.error.contains("mismatch") && f.proxy.insertCount == writes,
          "later IMK mismatch overwrote external content")
}

test("B71 external same-length change is rejected against the native baseline") {
    let (f, _) = prefixFixture()
    let s = try prefixEdit(f)
    f.proxy.view.textStorage!.replaceCharacters(in: NSRange(location: 0, length: 2), with: "外改")
    f.proxy.view.setSelectedRange(NSRange(location: 2, length: 0))
    let writes = f.proxy.insertCount
    s.cancel(); f.drain(s)
    check(s.error.contains("replacement_source_mismatch") && f.proxy.insertCount == writes &&
          f.proxy.view.string == "外改后文", "external edit was overwritten")
}

test("B71 cancellation or target change during baseline read cannot publish history") {
    for cancel in [false, true] {
        let (f, d) = prefixFixture(); var reads = 0
        f.proxy.onTextRead = { _ in
            guard f.proxy.insertCount >= 2 else { return }
            reads += 1
            if reads == 1 {
                if cancel { f.adapter.cancelClientOperation() } else { d.valid = false }
            }
        }
        let s = try prefixEdit(f)
        check(s.phase != .edited && s.undoRecord() == nil && f.proxy.insertCount == 2,
              "retired baseline published")
    }
}


test("B71 redo refreshes its undo baseline when the control transforms again") {
    let (f, _) = prefixFixture()
    var s = try prefixEdit(f)
    guard let record = s.undoRecord() else { fatalError("missing edit record") }
    s.cancel(); f.drain(s)
    guard let redo = s.redoRecord(afterUndoing: record) else { fatalError("missing redo record") }
    f.proxy.onInsert = {
        if f.proxy.view.string == "新文后文" {
            f.proxy.view.textStorage!.replaceCharacters(in: NSRange(location: 0, length: 2), with: "新!文")
            f.proxy.view.setSelectedRange(NSRange(location: 3, length: 0))
        }
    }
    s = CompositionSession(client: f.adapter, undo: redo.result, utteranceID: "redo", sequence: 1,
                           revision: s.revision + 1, clock: { f.now })
    s.restoreRedo(redo); f.drain(s)
    check(s.phase == .edited && s.undoRecord()?.editedText == "新!文", "redo retained previous body")
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "undo after transformed redo failed")
}

test("B71 interior restoration cannot split a combined character") {
    let (f, _) = prefixFixture(text: "前文目标后文", caret: 4)
    f.adapter.replaceEditing(NSRange(location: 2, length: 2), with: "e\u{301}",
                             restoringCaret: 3, expected: "目标")
    for _ in 0..<50 { f.now += 0.06; f.adapter.advanceClientOperation() }
    check(f.adapter.clientOperationFailure != nil &&
          f.keys.isEmpty, "restoration split a composed character")
}

// B72 reproduces the Codex trace: current AX/IMK caret, nil full IMK body.
test("B80 nil IMK postwrite read uses one AX snapshot and preserves rich suffix") {
    let (f, driver) = prefixFixture()
    driver.textEnabled = true
    f.proxy.view.textStorage!.addAttribute(.foregroundColor, value: NSColor.red, range: NSRange(location: 2, length: 2))
    f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
    let s = try prefixEdit(f)
    check(s.phase == .edited, "nil IMK plus valid AX did not finish: \(s.error)")
    check(s.undoRecord()?.editedText == "新文", "AX baseline not saved")
    check(driver.events.contains { $0["undo_baseline_source"] as? String == "post_caret_ax" && $0["event"] as? String == "confirmed" }, "AX provenance missing")
    check(driver.textReadCount == 1, "AX baseline read more than once")
    check(f.proxy.view.textStorage!.attribute(.foregroundColor, at: 2, effectiveRange: nil) as? NSColor == .red, "suffix formatting changed")
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "AX-backed undo failed: \(s.error)")
}

test("B72 healthy IMK baseline does not read AX body") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    let s = try prefixEdit(f)
    check(s.phase == .edited && driver.textReadCount == 0, "AX replaced healthy IMK")
    check(f.adapter.verifiedReplacement?.source == .imk, "IMK source not retained")
}

test("B72 partial IMK read falls back to a complete UTF16 AX snapshot") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    f.proxy.onInsert = {
        if f.proxy.view.string.hasPrefix("新😀") {
            f.proxy.textReadOverride = { range in ("新", NSRange(location: range.location, length: 1)) }
        }
    }
    let s = try prefixEdit(f, candidate: "新😀\n文")
    check(s.phase == .edited && s.undoRecord()?.editedText == "新😀\n文", "partial IMK did not fall back: \(s.error)")
    check(f.adapter.verifiedReplacement?.source == .ax, "AX source missing")
}

test("B72 AX unavailable partial and moving selection cannot establish baseline") {
    for fault in ["unavailable", "partial", "caret"] {
        let (f, driver) = prefixFixture(); driver.textEnabled = true
        f.proxy.onInsert = {
            if f.proxy.view.string.hasPrefix("新文") {
                f.proxy.unavailableDocumentRead = true
                if fault == "unavailable" { driver.textEnabled = false }
                if fault == "partial" { driver.textOverride = { _ in "新" } }
                if fault == "caret" { driver.onTextRead = { driver.axOverride = NSRange(location: 1, length: 0) } }
            }
        }
        let s = try prefixEdit(f)
        check(s.phase == .error && s.undoRecord() == nil, "unsafe AX baseline: \(fault)")
        check(f.proxy.view.string == "新文后文", "failed baseline rewrote text")
    }
}

test("B80 first complete AX sample becomes baseline and later mismatch refuses undo") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
    driver.textOverride = { _ in driver.textReadCount % 2 == 0 ? "甲乙" : "丙丁" }
    let s = try prefixEdit(f)
    check(s.phase == .edited && driver.textReadCount == 1 && s.undoRecord()?.editedText == "丙丁",
          "single complete AX sample not saved")
    let writes = f.proxy.insertCount
    s.cancel(); f.drain(s)
    check(s.phase == .error && s.error.contains("mismatch") && f.proxy.insertCount == writes,
          "later AX mismatch bypassed saved baseline")
}

test("B72 target change or cancellation inside AX read cannot publish history") {
    for cancel in [false, true] {
        let (f, driver) = prefixFixture(); driver.textEnabled = true
        let s = try f.session()
        f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
        driver.onTextRead = { if cancel { s.interrupt("test AX cancellation") } else { driver.valid = false } }
        s.update("改写", final: false); s.convert(); s.update("改写", final: true)
        s.applyEdit(text: "新文", revision: s.revision, error: nil); f.drain(s)
        check(s.phase != .edited && s.undoRecord() == nil, "retired AX read published history")
        check(f.proxy.view.string == "新文后文", "target change caused extra write")
    }
}

test("B79 complete IMK baseline mismatch never falls back to matching AX") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    let s = try prefixEdit(f)
    check(s.phase == .edited, "edit failed")
    f.proxy.unavailableDocumentRead = false
    f.proxy.textReadOverride = { range in (String(repeating: "旧", count: range.length), range) }
    let writes = f.proxy.insertCount
    s.cancel(); f.drain(s)
    check(s.phase == .error && f.proxy.insertCount == writes, "mismatched complete IMK text was bypassed via AX")
}

test("B72 external same-length AX change refuses undo before write") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
    let s = try prefixEdit(f)
    check(s.phase == .edited, "edit failed")
    let writes = f.proxy.insertCount
    f.proxy.view.textStorage!.replaceCharacters(in: NSRange(location: 0, length: 2), with: "别人")
    s.cancel(); f.drain(s)
    check(s.phase == .error && f.proxy.insertCount == writes, "changed AX body overwritten")
    check(f.proxy.view.string == "别人后文", "external text lost")
}

test("B72 AX baseline survives saved history repeated undo and redo") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat"] {
        let (f, driver) = prefixFixture(app); driver.textEnabled = true
        f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
        let h = CompositionUndoHistory()
        var s = try f.session()
        func wire() { s.onSettled = { _, _ in h.settled(s) }; s.onInterrupted = { _ in h.clear() } }
        wire(); s.update("改写", final: false); s.convert(); s.update("改写", final: true)
        s.applyEdit(text: "新文", revision: s.revision, error: nil); f.drain(s)
        for _ in 0..<3 {
            check(s.phase == .edited && f.adapter.verifiedReplacement?.source == .ax, "lost AX baseline: \(s.error)")
            h.undo(s); f.drain(s)
            check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "AX history undo failed: \(s.error)")
            guard let redo = h.prepareRedo(s) else { fatalError("AX redo unavailable") }
            s = CompositionSession(client: f.adapter, undo: redo.result, utteranceID: "ax-redo", sequence: 1,
                                   revision: s.revision + 1, clock: { f.now })
            wire(); s.restoreRedo(redo); f.drain(s)
            check(f.proxy.view.string == "新文后文", "AX redo failed: \(s.error)")
        }
    }
}

test("B72 AX baseline supports suspension and restored undo") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
    let s = try prefixEdit(f)
    let saved = try s.suspension()
    let resumed = try CompositionSession(client: f.adapter, resuming: saved, sequence: 100, clock: { f.now })
    resumed.cancel(); f.drain(resumed)
    check(resumed.phase == .dictated && f.proxy.view.string == "前文改写后文", "AX suspension source lost: \(resumed.error)")
}


test("B79 recovered IMK cannot replace an AX validation source") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
    let s = try prefixEdit(f)
    check(s.phase == .edited && driver.textReadCount > 0, "AX baseline failed")
    f.proxy.unavailableDocumentRead = false; f.proxy.onInsert = nil
    let axReads = driver.textReadCount
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && driver.textReadCount > axReads, "AX baseline lost its reader when IMK recovered")
}

test("B72 AX supports second sentence undo and redo after edit undo") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
    let s = try prefixEdit(f)
    s.cancel(); f.drain(s)
    check(s.phase == .dictated, "first undo failed")
    check(s.undoRecord() != nil, "restored sentence lost its history")
    s.cancel(); f.drain(s)
    check(s.phase == .undone && f.proxy.view.string == "前文后文", "second AX-backed undo failed: \(s.error)")
}


test("B72 double read failure is terminal even if a later read would recover") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
    driver.textOverride = { range in driver.textReadCount == 1 ? nil : (f.proxy.view.string as NSString).substring(with: range) }
    let s = try prefixEdit(f)
    check(s.phase == .error && driver.textReadCount == 1, "double failure was retried")
    check(s.undoRecord() == nil, "failed read created history")
}

test("B79 IMK baseline unavailability stops without AX fallback") {
    let (f, driver) = prefixFixture("com.tencent.xinWeChat"); driver.textEnabled = true
    let s = try prefixEdit(f)
    f.proxy.unavailableDocumentRead = true
    let writes = f.proxy.insertCount
    driver.textOverride = { range in driver.textReadCount == 1 ? nil : (f.proxy.view.string as NSString).substring(with: range) }
    s.cancel(); f.drain(s)
    check(s.phase == .error && driver.textReadCount == 0 && f.proxy.insertCount == writes, "ownership double failure retried/wrote phase=\(s.phase) reads=\(driver.textReadCount) writes=\(f.proxy.insertCount)/\(writes) error=\(s.error)")
}

test("B72 AX prefix cannot be promoted to a full snapshot") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    f.proxy.reportedLength = 2; f.proxy.unavailableDocumentRead = true
    let snapshot = try f.adapter.snapshot()
    check(snapshot.text == nil && !snapshot.complete, "AX prefix became complete document")
}

test("B72 full AX snapshot checks IMK geometry after the remote read") {
    let (f, driver) = prefixFixture(); driver.textEnabled = true
    f.proxy.reportedLength = 4; f.proxy.unavailableDocumentRead = true
    driver.onTextRead = { f.proxy.fixedReportedSelection = NSRange(location: 1, length: 0) }
    var rejected = false
    do { _ = try f.adapter.snapshot() } catch { rejected = true }
    check(rejected, "AX snapshot retained stale IMK geometry")
}

test("B75 initial partial IMK context uses AX prefix before the next model request") {
    let body = String(repeating: "文", count: 231)
    let (f, driver) = prefixFixture(text: body + "后文", caret: 231)
    driver.textEnabled = true
    f.proxy.textReadOverride = { range in
        if f.proxy.insertCount == 0 && !f.proxy.view.hasMarkedText() {
            if range.location >= 174 && NSMaxRange(range) <= 233 {
                return ((f.proxy.view.string as NSString).substring(with: range), range)
            }
            return (nil, NSRange(location: 0, length: 0))
        }
        return nil
    }
    let s = try f.session()
    check(s.editRequestText == body, "B75 regression: initial source is unavailable despite a complete AX prefix")
    check(!s.original.complete && s.original.text == nil, "prefix falsely claimed a full document")
    check(driver.calls.isEmpty && f.keys.isEmpty && f.proxy.insertCount == 0 &&
          f.proxy.view.selectedRange() == NSRange(location: 231, length: 0), "source read changed the editor")
    var requests = 0
    s.onEdit = { source, instruction, _ in
        check(source == body && instruction == "精简", "wrong next-edit model source")
        requests += 1
    }
    s.update("精简", final: false); s.convert(); s.update("精简", final: true); f.drain(s)
    check(s.phase == .editing && requests == 1, "next edit still failed before the model: \(s.error)")
}

test("B75 initial nil partial and wrong-length IMK reads use exact UTF16 AX prefix") {
    for mode in ["nil", "partial", "length"] {
        let (f, driver) = prefixFixture(text: "前😀文后文", caret: 4)
        driver.textEnabled = true
        f.proxy.textReadOverride = { range in
            if mode == "nil" { return (nil, NSRange(location: 0, length: 0)) }
            return ("前", mode == "partial" ? NSRange(location: 0, length: 1) : range)
        }
        let s = try f.session()
        check(s.editRequestText == "前😀文" && s.original.readableContext?.source == .ax,
              "incomplete IMK \(mode) did not use AX")
        let d = f.adapter.lastReadDiagnostics
        check(d["query"] as? String == "snapshot" && d["read_source"] as? String == "ax" &&
              d["ax_attempted"] as? Bool == true && d["requested_range"] as? [Int] == [0, 4] &&
              d["context_readable_range"] as? [Int] == [0, 4] && d["imk_read"] != nil,
              "initial read lost diagnostic provenance")
        check(driver.textReadCount == 1 && driver.calls.isEmpty && f.keys.isEmpty, "initial read selected or retried")
    }
}

test("B75 healthy initial IMK prefix is preferred even if AX contains different text") {
    let (f, driver) = prefixFixture()
    driver.textEnabled = true; driver.textOverride = { _ in "别文" }
    let s = try f.session()
    check(s.editRequestText == "前文" && driver.textReadCount == 0, "healthy IMK was bypassed")
}

test("B75 a long exact IMK prefix is tried before AX after bounded context probing") {
    let body = String(repeating: "文", count: 33_000)
    let (f, driver) = prefixFixture(text: body + "后文", caret: body.utf16.count)
    driver.textEnabled = true
    let s = try f.session()
    check(s.editRequestText == body && driver.textReadCount == 0, "bounded context skipped a complete exact IMK prefix")
}

test("B75 initial selected text comes from the same AX prefix and excludes it from model source") {
    let (f, driver) = prefixFixture(text: "前文选😀后文", caret: 2)
    f.proxy.view.setSelectedRange(NSRange(location: 2, length: 3))
    f.proxy.unavailableDocumentRead = true; driver.textEnabled = true
    let s = try f.session()
    check(s.editRequestText == "前文" && s.original.selectedText == "选😀" &&
          s.original.readableContext?.range == NSRange(location: 0, length: 5), "selected text lost or sent to the model")
    check(driver.textReadCount == 1 && driver.calls.isEmpty && f.keys.isEmpty, "selected prefix was read twice or moved")
}

test("B75 initial AX context preserves rich suffix through edit undo and a fresh edit") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat"] {
        let (f, driver) = prefixFixture(app)
        driver.textEnabled = true
        f.proxy.view.textStorage!.addAttribute(.foregroundColor, value: NSColor.red, range: NSRange(location: 2, length: 2))
        f.proxy.textReadOverride = { range in
            if f.proxy.view.selectedRange().length == 0 && !f.proxy.view.hasMarkedText() && range.location == 0 {
                return (nil, NSRange(location: 0, length: 0))
            }
            return nil
        }
        let first = try prefixEdit(f)
        check(first.phase == .edited && f.proxy.view.string == "新文后文", "initial AX edit failed in \(app): \(first.error)")
        let next = try f.session()
        check(next.editRequestText == "新文" && next.original.readableContext?.source == .ax, "fresh next edit failed after AX baseline")
        first.cancel(); f.drain(first)
        check(first.phase == .dictated && f.proxy.view.string == "前文改写后文", "undo failed after AX initial context: \(first.error)")
        let attrs = f.proxy.view.textStorage!.attributes(at: 4, effectiveRange: nil)
        check(attrs[.foregroundColor] as? NSColor == .red, "initial AX context damaged formatted suffix")
    }
}

test("B75 missing or short AX initial body stops editing with a precise error and preserves dictation") {
    for mode in ["nil", "short", "target"] {
        let (f, driver) = prefixFixture()
        driver.textEnabled = true
        f.proxy.textReadOverride = { range in range.location == 0 ? (nil, NSRange(location: 0, length: 0)) : nil }
        if mode == "target" { driver.onTextRead = { driver.valid = false } }
        else { driver.textOverride = { _ in mode == "nil" ? nil : "前" } }
        let s = try f.session()
        check(s.editRequestText == nil && s.original.contextReadFailure?.contains("B84/context_read/imk_ax_unavailable") == true,
              "failed AX became editable or lost failure detail")
        check(f.adapter.terminalTextReadFailure == nil && driver.textReadCount == 1, "initial read poisoned dictation or retried")
        var requests = 0; s.onEdit = { _, _, _ in requests += 1 }
        s.update("改写", final: false); s.convert(); s.update("改写", final: true); f.drain(s)
        check(requests == 0 && s.phase == .dictated && f.proxy.view.string == "前文改写后文" &&
              s.error.contains("B84/context_read/imk_ax_unavailable"), "failed context blocked dictation or requested a model: \(s.error)")
    }
}

test("B75 IMK caret or marked state changing during initial AX read rejects stale context") {
    for marked in [false, true] {
        let (f, driver) = prefixFixture()
        driver.textEnabled = true; f.proxy.unavailableDocumentRead = true
        driver.onTextRead = {
            if marked { f.proxy.reportedMarkedRanges = Array(repeating: NSRange(location: 2, length: 1), count: 6) }
            else { f.proxy.fixedReportedSelection = NSRange(location: 1, length: 0) }
        }
        var failure = ""
        do { _ = try f.session() } catch { failure = String(describing: error) }
        check(failure.contains("context_read/geometry_changed") && f.proxy.insertCount == 0 && driver.calls.isEmpty,
              "changing IMK state accepted initial AX context")
    }
}

test("B75 invalidated input lease during initial AX read discards the result") {
    let proxy = StrictIMKClient("前文")
    proxy.unavailableDocumentRead = true
    var current = true
    let adapter = NativeInputClient(proxy, isCurrent: { current })
    let driver = MainPrefixDriver(); driver.proxy = proxy; driver.textEnabled = true
    adapter.configureKeyDelivery({ _, _, _, _ in fatalError("read must not send keys") }, selectionDriver: driver)
    driver.onTextRead = { current = false }
    var rejected = false
    do { _ = try adapter.snapshotForBeginning() } catch { rejected = true }
    check(rejected && proxy.insertCount == 0, "stale lease accepted initial AX context")
}

test("B75 initial context fallback does not broaden active composition or empty ranges") {
    for mode in ["marked", "empty"] {
        let (f, driver) = prefixFixture(text: mode == "empty" ? "" : "前文", caret: mode == "empty" ? 0 : 2)
        driver.textEnabled = true; f.proxy.unavailableDocumentRead = true
        if mode == "marked" { f.proxy.reportedMarkedRanges = Array(repeating: NSRange(location: 0, length: 2), count: 4) }
        let snapshot = try f.adapter.snapshotForBeginning()
        check(driver.textReadCount == 0 && driver.calls.isEmpty && f.keys.isEmpty, "unsafe or unnecessary initial AX read: \(mode)")
        check(mode == "empty" ? snapshot.readableContext?.text == "" : snapshot.readableContext == nil,
              "invalid context became editable")
    }
}


test("B78 AX initial context uses its own caret instead of stale IMK 1536") {
    let (f, driver) = prefixFixture(text: "前文后文", caret: 2)
    driver.textEnabled = true
    f.proxy.fixedReportedSelection = NSRange(location: 1536, length: 0)
    f.proxy.unavailableDocumentRead = true
    let s = try f.session()
    check(s.editRequestText == "前文", "B78 regression: AX own prefix was rejected because IMK requested 1536")
    check(driver.calls.isEmpty && f.keys.isEmpty && f.proxy.insertCount == 0, "context fallback changed the editor")
    check(s.original.selection == NSRange(location: 1536, length: 0) &&
          s.original.contextSelection == NSRange(location: 2, length: 0), "AX caret replaced native geometry")
}

test("B78 AX full snapshot ignores stale IMK length and keeps native geometry separate") {
    let (f, d) = prefixFixture(text: "前文后文", caret: 2); d.textEnabled = true
    f.proxy.fixedReportedSelection = NSRange(location: 1536, length: 0)
    f.proxy.reportedLength = 1536; f.proxy.unavailableDocumentRead = true
    let s = try f.session()
    check(s.editRequestText == "前文" && s.original.readableContext?.text == "前文后文", "AX full text was bounded by IMK length")
    check(s.original.selection.location == 1536 && s.original.contextSelection?.location == 2 &&
          !s.original.complete && !s.canReplace, "AX text authorized an IMK full-document replacement")
    check(d.textReadCount == 1 && f.proxy.insertCount == 0 && f.keys.isEmpty, "snapshot reread or changed text")
}

test("B78 AX initial noncollapsed selection supplies both model boundary and selected text") {
    let (f, d) = prefixFixture(text: "前文选区后文", caret: 2); d.textEnabled = true
    f.proxy.view.setSelectedRange(NSRange(location: 2, length: 2))
    f.proxy.fixedReportedSelection = NSRange(location: 1536, length: 2)
    f.proxy.unavailableDocumentRead = true
    let s = try f.session()
    check(s.editRequestText == "前文" && s.original.selectedText == "选区" && s.editOriginal?.text == "前文选区", "model slicing mixed AX and IMK coordinates")
    check(s.original.selection.location == 1536 && s.original.contextSelection == NSRange(location: 2, length: 2), "native selection lost")
}

test("B78 relative body reads and explicit tail use AX anchors in either offset direction") {
    let (f, d) = prefixFixture(text: "甲乙丙丁戊己", caret: 4); d.textEnabled = true
    f.proxy.unavailableDocumentRead = true
    f.proxy.fixedReportedSelection = NSRange(location: 12, length: 0)
    let preceding = f.adapter.readBody(in: NSRange(location: 10, length: 2), fallback: nil)
    check(preceding?.text == "丙丁" && preceding?.range == NSRange(location: 2, length: 2), "relative read reused IMK origin")
    f.proxy.fixedReportedSelection = NSRange(location: 2, length: 0)
    let prefix = f.adapter.readBody(in: NSRange(location: 0, length: 2), fallback: .prefix(tail: 1))
    check(prefix?.text == "甲乙丙丁戊" && prefix?.range == NSRange(location: 0, length: 5), "AX prefix was truncated to IMK caret")
    check(d.calls.isEmpty && f.keys.isEmpty && f.proxy.insertCount == 0, "body read mutated editor")
}

test("B78 unanchored interior fallback fails without reading an unrelated AX range") {
    let (f, d) = prefixFixture(); d.textEnabled = true
    f.proxy.unavailableDocumentRead = true
    f.proxy.fixedReportedSelection = NSRange(location: 1536, length: 0)
    check(f.adapter.readText(in: NSRange(location: 20, length: 2)) == nil &&
          f.adapter.terminalTextReadFailure?.contains("unanchored_range") == true && d.textReadCount == 0,
          "unrelated IMK interior range was translated into an AX write proof")
}

test("B78 selected AX fallback verifies its own prefix before the candidate write") {
    let (f, d) = prefixFixture(); d.textEnabled = true; f.proxy.unavailableDocumentRead = true
    let s = try f.session()
    s.update("改写", final: false); s.convert(); s.update("改写", final: true)
    f.proxy.onInsert = {
        if f.proxy.view.string == "前文改写后文" {
            f.proxy.fixedReportedSelection = NSRange(location: 100, length: 0)
            f.proxy.unavailableDocumentRead = true
        } else { f.proxy.fixedReportedSelection = nil }
    }
    d.attempt = .unavailable("ax_imk_caret_disagreement")
    f.afterKey = { key, _ in
        if key == "select_to_start" { f.proxy.fixedReportedSelection = NSRange(location: 0, length: 100) }
    }
    s.applyEdit(text: "新文", revision: s.revision, error: nil); f.drain(s)
    check(s.phase == .edited && f.proxy.view.string == "新文后文", "AX selected source could not complete: \(s.error)")
    check(f.keys == ["select_to_start"] && f.proxy.insertCount == 2 &&
          s.original.contextSelection == NSRange(location: 2, length: 0) && s.original.selection.location == 98,
          "selected text geometry was used as native IMK geometry")
    f.afterKey = nil; f.proxy.onInsert = nil; d.attempt = .submitted
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "AX selected context could not restore dictation: \(s.error)")
}

test("B78 AX baseline undo and redo derive native selection from current IMK caret") {
    let (f, d) = prefixFixture(); d.textEnabled = true
    let candidate = String(repeating: "字", count: 235)
    prepareRightRepair(f, d, candidate: candidate, before: 225)
    f.proxy.onTextRead = nil
    f.proxy.onInsert = {
        if f.proxy.view.string.hasPrefix(candidate) {
            f.proxy.view.setSelectedRange(NSRange(location: 225, length: 0))
            f.proxy.fixedReportedSelection = NSRange(location: 231, length: 0)
            f.proxy.unavailableDocumentRead = true
        } else { f.proxy.fixedReportedSelection = nil }
    }
    f.afterKey = { _, _ in
        f.proxy.view.setSelectedRange(NSRange(location: 229, length: 0))
        f.proxy.fixedReportedSelection = NSRange(location: 235, length: 0)
    }
    let s = try prefixEdit(f, candidate: candidate)
    guard let record = s.undoRecord() else { fatalError("AX edit history missing") }
    check(record.editedText == String(candidate.prefix(229)), "baseline used model/IMK length")
    let suffix = String(candidate.suffix(6)) + "后文"
    f.afterKey = { key, _ in
        if key == "select_to_start" { f.proxy.fixedReportedSelection = NSRange(location: 0, length: 235) }
    }
    d.attempt = .unavailable("ax_imk_caret_disagreement")
    s.cancel(); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写" + suffix,
          "AX undo was bounded by saved AX offset as IMK: \(s.error)")
    check(d.calls.last == NSRange(location: 0, length: 235), "AX offset 229 used as native selection target")
    guard let redo = s.redoRecord(afterUndoing: record) else { fatalError("AX undo lost redo") }
    f.afterKey = nil; f.proxy.onInsert = nil; d.attempt = .submitted
    let resumed = CompositionSession(client: f.adapter, undo: record, utteranceID: "b78-redo", sequence: 1,
                                     revision: s.revision + 1, clock: { f.now })
    resumed.restoreRedo(redo); f.drain(resumed)
    check(resumed.phase == .edited && f.proxy.view.string == candidate + "后文" &&
          f.proxy.view.selectedRange() == NSRange(location: 229, length: 0), "AX redo used saved IMK caret inside result: \(resumed.error)")
}


test("B78 oversized stale IMK prefix cannot constrain a small valid AX prefix") {
    let (f, d) = prefixFixture(); d.textEnabled = true
    f.proxy.fixedReportedSelection = NSRange(location: 512 * 1024 + 1, length: 0)
    f.proxy.unavailableDocumentRead = true
    let s = try f.session()
    check(s.editRequestText == "前文" && d.textReadCount == 1 && f.keys.isEmpty, "IMK range limit rejected AX own bounded range")
    d.axOverride = NSRange(location: 512 * 1024 + 1, length: 0)
    check(f.adapter.readBody(in: NSRange(location: 0, length: 512 * 1024 + 1), fallback: .prefix(tail: 0)) == nil,
          "AX own range limit was removed")
}

// Same input field, different AX/IMK paragraph representations; mirrors B78's
// AX 254 / IMK 257 source_mismatch after the selection refreshed IMK.
test("B79 AX context keeps AX validation when selected IMK recovers with extra newlines") {
    let ax = "甲\n乙", imk = "甲\n\n\n\n乙", instruction = "改写"
    let (f, d) = prefixFixture(text: ax + "后文", caret: ax.utf16.count)
    d.textEnabled = true; d.attempt = .unavailable("ax_imk_caret_disagreement")
    f.proxy.unavailableDocumentRead = true
    f.proxy.onInsert = {
        if f.proxy.view.string == ax + instruction + "后文" {
            f.proxy.unavailableDocumentRead = false
            f.proxy.fixedReportedSelection = NSRange(location: imk.utf16.count + instruction.utf16.count, length: 0)
        } else { f.proxy.fixedReportedSelection = nil }
    }
    f.afterKey = { key, _ in
        if key == "select_to_start" {
            f.proxy.fixedReportedSelection = NSRange(location: 0, length: imk.utf16.count + instruction.utf16.count)
        }
    }
    f.proxy.textReadOverride = { range in
        if f.proxy.view.string == ax + instruction + "后文", range == NSRange(location: 0, length: 8) {
            return (imk + instruction, range)
        }
        return nil
    }
    let s = try prefixEdit(f)
    check(s.phase == .edited && f.proxy.view.string == "新文后文",
          "B79 regression: AX baseline was compared against recovered IMK text: \(s.error)")
    check(d.events.contains { $0["source_read_method"] as? String == "ax" }, "AX validation provenance missing")
}

test("B79 bound AX read skips a healthy different IMK body and records the exact source") {
    let (f, d) = prefixFixture(); d.textEnabled = true
    let proof = EditorTextRange(text: "前文", range: NSRange(location: 0, length: 2), source: .ax, readScope: .prefix(tail: 0))
    f.proxy.textReadOverride = { range in (String(repeating: "错", count: range.length), range) }
    let reads = f.proxy.reads
    let observed = try f.adapter.validateBody(proof, stage: "retry_ownership")
    check(observed.source == .ax && f.proxy.reads == reads, "bound AX performed IMK text reads")
    let event = d.events.last!
    check(event["validation_stage"] as? String == "retry_ownership" && event["baseline_source"] as? String == "ax" &&
          event["observed_source"] as? String == "ax" && event["validation_result"] as? String == "matched", "validation diagnostic lost source or stage")
}

test("B79 bound reader unavailability stops even when the other source matches") {
    for source in [EditorTextSource.ax, .imk] {
        let (f, d) = prefixFixture(); d.textEnabled = source == .imk
        f.proxy.unavailableDocumentRead = source == .imk
        let proof = EditorTextRange(text: "前文", range: NSRange(location: 0, length: 2), source: source, readScope: .prefix(tail: 0))
        var message = ""
        do { try f.adapter.validateBody(proof, stage: "redo_ownership") }
        catch { message = error.localizedDescription }
        check(message.contains("B84/redo_ownership/" + source.rawValue + "/read_unavailable"), "missing source-specific read failure: \(message)")
        check(d.events.last?["validation_result"] as? String == "unavailable", "read failure mislabeled content change")
        if source == .imk { check(d.textReadCount == 0, "IMK baseline switched to AX") }
        else { check(f.proxy.reads == 0, "AX baseline tried IMK") }
    }
}

test("B79 bound source mismatch is not bypassed and has source-specific diagnostics") {
    for source in [EditorTextSource.ax, .imk] {
        let (f, d) = prefixFixture(); d.textEnabled = true
        if source == .ax { d.textOverride = { _ in "别文" } }
        else { f.proxy.textReadOverride = { range in ("别文", range) } }
        let proof = EditorTextRange(text: "前文", range: NSRange(location: 0, length: 2), source: source, readScope: .prefix(tail: 0))
        var message = ""
        do { try f.adapter.validateBody(proof, stage: "resume_body") }
        catch { message = error.localizedDescription }
        check(message.contains("B84/resume_body/" + source.rawValue + "/body_mismatch"), "mismatch not attributed: \(message)")
        check(d.events.last?["validation_result"] as? String == "mismatch" && f.proxy.insertCount == 0, "mismatch changed document")
    }
}

test("B80 single AX baseline retains source after IMK recovers") {
    let (f, d) = prefixFixture(); d.textEnabled = true
    f.proxy.onInsert = { if f.proxy.view.string.hasPrefix("新文") { f.proxy.unavailableDocumentRead = true } }
    d.onTextRead = { f.proxy.unavailableDocumentRead = false }
    let s = try prefixEdit(f)
    check(s.phase == .edited && s.undoRecord()?.editBody?.source == .ax && d.textReadCount == 1,
          "single AX receipt lost its source: \(s.error)")
}

// A fresh write needs a new baseline; outside writes IMK recovers with a
// different complete representation. AX stays coherent through every step.
func axHistoryFixture() throws -> (WeChatFixture, MainPrefixDriver, CompositionSession, () -> Void) {
    let (f, d) = prefixFixture(); d.textEnabled = true
    f.proxy.unavailableDocumentRead = true
    var recovered = false
    f.proxy.onInsert = { recovered = false; f.proxy.unavailableDocumentRead = true }
    f.proxy.textReadOverride = { range in
        if recovered, !f.proxy.view.hasMarkedText(), isValidRange(range, length: f.proxy.view.string.utf16.count) {
            return (String(repeating: "错", count: range.length), range)
        }
        return nil
    }
    let s = try prefixEdit(f)
    check(s.phase == .edited && s.undoRecord()?.editBody?.source == .ax, "AX history fixture failed: \(s.error)")
    let recover = { recovered = true; f.proxy.unavailableDocumentRead = false }
    return (f, d, s, recover)
}

test("B79 AX edit baseline survives suspension resume undo redo and repeated undo") {
    let (f, d, initial, recover) = try axHistoryFixture()
    recover()
    let saved = try initial.suspension()
    var s = try CompositionSession(client: f.adapter, resuming: saved, sequence: 100, clock: { f.now })
    let history = CompositionUndoHistory(); history.configure(enabled: true)
    history.settled(s)
    s.onSettled = { _, _ in history.settled(s) }
    history.undo(s); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "AX resumed undo failed: \(s.error)")
    recover()
    guard let redo = history.prepareRedo(s) else { fatalError("saved-source redo missing") }
    check(redo.before.source == .ax && redo.result.editBody?.source == .ax, "redo flattened source")
    s = CompositionSession(client: f.adapter, undo: redo.result, utteranceID: "b79-redo", sequence: 1, revision: s.revision + 1, clock: { f.now })
    s.onSettled = { _, _ in history.settled(s) }
    s.restoreRedo(redo); f.drain(s)
    check(s.phase == .edited && f.proxy.view.string == "新文后文" && s.undoRecord()?.editBody?.source == .ax, "AX redo failed: \(s.error)")
    recover(); history.undo(s); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "AX repeated undo failed: \(s.error)")
    check(d.events.contains { $0["validation_stage"] as? String == "redo_ownership" && $0["baseline_source"] as? String == "ax" }, "redo diagnostic missing")
}

test("B79 restored AX instruction keeps its source through history capture and retry") {
    let (f, d, initial, recover) = try axHistoryFixture()
    recover(); initial.cancel(); f.drain(initial)
    check(initial.phase == .dictated && initial.canRetryEdit, "initial undo failed")
    guard let record = initial.undoRecord() else { fatalError("restored instruction record missing") }
    check(record.editBody?.source == .ax && record.sentenceBody?.source == .ax, "restored receipt flattened")
    recover()
    let restored = CompositionSession(client: f.adapter, undo: record, utteranceID: "b79-retry", sequence: 1, revision: initial.revision + 1, clock: { f.now })
    var requests = 0
    restored.onEdit = { source, instruction, _ in
        check(source == "前文" && instruction == "改写", "retry changed original request")
        requests += 1
    }
    let writes = f.proxy.insertCount
    restored.retryEdit(); f.drain(restored)
    check(restored.phase == .editing && requests == 1 && f.proxy.insertCount == writes, "AX retry mixed reader: \(restored.error)")
    restored.applyEdit(text: "再次", revision: restored.revision, error: nil); f.drain(restored)
    check(restored.phase == .edited && f.proxy.view.string == "再次后文", "AX retry write failed: \(restored.error)")
    check(d.events.contains { $0["validation_stage"] as? String == "retry_ownership" && $0["baseline_source"] as? String == "ax" }, "retry diagnostic missing")
}

test("B79 new BEGIN cannot discard AX history by comparing an IMK snapshot") {
    let (f, _, s, recover) = try axHistoryFixture()
    let history = CompositionUndoHistory(); history.configure(enabled: true); history.settled(s)
    let saved = history.snapshot(); history.clear(); history.restore(saved)
    recover()
    let snapshot = try f.adapter.snapshot()
    check(snapshot.text != "新文后文", "fixture did not recover a different IMK representation")
    history.begin(snapshot)
    guard let record = history.pop() else { fatalError("BEGIN discarded AX history using IMK text") }
    check(record.editBody?.source == .ax, "history serialization lost reader")
    let restored = CompositionSession(client: f.adapter, undo: record, utteranceID: "b79-history", sequence: 1, revision: 1, clock: { f.now })
    restored.cancel(); f.drain(restored)
    check(restored.phase == .dictated && f.proxy.view.string == "前文改写后文", "restored historical AX undo failed: \(restored.error)")
}

test("B79 unavailable AX history refuses undo before any write despite matching IMK") {
    let (f, d, s, _) = try axHistoryFixture()
    f.proxy.textReadOverride = nil; f.proxy.unavailableDocumentRead = false; d.textEnabled = false
    let writes = f.proxy.insertCount
    s.cancel(); f.drain(s)
    check(s.phase == .error && f.proxy.insertCount == writes && s.error.contains("/ax/") && s.error.contains("source_read_unavailable"),
          "AX failure silently used IMK or lost stage: \(s.error)")
    check(d.events.last?["validation_stage"] as? String == "undo_edit_prewrite", "failure stage missing")
}

// B80: a no-motion undo must finish on its first valid post-write sample.
test("B80 single valid tick completes undo without two stabilization windows") {
    for reader in ["imk", "ax", "imk_without_ax_caret"] {
        let (f, d) = prefixFixture(); d.textEnabled = true
        let s = try prefixEdit(f)
        let offset = d.events.count
        f.proxy.onInsert = {
            f.proxy.unavailableDocumentRead = reader == "ax"
            d.readUnavailable = reader == "imk_without_ax_caret"
        }
        s.cancel()
        for _ in 0..<40 {
            if d.events.dropFirst(offset).contains(where: { $0["event"] as? String == "write_sent" }) { break }
            f.now += 0.06; f.adapter.advanceClientOperation(); s.advanceReadback()
        }
        check(d.events.dropFirst(offset).contains { $0["event"] as? String == "write_sent" }, "undo did not write")
        f.now += 0.06; f.adapter.advanceClientOperation(); s.advanceReadback()
        check(s.phase == .dictated && !s.awaitingReadback && !f.adapter.clientOperationPending,
              "B80 single-read undo still waits after first valid postwrite tick: \(reader)")
        check(f.proxy.view.string == "前文改写后文" && f.keys.isEmpty, "single read changed restored text or moved caret")
        let events = Array(d.events.dropFirst(offset))
        let sent = events.first { $0["event"] as? String == "write_sent" }!["operation_ms"] as! Double
        let done = events.first { $0["event"] as? String == "confirmed" }!["operation_ms"] as! Double
        check(done - sent <= 60.001, "two stabilization windows remain")
    }
}


test("B80 timing records include each write phase on success and unavailable read") {
    for available in [false, true] {
        let (f, d) = prefixFixture()
        f.proxy.onTextRead = { _ in if f.proxy.insertCount >= 2 { f.now += 0.007 } }
        d.onRead = { _ in if f.proxy.insertCount >= 2 { f.now += 0.003 } }
        d.onValidate = { _ in if f.proxy.insertCount >= 2 { f.now += 0.002 } }
        f.proxy.onInsert = { if f.proxy.insertCount >= 2 { f.proxy.unavailableDocumentRead = !available } }
        let s = try prefixEdit(f)
        let event = d.events.last { $0["event"] as? String == (available ? "confirmed" : "failed") }!
        check((available ? s.phase == .edited : s.phase == .error), "wrong timing fixture state")
        let phases = event["stage_elapsed_ms"] as! [String: Double]
        for phase in ["written", "postwrite_caret", "postwrite_baseline"] {
            check(phases[phase] != nil && phases[phase]! >= 0, "missing phase duration: \(phase)")
        }
        check(phases["postwrite_caret"]! >= 4.999 && phases["postwrite_baseline"]! >= 8.999,
              "API duration excluded from phase timing")
        let total = event["operation_ms"] as! Double
        check(abs(phases.values.reduce(0, +) - total) < 0.001, "phase durations overlap or omit time")
        check((event["post_write_ms"] as! Double) <= total, "postwrite total exceeds operation")
        let read = event["baseline_read"] as! [String: Any]
        check((read["read_elapsed_ms"] as? Double ?? -1) >= 0, "body API duration missing")
        if available {
            check(event["baseline_confirmation"] as? String == "single_complete_read" &&
                  event["baseline_stable"] as? Bool == false, "single read falsely claims repeated stability")
        }
    }
}

test("B80 single-read completion still waits for valid collapsed caret") {
    let (f, d) = prefixFixture()
    let s = try f.session()
    s.update("改写", final: false); s.convert(); s.update("改写", final: true)
    f.proxy.onInsert = { if f.proxy.insertCount >= 2 { f.proxy.fixedReportedSelection = NSRange(location: 0, length: 2) } }
    s.applyEdit(text: "新文", revision: s.revision, error: nil)
    for _ in 0..<4 { f.now += 0.06; f.adapter.advanceClientOperation(); s.advanceReadback() }
    check(f.adapter.clientOperationPending && s.undoRecord() == nil &&
          !d.events.contains { $0["event"] as? String == "postwrite_baseline_begin" }, "uncollapsed selection became receipt")
    f.proxy.fixedReportedSelection = nil
    f.now += 0.06; f.adapter.advanceClientOperation(); s.advanceReadback()
    check(s.phase == .edited && s.undoRecord()?.editedText == "新文", "valid first caret did not finish")
}


test("B82 postwrite target failure exposes category reason and callsite") {
    for (category, reason) in [("foreground", "application_changed"), ("foreground", "application_unavailable"), ("session", "current_guard_failed")] {
        let (f, d) = prefixFixture()
        f.proxy.onInsert = {
            if f.proxy.insertCount >= 2 {
                d.valid = false
                d.metadata["target_check_result"] = "failed"
                d.metadata["target_last_failure"] = ["category": category, "reason": reason, "step": "advance.before"]
            }
        }
        let s = try prefixEdit(f)
        check(s.phase == .error && f.proxy.insertCount == 2 && s.error.contains(category + "/" + reason), "target error lacks cause: \(s.error)")
        let failed = d.events.last { $0["event"] as? String == "failed" }!
        check((failed["target_failure"] as? [String: Any])?["reason"] as? String == reason &&
              failed["operation_stage"] as? String == "written" && failed["write_sent"] as? Bool == true,
              "postwrite target failure lost its stage")
    }
}
test("B82 invalid current session is diagnosed before driver target check") {
    var current = true
    let d = MainPrefixDriver()
    let f = WeChatFixture("前文后文", caret: 2, codex: true, directRange: false, prefixOnly: true, selectionDriver: d, isCurrent: { current })
    d.proxy = f.proxy
    f.proxy.onInsert = { if f.proxy.insertCount >= 2 { current = false } }
    let s = try prefixEdit(f)
    check(s.phase == .error && s.error.contains("session/current_guard_failed") && f.proxy.insertCount == 2,
          "short circuit lost session attribution: \(s.error)")
}
test("B82 prewrite target failure survives subsequent diagnostic activity and never writes") {
    let (f, d) = prefixFixture()
    d.onValidateStep = { step in
        if step == "write_preflight" {
            d.valid = false
            d.metadata["target_check_result"] = "failed"
            d.metadata["target_last_failure"] = ["category": "foreground", "reason": "application_changed", "step": step]
        }
    }
    d.onRead = { _ in
        // A subsequent observation must not erase the failed prewrite check.
        if !d.valid { d.valid = true; d.metadata["target_check_result"] = "passed"; d.metadata["target_last_failure"] = nil }
    }
    let s = try prefixEdit(f)
    check(s.phase == .error && f.proxy.insertCount == 1 && s.error.contains("foreground/application_changed"),
          "prewrite failure overwritten or write proceeded: \(s.error)")
}

final class TestClipboardEnvironment: ClipboardReadEnvironment {
    let proxy: StrictIMKClient
    var now: TimeInterval = 0
    var changeCount = 1
    var contents: ClipboardContents = [["public.utf8-plain-text": Data("用户剪贴板".utf8), "public.rtf": Data([1, 2, 3])], ["custom.item": Data([4, 5])]]
    var valid = true
    var copyEnabled = true
    var backupFails = false
    var restoreCaret = true
    var partialPrefix = false
    var conflictOnRestore = false
    var commands: [ClipboardReadKey] = []
    var lastTag: Int64 = 0
    var copiedOverride: String?
    var onPost: ((ClipboardReadKey) -> Void)?
    var onPump: (() -> Void)?
    init(_ proxy: StrictIMKClient) { self.proxy = proxy }
    func current() -> Bool { valid }
    func selection() -> NSRange { proxy.selectedRange() }
    func marked() -> NSRange { proxy.markedRange() }
    func backup() throws -> ClipboardContents {
        if backupFails { throw ClipboardReadError(code: "pasteboard_backup_unavailable") }
        return contents
    }
    func text() -> String? { contents.first?["public.utf8-plain-text"].flatMap { String(data: $0, encoding: .utf8) } }
    func restore(_ saved: ClipboardContents, replacing count: Int) throws {
        if conflictOnRestore { contents = [["public.utf8-plain-text": Data("用户的新复制".utf8)]]; changeCount += 1 }
        guard count == changeCount else { throw ClipboardReadError(code: "pasteboard_restore_conflict") }
        if contents != saved { contents = saved; changeCount += 1 }
    }
    func post(_ key: ClipboardReadKey, tag: Int64) throws {
        commands.append(key); lastTag = tag
        let selection = proxy.view.selectedRange()
        switch key {
        case .prefix:
            proxy.view.setSelectedRange(NSRange(location: partialPrefix ? 1 : 0, length: selection.location - (partialPrefix ? 1 : 0)))
        case .suffix:
            proxy.view.setSelectedRange(NSRange(location: selection.location, length: proxy.view.string.utf16.count - selection.location))
        case .copy:
            if copyEnabled {
                let text = copiedOverride ?? (proxy.view.string as NSString).substring(with: selection)
                contents = [["public.utf8-plain-text": Data(text.utf8)]]; changeCount += 1
            }
        case .collapseStart: if restoreCaret { proxy.view.setSelectedRange(NSRange(location: selection.location, length: 0)) }
        case .collapseEnd: if restoreCaret { proxy.view.setSelectedRange(NSRange(location: NSMaxRange(selection), length: 0)) }
        }
        onPost?(key)
    }
    func pump() { now += 0.01; onPump?() }
}
func clipboardFixture(_ app: String = "test.editor", text: String = "前文后文", caret: Int = 2) -> (WeChatFixture, MainPrefixDriver, TestClipboardEnvironment, ClipboardBodyReader) {
    let (f, driver) = prefixFixture(app, text: text, caret: caret)
    let environment = TestClipboardEnvironment(f.proxy)
    let reader = ClipboardBodyReader(environment)
    reader.record = { driver.record($0) }
    f.adapter.configureClipboardReader(reader)
    return (f, driver, environment, reader)
}
test("B84 healthy IMK and AX do not copy or disturb the pasteboard") {
    for ax in [false, true] {
        let (f, d, env, _) = clipboardFixture()
        f.proxy.unavailableDocumentRead = ax; d.textEnabled = ax
        let saved = env.contents
        let body = f.adapter.readBody(in: NSRange(location: 0, length: 2), fallback: .prefix(tail: 0))
        check(body?.source == (ax ? .ax : .imk) && env.commands.isEmpty && env.contents == saved, "healthy reader invoked copy")
    }
}
test("B84 third reader captures initial prefix and preserves rich suffix and all clipboard items") {
    for app in ["com.openai.codex", "com.tencent.xinWeChat", "com.microsoft.VSCode", "test.editor"] {
        let (f, _, env, reader) = clipboardFixture(app, text: "甲😀\n乙后文", caret: 5)
        f.proxy.unavailableDocumentRead = true
        f.proxy.view.textStorage!.addAttribute(.foregroundColor, value: NSColor.red, range: NSRange(location: 5, length: 2))
        let saved = env.contents
        let s = try f.session()
        check(s.original.readableContext?.source == .clipboard && s.editRequestText == "甲😀\n乙", "initial clipboard context failed: \(s.original)")
        check(f.proxy.view.selectedRange() == NSRange(location: 5, length: 0) && env.contents == saved, "context did not restore caret/pasteboard")
        check(f.proxy.view.textStorage!.attribute(.foregroundColor, at: 5, effectiveRange: nil) as? NSColor == .red, "copy altered rich suffix")
        check(reader.diagnostics["clipboard_result"] as? String == "available", "missing clipboard receipt")
    }
}
test("B84 bound clipboard validation bypasses healthy but different IMK and AX") {
    let (f, d, env, _) = clipboardFixture(); d.textEnabled = true
    f.proxy.staleDocumentRead = true; d.textOverride = { _ in "错文" }
    let baseline = EditorTextRange(text: "前文", range: NSRange(location: 0, length: 2), source: .clipboard, readScope: .prefix(tail: 0))
    let before = f.proxy.reads, axBefore = d.textReadCount
    let observed = try f.adapter.validateBody(baseline, stage: "undo_ownership")
    check(observed.source == .clipboard && f.proxy.reads == before && d.textReadCount == axBefore, "clipboard source changed reader")
    check(env.commands.count == 3, "clipboard validation failed to select/copy/restore")
}
test("B84 saved IMK or AX failures never escape to matching clipboard") {
    for source in [EditorTextSource.imk, .ax] {
        let (f, _, env, _) = clipboardFixture(); f.proxy.unavailableDocumentRead = true
        let baseline = EditorTextRange(text: "前文", range: NSRange(location: 0, length: 2), source: source, readScope: .prefix(tail: 0))
        var failed = false
        do { try f.adapter.validateBody(baseline, stage: "redo_ownership") } catch { failed = true }
        check(failed && env.commands.isEmpty, "saved source was replaced with clipboard")
    }
}
test("B84 clipboard mismatch is distinct from unavailable and never rechecks IMK") {
    let (f, _, _, _) = clipboardFixture()
    let baseline = EditorTextRange(text: "旧文", range: NSRange(location: 0, length: 2), source: .clipboard, readScope: .prefix(tail: 0))
    var message = ""
    do { try f.adapter.validateBody(baseline, stage: "retry_ownership") } catch { message = error.localizedDescription }
    check(message.contains("clipboard/body_mismatch") && f.proxy.reads == 0 && f.proxy.insertCount == 0, "mismatch escaped clipboard validation")
}
test("B84 copy timeout rejects old clipboard and still restores caret") {
    let (f, _, env, reader) = clipboardFixture(); env.copyEnabled = false
    let saved = env.contents
    check(reader.read(.prefix(tail: 0), validationStage: "postwrite") == nil, "old pasteboard treated as fresh copy")
    check(reader.diagnostics["clipboard_failure"] as? String == "copy_timeout" && reader.diagnostics["clipboard_failure_step"] as? String == "copy_wait", "timeout attribution missing")
    check(env.contents == saved && f.proxy.view.selectedRange() == NSRange(location: 2, length: 0), "timeout failed cleanup")
}
test("B84 intercepted partial prefix selection fails before copy and restores its endpoint") {
    let (f, _, env, reader) = clipboardFixture(); env.partialPrefix = true
    check(reader.read(.prefix(tail: 0), validationStage: "context") == nil, "partial prefix accepted")
    check(!env.commands.contains(.copy) && f.proxy.view.selectedRange() == NSRange(location: 2, length: 0), "partial selection copied or left behind")
    check(reader.diagnostics["clipboard_failure"] as? String == "selection_not_confirmed", "selection failure hidden")
}
test("B84 clipboard conflict preserves concurrent user copy and rejects the receipt") {
    let (f, _, env, reader) = clipboardFixture(); env.conflictOnRestore = true
    check(reader.read(.prefix(tail: 0), validationStage: "postwrite") == nil, "restore conflict accepted")
    check(env.text() == "用户的新复制" && f.proxy.view.selectedRange() == NSRange(location: 2, length: 0), "new user copy overwritten")
    check(reader.diagnostics["clipboard_cleanup_failure"] as? String == "pasteboard_restore_conflict", "cleanup conflict hidden")
}
test("B84 caret restore failure rejects text and still restores the clipboard") {
    let (_, _, env, reader) = clipboardFixture(); env.restoreCaret = false
    let saved = env.contents
    check(reader.read(.prefix(tail: 0), validationStage: "undo") == nil && env.contents == saved, "caret failure returned text or lost pasteboard")
    check(reader.diagnostics["clipboard_failure"] as? String == "caret_restore_timeout", "caret restore failure hidden")
    check(env.commands.filter { $0 == .collapseEnd }.count == 1, "uncertain restore key was resent")
}
test("B84 copying existing selection never collapses or recreates it") {
    let (f, _, env, reader) = clipboardFixture(); f.proxy.view.setSelectedRange(NSRange(location: 0, length: 2))
    let saved = env.contents
    let body = reader.read(.selected, validationStage: "edit_prewrite")
    check(body?.text == "前文" && body?.source == .clipboard && env.commands == [.copy], "selected read moved selection")
    check(f.proxy.view.selectedRange() == NSRange(location: 0, length: 2) && env.contents == saved, "selected read lost state")
}
test("B84 before and prefix-tail scopes use copied UTF16 rather than arrow counts") {
    for scope in [EditorReadScope.before(2), .prefix(tail: 1), .after(1)] {
        let (f, _, env, reader) = clipboardFixture(text: "甲😀乙后文", caret: 3)
        let saved = env.contents
        let result = reader.read(scope, validationStage: "undo_postwrite")
        let expected = scope == .before(2) ? "😀" : (scope == .after(1) ? "乙" : "甲😀乙")
        check(result?.text == expected && result?.readScope == scope, "semantic copied span is wrong: \(String(describing: result))")
        check(env.contents == saved && f.proxy.view.selectedRange() == NSRange(location: 3, length: 0), "multi-copy failed restoration")
    }
}
test("B84 backup failure and live composition never send selection or copy keys") {
    for marked in [false, true] {
        let (f, _, env, reader) = clipboardFixture()
        if marked { f.adapter.mark("正在说话") } else { env.backupFails = true }
        check(reader.read(.prefix(tail: 0), validationStage: "context") == nil && env.commands.isEmpty, "unsafe clipboard preflight sent keys")
    }
}
test("B84 cancellation never restores a caret into a changed target") {
    let (_, _, env, reader) = clipboardFixture()
    let saved = env.contents
    env.onPost = { key in if key == .copy { env.valid = false } }
    check(reader.read(.prefix(tail: 0), validationStage: "context") == nil, "target change returned body")
    check(!env.commands.contains(.collapseEnd), "caret restored after target changed")
    // A completed copy is still observed and restored even if the session changed.
    check(reader.diagnostics["clipboard_result"] as? String == "failed", "target failure missing")
    _ = saved
}
test("B84 postwrite clipboard baseline survives suspension resume undo redo and repeat undo") {
    let (f, d, env, _) = clipboardFixture(); f.proxy.unavailableDocumentRead = true
    let savedClipboard = env.contents
    let initial = try prefixEdit(f)
    check(initial.phase == .edited && initial.undoRecord()?.editBody?.source == .clipboard, "clipboard edit failed: \(initial.error)")
    f.proxy.unavailableDocumentRead = false; f.proxy.staleDocumentRead = true; d.textEnabled = true
    let saved = try initial.suspension()
    var s = try CompositionSession(client: f.adapter, resuming: saved, sequence: 100, clock: { f.now })
    let history = CompositionUndoHistory(); history.configure(enabled: true); history.settled(s)
    s.onSettled = { _, _ in history.settled(s) }
    f.proxy.onInsert = { f.proxy.unavailableDocumentRead = true; d.textEnabled = false }
    history.undo(s); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文", "clipboard undo failed: \(s.error)")
    guard let redo = history.prepareRedo(s) else { fatalError("clipboard redo unavailable") }
    check(redo.before.source == .clipboard && redo.result.editBody?.source == .clipboard, "redo lost clipboard source: before=\(redo.before.source), result=\(String(describing: redo.result.editBody?.source)), restored=\(String(describing: s.undoRecord()?.editBody?.source))")
    s = CompositionSession(client: f.adapter, undo: redo.result, utteranceID: "b84-redo", sequence: 1, revision: s.revision + 1, clock: { f.now })
    s.onSettled = { _, _ in history.settled(s) }
    s.restoreRedo(redo); f.drain(s)
    check(s.phase == .edited && f.proxy.view.string == "新文后文", "clipboard redo failed: \(s.error)")
    history.undo(s); f.drain(s)
    check(s.phase == .dictated && f.proxy.view.string == "前文改写后文" && env.contents == savedClipboard, "repeat undo or pasteboard restore failed: \(s.error)")
}
test("B84 clipboard history retry keeps original source after history restore") {
    let (f, _, _, _) = clipboardFixture(); f.proxy.unavailableDocumentRead = true
    let s = try prefixEdit(f); s.cancel(); f.drain(s)
    check(s.phase == .dictated && s.canRetryEdit, "clipboard retry fixture failed: \(s.error)")
    guard let record = s.undoRecord() else { fatalError("clipboard undo record missing") }
    check(record.editBody?.source == .clipboard, "history capture dropped source")
    let restored = CompositionSession(client: f.adapter, undo: record, utteranceID: "b84-retry", sequence: 1, revision: s.revision + 1, clock: { f.now })
    var requests = 0
    restored.onEdit = { _, _, _ in requests += 1 }
    restored.retryEdit(); f.drain(restored)
    check(restored.phase == .editing && requests == 1, "clipboard retry failed: \(restored.error)")
    restored.applyEdit(text: "再次", revision: restored.revision, error: nil); f.drain(restored)
    check(restored.phase == .edited && f.proxy.view.string == "再次后文", "clipboard retried write failed: \(restored.error)")
}


test("B84 native pasteboard restores every item and type without touching the general pasteboard") {
    let pasteboard = NSPasteboard(name: NSPasteboard.Name("Mythlink.B84.Tests." + UUID().uuidString))
    defer { pasteboard.releaseGlobally() }
    let env = NativeClipboardReadEnvironment(pid: 0, bundle: "", pasteboard: pasteboard,
        current: { true }, selection: { NSRange(location: 0, length: 0) }, marked: { unspecifiedRange })
    let first = NSPasteboardItem(), second = NSPasteboardItem()
    first.setString("原始文字😀", forType: .string)
    first.setData(Data([1, 4, 9]), forType: NSPasteboard.PasteboardType("test.rich"))
    second.setData(Data([2, 8, 16]), forType: NSPasteboard.PasteboardType("test.file"))
    pasteboard.clearContents(); check(pasteboard.writeObjects([first, second]), "fixture pasteboard write failed")
    let saved = try env.backup()
    pasteboard.clearContents(); pasteboard.setString("临时复制", forType: .string)
    try env.restore(saved, replacing: env.changeCount)
    let restored = try env.backup()
    check(restored == saved, "native multi-item rich pasteboard was not restored")
    let oldCount = env.changeCount
    pasteboard.clearContents(); pasteboard.setString("其他程序的新复制", forType: .string)
    var failure = ""
    do { try env.restore(saved, replacing: oldCount) } catch { failure = (error as? ClipboardReadError)?.code ?? "unknown" }
    check(failure == "pasteboard_restore_conflict" && env.text() == "其他程序的新复制", "native restore overwrote a new clipboard owner")
}


test("B84 nested clipboard read cannot reset the transaction and late own keys are consumed") {
    let (_, _, env, reader) = clipboardFixture()
    var called = false
    env.onPost = { key in
        if key == .copy, !called {
            called = true
            check(reader.handling(tag: env.lastTag) == false, "active own key was not passed through")
            check(reader.read(.prefix(tail: 0), validationStage: "nested") == nil && reader.active, "nested copy replaced active read")
        }
    }
    check(reader.read(.prefix(tail: 0), validationStage: "original")?.text == "前文", "outer read failed after nested attempt")
    check(reader.diagnostics["validation_stage"] as? String == "original", "nested read overwrote diagnostics")
    check(reader.handling(tag: env.lastTag) == true && reader.handling(tag: 0) == nil, "late own key not consumed or normal key swallowed")
}

test("B84 passive stroke context never selects or copies") {
    let (f, _, env, _) = clipboardFixture(); f.proxy.unavailableDocumentRead = true
    check(f.adapter.strokeContext() == nil && env.commands.isEmpty, "stroke hint used disruptive copy fallback")
}


test("B84 nonempty selection copied as empty text is unavailable not an empty baseline") {
    let (_, _, env, reader) = clipboardFixture(); env.copiedOverride = ""
    let saved = env.contents
    check(reader.read(.prefix(tail: 0), validationStage: "postwrite") == nil, "empty copy became a baseline")
    check(reader.diagnostics["clipboard_failure"] as? String == "copy_text_empty" && env.contents == saved, "empty copy error or cleanup missing")
}


test("B84 clipboard text length remains independent of IMK selection coordinates during replacement") {
    let (f, d, env, _) = clipboardFixture()
    f.proxy.unavailableDocumentRead = true
    d.attempt = .unavailable("ax_selection_unavailable"); d.readUnavailable = true
    f.proxy.selectionReadOverride = {
        let native = f.proxy.view.selectedRange()
        if native.length > 0 { return NSRange(location: native.location, length: native.length + 3) }
        return NSRange(location: native.location + 3, length: 0)
    }
    let saved = env.contents
    var baseline = f.adapter.readBody(in: NSRange(location: 0, length: 5), fallback: .prefix(tail: 0))!
    check(baseline.text == "前文" && baseline.range.length == 2, "copied body inherited IMK length")
    for target in ["新文", "前文"] {
        f.adapter.replaceEditingPrefix(baseline, with: target, tail: 0, restoringCaret: nil, stage: "clipboard_coordinate_test", preservedSource: .clipboard)
        for _ in 0..<60 {
            f.now += 0.06; f.adapter.advanceClientOperation()
            if !f.adapter.clientOperationPending { break }
        }
        check(f.adapter.clientOperationFailure == nil, "copied source compared with IMK length: \(f.adapter.clientOperationFailure ?? "")")
        guard let receipt = f.adapter.verifiedReplacement else { fatalError("clipboard receipt missing") }
        check(receipt.source == .clipboard && receipt.text == target && receipt.range.length == 2, "written receipt changed source or geometry")
        check(f.proxy.view.string == target + "后文" && f.proxy.view.selectedRange() == NSRange(location: 2, length: 0) && env.contents == saved, "copied replacement changed suffix or caret")
        baseline = receipt
    }
}
