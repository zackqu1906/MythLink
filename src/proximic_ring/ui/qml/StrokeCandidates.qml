import QtQuick

Rectangle {
    property string code: ""
    property var candidates: []
    property int selected: 0
    color: "#F8FAFF"
    border.color: "#B8C7E9"
    border.width: 1
    radius: 12

    Column {
        anchors.fill: parent
        anchors.margins: 11
        spacing: 5
        Text { text: code; color: "#314F98"; font.pixelSize: 15 }
        Row {
            spacing: 10
            Repeater {
                model: candidates
                delegate: Text {
                    required property int index
                    required property string modelData
                    text: modelData
                    color: index === selected ? "#0735BA" : "#263250"
                    font.pixelSize: 24
                    font.bold: index === selected
                }
            }
        }
    }
}
