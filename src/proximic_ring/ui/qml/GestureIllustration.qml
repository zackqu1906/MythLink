import QtQuick

// Each file is an unmodified Figma export, including its circular background.
Item {
    id: illustration
    required property string gesture
    property bool locked: false
    objectName: "gestureIllustration_" + gesture
    implicitWidth: 83
    implicitHeight: 83
    Image {
        anchors.fill: parent
        source: "../assets/figma/gesture-" + (illustration.gesture === "double-tap" ? "tap"
            : illustration.gesture === "double-index-pinch" ? "index-pinch"
            : illustration.gesture === "double-middle-pinch" ? "middle-pinch" : illustration.gesture) + ".png"
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
        opacity: illustration.locked ? 0.48 : 1
    }
    Text {
        visible: illustration.gesture === "double-index-pinch" || illustration.gesture === "double-middle-pinch"
        anchors.right: parent.right; anchors.top: parent.top
        text: "2×"; color: "#315AB5"; font.bold: true; font.pixelSize: 14
    }
}
