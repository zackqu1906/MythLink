import Foundation

/// Display-only preferences; 80% uses the shared compact 32 pt palette height.
struct VoiceHudAppearance: Equatable {
    private(set) var scalePercent = 80
    var scale: CGFloat { CGFloat(scalePercent) / 100 }

    mutating func apply(_ value: Any?) {
        guard let number = value as? NSNumber, number.doubleValue.isFinite,
              (80...140).contains(number.doubleValue) else { return }
        scalePercent = number.intValue
    }
}

/// Display metadata only. The host remains the sole owner of gesture routing.
struct VoiceHudBindings: Equatable {
    static let names = [
        "tap": "轻点", "double-tap": "双击 tap", "swipe-left": "左滑", "swipe-right": "右滑",
        "swipe-up": "上滑", "swipe-down": "下滑", "snap": "响指",
        "circle-clockwise": "顺时针", "circle-counterclockwise": "逆时针",
        "index-pinch": "食指捏合", "middle-pinch": "中指捏合", "clench": "握拳",
        "double-index-pinch": "双食指捏合", "double-middle-pinch": "双中指捏合"
    ]
    private(set) var actions: [String: [String]] = [
        "undo": ["swipe-left"], "confirm": ["double-tap"], "end": ["tap"], "switch_mode": ["swipe-right"],
        "send": ["swipe-up"], "polish": ["swipe-down"]
    ]

    static func assetName(_ gesture: String) -> String {
        return "gesture-" + (gesture.hasPrefix("double-") ? String(gesture.dropFirst(7)) : gesture)
    }

    mutating func apply(_ value: Any?) {
        let legacy = Set(actions.keys).subtracting(["send", "polish"])
        guard let payload = value as? [String: [String]],
              [Set(actions.keys), legacy, legacy.subtracting(["end"])].contains(Set(payload.keys)),
              payload.values.allSatisfy({ $0.count <= Self.names.count && $0.allSatisfy { $0.isEmpty || Self.names[$0] != nil } }) else { return }
        var updated = payload.mapValues { $0.filter { !$0.isEmpty } }
        if updated["end"] == nil { updated["end"] = updated["confirm"] }
        let occupied = Set(updated.values.flatMap { $0 })
        for (action, gesture) in [("send", "swipe-up"), ("polish", "swipe-down")] where updated[action] == nil {
            updated[action] = occupied.contains(gesture) ? [] : [gesture]
        }
        actions = updated
    }
}
