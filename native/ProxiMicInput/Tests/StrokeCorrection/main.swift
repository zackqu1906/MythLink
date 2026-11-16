import Foundation

final class Editor: CompositionClient {
    var text: String
    var caret: NSRange
    var writes = 0
    var ignoresWrites = false
    var unreadable = false
    var onRead: (() -> Void)?
    init(_ text: String, caret: Int? = nil) {
        self.text = text; self.caret = NSRange(location: caret ?? text.utf16.count, length: 0)
    }
    func snapshot() throws -> EditorSnapshot {
        EditorSnapshot(text: text, selection: caret, markedRange: unspecifiedRange,
                       selectedText: "", documentAccess: true)
    }
    func probe() -> CompositionProbe { CompositionProbe(selection: caret, markedRange: unspecifiedRange, markedText: nil) }
    func selection() -> NSRange { caret }
    func mark(_ text: String) {}
    func commit(_ text: String) { replace(caret, with: text) }
    func replace(_ range: NSRange, with text: String) {
        writes += 1
        guard !ignoresWrites else { return }
        self.text = (self.text as NSString).replacingCharacters(in: range, with: text)
        caret = NSRange(location: range.location + text.utf16.count, length: 0)
    }
    func readText(in range: NSRange) -> String? {
        let f = onRead; onRead = nil; f?()
        guard !unreadable, isValidRange(range, length: text.utf16.count) else { return nil }
        return (text as NSString).substring(with: range)
    }
}

func check(_ value: @autoclosure () -> Bool, _ message: String) {
    if !value() { fputs("FAIL: \(message)\n", stderr); exit(1) }
}
func append(_ text: String, word: String? = nil, editor: Editor, correction: StrokeTextCorrection) {
    let range = editor.caret
    editor.commit(text)
    correction.recordCommit(text, word: word ?? text, replacing: range)
}
func make(_ original: String = "明天去青华大学开会。") -> (Editor, StrokeTextCorrection) {
    let editor = Editor(original)
    return (editor, StrokeTextCorrection(preceding: EditorTextRange(text: original,
        range: NSRange(location: 0, length: original.utf16.count)), client: editor, owns: { true }))
}

do {
    let text = "已有🙂。明天去青华大学。", suffix = "后文不参与匹配"
    let editor = Editor(text + suffix, caret: text.utf16.count)
    let snapshot = try editor.snapshot()
    check(StrokeTextCorrection.precedingText(in: snapshot) == EditorTextRange(text: text,
        range: NSRange(location: 0, length: text.utf16.count)), "existing text before the caret was not captured")
    let bounded = EditorSnapshot(text: nil, selection: editor.caret, markedRange: unspecifiedRange,
        selectedText: "", documentAccess: false, readableContext: EditorTextRange(text: "明天去青华大学。" + suffix,
            range: NSRange(location: "已有🙂。".utf16.count, length: ("明天去青华大学。" + suffix).utf16.count)))
    check(StrokeTextCorrection.precedingText(in: bounded)?.text == "明天去青华大学。",
          "bounded context included text after the handwriting anchor")
    let long = Editor(String(repeating: "甲", count: 20000) + "🙂青华")
    let source = StrokeTextCorrection.precedingText(in: try long.snapshot())!
    check(source.text.count == 16384 && NSMaxRange(source.range) == long.caret.location
        && long.readText(in: source.range) == source.text, "bounded source lost UTF-16 alignment")
}
for unreadable in [false, true] {
    let editor = Editor(unreadable ? "已有前文" : "")
    let snapshot = EditorSnapshot(text: nil, selection: editor.caret, markedRange: unspecifiedRange,
                                  selectedText: "", documentAccess: false)
    let correction = StrokeTextCorrection(preceding: StrokeTextCorrection.precedingText(in: snapshot)!,
                                          client: editor, owns: { true })
    append("清华", editor: editor, correction: correction)
    correction.onRequest = { _, _, _ in check(false, "empty/unreadable preceding text reached the model") }
    correction.request("empty-context")
    check(correction.word == "清华" && correction.handwritingRange.length == 2 && !correction.busy,
          "unavailable preceding text hid the confirmed handwriting scope")
    check(correction.message == (unreadable ? "无法读取光标前的文本" : "光标前没有可修正的文本"),
          "missing context was not explained")
}
do {
    let (editor, correction) = make("明天去清华大学。")
    append("清华", editor: editor, correction: correction)
    correction.request("identical"); correction.apply(id: "identical", fragment: "清华", error: "")
    check(editor.text == "明天去清华大学。" && correction.canUndo,
          "identical matching text left duplicate handwriting at the caret")
    correction.undoCorrection()
    check(editor.text == "明天去清华大学。清华", "identical correction could not be undone")
}

