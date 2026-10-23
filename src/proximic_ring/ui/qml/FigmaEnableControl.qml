import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

AbstractButton {
    id: control
    // Checked follows the controller; a click requests a transition, never fabricates state.
    implicitWidth: 128; implicitHeight: 30
    hoverEnabled: true
    opacity: enabled ? 1 : 0.45
    FigmaTheme { id: theme }
    background: Rectangle {
        radius: 16
        color: control.down ? theme.selection : control.hovered ? "#E3E5EC" : "#E9E9EB"
        border.width: control.visualFocus ? 2 : 0; border.color: theme.primary
    }
    contentItem: Row {
        padding: 3; spacing: 0
        Repeater {
            model: ["禁用", "开启"]
            Rectangle {
                required property int index
                required property string modelData
                width: 61; height: 24; radius: 12
                color: (control.checked ? 1 : 0) === index ? "white" : "transparent"
                Text {
                    anchors.centerIn: parent; text: modelData
                    font.family: theme.family; font.pixelSize: 14; font.weight: Font.Medium
                    color: index === 1 && control.checked ? theme.primary : theme.text
                }
            }
        }
    }
}
