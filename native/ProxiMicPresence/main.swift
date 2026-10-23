import AppKit
import ApplicationServices
import CoreBluetooth
import Security
import LocalAuthentication
import OpenDirectory
import IOKit.pwr_mgt

let service = "com.proximic.Presence.login"
func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return }
    FileHandle.standardOutput.write(data + Data([10]))
}
func permitted() -> Bool { AXIsProcessTrusted() && CGPreflightPostEventAccess() }
func credentialQuery() -> [String: Any] {
    [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
     kSecAttrAccount as String: NSUserName()]
}
func credentialExists() -> Bool {
    SecKeychainSetUserInteractionAllowed(false)
    var query = credentialQuery(); query[kSecReturnAttributes as String] = true
    let context = LAContext(); context.interactionNotAllowed = true
    query[kSecUseAuthenticationContext as String] = context
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    return status == errSecSuccess || status == errSecInteractionNotAllowed
}
func credential(allowInteraction: Bool = false) -> Data? {
    SecKeychainSetUserInteractionAllowed(allowInteraction)
    defer { SecKeychainSetUserInteractionAllowed(false) }
    var query = credentialQuery(); query[kSecReturnData as String] = true
    if !allowInteraction {
        let context = LAContext(); context.interactionNotAllowed = true
        query[kSecUseAuthenticationContext as String] = context
        // Classic macOS Keychain ACL dialogs also need the process-wide flag above.
        query[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
    }
    var result: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else { return nil }
    return result as? Data
}
func credentialReady() -> Bool {
    guard var data = credential() else { return false }
    defer { data.resetBytes(in: 0..<data.count) }
    return !data.isEmpty
}
func status(_ error: String = "") {
    let exists = credentialExists()
    emit(["event": "status", "permission": permitted(), "password": exists,
          "password_access": exists && credentialReady(), "error": error])
}
func lockedSession() -> Bool? {
    guard let session = CGSessionCopyCurrentDictionary() as? [String: Any],
          let uid = session[kCGSessionUserIDKey as String] as? NSNumber, uid.uint32Value == getuid(),
          let console = session[kCGSessionOnConsoleKey as String] as? Bool, console else { return nil }
    // This macOS session key is also used by BLEUnlock. Unknown is never permission to type.
    return (session["CGSSessionScreenIsLocked"] as? NSNumber)?.boolValue ?? false
}
func key(_ code: CGKeyCode, flags: CGEventFlags = []) {
    guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
          let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) else { return }
    down.flags = flags; up.flags = flags
    down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
}
func configurePassword() {
    SecKeychainSetUserInteractionAllowed(true)
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory); app.activate(ignoringOtherApps: true)
    let alert = NSAlert()
    alert.messageText = "设置本机解锁密码"
    alert.informativeText = "输入当前 Mac 用户的登录密码。密码只保存在这台 Mac 的钥匙串中，识别到弹指（snap）手势时用于解锁。不会发送到模型、戒指或写入日志。"
    alert.addButton(withTitle: "保存到钥匙串"); alert.addButton(withTitle: "取消")
    let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 28))
    field.placeholderString = "Mac 登录密码"; alert.accessoryView = field
    alert.window.initialFirstResponder = field
    guard alert.runModal() == .alertFirstButtonReturn else { status(); return }
    defer { field.stringValue = "" }
    guard !field.stringValue.isEmpty, field.stringValue.utf16.count <= 128 else {
        status("请输入有效的 Mac 登录密码（最多 128 个字符）"); return
    }
    do {
        let node = try ODNode(session: ODSession.default(), type: UInt32(kODNodeTypeLocalNodes))
        let record = try node.record(withRecordType: kODRecordTypeUsers, name: NSUserName(), attributes: nil)
        try record.verifyPassword(field.stringValue)
    } catch { status("登录密码验证失败，请确认是当前 Mac 用户的密码"); return }
    let data = Data(field.stringValue.utf8)
    let attributes: [String: Any] = [kSecValueData as String: data]
    let result = SecItemUpdate(credentialQuery() as CFDictionary, attributes as CFDictionary)
    if result == errSecItemNotFound {
        var query = credentialQuery(); query[kSecValueData as String] = data
        query[kSecAttrLabel as String] = "Mythlink 距离解锁 · \(NSUserName())"
        let added = SecItemAdd(query as CFDictionary, nil)
        status(added == errSecSuccess ? "" : "密码未保存到钥匙串，请重新设置")
    } else { status(result == errSecSuccess ? "" : "无法更新钥匙串，请重新授权此组件") }
}