do {
    let prefix = "🙂已有前文。", original = "明天去青华大学开会。", suffix = "保留后文"
    let editor = Editor(prefix + original + suffix, caret: (prefix + original).utf16.count)
    let correction = StrokeTextCorrection(preceding: EditorTextRange(text: original,
        range: NSRange(location: prefix.utf16.count, length: original.utf16.count)), client: editor, owns: { true })
    append("清 ", word: "清", editor: editor, correction: correction)
    append("华", editor: editor, correction: correction)
    check(correction.word == "清华" && correction.inserted == "清 华", "automatic spaces polluted correction word")
    let before = editor.text, writes = editor.writes
    correction.onRequest = { id, original, word in
        check(id == "one" && original == "明天去青华大学开会。" && word == "清华", "wrong model scope")
    }
    correction.request("one")
    correction.onRequest = nil
    correction.apply(id: "stale", fragment: "青华", error: "")
    check(editor.writes == writes && correction.busy, "stale request wrote text")
    correction.apply(id: "one", fragment: "青华", error: "")
    check(editor.text == prefix + "明天去清华大学开会。" + suffix && editor.writes == writes + 1,
          "correction must preserve outside text and remove temporary handwriting in one write")
    check(correction.canUndo && correction.word.isEmpty && !correction.busy, "missing confirmed undo")
    correction.undoCorrection()
    check(editor.text == before && correction.word == "清华" && !correction.canUndo, "undo did not restore exact pre-correction state")
    correction.request("two"); correction.apply(id: "two", fragment: "青华", error: "")
    append("讨论", editor: editor, correction: correction)
    correction.request("three"); correction.apply(id: "three", fragment: "开会", error: "")
    check(editor.text == prefix + "明天去清华大学讨论。" + suffix, "second correction lost updated preceding range")
}
do {
    let (editor, correction) = make("明天去清大。")
    append("清华大学", editor: editor, correction: correction)
    correction.request("long"); correction.apply(id: "long", fragment: "清大", error: "")
    check(editor.text == "明天去清华大学。" && editor.caret.location == editor.text.utf16.count, "different-length correction failed")
}
for mode in ["repeated", "missing", "same", "error", "caret", "changed", "unreadable"] {
    let original = mode == "repeated" ? "青华，青华。" : "明天去青华大学开会。"
    let (editor, correction) = make(original)
    append("清华", editor: editor, correction: correction)
    correction.request(mode)
    if mode == "caret" { editor.caret.location -= 1 }
    if mode == "changed" { editor.text = editor.text.replacingOccurrences(of: "明天", with: "后天") }
    if mode == "unreadable" { editor.unreadable = true }
    let before = editor.text, writes = editor.writes
    correction.apply(id: mode, fragment: mode == "missing" ? "北大" : mode == "same" ? "清华" : "青华",
                     error: mode == "error" ? "没有可靠对应" : "")
    check(editor.text == before && editor.writes == writes, "\(mode) changed the editor")
}
do {
    let (editor, correction) = make()
    append("清 ", word: "清", editor: editor, correction: correction)
    correction.backspace(); editor.text.removeLast(); editor.caret.location -= 1
    check(correction.word == "清" && correction.inserted == "清", "backspace removed letter with automatic space")
    correction.backspace(); editor.text.removeLast(); editor.caret.location -= 1
    check(correction.word.isEmpty && correction.valid, "backspace did not shrink handwriting scope")
    correction.backspace()
    check(!correction.valid, "deleting preceding text must invalidate the anchor")
}
do {
    var time = 0.0
    let editor = Editor("青华")
    let correction = StrokeTextCorrection(preceding: EditorTextRange(text: "青华", range: NSRange(location: 0, length: 2)),
        client: editor, owns: { true }, now: { time })
    append("清华", editor: editor, correction: correction)
    correction.request("timeout"); time = 31; correction.advance()
    correction.apply(id: "timeout", fragment: "青华", error: "")
    check(editor.text == "青华清华" && correction.word == "清华" && !correction.busy, "late model reply was applied")
    correction.request("ignored"); editor.ignoresWrites = true
    correction.apply(id: "ignored", fragment: "青华", error: "")
    time += 5; correction.advance()
    correction.request("again"); correction.apply(id: "again", fragment: "青华", error: "")
    check(editor.writes == 2 && !correction.valid, "unconfirmed delivery retried a destructive write")
}
do {
    let (editor, correction) = make()
    append("清华", editor: editor, correction: correction)
    correction.request("reentry")
    editor.onRead = { correction.invalidate("已切换输入框") }
    let writes = editor.writes
    correction.apply(id: "reentry", fragment: "青华", error: "")
    check(editor.writes == writes, "reentrant focus loss wrote through old ownership")
}
print("Stroke correction: scope, matching, one-write replacement, undo, reentry and timeout passed")
