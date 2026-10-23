// A transport-only integration harness: never creates NSApplication or touches another app.
import Foundation
import Darwin

let transport = IPCClient()
var epoch = ""
var requestCount = 0
let duration = Double(ProcessInfo.processInfo.environment["PROXIMIC_IME_HARNESS_SECONDS"] ?? "10") ?? 10
func state(_ requestID: String? = nil) {
    var response: [String: Any] = ["type": "state", "epoch": epoch, "ready": true, "client_id": "ipc-test",
                                  "utterance_id": "", "phase": "idle", "application": "com.proximic.test",
                                  "revision": 0, "edit_requested": false, "raw": "", "error": "",
                                  "original": "中文😀\n上下文", "selection": [8, 0], "context_complete": true,
                                  "capabilities": ["marked_text": false, "context_complete": true, "document_access": false,
                                                   "range_replacement": "unavailable", "selection_restore": false]]
    if let requestID { response["request_id"] = requestID }
    transport.send(response)
}
transport.onMessage = { message in
    if message["type"] as? String == "welcome", message["protocol"] as? Int == 1, let value = message["epoch"] as? String {
        epoch = value
        print("SWIFT_IPC_WELCOME")
        state()
        transport.send(["type": "wechat_key", "epoch": epoch, "client_id": "ipc-test",
                        "utterance_id": "test", "request_id": "key-test", "application": "com.tencent.xinWeChat",
                        "command": "select_all", "count": 0, "event_tag": 123])
    } else if message["epoch"] as? String == epoch, message["type"] as? String == "ping" {
        requestCount += 1
        state(message["request_id"] as? String)
        print("SWIFT_IPC_PING \(requestCount)")
    } else if message["epoch"] as? String == epoch, message["type"] as? String == "polish" {
        guard message["restore_raw_on_undo"] as? Bool == false else { exit(2) }
        transport.send(["type": "polish_requested", "epoch": epoch, "client_id": "ipc-test",
                        "utterance_id": "polish-test", "revision": 7, "text": "本句中文😀"])
    } else if message["epoch"] as? String == epoch, message["type"] as? String == "polish_result" {
        guard message["client_id"] as? String == "ipc-test",
              message["utterance_id"] as? String == "polish-test",
              message["revision"] as? Int == 7,
              message["text"] as? String == "润色后的本句😀" else { exit(3) }
        transport.send(["type": "settled", "epoch": epoch, "client_id": "ipc-test",
                        "utterance_id": "polish-test", "revision": 8,
                        "phase": "dictated", "text": "润色后的本句😀"])
        print("SWIFT_IPC_POLISH_RESULT")
    } else if message["epoch"] as? String == epoch, message["type"] as? String == "hotwords_learned" {
        guard message["client_id"] as? String == "ipc-test", message["utterance_id"] as? String == "edit-test",
              message["revision"] as? Int == 2, message["words"] as? [String] == ["张玮", "Kubernetes"] else { exit(4) }
        transport.send(["type": "pong", "epoch": epoch, "echo": "hotwords_learned"])
        print("SWIFT_IPC_HOTWORDS_LEARNED")
    } else if message["epoch"] as? String == epoch, message["type"] as? String == "reset" {
        transport.send(["type": "pong", "epoch": epoch, "echo": "中文😀\n上下文"])
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { transport.stop(); exit(0) }
    }
}
transport.start()
RunLoop.main.run(until: Date().addingTimeInterval(duration))
transport.stop()
print("SWIFT_IPC_TIMEOUT \(requestCount)")
exit(1)
