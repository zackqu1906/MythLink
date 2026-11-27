import QtQuick

Rectangle {
    id: panel
    property string statusText
    property string statusIcon
    property string suggestion
    property var actions: []
    property bool processing
    property real uiScale: 0.8
    readonly property url assets: Qt.resolvedUrl("../assets/figma/voice-hud/")
    readonly property real rowHeight: Math.round(40 * uiScale)
    readonly property real inset: Math.round(12 * uiScale)
    readonly property real actionsWidth: actions.length * 84 * uiScale
        + Math.max(0, actions.length - 1) * 6 * uiScale
    implicitWidth: Math.ceil(2 * inset + Math.max(150 * uiScale, status.implicitWidth + 50 * uiScale)
                             + actionsWidth + (actions.length ? inset : 0))
    implicitHeight: rowHeight * (suggestion ? 2 : 1)
    radius: 10
    color: palette.window
    border.color: Qt.rgba(palette.windowText.r, palette.windowText.g, palette.windowText.b, 0.18)
    SystemPalette { id: palette }

    Row {
        id: chips
        anchors.right: parent.right
        anchors.rightMargin: panel.inset
        y: (panel.rowHeight - height) / 2
        spacing: 6 * panel.uiScale
        Repeater {
            model: panel.actions
            delegate: Rectangle {
                id: chip
                required property var modelData
                readonly property var gestures: modelData.gestures
                width: 84 * panel.uiScale
                height: 32 * panel.uiScale
                radius: 4 * panel.uiScale
                color: Qt.rgba(palette.windowText.r, palette.windowText.g, palette.windowText.b, 0.06)
                Accessible.name: modelData.description
                Row {
                    anchors.centerIn: parent
                    spacing: 3 * panel.uiScale
                    Repeater {
                        model: chip.gestures
                        delegate: Item {
                            required property string modelData
                            width: (chip.gestures.length > 1 ? 17 : 22) * panel.uiScale
                            height: 26 * panel.uiScale
                            Image {
                                anchors.centerIn: parent
                                width: parent.width; height: width
                                source: panel.assets + "gesture-" + parent.modelData.replace(/^double-/, "") + ".svg"
                                fillMode: Image.PreserveAspectFit
                            }
                            Text {
                                visible: parent.modelData.indexOf("double-") === 0
                                anchors.right: parent.right; anchors.bottom: parent.bottom
                                text: "2"; font.bold: true; font.pixelSize: 9 * panel.uiScale
                                font.family: "Microsoft YaHei UI"
                                color: palette.highlight
                            }
                        }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: chip.modelData.label; color: palette.windowText
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: Math.max(11, Math.round(14 * panel.uiScale))
                    }
                }
            }
        }
    }
    Image {
        id: icon
        x: panel.inset; y: (panel.rowHeight - height) / 2
        width: 26 * panel.uiScale; height: width
        source: panel.statusIcon ? panel.assets + panel.statusIcon + ".svg" : ""
        fillMode: Image.PreserveAspectFit
        RotationAnimator on rotation {
            from: 0; to: 360; duration: 1050; loops: Animation.Infinite
            running: panel.processing && panel.visible
        }
        onSourceChanged: rotation = 0
    }
    Image {
        id: dots
        anchors.left: icon.right; anchors.leftMargin: 4 * panel.uiScale
        anchors.verticalCenter: icon.verticalCenter
        visible: panel.processing
        width: visible ? 14 * panel.uiScale : 0; height: 10 * panel.uiScale
        source: panel.assets + "processing-dots.svg"; fillMode: Image.PreserveAspectFit
    }
    Text {
        id: status
        anchors.left: dots.right; anchors.leftMargin: 4 * panel.uiScale
        anchors.right: chips.left; anchors.rightMargin: panel.inset
        y: (panel.rowHeight - height) / 2
        text: panel.statusText
        font.family: "Microsoft YaHei UI"
        textFormat: Text.PlainText; elide: Text.ElideRight
        font.pixelSize: Math.max(11, Math.round(13 * panel.uiScale))
        color: panel.statusIcon === "listening-waveform" ? palette.highlight : palette.windowText
    }
    Text {
        visible: !!panel.suggestion
        x: panel.inset; width: panel.width - 2 * x
        y: panel.rowHeight; height: panel.rowHeight
        verticalAlignment: Text.AlignVCenter
        text: panel.suggestion; textFormat: Text.PlainText; elide: Text.ElideRight
        font.family: "Microsoft YaHei UI"
        color: palette.windowText
        font.pixelSize: Math.max(12, Math.round(15 * panel.uiScale))
    }
}
