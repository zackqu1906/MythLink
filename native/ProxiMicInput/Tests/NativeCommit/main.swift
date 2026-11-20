import AppKit

var passed = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else { fatalError(message) }
}
func test(_ name: String, _ body: () -> Void) {
    body()
    passed += 1
    print("PASS \(name)")
}

// NSTextView is never put in a window, activated, or connected to an external
// application's editor. These tests exercise real AppKit composition semantics.
func editor(_ text: String, selection: NSRange) -> NSTextView {
    let view = NSTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 100))
    view.string = text
    view.setSelectedRange(selection)
    return view
}
func compose(_ view: NSTextView, _ text: String) {
    view.setMarkedText(text, selectedRange: NSRange(location: (text as NSString).length, length: 0),
                       replacementRange: unspecifiedRange)
}
func commit(_ view: NSTextView, _ text: String, swallowEmptyInsert: Bool = false) -> (marks: Int, inserts: Int) {
    var marks = 0
    var inserts = 0
    CompositionCommitDelivery.send(text, markedRange: { view.markedRange() },
                                   clearMarkedText: { marks += 1; compose(view, "") },
                                   insertText: {
        inserts += 1
        if !($0.isEmpty && swallowEmptyInsert) {
            view.insertText($0, replacementRange: unspecifiedRange)
        }
    })
    return (marks, inserts)
}

test("Empty commit clears only owned preedit and preserves UTF-16 neighbours") {
    let view = editor("前😀尾巴", selection: NSRange(location: 3, length: 0))
    compose(view, "正在听写中")
    check(view.hasMarkedText(), "test did not establish a real AppKit composition")
    let calls = commit(view, "")
    check(view.string == "前😀尾巴", "empty commit removed neighbouring committed text")
    check(!view.hasMarkedText() && view.selectedRange() == NSRange(location: 3, length: 0),
          "empty commit left composition or moved the caret")
    check(calls == (1, 1), "empty commit must clear once and insert once")
}

test("Empty commit still clears preedit when empty insertion is swallowed") {
    let view = editor("前😀尾巴", selection: NSRange(location: 3, length: 0))
    compose(view, "正在听写中")
    _ = commit(view, "", swallowEmptyInsert: true)
    check(view.string == "前😀尾巴" && !view.hasMarkedText(),
          "empty-insert transport left a phantom composition")
    check(view.selectedRange() == NSRange(location: 3, length: 0), "cancel changed caret")
    compose(view, "下一句")
    _ = commit(view, "下一句")
    check(view.string == "前😀下一句尾巴" && !view.hasMarkedText(),
          "cancel prevented the next sentence from being committed")
}

test("Nonempty commit retains ordinary insertion without a preliminary clear") {
    let view = editor("前旧😀后", selection: NSRange(location: 1, length: 3))
    compose(view, "口述")
    let calls = commit(view, "旧😀")
    check(view.string == "前旧😀后" && !view.hasMarkedText(), "original selected text was not restored")
    check(calls == (0, 1), "nonempty restore must be a single insertion")
}

test("Already-cleared composition does not create another marked range") {
    let view = editor("前😀尾巴", selection: NSRange(location: 3, length: 0))
    let calls = commit(view, "")
    check(calls == (0, 1), "adapter created a mark without an existing composition")
    check(view.string == "前😀尾巴" && !view.hasMarkedText() && view.selectedRange().location == 3,
          "no-composition empty commit changed the document")
}

test("Invalid or zero-length marked ranges never trigger a clearing write") {
    let invalid = [unspecifiedRange, NSRange(location: 4, length: 0),
                   NSRange(location: NSNotFound, length: 5), NSRange(location: -1, length: 5),
                   NSRange(location: Int.max - 1, length: 8)]
    for range in invalid {
        var clears = 0
        var inserts = 0
        CompositionCommitDelivery.send("", markedRange: { range },
                                       clearMarkedText: { clears += 1 }, insertText: { _ in inserts += 1 })
        check(clears == 0 && inserts == 1, "invalid marked range caused an extra write: \(range)")
    }
}

