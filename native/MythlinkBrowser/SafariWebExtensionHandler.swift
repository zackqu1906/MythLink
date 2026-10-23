import Foundation
import SafariServices

@objc(SafariWebExtensionHandler)
final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        func reply(_ data: [String: Any]) {
            let item = NSExtensionItem()
            item.userInfo = [SFExtensionMessageKey: data]
            context.completeRequest(returningItems: [item], completionHandler: nil)
        }
        guard let item = context.inputItems.first as? NSExtensionItem,
              var data = item.userInfo?[SFExtensionMessageKey] as? [String: Any],
              let path = data.removeValue(forKey: "path") as? String,
              ["/connect", "/exchange"].contains(path),
              data["version"] as? Int == 1 else {
            reply(["error": "invalid_message"]); return
        }
        let token = data.removeValue(forKey: "token") as? String ?? ""
        guard let body = try? JSONSerialization.data(withJSONObject: data), body.count <= 262144 else {
            reply(["error": "invalid_message"]); return
        }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:47837" + path)!)
        request.httpMethod = "POST"
        request.httpBody = body
        request.timeoutInterval = 2
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("1", forHTTPHeaderField: "X-Mythlink-Native")
        request.setValue(token, forHTTPHeaderField: "Authorization")
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = [:]
        let session = URLSession(configuration: config)
        session.dataTask(with: request) { bytes, response, error in
            defer { session.finishTasksAndInvalidate() }
            guard error == nil, (response as? HTTPURLResponse)?.statusCode == 200,
                  let bytes = bytes, bytes.count <= 262144,
                  let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
                reply(["error": "desktop_unavailable"]); return
            }
            reply(object)
        }.resume()
    }
}
