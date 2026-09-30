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
        source: "../assets/figma/gesture-" + illustration.gesture + ".png"
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
        opacity: illustration.locked ? 0.48 : 1
    }
}
