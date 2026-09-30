import QtQuick
import QtQuick.Controls

Switch {
    id: control
    UiTheme { id: theme }
    implicitWidth: 48; implicitHeight: 36
    padding: 3
    indicator: Rectangle {
        x: (control.width - width) / 2
        y: (control.height - height) / 2
        width: 42; height: 25; radius: 13
        color: control.checked ? theme.primary : "#858D9A"
        opacity: control.enabled ? 1 : 0.45
        border.width: control.activeFocus ? 2 : 0
        border.color: "#7192FF"
        Rectangle {
            x: control.checked ? 20 : 3
            y: 3; width: 19; height: 19; radius: 10
            color: "white"
        }
    }
    contentItem: Item { }
}
