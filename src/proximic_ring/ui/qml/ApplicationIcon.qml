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
        anchors.fill: parent; anchors.margins: appIcon.width <= 24 ? 1 : 4; radius: appIcon.width <= 24 ? 4 : 10
        visible: !appIcon.ready
        color: "#EEF1FA"
        Label { anchors.centerIn: parent; text: appIcon.label.slice(0, 1); color: "#687286"; font.pixelSize: Math.min(20, appIcon.width / 2) }
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
