import QtQuick
import QtQuick.Controls

UiTextField {
    id: control
    FigmaTheme { id: theme }
    implicitHeight: 40
    leftPadding: 12; rightPadding: 12; topPadding: 0; bottomPadding: 0
    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
    font.family: theme.family; font.pixelSize: 14
    color: theme.text; placeholderTextColor: theme.muted
    selectionColor: theme.selection; selectedTextColor: theme.text
    opacity: enabled ? 1 : 0.45
    background: Rectangle { radius: 8; color: control.hovered ? theme.subtle : theme.background; border.width: control.activeFocus ? 2 : 1; border.color: control.activeFocus ? theme.primary : theme.line }
}
