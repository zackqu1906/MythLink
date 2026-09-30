import QtQuick
import QtQuick.Controls

Item {
    id: appIcon
    required property string bundle
    property string applicationPath: ""
    property string label: ""
    readonly property bool ready: nativeIcon.status === Image.Ready
    implicitWidth: 44
    implicitHeight: 44
    Rectangle {
        anchors.fill: parent; anchors.margins: 4; radius: 10
        visible: !appIcon.ready
        color: "#EEF1FA"
        Label { anchors.centerIn: parent; text: appIcon.label.slice(0, 1); color: "#687286"; font.pixelSize: 20 }
    }
    Image {
        id: nativeIcon
        objectName: "nativeApplicationIcon"
        anchors.fill: parent
        source: appIcon.bundle ? "image://applicationIcons/" + encodeURIComponent(appIcon.bundle) + "/" + encodeURIComponent(appIcon.applicationPath) : ""
        sourceSize.width: 128; sourceSize.height: 128
        asynchronous: true
        fillMode: Image.PreserveAspectFit
    }
}
