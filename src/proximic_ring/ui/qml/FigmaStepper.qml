import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic

Basic.SpinBox {
    id: control
    property string unit: ""
    FigmaTheme { id: theme }
    implicitWidth: 144; implicitHeight: 40
    leftPadding: 36; rightPadding: 36
    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
    font.family: theme.family; font.pixelSize: 16; font.weight: Font.Medium
    opacity: enabled ? 1 : 0.45
    background: Rectangle { color: theme.surface; radius: 8; border.width: control.activeFocus ? 2 : 1; border.color: control.activeFocus ? theme.primary : theme.line }
    contentItem: TextInput {
        text: control.value + control.unit
        font: control.font; color: theme.text
        horizontalAlignment: TextInput.AlignHCenter; verticalAlignment: TextInput.AlignVCenter
        readOnly: !control.editable; selectByMouse: control.editable
        validator: control.validator; inputMethodHints: Qt.ImhFormattedNumbersOnly
    }
    down.indicator: Rectangle {
        x: 4; y: 4; width: 32; height: 32; radius: 6
        color: control.down.pressed ? theme.selection : control.down.hovered ? theme.subtle : "transparent"
        Text { anchors.centerIn: parent; text: "−"; color: theme.text; font: control.font }
    }
    up.indicator: Rectangle {
        x: control.width - width - 4; y: 4; width: 32; height: 32; radius: 6
        color: control.up.pressed ? theme.selection : control.up.hovered ? theme.subtle : "transparent"
        Text { anchors.centerIn: parent; text: "＋"; color: theme.text; font: control.font }
    }
}
