import Foundation

final class Editor: CompositionClient {
    var body: String
    var selected: NSRange
    var marked = unspecifiedRange
    var reads = 0
    var writes = 0
    var fullDocument = true
    var acceptWrites = true
    var onProbe: (() -> Void)?
    init(_ text: String = "") { body = text; selected = NSRange(location: (text as NSString).length, length: 0) }
    func snapshot() throws -> EditorSnapshot {
        reads += 1
        return EditorSnapshot(text: fullDocument ? body : nil, selection: selected, markedRange: marked,
                              selectedText: (body as NSString).substring(with: selected), documentAccess: true)
    }
    func readText(in range: NSRange) -> String? {
        reads += 1
        return isValidRange(range, length: (body as NSString).length) ? (body as NSString).substring(with: range) : nil
    }
    func selection() -> NSRange { selected }
    func probe() -> CompositionProbe {
        reads += 1
        onProbe?()
        return CompositionProbe(selection: selected, markedRange: marked,
                                markedText: isValidRange(marked) ? (body as NSString).substring(with: marked) : nil)
    }
    func replace(_ range: NSRange, with text: String) {
        writes += 1
        guard acceptWrites else { return }
        body = (body as NSString).replacingCharacters(in: range, with: text)
        selected = NSRange(location: range.location + (text as NSString).length, length: 0)
        marked = unspecifiedRange
    }
    func mark(_ text: String) {
        let range = isValidRange(marked) ? marked : selected
        replace(range, with: text)
        marked = NSRange(location: range.location, length: (text as NSString).length)
    }
    func commit(_ text: String) { replace(isValidRange(marked) ? marked : selected, with: text) }
    func replaceEditing(_ range: NSRange, with text: String, restoringCaret: Int?) {
        replace(range, with: text)
        if acceptWrites, let restoringCaret { selected = NSRange(location: restoringCaret, length: 0) }
    }
}

final class Fixture {
    let editor: Editor
    let history: CompositionUndoHistory
    var session: CompositionSession!
    var source: String?
    init(_ text: String = "", enabled: Bool = true) {
        editor = Editor(text)
        history = CompositionUndoHistory()
        history.configure(enabled: enabled)
    }
    func begin() throws {
        let next = try CompositionSession(client: editor, utteranceID: UUID().uuidString, sequence: 1)
        history.begin(next.original)
        source = nil
        session = next
        wire()
    }
    func wire() {
        session.onSettled = { [unowned self] _, _ in history.settled(session, sourceUtteranceID: source) }
        session.onInterrupted = { [unowned self] _ in history.clear() }
    }
    func say(_ text: String) throws { try begin(); session.update(text, final: true) }
    func edit(_ instruction: String, to result: String) throws {
        try begin(); session.update(instruction, final: false); session.convert()
        session.update(instruction, final: true)
        session.applyEdit(text: result, revision: session.revision, error: nil)
    }
    func undo() {
        if session.phase == .undone, let record = history.pop() {
            source = record.sourceUtteranceID
            session = CompositionSession(client: editor, undo: record, utteranceID: session.utteranceID,
                                         sequence: session.lastSequence, revision: session.revision + 1)
            wire()
        }
        history.undo(session, sourceUtteranceID: source)
    }
    func redo() {
        guard let step = history.prepareRedo(session, sourceUtteranceID: source) else { return }
        source = step.result.sourceUtteranceID
        session = CompositionSession(client: editor, undo: step.result, utteranceID: session.utteranceID,
                                     sequence: session.lastSequence, revision: session.revision + 1)
        wire()
        session.restoreRedo(step)
    }
    func roundTrip() throws {
        let writes = editor.writes
        let saved = try session.suspension()
        let stack = history.snapshot()
        history.clear()
        session = try CompositionSession(client: editor, resuming: saved, sequence: saved.lastSequence + 1)
        history.restore(stack)
        wire()
        check(editor.writes == writes, "suspend/resume modified text")
    }
}

var passed = 0
func check(_ value: @autoclosure () -> Bool, _ message: String) { if !value() { fatalError(message) } }
func test(_ name: String, _ block: () throws -> Void) throws { try block(); passed += 1; print("PASS \(name)") }