class TextViewClient: CompositionClient {
    let view: NSTextView
    let complete: Bool
    init(_ view: NSTextView, complete: Bool = true) { self.view = view; self.complete = complete }
    func snapshot() throws -> EditorSnapshot {
        let range = view.selectedRange()
        return EditorSnapshot(text: complete ? view.string : nil, selection: range, markedRange: view.markedRange(),
                              selectedText: (view.string as NSString).substring(with: range), documentAccess: true)
    }
    func probe() -> CompositionProbe {
        let range = view.markedRange()
        return CompositionProbe(selection: view.selectedRange(), markedRange: range,
                                markedText: isValidRange(range) ? readText(in: range) : nil)
    }
    func selection() -> NSRange { view.selectedRange() }
    func readText(in range: NSRange) -> String? {
        if range.length == 0 { return "" }
        var actual = unspecifiedRange
        let value = view.attributedSubstring(forProposedRange: range, actualRange: &actual)?.string
        return actual == range ? value : nil
    }
    func mark(_ text: String) { compose(view, text) }
    func commit(_ text: String) {
        CompositionCommitDelivery.send(text, markedRange: { view.markedRange() },
                                       clearMarkedText: { compose(view, "") },
                                       insertText: { view.insertText($0, replacementRange: unspecifiedRange) })
    }
    func replace(_ range: NSRange, with text: String) { view.insertText(text, replacementRange: range) }
    func replaceEditing(_ range: NSRange, with text: String, restoringCaret: Int?) {
        replace(range, with: text)
        if let restoringCaret { view.setSelectedRange(NSRange(location: restoringCaret, length: 0)) }
    }
}

private final class UndoableEditor: NSTextView {
    private let history = UndoManager()
    override var undoManager: UndoManager? { history }
}

private final class SelectedTextViewClient: TextViewClient {
    override func replaceEditing(_ range: NSRange, with text: String, restoringCaret: Int?) {
        view.setSelectedRange(range)
        view.insertText(text, replacementRange: unspecifiedRange)
        if let restoringCaret { view.setSelectedRange(NSRange(location: restoringCaret, length: 0)) }
    }
}

test("AppKit native undo restores committed instruction and the next retry replaces it once") {
    let view = UndoableEditor(frame: NSRect(x: 0, y: 0, width: 400, height: 100))
    view.allowsUndo = true
    view.string = "明天三点"
    view.setSelectedRange(NSRange(location: 4, length: 0))
    view.undoManager!.removeAllActions()
    let session = try! CompositionSession(client: SelectedTextViewClient(view), utteranceID: "trial", sequence: 1)
    session.systemUndoTrial = true
    session.hostUndoTag = 91
    var requests = 0
    session.onEdit = { source, raw, _ in
        check(source == "明天三点" && raw == "改到四点" && !view.hasMarkedText(), "model received incomplete input")
        requests += 1
    }
    session.update("改到四点", final: false)
    session.convert()
    session.update("改到四点", final: true)
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.03))
    session.applyEdit(text: "明天四点", revision: session.revision, error: nil)
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.03))
    check(view.string == "明天四点" && view.undoManager!.canUndo, "edited text missing from native undo")
    session.systemUndo { ack in view.undoManager!.undo(); ack(nil) }
    check(view.string == "明天三点改到四点" && session.canRetryEdit && requests == 1, "unexpected native undo grouping")
    let returned = view.selectedRange()
    if returned.length == 0 {
        check(session.takeRestoredReplacementSelection() == nil, "moved an already collapsed edit caret")
    } else {
        check(session.takeRestoredReplacementSelection() == returned, "missed the restored edit selection")
        view.moveRight(nil)
    }
    check(view.selectedRange() == NSRange(location: 8, length: 0), "caret did not return to the instruction end")
    session.retryEdit()
    session.applyEdit(text: "明天下午四点", revision: session.revision, error: nil)
    check(requests == 2 && session.phase == .edited && view.string == "明天下午四点", "AppKit retry failed")
}

