import AppKit
import Foundation

struct SourceSelectionFailure: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}

struct UpdateSourceHandoff: Codable {
    let originalSource: String
    let temporarySource: String
    let pid: Int32
    let bundle: String
}

/// Explicit installation only: release our selected input source before replacing its bundle.
func prepareUpdateSource(voiceIDs: Set<String>, currentSource: () -> String,
                         foreground: () -> (Int32, String), fallback: () throws -> String,
                         select: (String) throws -> Void) throws -> UpdateSourceHandoff? {
    let original = currentSource()
    guard !original.isEmpty else { throw SourceSelectionFailure("无法确认当前输入法，请稍后重试") }
    guard voiceIDs.contains(original) else { return nil }
    let origin = foreground()
    guard origin.0 > 0, !origin.1.isEmpty else {
        throw SourceSelectionFailure("无法确认当前应用，请返回 Mythlink 后重试更新")
    }
    let temporary = try fallback()
    guard !temporary.isEmpty, !voiceIDs.contains(temporary) else {
        throw SourceSelectionFailure("未找到可用的系统键盘，输入法尚未更新")
    }
    guard foreground() == origin, currentSource() == original else {
        throw SourceSelectionFailure("应用或输入法已变化，请重新点击更新")
    }
    try select(temporary)
    guard currentSource() == temporary else {
        throw SourceSelectionFailure("系统未确认临时切换输入法，原组件未被替换")
    }
    return UpdateSourceHandoff(originalSource: original, temporarySource: temporary,
                               pid: origin.0, bundle: origin.1)
}

/// Never override a newer application/source choice made during the installation.
func restoreUpdateSource(_ handoff: UpdateSourceHandoff, voiceIDs: Set<String>,
                         currentSource: () -> String, foreground: () -> (Int32, String),
                         select: (String) throws -> Void) throws -> Bool {
    guard voiceIDs.contains(handoff.originalSource), !handoff.temporarySource.isEmpty,
          !voiceIDs.contains(handoff.temporarySource), handoff.pid > 0, !handoff.bundle.isEmpty else {
        throw SourceSelectionFailure("输入法更新交接信息无效")
    }
    guard foreground() == (handoff.pid, handoff.bundle), currentSource() == handoff.temporarySource else {
        return false
    }
    try select(handoff.originalSource)
    guard currentSource() == handoff.originalSource else {
        throw SourceSelectionFailure("安装已完成，但系统尚未恢复 Mythlink 输入法；可手动选择或双击 tap 切入")
    }
    return true
}

/// A source-only operation: never queries AX, a text field or its contents.
/// Returns true when already selected, so an active composition is untouched.
func ensureVoiceSource(modeID: String, currentSource: () -> String,
                       sameApplication: () -> Bool, select: () throws -> Void) throws -> Bool {
    guard sameApplication() else { throw SourceSelectionFailure("前台应用已变化，请重新 tap") }
    let current = currentSource()
    guard !current.isEmpty else { throw SourceSelectionFailure("系统未返回当前输入法，请重试") }
    if current == modeID { return true }
    guard sameApplication() else { throw SourceSelectionFailure("前台应用已变化，请重新 tap") }
    try select()
    guard currentSource() == modeID else { throw SourceSelectionFailure("系统未确认切换到语音输入法") }
    return false
}

/// Restore the foreground client's input context through a real, bounded focus
/// round trip. No AX, keyboard events, clipboard access or source cycling.
func matchesSourceApplication(pid: Int32?, bundle: String?, expectedPID: Int32, expectedBundle: String) -> Bool {
    // Source-launched Qt applications have no bundle identifier. Keep the PID
    // check mandatory; an empty identifier must never become a wildcard.
    expectedPID > 0 && pid == expectedPID && (bundle ?? "") == expectedBundle
}

