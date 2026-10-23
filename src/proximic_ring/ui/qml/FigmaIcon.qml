import QtQuick

// Keep original exported SVGs at their intrinsic dimensions inside the slot.
Item {
    id: icon
    property string symbol: "home"
    property bool selected: false
    property url asset: ""
    // Qt rounds fractional SVG bounds; callers can retain the exact export size.
    property size intrinsicSize: Qt.size(-1, -1)
    property real assetRotation: symbol === "chevron-right" ? -90 : symbol === "chevron-left" ? 90 : 0
    readonly property var assets: ({
        home: selected ? "383-9247-icon-phosphor-home20" : "567-32820-icon-phosphor-home20",
        mic: "383-9247-frame", microphone: "383-9247-frame", voice: "383-9247-frame",
        gesture: "383-9247-frame3", pointer: selected ? "touch-selected-frame" : "425-5128-frame",
        lock: selected ? "lock-selected-frame" : "425-5134-frame",
        mail: "383-9247-icon-phosphor-envelope-simple20", settings: "383-9247-icon1", help: "383-9247-icon2",
        ring: "567-32820-frame2", wifi: "383-9247-wifi",
        history: "383-9247-icon-phosphor-file-text-regular24", clock: "383-9247-icon-phosphor-clock-regular24",
        operation: "383-9247-icon-phosphor-confetti-regular24", copy: "383-9247-icon-phosphor-copy16",
        account: "259-7570-icon-phosphor-user20", close: "555-14138-icon-phosphor-close20", trash: "383-9247-trash", more: "383-9247-more-horizontal",
        "preference-settings": "259-7570-icon-phosphor-gear-six20", "preference-help": "259-7570-icon-phosphor-question20", "preference-close": "259-7570-icon-phosphor-close20",
        "chevron-down": "383-9247-frame1", "chevron-right": "383-9247-frame2", "chevron-left": "383-9247-frame2",
        snap: "383-10748-icon", tap: "567-32820-icon3",
        "swipe-up": "567-32820-up", "swipe-down": "567-32820-down",
        "swipe-left": "567-32820-left", "swipe-right": "567-32820-right"
    })
    implicitWidth: 20
    implicitHeight: 20
    Image {
        anchors.centerIn: parent
        width: icon.intrinsicSize.width >= 0 ? icon.intrinsicSize.width : implicitWidth
        height: icon.intrinsicSize.height >= 0 ? icon.intrinsicSize.height : implicitHeight
        source: icon.asset.toString().length ? icon.asset : icon.assets[icon.symbol] ? "../assets/figma/redesign-" + icon.assets[icon.symbol] + ".svg" : ""
        rotation: icon.assetRotation
        smooth: true
    }
}