final class Monitor: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    var policy: PresencePolicy
    var calibration: ProximityCalibration
    var activityCalibration = ActivityCalibration()
    var returnWake = ReturnWakePolicy()
    var returnDisplay = ReturnDisplayGate()
    var returnConfirmedAt: Double?
    let unlockSecret = UnlockSecret()
    var unlockToken = ""
    var hostGestureReady = false
    var hostConnection = HostConnectionState()
    var hostInstalledUnlockToken = ""
    var lastHostGestureState = -100.0
    var inputBuffer = Data()
    let target: UUID
    let observing: Bool
    let calibrating: Bool
    let startedAt = ProcessInfo.processInfo.systemUptime
    var central: CBCentralManager!
    var peripheral: CBPeripheral?
    var timer: Timer?
    var awake = true
    var lastDiscovery = -30.0
    var scanUntil = 0.0
    var lastPermission = -10.0
    var permission = false
    var error = ""
    var awaitingHost: Double?
    var lastLock: Bool?
    var lastReportAt = -100.0
    var connectedSince: Double?
    var rssiSamples = 0
    var lastRawRSSI: Double?
    var lastRSSIAt: Double?
    var lastRSSIRequestAt = -100.0
    var awakeAssertion: IOPMAssertionID = 0
    var displayActivityAssertion: IOPMAssertionID = 0
    var observers: [NSObjectProtocol] = []
    var now: Double { ProcessInfo.processInfo.systemUptime }

    /// Recent local input is the only cheap evidence that the wearer is sitting
    /// at this Mac rather than walking around with the link still up.
    func idleSeconds() -> Double {
        let anyInput = CGEventType(rawValue: ~0)!
        return [CGEventSourceStateID.hidSystemState, .combinedSessionState]
            .map { CGEventSource.secondsSinceLastEventType($0, eventType: anyInput) }
            .filter { $0.isFinite && $0 >= 0 }.min() ?? .infinity
    }
    func keyboardMouseIdleSeconds() -> Double {
        let types: [CGEventType] = [.keyDown, .keyUp, .flagsChanged, .mouseMoved,
            .leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp,
            .otherMouseDown, .otherMouseUp, .leftMouseDragged, .rightMouseDragged,
            .otherMouseDragged, .scrollWheel]
        return [CGEventSourceStateID.hidSystemState, .combinedSessionState].flatMap { state in
            types.map { CGEventSource.secondsSinceLastEventType(state, eventType: $0) }
        }
            .filter { $0.isFinite && $0 >= 0 }.min() ?? .infinity
    }
    var canTrackActivity: Bool {
        !calibrating && !observing && awake && permission && !policy.failed
        && central.state == .poweredOn && peripheral?.state == .connected
        && lockingConnectionReady && lockedSession() == false
        && calibration.qualified
    }
    var canLearnFromActivity: Bool {
        canTrackActivity && !policy.lockPending && awaitingHost == nil
    }
    var locallyActive: Bool {
        activityCalibration.preventsLock(now: now, localIdle: idleSeconds())
    }
    init(target: UUID, policy: PresencePolicy, observing: Bool = false,
         calibrating: Bool = false, baseline: Double? = nil) {
        self.target = target; self.policy = policy; self.observing = observing
        self.calibrating = calibrating
        self.calibration = ProximityCalibration(baseline: baseline)
        self.policy.thresholdsReady = self.calibration.qualified
        self.policy.setLockingEnabled(observing)
        super.init()
        // Keep BLE monitoring alive while the display is off. Closing the lid or
        // explicitly sleeping still suspends the Mac; process exit releases this assertion.
        IOPMAssertionCreateWithName(kIOPMAssertionTypePreventUserIdleSystemSleep as CFString,
                                   IOPMAssertionLevel(kIOPMAssertionLevelOn),
                                   "ProxiMic proximity monitoring" as CFString, &awakeAssertion)
        central = CBCentralManager(delegate: self, queue: .main,
                                   options: [CBCentralManagerOptionShowPowerAlertKey: false])
        let notifications = NSWorkspace.shared.notificationCenter
        observers.append(notifications.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { [weak self] _ in
            if self?.calibrating == true { self?.calibrationFailed("电脑已睡眠，校准未保存，请重新校准") }
            self?.awake = false; self?.policy.suspend(); self?.awaitingHost = nil
            self?.returnWake.reset(); self?.lastRSSIAt = nil
            self?.activityCalibration.reset(now: self?.now ?? 0)
            self?.invalidateHostGesture()
        })
        observers.append(notifications.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            self?.awake = true; self?.connectedSince = nil; self?.policy.disconnected(now: self?.now ?? 0)
        })
        if !observing || calibrating { FileHandle.standardInput.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            DispatchQueue.main.async {
                guard !data.isEmpty else { exit(0) }
                self?.receiveCommands(data)
            }
        }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in self?.tick() }
    }
    func receiveCommands(_ data: Data) {
        inputBuffer.append(data)
        guard inputBuffer.count <= 4096 else { exit(2) }
        while let end = inputBuffer.firstIndex(of: 10) {
            let line = String(data: inputBuffer[..<end], encoding: .utf8) ?? ""
            inputBuffer.removeSubrange(...end)
            let fields = line.split(separator: " ").map(String.init)
            if fields.count == 4 && fields[0] == "baseline-options",
               let revision = Int(fields[1]), revision >= activityCalibration.revision,
               let mask = Int(fields[2]), (0...7).contains(mask),
               let savedBaseline = Double(fields[3]), savedBaseline.isFinite, (-100 ... -20).contains(savedBaseline) {
                activityCalibration.configure(mask: mask, revision: revision, now: now)
                // The host's saved baseline wins over an in-flight update from
                // an older options revision that the host has already rejected.
                if calibration.baseline != savedBaseline {
                    calibration = ProximityCalibration(baseline: savedBaseline)
                    if let levels = calibration.thresholds() {
                        policy.setThresholds(lock: levels.lockRSSI, unlock: levels.unlockRSSI)
                    }
                    policy.invalidateSignal(); returnWake.reset()
                }
                lastReportAt = -100
            } else if fields.count == 3 && fields[0] == "baseline-activity",
                      Int(fields[1]) == activityCalibration.revision,
                      let source = ActivityCalibration.Source(rawValue: fields[2]), source != .keyboardMouse,
                      canTrackActivity {
                activityCalibration.activity(source, at: now, now: now)
                if locallyActive {
                    awaitingHost = nil; policy.cancelLockRequest(); lastReportAt = -100
                }
            } else if line == "host-connected 1" || line == "host-connected 0" {
                hostConnection.update(connected: line.hasSuffix("1"), now: now)
                refreshLockingAvailability()
            } else if line == "lock-cancel" {
                awaitingHost = nil; policy.cancelLockRequest()
            } else if line == "lock-ready" { hostReady() }
            else if line == "gesture-ready 1" || line == "gesture-ready 0" {
                let ready = line.hasSuffix("1")
                let changed = ready != hostGestureReady
                hostGestureReady = ready; lastHostGestureState = now
                if !ready { hostInstalledUnlockToken = "" }
                if changed { lastReportAt = -100; tick() }
            } else if !unlockToken.isEmpty && line == "gesture-installed " + unlockToken {
                if hostGestureReady && now - lastHostGestureState < 3 && hostInstalledUnlockToken != unlockToken {
                    hostInstalledUnlockToken = unlockToken
                    lastReportAt = -100; tick()
                }
            } else if line == "gesture-unlock " + unlockToken && !unlockToken.isEmpty {
                // Refresh the actual session before consuming the one-lock token.
                tick()
                if line == "gesture-unlock " + unlockToken && !unlockToken.isEmpty,
                   hostGestureReady && now - lastHostGestureState < 3,
                   hostInstalledUnlockToken == unlockToken,
                   unlockSecret.isPrepared,
                   policy.requestGestureUnlock(now: now, locked: lockedSession(),
                    connected: peripheral?.state == .connected, permitted: permitted(), awake: awake) == .unlock {
                    unlockToken = ""
                    unlock()
                } else if policy.awaitingGesture {
                    // A changed live condition rejects this gesture. A later
                    // gesture can use a new token; never replay this request.
                    unlockToken = UUID().uuidString.lowercased()
                }
            }
        }
    }
    func invalidateHostGesture() {
        hostGestureReady = false; lastHostGestureState = -100
        hostInstalledUnlockToken = ""; unlockToken = ""
    }
    var lockingConnectionReady: Bool {
        peripheral?.state == .connected && (observing || hostConnection.isConnected(now: now))
    }
    func refreshLockingAvailability() {
        if peripheral?.state != .connected { policy.disconnected(now: now) }
        policy.setLockingEnabled(lockingConnectionReady)
        if !lockingConnectionReady { awaitingHost = nil }
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn { error = ""; discover() }
        else {
            if calibrating && central.state != .unknown && central.state != .resetting {
                calibrationFailed("蓝牙未就绪，校准未保存")
            }
            peripheral = nil; policy.disconnected(now: now); refreshLockingAvailability()
            returnWake.reset(); lastRSSIAt = nil
            activityCalibration.reset(now: now)
            invalidateHostGesture()
            error = central.state == .unauthorized ? "请在系统设置允许距离锁屏组件使用蓝牙" : "蓝牙未就绪，距离监测已暂停"
        }
    }
    func discover() {
        guard awake, central.state == .poweredOn else { return }
        if central.isScanning { return }
        if let peripheral, peripheral.state == .connected || peripheral.state == .connecting { return }
        lastDiscovery = now
        if let known = central.retrievePeripherals(withIdentifiers: [target]).first {
            connect(known)
        } else {
            central.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
            scanUntil = now + 5
        }
    }
    func connect(_ device: CBPeripheral) {
        guard device.identifier == target else { return }
        central.stopScan(); peripheral = device; device.delegate = self
        central.connect(device, options: nil)
    }
    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        guard peripheral.identifier == target, self.peripheral?.state != .connecting else { return }
        connect(peripheral)
    }
    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        connectedSince = now
        awaitingHost = nil
        activityCalibration.reset(now: now)
        invalidateHostGesture()
        returnWake.connectionChanged()
        policy.disconnected(now: now)
        rssiSamples = 0; lastRawRSSI = nil; lastRSSIAt = nil; lastRSSIRequestAt = -100
        if calibrating { calibration.begin(now: now) }
        peripheral.delegate = self
        tick() // Request the first RSSI without waiting for the periodic timer.
    }
    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        if calibrating { calibrationFailed("连接失败，校准未保存，请重新校准") }
        self.peripheral = nil; connectedSince = nil; lastRSSIAt = nil; policy.disconnected(now: now)
        refreshLockingAvailability()
        activityCalibration.reset(now: now)
    }
    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        if calibrating { calibrationFailed("校准期间戒指断连，原校准数据已保留") }
        returnWake.connectionChanged()
        activityCalibration.reset(now: now)
        invalidateHostGesture()
        self.peripheral = nil; connectedSince = nil; lastRSSIAt = nil; policy.disconnected(now: now)
        refreshLockingAvailability()
        discover() // Queue the OS reconnect immediately; do not wait for the next scan interval.
    }
    func peripheral(_ peripheral: CBPeripheral, didReadRSSI RSSI: NSNumber, error: Error?) {
        guard peripheral === self.peripheral, peripheral.state == .connected else { return }
        let value = RSSI.doubleValue
        guard error == nil, value.isFinite, (-100 ... -20).contains(value) else {
            lastRSSIAt = nil; policy.invalidateSignal(); returnWake.invalidateSignal(); return
        }
        rssiSamples += 1; lastRawRSSI = value
        let sampleTime = now
        let previousSampleTime = lastRSSIAt
        lastRSSIAt = sampleTime
        if canTrackActivity {
            let idle = keyboardMouseIdleSeconds()
            activityCalibration.activity(.keyboardMouse, at: sampleTime - idle, now: sampleTime)
            if canLearnFromActivity,
               let capture = activityCalibration.observe(value, now: sampleTime, eligible: true) {
                calibration = ProximityCalibration(baseline: capture.baseline)
                if let thresholds = calibration.thresholds() {
                    policy.setThresholds(lock: thresholds.lockRSSI, unlock: thresholds.unlockRSSI)
                }
                policy.invalidateSignal(); returnWake.reset()
                lastReportAt = -100
                emit(["event": "baseline_updated", "device": target.uuidString.lowercased(),
                      "revision": activityCalibration.revision, "baseline": capture.baseline,
                      "samples": capture.readings, "duration_seconds": capture.duration,
                      "sources": capture.sources])
            }
        }
        policy.sample(value, now: sampleTime)
        returnWake.sample(policy.smoothedRSSI ?? value, now: sampleTime, farThreshold: policy.lockRSSI)
        let collecting = calibrating
        if calibrating { calibration.observe(value, now: now) }
        var sampleReport: [String: Any] = ["event": "calibration_sample", "raw_rssi": value,
              "decision_rssi": policy.smoothedRSSI ?? value, "collecting": collecting,
              "lock": policy.lockRSSI, "unlock": policy.unlockRSSI]
        if let previousSampleTime {
            sampleReport["sample_interval_seconds"] = sampleTime - previousSampleTime
        }
        emit(sampleReport)
        if !calibrating { tick() } // Act on the callback immediately, not up to 250 ms later.
    }
    func fail(_ message: String) {
        unlockSecret.discard()
        unlockToken = ""
        error = message; policy.fail(); awaitingHost = nil
        emit(["event": "error", "error": message])
    }
    func calibrationFailed(_ message: String) {
        emit(["event": "error", "error": message]); exit(2)
    }
    func wakeDisplayForReturn() {
        // Use power management, not synthetic keys or the password path. A
        // one-shot declaration expires under the user's normal display timer.
        guard !observing, !calibrating, awake, lockedSession() == true,
              central.state == .poweredOn, peripheral?.state == .connected else { return }
        let result = IOPMAssertionDeclareUserActivity("ProxiMic Ring returned" as CFString,
                                                     kIOPMUserActiveLocal, &displayActivityAssertion)
        var report: [String: Any] = ["event": "return_wake", "success": result == kIOReturnSuccess, "result": result,
              "owned": policy.ownsLock,
              "unlock_ready": policy.awaitingGesture && unlockSecret.isPrepared && hostGestureReady && now - lastHostGestureState < 3
                              && !unlockToken.isEmpty && hostInstalledUnlockToken == unlockToken]
        if let returnConfirmedAt { report["return_to_wake_ms"] = (now - returnConfirmedAt) * 1000 }
        emit(report)
    }
    func calibrationTick() {
        guard awake, lockedSession() == false else {
            calibrationFailed("请保持电脑解锁，校准未保存"); return
        }
        if let start = calibration.startedAt {
            let elapsed = now - start
            if elapsed >= ProximityCalibration.duration {
                guard peripheral?.state == .connected, calibration.finish(now: now),
                      let baseline = calibration.baseline else {
                    calibrationFailed("10 秒内有效信号不足，原校准数据已保留，请重新校准"); return
                }
                emit(["event": "calibration_complete", "device": target.uuidString.lowercased(),
                      "baseline": baseline, "samples": calibration.readings, "duration_seconds": 10])
                exit(0)
            }
            emit(["event": "calibration_progress", "elapsed": elapsed, "samples": calibration.samples])
        } else if now - startedAt >= 15 {
            calibrationFailed("未能连接戒指，校准未保存，请确认戒指已连接")
        } else {
            emit(["event": "calibration_progress", "elapsed": 0, "samples": 0])
        }
    }
    func tick() {
        guard getppid() > 1 else { exit(0) }
        if observing && !calibrating && now - startedAt >= 12 { exit(0) }
        let locked = lockedSession()
        if now - lastPermission >= 5 { permission = permitted(); lastPermission = now }
        if central.isScanning && now >= scanUntil { central.stopScan() }
        let discoveryInterval = locked == true || returnWake.awaitingReturn ? 1.0 : 10.0
        if now - lastDiscovery >= discoveryInterval { discover() }
        let connected = peripheral?.state == .connected
        refreshLockingAvailability()
        if connected { if connectedSince == nil { connectedSince = now } } else { connectedSince = nil }
        // Request RSSI faster while locked/away; normal monitoring and manual
        // calibration request 2 Hz. Actual callback cadence can be much slower.
        // Allow small timer jitter without skipping a tick.
        let rssiInterval = !calibrating && (locked == true || returnWake.awaitingReturn) ? 0.25 : 0.5
        if awake && connected && now - lastRSSIRequestAt >= rssiInterval - 0.02 {
            lastRSSIRequestAt = now; peripheral?.readRSSI()
        }
        if calibrating { calibrationTick(); return }
        if !canTrackActivity { activityCalibration.reset(now: now) }
        else { activityCalibration.expire(now: now) }
        // Change boundaries only through the policy, which resets pending dwell.
        let learned = calibration.thresholds()
        policy.thresholdsReady = learned != nil
        if let learned { policy.setThresholds(lock: learned.lockRSSI, unlock: learned.unlockRSSI) }
        if locked != lastLock {
            lastReportAt = -100 // Install the snap route as soon as any lock is observed.
            if locked == true { awaitingHost = nil }
            if let locked, lastLock != nil { emit(["event": locked ? "locked" : "unlocked"]) }
            if locked == false && lastLock == true {
                error = ""; unlockSecret.discard(); invalidateHostGesture()
            }
            lastLock = locked
        }
        if locked == nil { unlockSecret.discard(); invalidateHostGesture() }
        // Turning Bluetooth off is not evidence that the wearer walked away.
        if let action = policy.step(now: now, locked: locked, connected: connected,
                                    permitted: permission && central.state == .poweredOn, awake: awake,
                                    locallyActive: locallyActive) {
            if observing { emit(["event": "would_act", "action": action == .lock ? "lock" : "unlock"]) }
            else if action == .lock {
                awaitingHost = now; emit(["event": "prepare_lock", "reason": policy.lastLockReason,
                                         "lock": policy.lockRSSI, "unlock": policy.unlockRSSI])
            }
        }
        if !policy.lockPending { awaitingHost = nil }
        if let pending = awaitingHost, now - pending > 2 { fail("主程序未响应，未执行锁屏；请重新开启此功能") }
        if policy.failed && error.isEmpty { fail("系统未确认锁定或解锁；请手动解锁后重新开启此功能") }
        // Manual/idle locks have no prepare_lock handshake. Arm once while
        // unlocked, and re-arm only after the previous lock has ended.
        if !observing && awake && permission && locked == false && !policy.failed {
            if !unlockSecret.prepare(locked: lockedSession(), load: { credential() }) {
                // A lock racing this tick keeps the already prepared credential.
                if lockedSession() == false { fail("无法准备解锁凭据，请在设置中允许访问已存密码后重新开启功能") }
            }
        }
        let didReturn = returnWake.step(now: now, locked: locked, connected: connected,
                           available: awake && permission && central.state == .poweredOn
                                      && policy.thresholdsReady && !policy.failed,
                           locallyActive: locallyActive, rawRSSI: lastRawRSSI, sampledAt: lastRSSIAt,
                           weakDisconnect: policy.disconnectEvidence == "weak",
                           farThreshold: policy.lockRSSI, nearThreshold: policy.unlockRSSI,
                           awaySamples: policy.awaySamples, lostDelay: policy.lostDelay)
        if didReturn {
            returnConfirmedAt = lastRSSIAt ?? now
            emit(["event": "return_confirmed", "decision_rssi": lastRawRSSI ?? 0,
                  "signal_source": "raw", "smoothed_rssi": policy.smoothedRSSI ?? 0,
                  "threshold": policy.unlockRSSI])
        } else if !returnWake.returnConfirmed { returnConfirmedAt = nil }
        let canOfferGesture = !observing && locked == true && policy.awaitingGesture
                              && unlockSecret.isPrepared && awake && permission && central.state == .poweredOn
        if canOfferGesture {
            if unlockToken.isEmpty { unlockToken = UUID().uuidString.lowercased() }
        } else { unlockToken = ""; hostInstalledUnlockToken = "" }
        let gestureReady = connected && hostGestureReady && now - lastHostGestureState < 3
        let unlockReady = canOfferGesture && gestureReady && hostInstalledUnlockToken == unlockToken
        let didWake = returnDisplay.step(returnConfirmed: returnWake.returnConfirmed, locked: locked == true,
                              requiresGesture: canOfferGesture,
                              available: awake && permission && central.state == .poweredOn && !policy.failed,
                              connected: connected, gestureReady: gestureReady,
                              token: unlockToken, installedToken: hostInstalledUnlockToken)
        if didWake {
            if observing { emit(["event": "would_act", "action": "wake_display"]) }
            else { wakeDisplayForReturn() }
        }
        if didReturn || didWake || now - lastReportAt >= 1 {
            lastReportAt = now
            let state = !permission ? "需要辅助功能权限" : !awake ? "电脑睡眠中" : policy.failed ? "自动操作已暂停" :
                    locked == true ? (canOfferGesture ? (unlockReady ? "手势已连接，请弹指（snap）解锁" :
                        "屏幕已锁定，正在恢复戒指手势连接") : "本次锁屏未准备解锁凭据，请先使用系统方式解锁") :
                    !connected ? "戒指已断连，正在重连；远离锁屏已暂停" :
                    !policy.lockingEnabled ? "主程序已断开，远离锁屏已暂停" :
                    locallyActive ? "检测到近期操作，暂缓远离锁屏" :
                    policy.armed ? "正在监测距离" : "请把戒指靠近电脑，正在确认信号"
            let note = learningNote
            var report: [String: Any] = ["event": "monitor", "permission": permission,
                "connected": connected, "armed": policy.armed, "owned": policy.ownsLock,
                "locking_enabled": policy.lockingEnabled,
                "reconnect_allowed": canOfferGesture,
                "gesture_eligible": canOfferGesture,
                "locked": locked ?? false, "lock_pending": awaitingHost != nil || policy.lockPending,
                "unlock_token": canOfferGesture && gestureReady ? unlockToken : "",
                "gesture_ready": gestureReady, "unlock_ready": unlockReady,
                "return_wake_pending": returnWake.returnConfirmed && !returnDisplay.wokeForReturn,
                "error": error, "observing": observing,
                "state": note.isEmpty ? state : "\(state) · \(note)",
                "calibration_samples": calibration.samples,
                "calibration_window": calibration.samples,
                "calibration_active": false,
                "activity_calibration_samples": activityCalibration.samples,
                "activity_calibration_active": canLearnFromActivity && !activityCalibration.activeSources(now: now).isEmpty,
                "activity_lock_protected": locallyActive,
                "rssi_samples": rssiSamples,
                "thresholds_ready": policy.thresholdsReady, "disconnect_evidence": policy.disconnectEvidence,
                "awaiting_return": returnWake.awaitingReturn,
                "return_confirmed": returnWake.returnConfirmed,
                "departure_samples": returnWake.departureSamples,
                "required_departure_samples": policy.awaySamples,
                "lock": (policy.lockRSSI * 10).rounded() / 10, "unlock": (policy.unlockRSSI * 10).rounded() / 10]
            if let baseline = calibration.baseline { report["baseline"] = (baseline * 10).rounded() / 10 }
            if let lastRSSIAt { report["rssi_age_seconds"] = now - lastRSSIAt }
            if connected, let raw = lastRawRSSI { report["raw_rssi"] = raw }
            if let rssi = policy.smoothedRSSI { report["rssi"] = Int(rssi.rounded()) }
            emit(report)
        }
    }
    /// Appended to the status line so the learned levels are visible in settings
    /// without adding another channel to the Qt side.
    var learningNote: String {
        if let baseline = calibration.baseline, calibration.qualified {
            return "本机基线 \(Int(baseline.rounded())) dBm"
        }
        return "尚未校准，请在设置中采集 10 秒基线"
    }
    func hostReady() {
        refreshLockingAvailability()
        guard !observing, let requested = awaitingHost, now - requested <= 2,
              policy.lockPending, lockingConnectionReady,
              lockedSession() == false, permitted(), awake else { return }
        if locallyActive {
            awaitingHost = nil; policy.cancelLockRequest(); return
        }
        awaitingHost = nil
        guard unlockSecret.prepare(locked: lockedSession(), load: { credential() }) else {
            fail("尚未允许读取解锁密码；请在设置中点击允许访问已存密码，再开启功能")
            return
        }
        guard lockedSession() == false, permitted(), awake else {
            fail("锁屏条件已改变，未执行锁屏"); return
        }
        if locallyActive || !lockingConnectionReady { policy.cancelLockRequest(); return }
        key(12, flags: [.maskControl, .maskCommand]) // macOS Lock Screen: Control-Command-Q
    }
    func unlock() {
        guard !observing, awake, policy.unlockPending, lockedSession() == true, permitted() else { fail("解锁条件已改变，未输入密码"); return }
        var assertion: IOPMAssertionID = 0
        IOPMAssertionDeclareUserActivity("ProxiMic ring returned" as CFString, kIOPMUserActiveLocal, &assertion)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
            guard let self, self.awake, self.policy.unlockPending, lockedSession() == true, permitted(),
                  self.peripheral?.state == .connected else { self?.fail("解锁条件已改变，请手动解锁"); return }
            guard var secret = self.unlockSecret.take(locked: lockedSession()), !secret.isEmpty,
                  let password = String(data: secret, encoding: .utf8), password.utf16.count <= 128 else {
                self.fail("本次锁屏没有可用的解锁凭据，请手动解锁后重新开启功能"); return
            }
            defer { secret.resetBytes(in: 0..<secret.count) }
            // Clear only the login screen's password entry, never a normal application.
            key(0, flags: .maskCommand); key(51)
            var units = Array(password.utf16)
            defer { _ = units.withUnsafeMutableBytes { $0.initializeMemory(as: UInt8.self, repeating: 0) } }
            for offset in stride(from: 0, to: units.count, by: 20) {
                guard lockedSession() == true,
                      let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
                      let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else { return }
                down.flags = []; up.flags = []
                units.withUnsafeBufferPointer {
                    down.keyboardSetUnicodeString(stringLength: min(20, units.count - offset),
                                                  unicodeString: $0.baseAddress! + offset)
                }
                down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
            }
            if lockedSession() == true { key(36) }
            emit(["event": "unlock_attempt"])
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let args = Array(CommandLine.arguments.dropFirst())
SecKeychainSetUserInteractionAllowed(false)
switch args.first {
case "status": status()
case "password": configurePassword()
case "authorize-password":
    guard lockedSession() == false else { status("请先手动解锁电脑，再允许密码访问"); exit(2) }
    app.activate(ignoringOtherApps: true)
    if var data = credential(allowInteraction: true) {
        data.resetBytes(in: 0..<data.count)
        status()
    } else { status("未允许读取已存密码，请重新尝试并在钥匙串弹窗中选择始终允许") }
case "forget":
    SecKeychainSetUserInteractionAllowed(true)
    let result = SecItemDelete(credentialQuery() as CFDictionary)
    status(result == errSecSuccess || result == errSecItemNotFound ? "" : "无法删除钥匙串密码")
case "authorize":
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    _ = AXIsProcessTrustedWithOptions(options); _ = CGRequestPostEventAccess()
    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
    status()
case "monitor", "observe":
    if args.first == "monitor" && lockedSession() != false {
        emit(["event": "error", "error": "请先使用系统方式解锁电脑，再开启响指解锁功能"]); exit(2)
    }
    guard (args.count == 4 || args.count == 5), let uuid = UUID(uuidString: args[1]),
          let away = Int(args[2]), let lost = Int(args[3]),
          away >= 1, away <= 60, lost >= 5, lost <= 120,
          (args.first == "observe" || credentialReady()) else { status("距离锁屏尚未就绪，请在设置中确认戒指、密码及密码访问权限"); exit(2) }
    let baseline = args.count == 5 ? Double(args[4]) : nil
    guard args.first == "observe" || (baseline != nil && baseline!.isFinite && (-100 ... -20).contains(baseline!)) else {
        status("请先在设置中完成当前戒指的 10 秒校准"); exit(2)
    }
    let monitor = Monitor(target: uuid, policy: PresencePolicy(awaySamples: away, lostDelay: Double(lost)),
                          observing: args.first == "observe", baseline: baseline)
    monitor.tick() // Prepare before the user can lock immediately after enabling.
    withExtendedLifetime(monitor) { app.run() }
case "calibrate":
    guard args.count == 2, let uuid = UUID(uuidString: args[1]), lockedSession() == false else {
        emit(["event": "error", "error": "请在电脑解锁时连接戒指并开始校准"]); exit(2)
    }
    let monitor = Monitor(target: uuid, policy: PresencePolicy(), observing: true, calibrating: true)
    withExtendedLifetime(monitor) { app.run() }
default: status("无效的组件命令"); exit(2)
}