func restoreInputFocus(pid: Int32, bundle: String, currentSource: @escaping () -> String,
                       modeID: String, requestHostFocus: (() -> Void)? = nil) throws {
    let workspace = NSWorkspace.shared
    let ownPID = ProcessInfo.processInfo.processIdentifier
    let parentPID = getppid()
    guard requestHostFocus == nil || pid == parentPID else {
        throw SourceSelectionFailure("窗口焦点只能由原宿主恢复")
    }
    guard let origin = NSRunningApplication(processIdentifier: pid),
          matchesSourceApplication(pid: origin.processIdentifier, bundle: origin.bundleIdentifier,
                                   expectedPID: pid, expectedBundle: bundle), origin.isActive else {
        throw SourceSelectionFailure("前台应用已变化，请重新 tap")
    }
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    let rect = NSScreen.main?.visibleFrame ?? .zero
    let window = NSWindow(contentRect: NSRect(x: rect.minX + 8, y: rect.minY + 8, width: 1, height: 1),
                          styleMask: [.titled], backing: .buffered, defer: false)
    window.alphaValue = 0
    window.hasShadow = false
    window.ignoresMouseEvents = true
    window.isReleasedWhenClosed = false
    window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
    var interrupted = false
    let observer = workspace.notificationCenter.addObserver(
        forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
    ) { notification in
        if let running = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
           running.processIdentifier != pid && running.processIdentifier != ownPID {
            interrupted = true
        }
    }
    defer {
        workspace.notificationCenter.removeObserver(observer)
        window.orderOut(nil)
    }
    func check() throws {
        guard !interrupted, !origin.isTerminated, parentPID > 1, getppid() == parentPID else {
            throw SourceSelectionFailure("已切换应用或取消启动")
        }
        let front = workspace.frontmostApplication?.processIdentifier
        guard front == pid || front == ownPID else {
            throw SourceSelectionFailure("已切换应用，本句已取消")
        }
    }
    var outcome: Result<Void, Error>?
    var phase = "acquiring"
    var phaseStart = ProcessInfo.processInfo.systemUptime
    var originSince: Double?
    var timer: Timer?
    func complete(_ result: Result<Void, Error>) {
        guard outcome == nil else { return }
        outcome = result
        timer?.invalidate()
        app.stop(nil)
        // Wake only our own event loop; this never injects a system input event.
        if let event = NSEvent.otherEvent(with: .applicationDefined, location: .zero,
            modifierFlags: [], timestamp: 0, windowNumber: 0, context: nil,
            subtype: 0, data1: 0, data2: 0) { app.postEvent(event, atStart: false) }
    }
    timer = Timer.scheduledTimer(withTimeInterval: 0.005, repeats: true) { _ in
        do {
            try check()
            let now = ProcessInfo.processInfo.systemUptime
            switch phase {
            case "acquiring":
                if app.isActive && window.isKeyWindow {
                    phase = "holding"; phaseStart = now
                } else if now - phaseStart > 0.7 {
                    throw SourceSelectionFailure("输入法激活未完成，请重试")
                }
            case "holding":
                if now - phaseStart >= 0.15 {
                    if workspace.frontmostApplication?.processIdentifier == ownPID {
                        if let requestHostFocus {
                            // The host knows its exact nonactivating Agent panel.
                            // Activating only its PID can raise the main window.
                            requestHostFocus()
                        } else {
                            guard origin.activate(options: []) else {
                                throw SourceSelectionFailure("未能返回原应用，请重新点入文本框")
                            }
                        }
                    }
                    window.orderOut(nil)
                    phase = "returning"; phaseStart = now
                }
            default:
                if workspace.frontmostApplication?.processIdentifier == pid {
                    if originSince == nil { originSince = now }
                    if now - originSince! >= 0.05 {
                        guard currentSource() == modeID else {
                            throw SourceSelectionFailure("输入法已变化，请重新 tap")
                        }
                        complete(.success(()))
                    }
                } else { originSince = nil }
                if now - phaseStart > 0.7 {
                    throw SourceSelectionFailure("未能返回原应用，请重新点入文本框")
                }
            }
        } catch { complete(.failure(error)) }
    }
    window.makeKeyAndOrderFront(nil)
    app.activate(ignoringOtherApps: true)
    app.run()
    timer?.invalidate()
    try (outcome ?? .failure(SourceSelectionFailure("输入法激活已取消"))).get()

}

func selectSourceSession(pid: Int32, bundle: String, modeID: String,
                         refreshOnly: Bool = false,
                         hostRestoresFocus: Bool = false,
                         currentSource: @escaping () -> String, select: () throws -> Void) -> Never {
    func emit(_ fields: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: fields) {
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data("\n".utf8))
        }
    }
    alarm(5)
    let start = ProcessInfo.processInfo.systemUptime
    do {
        guard !hostRestoresFocus || pid == getppid() else {
            throw SourceSelectionFailure("窗口焦点只能由原宿主恢复")
        }
        // Recovery of an already selected, inactive source never overrides a
        // newer manual input-source choice.
        if refreshOnly && currentSource() != modeID {
            throw SourceSelectionFailure("输入法已变化，请重新 tap")
        }
        let alreadySelected = try ensureVoiceSource(modeID: modeID, currentSource: currentSource,
            sameApplication: {
                let current = NSWorkspace.shared.frontmostApplication
                return matchesSourceApplication(pid: current?.processIdentifier, bundle: current?.bundleIdentifier,
                                                expectedPID: pid, expectedBundle: bundle)
            }, select: select)
        let restore = !alreadySelected || refreshOnly
        if restore {
            let hostFocus: (() -> Void)? = hostRestoresFocus
                ? { emit(["event": "restore_focus"]) } : nil
            try restoreInputFocus(pid: pid, bundle: bundle, currentSource: currentSource,
                                  modeID: modeID, requestHostFocus: hostFocus)
        }
        emit(["event": "selected", "already_selected": alreadySelected,
              "focus_restored": restore,
              "elapsed_ms": (ProcessInfo.processInfo.systemUptime - start) * 1000])
        exit(0)
    } catch {
        emit(["event": "error", "error": error.localizedDescription])
        exit(1)
    }
}
