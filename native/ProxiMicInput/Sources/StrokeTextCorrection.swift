import Foundation

/// The preceding text and contiguous, confirmed handwriting belong to one editor.
/// Model output identifies a fragment; native code owns every write range.
final class StrokeTextCorrection {
    struct Piece { var inserted: String; var word: String }
    private struct Undo { let preceding: EditorTextRange; let pieces: [Piece] }
    private struct Write {
        let result: EditorTextRange
        let preceding: EditorTextRange
        let pieces: [Piece]
        let undo: Undo?
        let message: String
    }
    private let client: CompositionClient
    private let owns: () -> Bool
    private let now: () -> TimeInterval
    private(set) var preceding: EditorTextRange
    private var pieces: [Piece] = []
    private var undo: Undo?
    private var write: Write?
    private var requestID: String?
    private var deadline = 0.0
    private(set) var valid = true
    private(set) var message = ""
    var onChanged: (() -> Void)?
    var onRequest: ((String, String, String) -> Void)?
    var inserted: String { pieces.map(\.inserted).joined() }
    var word: String { pieces.map(\.word).joined().trimmingCharacters(in: .whitespaces) }
    var busy: Bool { requestID != nil || write != nil }
    var canUndo: Bool { valid && undo != nil && !busy && pieces.isEmpty }
    var handwritingRange: NSRange { NSRange(location: NSMaxRange(preceding.range), length: inserted.utf16.count) }
    private var expected: EditorTextRange {
        EditorTextRange(text: preceding.text + inserted,
                       range: NSRange(location: preceding.range.location, length: preceding.range.length + inserted.utf16.count))
    }

    static func precedingText(in snapshot: EditorSnapshot) -> EditorTextRange? {
        let caret = snapshot.selection
        guard isValidRange(caret), caret.length == 0 else { return nil }
        let context = snapshot.text.map {
            EditorTextRange(text: $0, range: NSRange(location: 0, length: $0.utf16.count))
        } ?? snapshot.readableContext
        guard let context, isValidRange(context.range), context.text.utf16.count == context.range.length,
              context.range.location <= caret.location, NSMaxRange(context.range) >= caret.location else {
            // Keep the handwriting scope visible even when this editor cannot expose its preceding text.
            return EditorTextRange(text: "", range: caret)
        }
        let prefix = (context.text as NSString).substring(to: caret.location - context.range.location)
        let text = String(prefix.suffix(16384))
        return EditorTextRange(text: text, range: NSRange(location: caret.location - text.utf16.count,
                                                        length: text.utf16.count), source: context.source)
    }

    init(preceding: EditorTextRange, client: CompositionClient, owns: @escaping () -> Bool,
         now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.preceding = preceding; self.client = client; self.owns = owns; self.now = now
        valid = preceding.text.count <= 16384 && isValidRange(preceding.range)
            && preceding.range.length == preceding.text.utf16.count
        if !verify() { valid = false; message = "前文或光标已变化，请重新进入手写" }
    }

    private func verify() -> Bool {
        guard valid, owns(), !client.clientOperationPending else { return false }
        let expected = expected
        let probe = client.probe()
        guard valid, owns(), probe.selection == NSRange(location: NSMaxRange(expected.range), length: 0),
              !isValidRange(probe.markedRange) || probe.markedRange.length == 0,
              client.readText(in: expected.range) == expected.text else { return false }
        return valid && owns() && client.selection() == probe.selection && owns()
    }

    func recordCommit(_ text: String, word: String, replacing range: NSRange) {
        guard valid else { return }
        guard !busy, isValidRange(range), range.location == NSMaxRange(expected.range), owns() else {
            invalidate("手写位置已变化，请重新进入手写"); return
        }
        pieces.append(Piece(inserted: text, word: word))
        undo = nil; message = ""
        if self.word.count > 64 { invalidate("手写词过长，请重新进入手写") }
        else { onChanged?() }
    }

    func backspace() {
        guard valid, !busy, !pieces.isEmpty, verify() else {
            invalidate("前文已变化，请重新进入手写"); return
        }
        var last = pieces.removeLast()
        // A tap can append an automatic space; it is removed from the editor
        // without removing the character used as the correction word.
        if last.inserted == last.word, !last.word.isEmpty { last.word.removeLast() }
        if !last.inserted.isEmpty { last.inserted.removeLast() }
        if !last.inserted.isEmpty { pieces.append(last) }
        message = ""; onChanged?()
    }

