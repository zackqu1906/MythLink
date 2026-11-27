import QtQuick

Rectangle {
    id: panel
    property string statusText
    property string partialText
    property string hintText
    property bool listening
    property real uiScale: 0.8
    readonly property int padding: Math.round(12 * uiScale)
    implicitWidth: Math.max(210 * uiScale, Math.min(420 * uiScale,
        Math.max(status.implicitWidth + 14 * uiScale, partial.implicitWidth, hints.implicitWidth) + 2 * padding))
    implicitHeight: body.implicitHeight + 2 * padding
    radius: 10 * uiScale
    color: "#fafbff"
    border.color: "#cad3e9"
    Column {
        id: body
        x: panel.padding; y: panel.padding
        width: panel.width - 2 * panel.padding
        spacing: 4 * panel.uiScale
        Row {
            id: statusRow
            spacing: 6 * panel.uiScale
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 8 * panel.uiScale; height: width; radius: width / 2
                color: panel.listening ? "#2854ce" : "#68778e"
            }
            Text {
                id: status
                width: Math.max(1, Math.min(implicitWidth, body.width - 14 * panel.uiScale))
                text: panel.statusText; textFormat: Text.PlainText
                font.pixelSize: Math.round(16 * panel.uiScale); font.bold: true
                color: "#152232"; wrapMode: Text.Wrap
            }
        }
        Text {
            id: partial
            width: body.width; visible: text.length > 0
            text: panel.partialText; textFormat: Text.PlainText
            elide: Text.ElideLeft; font.pixelSize: Math.round(16 * panel.uiScale); color: "#152232"
        }
        Text {
            id: hints
            width: body.width; visible: text.length > 0
            text: panel.hintText; textFormat: Text.PlainText
            wrapMode: Text.Wrap; font.pixelSize: Math.round(14 * panel.uiScale); color: "#4f5d71"
        }
    }
}
