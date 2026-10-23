import Foundation
import Carbon
import AppKit

// Fresh installation preserves the input source. Updating a selected IME can
// temporarily release it and conditionally restore the same selection.
let productID = "com.proximic.inputmethod.ProxiMicVoice"
let installation = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Input Methods/ProxiMicInput.app")

func property(_ source: TISInputSource, _ key: CFString) -> AnyObject? {
    guard let pointer = TISGetInputSourceProperty(source, key) else { return nil }
    return Unmanaged<AnyObject>.fromOpaque(pointer).takeUnretainedValue()
}

func allSources() -> [TISInputSource] {
    guard let list = TISCreateInputSourceList(nil, true)?.takeRetainedValue() else { return [] }
    return (0..<CFArrayGetCount(list)).map {
        unsafeBitCast(CFArrayGetValueAtIndex(list, $0), to: TISInputSource.self)
    }
}

func sources() -> [TISInputSource] {
    allSources().filter { property($0, kTISPropertyBundleID) as? String == productID }
}

func currentSourceID() -> String {
    guard let source = TISCopyCurrentKeyboardInputSource()?.takeRetainedValue() else { return "" }
    return property(source, kTISPropertyInputSourceID) as? String ?? ""
}

func currentApplication() -> (Int32, String) {
    let front = NSWorkspace.shared.frontmostApplication
    return (front?.processIdentifier ?? 0, front?.bundleIdentifier ?? "")
}

func selectUpdateSource(_ identifier: String) throws {
    guard let source = allSources().first(where: { property($0, kTISPropertyInputSourceID) as? String == identifier }),
          property(source, kTISPropertyInputSourceIsEnabled) as? Bool == true,
          property(source, kTISPropertyInputSourceIsSelectCapable) as? Bool == true else {
        throw SourceSelectionFailure("未找到可用的目标输入法，原选择保持不变")
    }
    guard TISSelectInputSource(source) == noErr else {
        throw SourceSelectionFailure("系统未能切换输入法，请稍后重试")
    }
}

func fallbackKeyboardID() throws -> String {
    let candidates = allSources().filter {
        let identifier = property($0, kTISPropertyInputSourceID) as? String ?? ""
        return identifier.hasPrefix("com.apple.keylayout.")
            && property($0, kTISPropertyInputSourceType) as? String == kTISTypeKeyboardLayout as String
            && property($0, kTISPropertyInputSourceIsASCIICapable) as? Bool == true
            && property($0, kTISPropertyInputSourceIsEnabled) as? Bool == true
            && property($0, kTISPropertyInputSourceIsSelectCapable) as? Bool == true
    }.compactMap { property($0, kTISPropertyInputSourceID) as? String }
    guard let chosen = ["com.apple.keylayout.ABC", "com.apple.keylayout.US"].first(where: candidates.contains)
            ?? candidates.sorted().first else {
        throw SourceSelectionFailure("未找到已启用的系统键盘，请先在系统设置中添加 ABC 后重试")
    }
    return chosen
}

