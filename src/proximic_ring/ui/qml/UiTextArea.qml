import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.TextArea {
    id: control
    property string hintText: ""
    UiTheme { id: theme }

    leftPadding: 12; rightPadding: 12
    topPadding: 10; bottomPadding: 10
    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
    font.family: theme.family
    font.pixelSize: 13
    color: enabled ? theme.text : theme.muted
    placeholderText: activeFocus ? "" : hintText
    placeholderTextColor: theme.placeholder
    selectionColor: theme.primary
    selectedTextColor: "white"
    selectByMouse: true
    wrapMode: TextEdit.Wrap
    Accessible.name: hintText

    background: Rectangle {
        implicitWidth: 216; implicitHeight: 100
        radius: 8
        color: control.enabled ? theme.surface : theme.sidebar
        border.color: control.activeFocus ? theme.primary : theme.line
    }
}