    func request(_ id: String) {
        guard !busy, !id.isEmpty, id.count <= 128 else { return }
        guard valid else { onChanged?(); return }
        guard !word.isEmpty else { message = "请先手写并确认要修正的词"; onChanged?(); return }
        guard !preceding.text.isEmpty else {
            message = preceding.range.location == 0 ? "光标前没有可修正的文本" : "无法读取光标前的文本"
            onChanged?(); return
        }
        guard verify() else { invalidate("前文或光标已变化，未执行修正"); return }
        requestID = id; deadline = now() + 30
        message = "正在修正「\(word)」"; onChanged?()
        if valid, requestID == id, owns() { onRequest?(id, preceding.text, word) }
    }

    func apply(id: String, fragment: String, error: String) {
        guard requestID == id, write == nil, valid, owns() else { return }
        requestID = nil
        if !error.isEmpty { message = error; onChanged?(); return }
        guard verify() else { invalidate("前文或光标已变化，未执行修正"); return }
        let source = preceding.text as NSString
        let match = source.range(of: fragment)
        guard !fragment.isEmpty, fragment.count <= 64, match.location != NSNotFound,
              source.range(of: fragment, range: NSRange(location: match.location + 1,
                  length: source.length - match.location - 1)).location == NSNotFound else {
            message = "未找到唯一的对应片段，已保留手写内容"; onChanged?(); return
        }
        let corrected = source.replacingCharacters(in: match, with: word)
        let result = EditorTextRange(text: corrected,
            range: NSRange(location: preceding.range.location, length: corrected.utf16.count))
        beginWrite(Write(result: result, preceding: result, pieces: [], undo: Undo(preceding: preceding, pieces: pieces),
                         message: "\(fragment) → \(word) · 左滑撤销"))
    }

    func undoCorrection() {
        guard canUndo, let undo else { return }
        guard verify() else { invalidate("前文或光标已变化，未执行撤销"); return }
        let text = undo.preceding.text + undo.pieces.map(\.inserted).joined()
        beginWrite(Write(result: EditorTextRange(text: text,
            range: NSRange(location: undo.preceding.range.location, length: text.utf16.count)),
            preceding: undo.preceding, pieces: undo.pieces, undo: nil, message: "已撤销修正"))
    }

    private func beginWrite(_ operation: Write) {
        let before = expected
        write = operation; deadline = now() + 4
        message = "正在写回修正结果"; onChanged?()
        guard valid, owns(), write != nil else { return }
        // One replacement removes the appended handwriting as well as fixing
        // the preceding span. No cleanup deletion is ever sent independently.
        client.replaceEditing(before.range, with: operation.result.text,
                              restoringCaret: NSMaxRange(operation.result.range), expected: before.text)
        advance()
    }

    func advance() {
        guard valid, busy, owns() else { return }
        if let operation = write {
            if let error = client.clientOperationFailure {
                invalidate(error); return
            }
            if !client.clientOperationPending {
                let probe = client.probe()
                let receipt = client.verifiedReplacement
                let confirmed = receipt?.text == operation.result.text && receipt?.range == operation.result.range
                if probe.selection == NSRange(location: NSMaxRange(operation.result.range), length: 0),
                   !isValidRange(probe.markedRange) || probe.markedRange.length == 0,
                   confirmed || client.readText(in: operation.result.range) == operation.result.text,
                   valid, owns(), write != nil {
                    preceding = operation.preceding; pieces = operation.pieces; undo = operation.undo
                    write = nil; message = operation.message; onChanged?(); return
                }
            }
            if now() >= deadline { invalidate("未确认修正结果，请检查文本；未重复写入") }
        } else if now() >= deadline {
            requestID = nil; message = "修正超时，已保留手写内容"; onChanged?()
        }
    }

    func invalidate(_ reason: String) {
        valid = false; requestID = nil; write = nil; undo = nil; pieces = []
        message = reason
        client.cancelClientOperation()
        onChanged?()
    }
}
