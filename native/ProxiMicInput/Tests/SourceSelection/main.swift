import Foundation

precondition(matchesSourceApplication(pid: 123, bundle: nil, expectedPID: 123, expectedBundle: ""))
precondition(matchesSourceApplication(pid: 123, bundle: "test.app", expectedPID: 123, expectedBundle: "test.app"))
precondition(!matchesSourceApplication(pid: 456, bundle: nil, expectedPID: 123, expectedBundle: ""))
precondition(!matchesSourceApplication(pid: nil, bundle: nil, expectedPID: 123, expectedBundle: ""))
precondition(!matchesSourceApplication(pid: 123, bundle: "test.app", expectedPID: 123, expectedBundle: ""))
precondition(!matchesSourceApplication(pid: 0, bundle: nil, expectedPID: 0, expectedBundle: ""))

let voice = "test.voice.dictation"
var current = voice
var selections = 0
func run(_ app: @escaping () -> Bool = { true }, _ select: (() throws -> Void)? = nil) throws -> Bool {
    try ensureVoiceSource(modeID: voice, currentSource: { current }, sameApplication: app,
        select: select ?? { selections += 1; current = voice })
}
func rejects(_ action: () throws -> Bool) {
    do { _ = try action(); fatalError("Expected selection to fail") } catch { }
}
let already = try run()
precondition(already && selections == 0)
current = "system.pinyin"
let switched = try run()
precondition(!switched && selections == 1 && current == voice)
_ = try run()
precondition(selections == 1) // no re-selection of an existing composition
current = ""
rejects { try run() }
precondition(selections == 1)
current = "system.abc"
rejects { try run({ false }) }
var identityReads = 0
rejects { try run({ identityReads += 1; return identityReads == 1 }) }
precondition(selections == 1)
rejects { try run({ true }, { throw SourceSelectionFailure("Not installed") }) }
rejects { try run({ true }, { }) } // unsuccessful system switch
precondition(current == "system.abc" && selections == 1)
print("Source selection: 8 scenarios passed; no AX or real source switching")

let abc = "com.apple.keylayout.ABC"
let updateVoiceIDs: Set<String> = [voice]
var updateSource = "system.pinyin"
var updateApp: (Int32, String) = (123, "test.editor")
var updateSelections: [String] = []
func updateSelect(_ source: String) throws {
    updateSelections.append(source)
    updateSource = source
}
func prepareUpdate(_ fallback: () throws -> String = { abc },
                   _ select: (String) throws -> Void = updateSelect) throws -> UpdateSourceHandoff? {
    try prepareUpdateSource(voiceIDs: updateVoiceIDs, currentSource: { updateSource },
        foreground: { updateApp }, fallback: fallback, select: select)
}
func restoreUpdate(_ handoff: UpdateSourceHandoff,
                   _ select: (String) throws -> Void = updateSelect) throws -> Bool {
    try restoreUpdateSource(handoff, voiceIDs: updateVoiceIDs, currentSource: { updateSource },
        foreground: { updateApp }, select: select)
}
// Existing non-Mythlink selections never change, including on a fresh install.
let untouched = try prepareUpdate({ fatalError("must not find a fallback") })
precondition(untouched == nil && updateSelections.isEmpty)
updateSource = voice
let handoff = try prepareUpdate()!
precondition(handoff.originalSource == voice && handoff.temporarySource == abc)
precondition(updateSource == abc && updateSelections == [abc])
let restored = try restoreUpdate(handoff)
precondition(restored && updateSource == voice && updateSelections == [abc, voice])
rejects { try prepareUpdate({ throw SourceSelectionFailure("no enabled keyboard") }) != nil }
precondition(updateSource == voice)
rejects { try prepareUpdate({ voice }) != nil }
rejects { try prepareUpdate({ abc }, { _ in }) != nil } // macOS did not confirm switch
rejects { try prepareUpdate({ updateApp = (124, "other.app"); return abc }) != nil }
precondition(updateSource == voice && updateSelections.count == 2)
updateApp = (123, "test.editor")
rejects { try prepareUpdate({ updateSource = "system.pinyin"; return abc }) != nil }
precondition(updateSource == "system.pinyin" && updateSelections.count == 2)
// Both an app change and a manual source change prevent automatic restoration.
let sourceChanged = try restoreUpdate(handoff)
precondition(!sourceChanged && updateSelections.count == 2)
updateSource = abc
updateApp = (124, "other.app")
let appChanged = try restoreUpdate(handoff)
precondition(!appChanged && updateSource == abc && updateSelections.count == 2)
updateApp = (123, "test.editor")
rejects { try restoreUpdate(handoff, { _ in throw SourceSelectionFailure("unavailable") }) }
rejects { try restoreUpdate(handoff, { _ in }) }
let invalid = UpdateSourceHandoff(originalSource: "another.ime", temporarySource: abc, pid: 123, bundle: "test.editor")
rejects { try restoreUpdate(invalid) }
precondition(updateSource == abc && updateSelections.count == 2)
print("Input method update scenarios passed; no AX or real source switching")
