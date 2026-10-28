import QtQuick

Rectangle {
    id: preview
    required property var gestures
    property string functionAsset: ""
    property real maximumWidth: 1000
    readonly property var assignedGestures: gestures.filter(function(name) { return name.length > 0 })
    implicitWidth: Math.min(maximumWidth, Math.max(59, (assignedGestures.length + (functionAsset ? 1 : 0)) * 25 + 9))
    implicitHeight: contents.implicitHeight + 8
    radius: 4
    color: "#F0F4FC"
    Flow {
        id: contents
        anchors.centerIn: parent
        width: parent.width - 10
        spacing: 1
        Repeater {
            model: preview.assignedGestures
            Item {
                required property string modelData
                readonly property bool doubleGesture: modelData.indexOf("double-") === 0
                width: 24; height: 24
                Image {
                    objectName: "voicePreviewGesture_" + parent.modelData
                    anchors.centerIn: parent
                    width: 20; height: 20
                    source: "../assets/figma/voice-hud/gesture-" + parent.modelData.replace(/^double-/, "") + ".svg"
                    fillMode: Image.PreserveAspectFit
                }
                Text {
                    objectName: "voicePreviewCount_" + parent.modelData
                    visible: parent.doubleGesture
                    anchors.right: parent.right; anchors.bottom: parent.bottom
                    text: "2"; font.pixelSize: 10; font.bold: true; color: "#0019CE"
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