try test("Three dictations undo in LIFO order including Unicode and line breaks") {
    let f = Fixture("原文")
    for text in ["一😀", "二\n", "👨‍👩‍👧‍👦三"] { try f.say(text) }
    for expected in ["原文一😀二\n", "原文一😀", "原文"] { f.undo(); check(f.editor.body == expected, "wrong undo order") }
    let writes = f.editor.writes
    f.undo(); check(f.editor.writes == writes && f.history.count == 0, "empty history wrote")
}
try test("Input-source round trips preserve polish restore and multi-sentence undo") {
    let f = Fixture("原文")
    try f.say("甲"); try f.say("乙")
    try f.roundTrip()
    check(f.session.canPolish, "resumed dictation lost polishing")
    f.session.polish(restoringRawOnUndo: true)
    f.session.applyPolish(text: "润色乙", revision: f.session.revision, error: nil)
    try f.roundTrip()
    check(f.session.polished && f.session.canRestorePolish, "polish restoration disappeared")
    f.undo()
    check(f.editor.body == "原文甲乙" && !f.session.polished, "first undo did not restore dictation")
    try f.roundTrip()
    f.undo()
    check(f.editor.body == "原文甲" && f.session.phase == .undone, "second undo did not remove the sentence")
    try f.roundTrip()
    f.undo()
    check(f.editor.body == "原文", "older undo did not survive the source round trip")
}
try test("Edited input resumes with two-stage restore then undo semantics") {
    let f = Fixture("原文")
    try f.edit("改成乙", to: "结果乙")
    let revision = f.session.revision
    try f.roundTrip()
    check(f.session.phase == .edited && f.session.revision == revision, "resume changed the edited transaction")
    f.undo()
    check(f.editor.body == "原文改成乙", "edited resume lost original dictation")
    try f.roundTrip()
    f.undo()
    check(f.editor.body == "原文", "restored dictation could not be undone")
}
try test("Input-source round trips retain automatic routing status and manual routed-edit mode") {
    let f = Fixture()
    try f.begin()
    f.session.automaticMode = true
    f.session.setAutomaticProgress("generating", tasks: ["compose"])
    f.session.setAutomaticTask("edit")
    f.session.update("写个问候", final: true)
    f.session.applyEdit(text: "你好", revision: f.session.revision, error: nil)
    try f.roundTrip()
    check(f.session.phase == .edited && f.session.automaticMode && !f.session.routedEditMode &&
          f.session.automaticTask == "edit" && f.session.automaticStage == "generating" &&
          f.session.automaticTasks == ["compose"], "resume lost automatic routing state")

    try f.begin()
    f.session.routedEditMode = true
    f.session.update("下午好", final: true)
    try f.roundTrip()
    check(f.session.routedEditMode && !f.session.automaticMode && f.session.canPolish,
          "resume lost manual routed-edit controls")
}
try test("Suspended snapshots reject changed text, caret, composition and replay without writes") {
    for scenario in ["text", "caret", "mark", "sequence"] {
        let f = Fixture("原文")
        try f.say("甲")
        let saved = try f.session.suspension()
        if scenario == "text" { f.editor.body = "原文乙" }
        if scenario == "caret" { f.editor.selected = NSRange(location: 0, length: 0) }
        if scenario == "mark" { f.editor.marked = NSRange(location: 2, length: 1) }
        let writes = f.editor.writes
        do {
            _ = try CompositionSession(client: f.editor, resuming: saved,
                sequence: saved.lastSequence + (scenario == "sequence" ? 0 : 1))
            fatalError("resume accepted \(scenario)")
        } catch {}
        check(f.editor.writes == writes, "failed resume wrote into another owner")
    }
}
try test("Partial document uses exact sentence proof and keeps polish/undo") {
    let f = Fixture("原文")
    f.editor.fullDocument = false
    try f.say("甲")
    try f.roundTrip()
    check(f.session.canPolish, "readable partial sentence lost polish")
    let saved = try f.session.suspension()
    f.editor.body = "原文乙"
    let writes = f.editor.writes
    do {
        _ = try CompositionSession(client: f.editor, resuming: saved, sequence: saved.lastSequence + 1)
        fatalError("partial resume accepted changed sentence")
    } catch {}
    check(f.editor.writes == writes, "partial resume wrote before verification")
}
try test("Busy input cannot be suspended") {
    let f = Fixture()
    try f.begin()
    do { _ = try f.session.suspension(); fatalError("suspended live ASR") } catch {}
    f.session.update("甲", final: true)
    f.session.polish(restoringRawOnUndo: true)
    do { _ = try f.session.suspension(); fatalError("suspended pending polish") } catch {}
}
try test("Restoration binds only the newly verified client") {
    let f = Fixture("原文")
    try f.say("甲")
    let saved = try f.session.suspension()
    let next = Editor(f.editor.body)
    let resumed = try CompositionSession(client: next, resuming: saved, sequence: saved.lastSequence + 1)
    f.editor.body = "旧客户端已被接管"
    let oldReads = f.editor.reads, oldWrites = f.editor.writes
    resumed.cancel()
    check(next.body == "原文", "resumed undo did not target the fresh client")
    check(f.editor.reads == oldReads && f.editor.writes == oldWrites, "resumed transaction called the retired client")
}
try test("Mixed edit restores dictation, then reaches previous dictation and edit") {
    let f = Fixture("原文")
    try f.say("甲")
    try f.edit("改成乙", to: "原文乙")
    try f.say("丙")
    try f.edit("改成丁", to: "原文丁")
    for expected in ["原文乙丙改成丁", "原文乙丙", "原文乙", "原文甲改成乙", "原文甲", "原文"] {
        f.undo(); check(f.editor.body == expected, "mixed undo failed: \(f.editor.body) expected \(expected)")
    }
}
try test("Retired disabled setting cannot turn off multi-sentence history") {
    let f = Fixture(enabled: false)
    try f.say("甲"); try f.edit("改成乙", to: "乙")
    f.undo(); check(f.editor.body == "甲改成乙", "edit restore regressed")
    f.undo(); f.undo(); check(f.editor.body.isEmpty && f.history.count == 0, "legacy setting disabled history")
}
try test("Legacy configuration never clears retained undo or redo") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙")
    f.undo()
    f.history.configure(enabled: false); f.history.configure(enabled: true)
    f.redo(); check(f.editor.body == "甲乙", "legacy configure cleared redo")
    f.undo(); f.undo(); check(f.editor.body.isEmpty, "legacy configure cleared undo")
}
try test("Disabling redo preserves undo and reenabling cannot resurrect discarded future") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙"); f.undo()
    check(f.history.redoCount == 1 && f.history.count == 1, "missing history setup")
    f.history.configureRedo(enabled: false)
    check(f.history.redoCount == 0 && f.history.count == 1, "toggle damaged undo history")
    f.redo(); check(f.editor.body == "甲", "disabled redo wrote")
    f.undo(); check(f.editor.body.isEmpty && f.history.redoCount == 0, "undo-only mode retained redo")
    f.history.configureRedo(enabled: true)
    f.redo(); check(f.editor.body.isEmpty && !f.history.canRedo, "reenable resurrected future")
    try f.say("丙"); f.undo(); f.redo()
    check(f.editor.body == "丙", "new undos did not become redoable")
}
try test("Disabling during a pending redo lets its accepted write settle without losing undo") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙"); f.undo()
    f.editor.acceptWrites = false
    f.redo(); check(f.session.awaitingReadback, "expected an unconfirmed write")
    f.history.configureRedo(enabled: false)
    f.editor.body = "甲乙"; f.editor.selected = NSRange(location: 2, length: 0)
    f.editor.acceptWrites = true; f.session.advanceReadback()
    check(!f.session.awaitingReadback && f.history.redoCount == 0, "toggle lost pending redo receipt")
    f.undo(); f.undo()
    check(f.editor.body.isEmpty && f.history.redoCount == 0, "toggle broke past bookkeeping")
}
try test("A disabled host does not restore redo from a suspended snapshot") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙"); f.undo()
    let saved = f.history.snapshot()
    f.history.configureRedo(enabled: false); f.history.restore(saved)
    f.history.configureRedo(enabled: true)
    check(f.history.count == 1 && !f.history.canRedo, "suspension resurrected disabled future")
    f.history.restore(saved.withoutRedo())
    check(f.history.count == 1 && !f.history.canRedo, "discardRedo lost past or retained future")
}
try test("A reentrant ownership change during undo receipt cannot resurrect redo") {
    let f = Fixture()
    try f.say("甲")
    f.editor.onProbe = {
        if f.session.phase == .undone {
            f.editor.onProbe = nil
            f.history.clear()
        }
    }
    f.undo()
    check(f.editor.body.isEmpty && f.history.redoCount == 0, "stale receipt resurrected future")
}
try test("A caret change during undo receipt cannot become a new redo anchor") {
    let f = Fixture("原文")
    try f.say("甲")
    f.editor.onProbe = {
        if f.session.phase == .undone {
            f.editor.onProbe = nil
            f.editor.selected = NSRange(location: 0, length: 0)
        }
    }
    f.undo()
    check(f.editor.body == "原文" && !f.history.canRedo, "changed caret authorized a redo anchor")
}
try test("Mixed history redoes each undo in reverse, including after undoing everything") {
    let f = Fixture("原文")
    try f.say("甲😀"); try f.edit("改成乙", to: "原文乙"); try f.say("丙\n")
    let reversed = ["原文乙", "原文甲😀改成乙", "原文甲😀", "原文"]
    for expected in reversed { f.undo(); check(f.editor.body == expected, "mixed undo failed") }
    check(f.history.redoCount == 4 && f.history.count == 0, "redo disappeared at first step")
    try f.roundTrip()
    for expected in ["原文甲😀", "原文甲😀改成乙", "原文乙", "原文乙丙\n"] {
        f.redo(); check(f.editor.body == expected, "mixed redo failed: \(f.editor.body)")
        try f.roundTrip()
    }
    check(f.history.redoCount == 0, "redo was not consumed")
    let writes = f.editor.writes
    f.redo(); check(f.editor.writes == writes, "empty redo wrote")
    for expected in reversed { f.undo(); check(f.editor.body == expected, "replayed undo duplicated an entry") }
}
try test("Polish redo mirrors both current undo granularities without calling a model") {
    for restoreRaw in [true, false] {
        let f = Fixture("前文")
        try f.say("原句")
        f.session.polish(restoringRawOnUndo: restoreRaw)
        f.session.applyPolish(text: "润色结果😀", revision: f.session.revision, error: nil)
        f.undo()
        check(f.editor.body == (restoreRaw ? "前文原句" : "前文"), "polish undo granularity changed")
        if restoreRaw { f.undo(); f.redo(); check(f.editor.body == "前文原句", "raw redo failed") }
        f.redo()
        check(f.editor.body == "前文润色结果😀" && f.session.polished, "polish result not restored")
        f.session.applyPolish(text: "迟到结果", revision: f.session.revision - 1, error: nil)
        check(f.editor.body == "前文润色结果😀", "redo accepted a late model result")
    }
}
try test("Cancelled new recording retains future, successful new text discards it") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙"); f.undo()
    try f.begin(); f.session.update("临时口述", final: false); f.undo()
    check(f.editor.body == "甲" && f.history.redoCount == 1, "cancelled recording lost future")
    f.redo(); check(f.editor.body == "甲乙", "future after cancelled recording failed")
    f.undo(); try f.say("丙")
    check(f.history.redoCount == 0, "new branch kept old future")
    f.redo(); check(f.editor.body == "甲丙", "redo entered discarded branch")
    f.undo(); f.undo(); check(f.editor.body.isEmpty, "new branch corrupted past")
}
try test("Alternating directions restores exact text and caret without duplicate entries") {
    let f = Fixture("前后")
    f.editor.selected = NSRange(location: 1, length: 1)
    try f.say("😀")
    let caret = f.editor.selected
    for _ in 0..<4 {
        f.undo(); check(f.editor.body == "前后", "selected original not restored")
        f.redo(); check(f.editor.body == "前😀" && f.editor.selected == caret, "redo changed result or caret")
        check(f.history.count == 0, "alternation added a duplicate undo")
    }
}
try test("Cancelled recording after a partial edit undo does not duplicate its archived sentence") {
    let f = Fixture()
    try f.say("甲"); try f.edit("改成乙", to: "乙"); f.undo()
    try f.begin(); f.session.update("临时", final: false); f.undo()
    f.redo(); check(f.editor.body == "乙", "edit future lost across cancelled recording")
    f.undo(); f.undo(); f.undo()
    check(f.editor.body.isEmpty && f.history.count == 0, "same sentence was restored twice")
}
try test("Cancelled polish keeps future but a successful new polish discards it") {
    let f = Fixture()
    try f.say("甲")
    f.session.polish(restoringRawOnUndo: true)
    f.session.applyPolish(text: "旧润色", revision: f.session.revision, error: nil)
    f.undo()
    f.session.polish(restoringRawOnUndo: true); f.undo()
    check(f.history.redoCount == 1, "cancelled model request cleared future")
    f.session.polish(restoringRawOnUndo: true)
    f.session.applyPolish(text: "新润色", revision: f.session.revision, error: nil)
    check(f.history.redoCount == 0 && f.editor.body == "新润色", "successful new result kept old branch")
}
try test("Redo rejects changed text, caret or another composition before writing") {
    for scenario in ["text", "caret", "mark"] {
        let f = Fixture("原文")
        try f.edit("指令", to: "结果"); f.undo()
        if scenario == "text" { f.editor.body = "外文指令" }
        if scenario == "caret" { f.editor.selected = NSRange(location: 0, length: 0) }
        if scenario == "mark" { f.editor.marked = NSRange(location: 0, length: 1) }
        let text = f.editor.body, writes = f.editor.writes
        f.redo()
        check(f.editor.body == text && f.editor.writes == writes, "redo wrote across \(scenario)")
    }
}
try test("Failed or unconfirmed redo does not consume the future or issue repeat writes") {
    let f = Fixture()
    try f.say("甲"); f.undo()
    f.editor.acceptWrites = false
    f.redo()
    check(f.history.redoCount == 1 && f.session.awaitingReadback, "unconfirmed redo was consumed")
    let writes = f.editor.writes
    f.redo(); f.undo(); f.session.advanceReadback()
    check(f.editor.writes == writes && f.history.redoCount == 1, "busy history issued another write")
    f.session.interrupt("外部接管", commitIfOwned: false)
    check(f.history.redoCount == 0, "interruption retained redo")
}
try test("A new sentence cannot inherit an unconfirmed redo or its old future") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙"); try f.say("丙"); f.undo(); f.undo()
    f.editor.acceptWrites = false
    f.redo(); check(f.session.awaitingReadback, "expected pending redo")
    f.editor.acceptWrites = true
    try f.say("新句")
    check(f.editor.body == "甲新句" && f.history.redoCount == 0 && f.history.count == 0,
          "new sentence inherited unconfirmed history bookkeeping")
    f.undo(); check(f.editor.body == "甲", "new sentence lost its own undo")
}
try test("Empty/live cancelled sentence leaves earlier undo history reachable") {
    let f = Fixture()
    try f.say("甲"); try f.begin(); f.session.update("暂存", final: false)
    f.undo(); check(f.editor.body == "甲", "live cancel failed")
    f.undo(); check(f.editor.body.isEmpty, "previous sentence unreachable")
}
try test("New utterance after undo creates a new history branch") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙"); f.undo(); try f.say("丙")
    f.undo(); f.undo(); check(f.editor.body.isEmpty, "new branch retained removed operation")
}
try test("Same-length external text change is rejected before deletion") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙"); f.undo()
    f.editor.body = "X"
    let writes = f.editor.writes
    f.undo(); check(f.editor.body == "X" && f.editor.writes == writes, "foreign text deleted")
}
try test("Moved caret invalidates the stack at the next BEGIN") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙")
    f.editor.selected = NSRange(location: 0, length: 0)
    try f.say("丙"); f.undo(); f.undo()
    check(f.editor.body == "甲乙" && f.history.count == 0, "history survived caret takeover")
}
try test("Undo reaches every sentence after more than 100 entries and an input-source switch") {
    let f = Fixture("原文")
    let sentences = (1...258).map { "第\($0)句 " }
    for sentence in sentences { try f.say(sentence) }
    try f.roundTrip()
    check(f.history.count == sentences.count - 1, "older sentences disappeared")
    for sentence in sentences.reversed() {
        check(f.editor.body.hasSuffix(sentence), "undo order changed")
        f.undo()
    }
    check(f.editor.body == "原文", "could not reach the oldest sentence")
}
try test("A record larger than the former UTF-16 budget keeps its preceding sentence undoable") {
    let f = Fixture("原文")
    let large = String(repeating: "x", count: 4 * 1024 * 1024 + 1)
    try f.say("前"); try f.say(large); try f.say("后")
    try f.roundTrip()
    f.undo(); check(f.editor.body == "原文前" + large, "newest sentence was not undone")
    f.undo(); check(f.editor.body == "原文前", "large record was evicted")
    f.undo(); check(f.editor.body == "原文", "large record lost earlier history")
}
try test("History capture/restore performs no additional client reads and stores compact dictation") {
    let f = Fixture(String(repeating: "长", count: 100_000))
    try f.say("甲"); let reads = f.editor.reads
    let record = f.session.undoRecord()!
    check(record.original.text == nil && record.original.readableContext == nil && record.retainedUnits == 1,
          "dictation retained a full document")
    f.history.settled(f.session)
    _ = CompositionSession(client: f.editor, undo: record, utteranceID: "restore", sequence: 1, revision: 7)
    check(f.editor.reads == reads, "history did extra IPC reads")
}
try test("Late model/ASR results cannot mutate a restored historical operation") {
    let f = Fixture()
    try f.say("甲"); try f.say("乙"); f.undo(); f.undo()
    let writes = f.editor.writes
    f.session.update("late", final: true)
    f.session.applyEdit(text: "late", revision: 1, error: nil)
    check(f.editor.writes == writes && f.editor.body.isEmpty, "late result wrote")
}
try test("Historical replacement restores selected text, not just an empty deletion") {
    let f = Fixture("原始选中")
    f.editor.selected = NSRange(location: 2, length: 2)
    try f.say("新的😀"); try f.say("后续")
    f.undo(); f.undo()
    check(f.editor.body == "原始选中", "original selection lost")
}
try test("Empty edit result remains undoable after a later sentence") {
    let f = Fixture("原文")
    try f.edit("清空", to: ""); try f.say("后一句")
    f.undo(); f.undo()
    check(f.editor.body == "原文清空", "empty edit was mistaken for no edit")
    f.undo(); check(f.editor.body == "原文", "instruction not removable")
}
try test("Restored edit retries the original source and instruction without writing before the result") {
    let f = Fixture("原文")
    try f.say("甲")
    try f.edit("扩写", to: "第一次结果")
    f.undo()
    check(f.session.canRetryEdit && f.editor.body == "原文甲扩写", "undo lost retry provenance")
    let writes = f.editor.writes
    var requests = 0
    f.session.onEdit = { source, instruction, _ in
        check(source == "原文甲" && instruction == "扩写", "retry changed original inputs")
        requests += 1
    }
    f.session.retryEdit(); f.session.retryEdit()
    check(requests == 1 && f.editor.writes == writes && !f.session.canRetryEdit, "busy retry duplicated request or wrote text")
    f.session.applyEdit(text: "第二次结果", revision: f.session.revision, error: nil)
    check(f.editor.body == "第二次结果" && !f.session.canRetryEdit && !f.history.canRedo, "retry did not create a fresh branch")
    f.undo(); check(f.editor.body == "原文甲扩写" && f.session.canRetryEdit, "new result lost original instruction")
    f.undo(); check(f.editor.body == "原文甲" && !f.session.canRetryEdit, "deleted instruction remains retryable")
    f.undo(); check(f.editor.body == "原文", "retry corrupted earlier history")
}
try test("Failed or cancelled retries retain instruction and reject late results") {
    for cancel in [false, true] {
        let f = Fixture("原文")
        try f.edit("修改", to: "旧结果"); f.undo()
        let writes = f.editor.writes
        f.session.retryEdit()
        let revision = f.session.revision
        if cancel { f.session.cancel() }
        else { f.session.applyEdit(text: "", revision: revision, error: "模型超时") }
        check(f.editor.writes == writes && f.editor.body == "原文修改" && f.session.canRetryEdit, "retry failure changed instruction")
        f.session.applyEdit(text: "过期结果", revision: revision, error: nil)
        check(f.editor.writes == writes, "late retry result wrote")
        f.session.retryEdit(); f.session.applyEdit(text: "新结果", revision: f.session.revision, error: nil)
        check(f.editor.body == "新结果", "retry after failure was blocked")
    }
}
try test("Older edit and source-return snapshots preserve retry identity") {
    let f = Fixture("原文")
    try f.edit("扩写", to: "结果")
    let source = f.session.utteranceID
    try f.say("后来一句"); f.undo(); f.undo()
    check(f.source == source && f.session.canRetryEdit, "older edit instruction lost provenance")
    try f.roundTrip()
    var requests = 0
    f.session.onEdit = { text, instruction, _ in
        check(text == "原文" && instruction == "扩写", "historical retry used later input")
        requests += 1
    }
    f.session.retryEdit(); f.session.applyEdit(text: "再次扩写", revision: f.session.revision, error: nil)
    check(requests == 1 && f.editor.body == "再次扩写", "resumed historical retry failed")
}
try test("Ordinary dictation and restored polish are not retryable edit instructions") {
    let f = Fixture("原文")
    try f.say("你好")
    check(!f.session.canRetryEdit, "dictation offered retry")
    f.session.polish(restoringRawOnUndo: true)
    f.session.applyPolish(text: "您好", revision: f.session.revision, error: nil)
    f.undo()
    check(!f.session.canRetryEdit, "polish restoration offered retry")
    let writes = f.editor.writes
    f.session.onEdit = { _, _, _ in fatalError("ordinary dictation requested edit") }
    f.session.retryEdit()
    check(f.editor.writes == writes, "unavailable retry wrote")
}
try test("Retry validates current text caret and composition before requesting a model") {
    for change in ["text", "caret", "mark"] {
        let f = Fixture("原文")
        try f.edit("修改", to: "结果"); f.undo()
        if change == "text" { f.editor.body = "别处修改" }
        if change == "caret" { f.editor.selected = NSRange(location: 0, length: 0) }
        if change == "mark" { f.editor.marked = NSRange(location: 2, length: 2) }
        let writes = f.editor.writes
        f.session.onEdit = { _, _, _ in fatalError("unsafe retry requested model") }
        f.session.retryEdit()
        check(f.editor.writes == writes && !f.session.canRetryEdit, "unsafe retry was not blocked")
    }
}
try test("Leaving next clears cached future while preserving the restored instruction") {
    let f = Fixture("原文")
    try f.edit("修改", to: "结果"); f.undo()
    check(f.history.canRedo && f.session.canRetryEdit, "instruction must support the selected forward mode")
    f.history.configureRedo(enabled: false)
    check(!f.history.canRedo && f.session.canRetryEdit, "mode switch lost restored instruction")
    f.history.configureRedo(enabled: true)
    check(!f.history.canRedo, "mode switch resurrected future")
}

