import QtQuick
import QtQuick.Controls

FigmaAction {
    id: control
    property string asset: "more"
    property string hint: ""
    property bool filled: false
    property bool menuOpen: false
    quiet: true
    implicitWidth: 24; implicitHeight: 24
    leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
    Accessible.name: hint
    ToolTip.visible: hovered && !pressed && !menuOpen
    ToolTip.delay: 500
    ToolTip.text: hint
    background: Rectangle {
        radius: 4
        color: control.down ? "#D9DFF9" : control.hovered ? "#F6F7FD" : control.filled ? "#EDEEF6" : "transparent"
        border.width: control.visualFocus ? 2 : 0; border.color: "#0225BA"
    }
    contentItem: Item {
        implicitWidth: 16; implicitHeight: 16
        Image {
            objectName: control.objectName + "Icon"
            anchors.centerIn: parent
            source: control.asset === "copy" ? "../assets/figma/redesign-383-9247-icon-phosphor-copy16.svg" : control.asset === "trash" ? "../assets/figma/redesign-383-9247-trash.svg" : control.asset === "more" ? "../assets/figma/redesign-383-9247-more-horizontal.svg" : "../assets/figma/history/" + control.asset + ".svg"
            fillMode: Image.PreserveAspectFit
            opacity: control.enabled ? 1 : 0.35
        }
    }
}
