import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: notice
    property string message: ""
    property string actionText: "去开启"
    property color ink: "#9A6817"
    signal activated()
    spacing: 10
    visible: message.length > 0
    Label {
        text: "!"; color: notice.ink; font.pixelSize: 15; font.bold: true
        Layout.alignment: Qt.AlignTop; Layout.topMargin: 10
        Accessible.ignored: true
    }
    Label {
        objectName: notice.objectName + "Text"
        Layout.fillWidth: true
        text: notice.message; textFormat: Text.PlainText
        color: notice.ink; font.pixelSize: 13; wrapMode: Text.Wrap
        Accessible.name: notice.message
    }
    UiAction {
        objectName: notice.objectName + "Button"
        visible: notice.actionText.length > 0
        text: notice.actionText; quiet: true
        onClicked: notice.activated()
    }
}