func snapshot() -> [String: Any] {
    let items = sources().map { source -> [String: Any] in
        ["id": property(source, kTISPropertyInputSourceID) as? String ?? "",
         "name": property(source, kTISPropertyLocalizedName) as? String ?? "",
         "enabled": property(source, kTISPropertyInputSourceIsEnabled) as? Bool ?? false,
         "selected": property(source, kTISPropertyInputSourceIsSelected) as? Bool ?? false]
    }
    return ["bundle_id": productID, "path": installation.path,
            "installed": Bundle(url: installation)?.bundleIdentifier == productID,
            "sources": items]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

@main
struct InputMethodAdmin {
    static func main() throws {
        let command = CommandLine.arguments.dropFirst().first ?? "status"
        var extra: [String: Any] = [:]
        let voiceIDs: Set<String> = [productID, productID + ".dictation"]
        switch command {
        case "status": break
        case "prepare-update":
            do {
                if let handoff = try prepareUpdateSource(voiceIDs: voiceIDs,
                    currentSource: currentSourceID, foreground: currentApplication,
                    fallback: fallbackKeyboardID, select: selectUpdateSource) {
                    extra["update_handoff"] = try JSONSerialization.jsonObject(with: JSONEncoder().encode(handoff))
                } else { extra["update_handoff"] = NSNull() }
            } catch { fail(error.localizedDescription) }
        case "restore-after-update":
            guard CommandLine.arguments.count == 3 else { fail("缺少输入法更新交接信息") }
            do {
                let handoff = try JSONDecoder().decode(UpdateSourceHandoff.self, from: Data(CommandLine.arguments[2].utf8))
                extra["input_source_restored"] = try restoreUpdateSource(handoff, voiceIDs: voiceIDs,
                    currentSource: currentSourceID, foreground: currentApplication, select: selectUpdateSource)
            } catch { fail(error.localizedDescription) }
        case "ensure-selected", "refresh-selected":
            let args = CommandLine.arguments
            let hostRestoresFocus = args.count == 5 && args[4] == "--host-restores-focus"
            guard (args.count == 4 || hostRestoresFocus), let pid = Int32(args[2]), pid > 0 else {
                fail("ensure-selected requires a foreground PID and bundle ID")
            }
            let modeID = productID + ".dictation"
            selectSourceSession(pid: pid, bundle: CommandLine.arguments[3], modeID: modeID,
                refreshOnly: command == "refresh-selected",
                hostRestoresFocus: hostRestoresFocus,
                currentSource: {
                    guard let source = TISCopyCurrentKeyboardInputSource()?.takeRetainedValue() else { return "" }
                    return property(source, kTISPropertyInputSourceID) as? String ?? ""
                }, select: {
                    guard let source = sources().first(where: { property($0, kTISPropertyInputSourceID) as? String == modeID }),
                          property(source, kTISPropertyInputSourceIsEnabled) as? Bool == true,
                          property(source, kTISPropertyInputSourceIsSelectCapable) as? Bool == true else {
                        throw SourceSelectionFailure("语音输入法尚未安装或启用，请先在输入法设置中安装组件")
                    }
                    guard TISSelectInputSource(source) == noErr else { throw SourceSelectionFailure("自动切换语音输入法失败，请重试") }
                })
        case "select":
            // A queued gesture must not switch the input source of a newly focused app.
            if let argument = CommandLine.arguments.dropFirst(2).first {
                guard let pid = Int32(argument),
                      NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else {
                    fail("前台应用已变化，请重新做切换手势")
                }
            }
            let modeID = productID + ".dictation"
            guard let source = sources().first(where: {
                property($0, kTISPropertyInputSourceID) as? String == modeID
            }) else { fail("未找到 Mythlink，请先在输入法设置中安装组件") }
            guard property(source, kTISPropertyInputSourceIsEnabled) as? Bool == true,
                  property(source, kTISPropertyInputSourceIsSelectCapable) as? Bool == true else {
                fail("Mythlink 尚未启用，请在系统键盘设置中添加输入法")
            }
            // Re-selecting an active IME can reset composition in some clients.
            if property(source, kTISPropertyInputSourceIsSelected) as? Bool != true {
                let status = TISSelectInputSource(source)
                guard status == noErr else { fail("切换语音输入法失败：\(status)") }
            }
            guard let current = TISCopyCurrentKeyboardInputSource()?.takeRetainedValue(),
                  property(current, kTISPropertyInputSourceID) as? String == modeID else {
                fail("系统未确认输入法切换，请点入文本框后重试")
            }
        case "register":
            guard Bundle(url: installation)?.bundleIdentifier == productID else {
                fail("安装位置不是本产品的输入法组件：\(installation.path)")
            }
            let status = TISRegisterInputSource(installation as CFURL)
            guard status == noErr else { fail("输入法注册失败：\(status)") }
        case "enable":
            let matching = sources().sorted {
                let leftIsMode = (property($0, kTISPropertyInputSourceType) as? String) == (kTISTypeKeyboardInputMode as String)
                let rightIsMode = (property($1, kTISPropertyInputSourceType) as? String) == (kTISTypeKeyboardInputMode as String)
                return !leftIsMode && rightIsMode
            }
            guard !matching.isEmpty else { fail("未找到已注册的 Mythlink 输入源，请先安装。") }
            for source in matching {
                if property(source, kTISPropertyInputSourceIsEnableCapable) as? Bool == true {
                    let status = TISEnableInputSource(source)
                    guard status == noErr else { fail("输入法启用失败：\(status)") }
                }
            }
        case "disable":
            guard !sources().contains(where: { property($0, kTISPropertyInputSourceIsSelected) as? Bool == true }) else {
                fail("请先手动切回系统拼音或其他输入法，再卸载 Mythlink。")
            }
            for source in sources().reversed() {
                if property(source, kTISPropertyInputSourceIsEnabled) as? Bool == true {
                    let status = TISDisableInputSource(source)
                    guard status == noErr else { fail("输入法停用失败：\(status)") }
                }
            }
        default:
            fail("用法：InputMethodAdmin [status|register|enable|disable|prepare-update|restore-after-update 交接信息|select [前台进程 ID]]")
        }
        let json = try JSONSerialization.data(withJSONObject: snapshot().merging(extra) { _, new in new }, options: [.prettyPrinted, .sortedKeys])
        FileHandle.standardOutput.write(json)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
