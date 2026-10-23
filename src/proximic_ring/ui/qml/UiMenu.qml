import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic

Basic.Menu {
    id: menu
    UiTheme { id: theme }
    popupType: Popup.Item
    x: parent ? parent.width - width : 0
    y: parent ? parent.height + 6 : 0
    margins: 8
    padding: 6
    spacing: 2
    font.family: theme.family
    font.pixelSize: 12
    implicitWidth: Math.max(180, implicitContentWidth + leftPadding + rightPadding)
    delegate: UiMenuItem { }
    background: Rectangle {
        implicitWidth: 180
        implicitHeight: 44
        radius: 10
        color: theme.surface
        border.color: "#E3E7EF"
    }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 100 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }
}