print("\(passed) history tests passed")

if CommandLine.arguments.contains("--benchmark") {
    var rows: [[String: Any]] = []
    for size in [1_000, 100_000] {
        for enabled in [false, true] {
            let f = Fixture(String(repeating: "原", count: size), enabled: enabled)
            var times = ["begin": [Double](), "partial": [], "final": [], "undo_current": [], "undo_history": [], "edit_apply": []]
            func measure(_ key: String, _ work: () throws -> Void) rethrows {
                let t = ProcessInfo.processInfo.systemUptime
                try work()
                times[key]!.append((ProcessInfo.processInfo.systemUptime - t) * 1000)
            }
            for index in 0..<120 {
                try measure("begin") { try f.begin() }
                measure("partial") { f.session.update("第\(index)句😀", final: false) }
                measure("final") { f.session.update("第\(index)句话😀。", final: true) }
            }
            let reads = f.editor.reads
            let units = f.history.totalRetainedUnits
            measure("undo_current") { f.undo() }
            if enabled {
                for _ in 0..<f.history.count { measure("undo_history") { f.undo() } }
            }
            for index in 0..<10 {
                try f.begin(); f.session.update("改写", final: false); f.session.convert()
                f.session.update("改写", final: true)
                let result = String(repeating: "文", count: size) + String(index)
                measure("edit_apply") { f.session.applyEdit(text: result, revision: f.session.revision, error: nil) }
                check(f.session.phase == .edited, "benchmark edit failed")
            }
            var metrics: [String: Any] = [:]
            for (key, values) in times {
                guard !values.isEmpty else { continue }
                let sorted = values.sorted()
                metrics[key] = ["median_ms": sorted[sorted.count / 2], "p95_ms": sorted[min(sorted.count - 1, sorted.count * 95 / 100)]]
            }
            rows.append(["initial_characters": size, "enabled": enabled, "metrics": metrics,
                         "dictation_retained_utf16_units": units,
                         "after_ten_edits_retained_utf16_units": f.history.totalRetainedUnits,
                         "client_reads_before_undo": reads])
        }
    }
    let data = try JSONSerialization.data(withJSONObject: rows, options: [.prettyPrinted, .sortedKeys])
    print("BENCHMARK_JSON\n" + String(data: data, encoding: .utf8)!)
}
