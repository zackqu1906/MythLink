import QtQuick
import QtQuick.Effects

// Figma 170:2787 / 170:2801 / 170:2815 / 170:2829.
Item {
    id: root
    readonly property bool cancelled: proximityLock ? proximityLock.lockNoticeState === "cancelled" : false
    readonly property int seconds: proximityLock ? proximityLock.lockCountdownSeconds : 0
    readonly property int cardWidth: cancelled ? 230 : 300
    readonly property int cardHeight: cancelled ? 50 : 66
    implicitWidth: cardWidth + 36
    implicitHeight: cardHeight + 36
    width: implicitWidth; height: implicitHeight
    FigmaTheme { id: theme }

    Rectangle {
        id: card
        objectName: "proximityLockCard"
        x: 18; y: 12; width: root.cardWidth; height: root.cardHeight
        radius: root.cancelled ? 14 : 16
        color: Qt.rgba(1, 1, 1, 0.78)
        border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.7)
        layer.enabled: GraphicsInfo.api !== GraphicsInfo.Software
        layer.effect: MultiEffect {
            shadowEnabled: true; shadowColor: "#000000"
            shadowOpacity: root.cancelled ? 0.07 : 0.08
            shadowBlur: 0.6; shadowVerticalOffset: root.cancelled ? 5 : 6
        }

        Item {
            id: ring
            objectName: "proximityLockRing"
            visible: !root.cancelled
            x: 11; anchors.verticalCenter: parent.verticalCenter
            width: 42; height: 42
            Image { source: "../assets/figma/prelock-ring-base.svg"; width: 42; height: 42 }
            Image {
                objectName: "proximityLockArc"
                visible: root.seconds === 5 || root.seconds === 3 || root.seconds === 1
                source: "../assets/figma/prelock-ring-" + (root.seconds === 3 ? "3" : root.seconds === 1 ? "1" : "5") + ".svg"
                x: root.seconds === 3 ? 8.6565 : root.seconds === 1 ? 21 : 0
                width: root.seconds === 3 ? 33.3435 : root.seconds === 1 ? 19.9722 : 42
                height: root.seconds === 1 ? 15.4192 : 42
            }
            // The intermediate 4s / 2s progress is data-driven; supplied Figma
            // keyframes above keep their original SVGs and intrinsic geometry.
            Canvas {
                anchors.fill: parent
                visible: root.seconds === 4 || root.seconds === 2
                property int remaining: root.seconds
                onRemainingChanged: requestPaint()
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.lineWidth = 3
                    ctx.strokeStyle = remaining > 3 ? "#2DA060" : "#C78C29"
                    ctx.beginPath()
                    ctx.arc(21, 21, 19.5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * remaining / 5)
                    ctx.stroke()
                }
            }
            Text {
                objectName: "proximityLockSeconds"
                anchors.centerIn: parent
                text: root.seconds > 0 ? root.seconds : ""
                color: "#12141C"; font.family: ".AppleSystemUIFont"; font.pixelSize: 16
            }
        }
        Column {
            visible: !root.cancelled
            x: 63; anchors.verticalCenter: parent.verticalCenter; spacing: 2
            Text {
                height: 24; verticalAlignment: Text.AlignVCenter
                text: "识别到离开"; color: "#12141C"
                font.family: theme.family; font.pixelSize: 14
            }
            Item {
                width: 184; height: 20
                Text {
                    // Qt's pixelSize is integral; scale preserves Figma's 11.5 px
                    // independently of the screen's point-to-pixel conversion.
                    height: 40; scale: 0.5; transformOrigin: Item.TopLeft
                    verticalAlignment: Text.AlignVCenter
                    text: "即将锁定屏幕，返回将自动取消"; color: "#6B7380"
                    font.family: theme.family; font.pixelSize: 23
                }
            }
        }
        Image {
            visible: root.cancelled
            source: "../assets/figma/prelock-check.svg"
            x: 11; anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18
        }
        Text {
            visible: root.cancelled
            x: 38; anchors.verticalCenter: parent.verticalCenter; height: 24
            verticalAlignment: Text.AlignVCenter
            text: "已取消锁定"; color: "#2E8552"
            font.family: theme.family; font.pixelSize: 13
        }
        Image {
            objectName: "proximityLockIcon"
            source: "../assets/figma/" + (root.cancelled ? "prelock-unlock.svg" : "prelock-lock.svg")
            anchors.right: parent.right; anchors.rightMargin: 13
            anchors.verticalCenter: parent.verticalCenter; width: 17; height: 17
        }
    }
}
