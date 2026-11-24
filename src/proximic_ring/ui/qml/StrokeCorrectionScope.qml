import QtQuick

Rectangle {
    id: scope
    objectName: "strokeCorrectionScope"
    required property var touchpad
    property bool highlighted: false
    property string message: ""
    property string placeholder: "请在目标文本框中使用修正"
    readonly property alias text: label.text
    implicitHeight: label.implicitHeight + 20
    radius: 8
    color: highlighted ? "#234CDB" : "#E9EFFF"
    border.color: highlighted ? "#234CDB" : "#AABCF2"
    clip: true

    Text {
        id: label
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
        text: scope.message || (scope.touchpad.handwritingCorrectionText.length
            ? "修正用词「" + scope.touchpad.handwritingCorrectionText + "」"
            : scope.touchpad.handwritingCorrectionMessage || scope.placeholder)
        font.pixelSize: 16
        font.weight: Font.DemiBold
        color: scope.highlighted ? "white" : "#234CDB"
        wrapMode: Text.WrapAnywhere
        textFormat: Text.PlainText
        Accessible.role: Accessible.StaticText
        Accessible.name: text
    }
}
