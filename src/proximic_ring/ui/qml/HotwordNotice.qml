import QtQuick

Rectangle {
    id: notice
    property string message: ""
    property int widthLimit: 480
    implicitWidth: Math.min(480, widthLimit, Math.ceil(label.implicitWidth) + 24)
    implicitHeight: Math.max(30, Math.ceil(label.implicitHeight) + 12)
    radius: 10
    color: Qt.rgba(palette.window.r, palette.window.g, palette.window.b, 0.94)
    border.color: Qt.rgba(palette.windowText.r, palette.windowText.g, palette.windowText.b, 0.14)
    Text {
        id: label
        x: 12
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(1, notice.implicitWidth - 24)
        text: notice.message
        color: notice.palette.windowText
        font.pixelSize: 13
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }
}