test("Real NSTextView retains instruction until model result and restores dictation on cancellation") {
    for apply in [false, true] {
        let view = editor("前旧😀尾巴", selection: NSRange(location: 1, length: 3))
        let client = TextViewClient(view)
        let session = try! CompositionSession(client: client, utteranceID: "native-edit", sequence: 1)
        var requests = 0
        session.onEdit = { original, instruction, _ in
            check(original == "前" && instruction == "修改指令", "model must receive only the existing text before the caret")
            check(view.string == "前修改指令尾巴" && view.hasMarkedText(), "instruction disappeared while waiting for model")
            requests += 1
        }
        session.update("修改", final: false)
        session.convert()
        session.update("修改指令", final: true)
        check(requests == 1, "real native composition did not reach model")
        let revision = session.revision
        if apply { session.applyEdit(text: "实际结果😀", revision: revision, error: nil) }
        session.cancel()
        check(view.string == "前修改指令尾巴" && session.phase == .dictated, "cancel did not restore dictated sentence")
        session.applyEdit(text: "迟到", revision: revision, error: nil)
        session.cancel()
        check(view.string == "前旧😀尾巴" && session.phase == .undone, "second cancel: body=\(view.string), phase=\(session.phase), error=\(session.error), stage=\(session.readbackStage), selected=\(view.selectedRange()), diagnostics=\(session.readbackDiagnostics)")
    }
}

test("Real NSTextView exact committed-range undo works without whole-document context") {
    let view = editor("前😀后", selection: NSRange(location: 3, length: 0))
    let client = TextViewClient(view, complete: false)
    let session = try! CompositionSession(client: client, utteranceID: "native-local-undo", sequence: 1)
    session.update("听写", final: true)
    check(session.canCancel && session.committedRangeVerified, "native range read did not enable undo")
    session.cancel()
    check(view.string == "前😀后" && session.phase == .undone && !view.hasMarkedText(), "local native undo failed")
}

for complete in [true, false] {
    for restore in [true, false] {
        test("Real NSTextView polishes only one sentence and honors undo: full=\(complete), restore=\(restore)") {
            let view = editor("前😀旧字后缀", selection: NSRange(location: 3, length: 2))
            let client = TextViewClient(view, complete: complete)
            let session = try! CompositionSession(client: client, utteranceID: "native-polish", sequence: 1)
            session.update("嗯明天见", final: true)
            session.polish(restoringRawOnUndo: restore)
            session.applyPolish(text: "明天见。", revision: session.revision, error: nil)
            check(view.string == "前😀明天见。后缀" && session.polished && !view.hasMarkedText(), "native polish failed: \(session.error)")
            session.cancel()
            if restore {
                check(view.string == "前😀嗯明天见后缀" && session.phase == .dictated, "native raw restoration failed")
                session.cancel()
            }
            check(view.string == "前😀旧字后缀" && session.phase == .undone, "native sentence undo failed")
        }
    }
}

for automatic in [true, false] {
    test("Background preview leaves real editor ready; accepting and undo are sentence-scoped: automatic=\(automatic)") {
        let view = editor("前😀后缀", selection: NSRange(location: 3, length: 0))
        let session = try! CompositionSession(client: TextViewClient(view), utteranceID: "preview", sequence: 1)
        session.automaticMode = automatic
        var commits = 0
        session.onDictationCommitted = { commits += 1 }
        session.update("下班以后", final: true)
        let revision = session.revision
        check(commits == 1 && session.phase == .dictated && view.string == "前😀下班以后后缀", "preview modified or blocked raw commit")
        check(session.beforeDictationCursor == "前😀", "selection suffix leaked into model context")
        session.acceptSuggestion("过期", originalText: "下班以后", revision: revision + 1)
        session.acceptSuggestion("过期", originalText: "上一句", revision: revision)
        check(view.string == "前😀下班以后后缀" && session.phase == .dictated, "accepted stale preview")
        session.acceptSuggestion("下班以后来拿。", originalText: "下班以后", revision: revision)
        check(view.string == "前😀下班以后来拿。后缀" && session.polished, "accept failed: \(session.error)")
        session.cancel()
        check(view.string == "前😀下班以后后缀" && !session.polished && commits == 1, "undo lost raw or restarted generation")
    }
}
test("Preview accept rechecks edited text with unchanged insertion point") {
    let view = editor("前", selection: NSRange(location: 1, length: 0))
    let session = try! CompositionSession(client: TextViewClient(view), utteranceID: "stale", sequence: 1)
    session.update("甲", final: true)
    view.string = "前乙"
    view.setSelectedRange(NSRange(location: 2, length: 0))
    session.acceptSuggestion("甲。", originalText: "甲", revision: session.revision)
    check(view.string == "前乙" && !session.polished, "stale preview wrote over changed text")
}

print("\(passed) native commit tests passed")
