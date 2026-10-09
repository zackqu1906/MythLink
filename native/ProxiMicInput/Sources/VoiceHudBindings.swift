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
        "tap": "轻点", "swipe-left": "左滑", "swipe-right": "右滑",
        "swipe-up": "上滑", "swipe-down": "下滑", "snap": "响指",
        "circle-clockwise": "顺时针", "circle-counterclockwise": "逆时针",
        "index-pinch": "食指捏合", "middle-pinch": "中指捏合", "clench": "握拳"
    ]
    private(set) var actions: [String: [String]] = [
        "undo": ["swipe-left"], "confirm": ["tap"], "switch_mode": ["swipe-right"]
    ]

    mutating func apply(_ value: Any?) {
        guard let payload = value as? [String: [String]], Set(payload.keys) == Set(actions.keys),
              payload.values.allSatisfy({ $0.count <= 2 && $0.allSatisfy { $0.isEmpty || Self.names[$0] != nil } }),
              payload["confirm"]?.contains(where: { !$0.isEmpty }) == true else { return }
        actions = payload.mapValues { $0.filter { !$0.isEmpty } }
    }
}
