import QtQuick

Item {
    id: toast
    width: 248; height: 72
    clip: true
    property var gestures: []
    property int cardHeight: 72
    property int cardGap: 8
    readonly property int pushDuration: 160
    readonly property int fadeDuration: 160

    // Entries are chronological. Update incrementally so an old card never
    // gets recreated (and its animation never restarts) when another arrives.
    ListModel { id: activeGestures }
    onGesturesChanged: {
        if (gestures.length === 0) {
            activeGestures.clear()
            return
        }
        while (activeGestures.count > 0 && activeGestures.get(0).serial < gestures[0].serial)
            activeGestures.remove(0)
        for (var i = activeGestures.count; i < gestures.length; ++i)
            activeGestures.append(gestures[i])
    }

    Repeater {
        model: activeGestures
        Rectangle {
            id: card
            objectName: "gestureTriggerCard"
            required property int index
            required property int serial
            required property string label
            required property string name
            required property int lifetimeMs
            property string gestureLabel: label
            property string gestureName: name
            property bool positioned: false
            property real stackOffset: (activeGestures.count - index - 1) * (toast.cardHeight + toast.cardGap)
            width: toast.width; height: toast.cardHeight
            // Animate distance from the fixed bottom edge, not local y: the
            // native canvas grows upward immediately without jumping the cards.
            y: toast.height - height - stackOffset
            z: serial
            radius: 16
            color: "#D9232933"
            border.width: 1
            border.color: "#24FFFFFF"
            transformOrigin: Item.Center

            Behavior on stackOffset {
                enabled: card.positioned
                NumberAnimation { duration: toast.pushDuration; easing.type: Easing.OutCubic }
            }
            Component.onCompleted: {
                positioned = true
                lifetime.start()
            }
            SequentialAnimation {
                id: lifetime
                PauseAnimation { duration: Math.max(0, card.lifetimeMs - toast.fadeDuration) }
                ParallelAnimation {
                    NumberAnimation {
                        target: card; property: "opacity"; from: 1; to: 0
                        duration: Math.min(card.lifetimeMs, toast.fadeDuration)
                        easing.type: Easing.InOutQuad
                    }
                    NumberAnimation {
                        target: card; property: "scale"; from: 1; to: 0.97
                        duration: Math.min(card.lifetimeMs, toast.fadeDuration)
                        easing.type: Easing.InQuad
                    }
                }
            }

            Rectangle {
                x: 18; anchors.verticalCenter: parent.verticalCenter
                width: 8; height: 8; radius: 4
                color: "#97DBCE"
            }
            Column {
                x: 40; width: parent.width - x - 18
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                Text {
                    objectName: "gestureTriggerLabel"
                    width: parent.width
                    text: card.gestureLabel
                    color: "#FFFFFF"
                    font.pixelSize: 17; font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    width: parent.width
                    text: card.gestureName
                    color: "#B8C2CF"
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }
            }
        }
    }
}
