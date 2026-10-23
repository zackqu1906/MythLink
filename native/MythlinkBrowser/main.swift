import Cocoa
import SafariServices

private let extensionIdentifier = "com.proximic.voice.browserhost.extension"

private func readExtensionState(_ completion: @escaping (String) -> Void) {
    var completed = false
    func finish(_ state: String) {
        guard !completed else { return }
        completed = true
        completion(state)
    }
    SFSafariExtensionManager.getStateOfSafariExtension(withIdentifier: extensionIdentifier) { state, error in
        DispatchQueue.main.async {
            if let error = error as NSError? {
                // SFErrorNoExtensionFound = 1. A query failure or timeout is
                // not evidence that the extension is missing or unsigned.
                finish(error.domain == SFErrorDomain && error.code == 1 ? "unavailable" : "unknown")
                return
            }
            guard let state = state else { finish("unknown"); return }
            finish(state.isEnabled ? "enabled" : "disabled")
        }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { finish("unknown") }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var titleLabel: NSTextField!
    var detail: NSTextField!
    var checking = false
    var timer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 430),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Mythlink 浏览器助手"
        titleLabel = NSTextField(labelWithString: "正在检查 Safari 扩展")
        titleLabel.font = .boldSystemFont(ofSize: 23)
        detail = NSTextField(wrappingLabelWithString: "正在向 Safari 查询启用状态…")
        detail.font = .systemFont(ofSize: 14)
        let settings = NSButton(title: "打开 Safari 扩展设置", target: self, action: #selector(openSettings))
        let retry = NSButton(title: "重新检查", target: self, action: #selector(refreshState))
        settings.bezelStyle = .rounded
        retry.bezelStyle = .rounded
        let buttons = NSStackView(views: [settings, retry])
        buttons.spacing = 12
        let privacy = NSTextField(wrappingLabelWithString: "播放器状态只在本机传递，不上传网页或媒体内容。")
        privacy.font = .systemFont(ofSize: 12)
        privacy.textColor = .secondaryLabelColor
        let stack = NSStackView(views: [titleLabel, detail, buttons, privacy])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 20
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 28),
            stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -28),
            stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 26),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: window.contentView!.bottomAnchor, constant: -24),
            detail.widthAnchor.constraint(equalTo: stack.widthAnchor),
            privacy.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
        window.center()
        showWindow()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.refreshState() }
    }

    private func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func render(_ state: String) {
        switch state {
        case "enabled":
            titleLabel.stringValue = "Safari 扩展已启用"
            detail.stringValue = "1. 在 Safari 扩展设置中选择「Mythlink 浏览器助手」，检查网站访问权限。\n2. 允许需要控制的网站访问（可选所有网站）。\n3. 保持 Mythlink 运行，刷新视频或音频页面。\n\nMythlink 中显示「已连接」后，即可使用你设置的场景手势。"
        case "disabled":
            titleLabel.stringValue = "请在 Safari 中启用扩展"
            detail.stringValue = "扩展组件已随 Mythlink 安装。\n\n1. 点击下方按钮，勾选「Mythlink 浏览器助手」。\n2. 允许需要控制的网站访问（可选所有网站）。\n3. 保持 Mythlink 运行，刷新视频或音频页面。\n\nSafari 可能要求你亲手勾选扩展并确认授权。"
        default:
            titleLabel.stringValue = "暂时无法确认 Safari 启用状态"
            detail.stringValue = "扩展组件已包含在应用中，状态检查失败不代表未安装。\n\n点击「打开 Safari 扩展设置」，查看「Mythlink 浏览器助手」的启用状态和网站访问权限。\n\n如果使用尚未公证的测试版且列表为空，可能需要在 Safari 设置 → 开发者中手动允许未签名扩展，Safari 重启后该许可可能复位。正式发行需完成签名及公证。"
        }
    }

    private func refresh() {
        guard !checking else { return }
        checking = true
        readExtensionState { [weak self] state in
            guard let self = self else { return }
            self.checking = false
            self.render(state)
        }
    }

    @objc func refreshState() { refresh() }
    func applicationDidBecomeActive(_ notification: Notification) {
        if window != nil { refreshState() }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openSettings()
        return true
    }
    @objc func openSettings() {
        SFSafariApplication.showPreferencesForExtension(withIdentifier: extensionIdentifier) { [weak self] error in
            DispatchQueue.main.async {
                if error != nil {
                    self?.render("unavailable")
                    self?.showWindow()
                } else {
                    self?.window.orderOut(nil)
                }
            }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
if CommandLine.arguments.contains("--open-settings") {
    // A one-shot command: do not depend on a cached status, LaunchServices
    // argument delivery, or a second helper window stealing Safari's focus.
    app.setActivationPolicy(.accessory)
    DispatchQueue.main.async {
        SFSafariApplication.showPreferencesForExtension(withIdentifier: extensionIdentifier) { error in
            DispatchQueue.main.async {
                if let error = error as NSError? {
                    let data: [String: Any] = ["opened": false, "error_domain": error.domain, "error_code": error.code]
                    if let bytes = try? JSONSerialization.data(withJSONObject: data) {
                        FileHandle.standardError.write(bytes)
                    }
                    exit(1)
                }
                print("{\"opened\":true}")
                exit(0)
            }
        }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 6) { exit(2) }
    app.run()
} else if CommandLine.arguments.contains("--extension-status") {
    // Read-only mode used by the desktop UI. Never opens settings or a window.
    app.setActivationPolicy(.prohibited)
    func finish(_ state: String) {
        print("{\"state\":\"\(state)\"}")
        exit(0)
    }
    DispatchQueue.main.async { readExtensionState { finish($0) } }
    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { finish("unknown") }
    app.run()
} else {
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    withExtendedLifetime(delegate) { app.run() }
}
