import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    UiTheme { id: theme }
    implicitHeight: 44
    implicitWidth: 216
    leftPadding: 12; rightPadding: 32
    topInset: 0; bottomInset: 0
    font.family: theme.family; font.pixelSize: 13
    contentItem: Text {
        text: control.displayText
        font: control.font
        color: control.enabled ? theme.text : "#929AAD"
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 8
        color: control.enabled ? "#FCFCFE" : theme.sidebar
        border.color: control.activeFocus ? theme.primary : theme.line
    }
    ToolTip.visible: hovered && displayText.length > 0
    ToolTip.delay: 700
    ToolTip.text: displayText
}
