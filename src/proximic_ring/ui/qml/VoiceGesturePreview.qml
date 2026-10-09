import QtQuick

Rectangle {
    id: preview
    required property var gestures
    property string functionAsset: ""
    readonly property var assignedGestures: gestures.filter(function(name) { return name.length > 0 })
    implicitWidth: Math.max(59, contents.width + 10)
    implicitHeight: 32
    radius: 4
    color: "#F0F4FC"
    Row {
        id: contents
        anchors.centerIn: parent
        spacing: 1
        Repeater {
            model: preview.assignedGestures
            Item {
                required property string modelData
                width: 24; height: 24
                Image {
                    objectName: "voicePreviewGesture_" + parent.modelData
                    anchors.centerIn: parent
                    width: 20; height: 20
                    source: "../assets/figma/voice-hud/gesture-" + parent.modelData + ".svg"
                    fillMode: Image.PreserveAspectFit
                }
            }
        }
        Item {
            visible: preview.functionAsset.length > 0
            width: 24; height: 24
            Image {
                objectName: "voicePreviewFunction"
                anchors.centerIn: parent
                width: preview.functionAsset === "function-edit" ? 18 : 20
                height: width
                source: preview.functionAsset.length > 0 ? "../assets/figma/voice-hud/" + preview.functionAsset + ".svg" : ""
                fillMode: Image.PreserveAspectFit
            }
        }
    }
}
