import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic

Basic.ComboBox {
    id: control
    FigmaTheme { id: theme }
    implicitHeight: 40; implicitWidth: 216
    leftPadding: 12; rightPadding: 36
    topInset: 0; bottomInset: 0
    hoverEnabled: true
    font.family: theme.family; font.pixelSize: 14
    opacity: enabled ? 1 : 0.45
    contentItem: Text { text: control.displayText; font: control.font; color: theme.text; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
    indicator: FigmaIcon { x: control.width - 28; y: (control.height - 16) / 2; width: 16; height: 16; symbol: "chevron-down" }
    background: Rectangle { radius: 8; color: control.down ? theme.selection : control.hovered ? theme.subtle : theme.background; border.width: control.activeFocus ? 2 : 1; border.color: control.activeFocus ? theme.primary : theme.line }
    delegate: ItemDelegate {
        width: control.width; height: 40
        text: control.textRole ? (Array.isArray(control.model) ? modelData[control.textRole] : model[control.textRole]) : modelData
        font: control.font
        highlighted: control.highlightedIndex === index
        contentItem: Text { text: parent.text; font: control.font; color: theme.text; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        background: Rectangle { radius: 6; color: parent.highlighted ? theme.selection : parent.hovered ? theme.subtle : theme.background }
    }
    popup: Popup {
        y: control.height + 4; width: control.width; padding: 4
        implicitHeight: Math.min(320, contentItem.implicitHeight + 8)
        background: Rectangle { color: theme.background; radius: 8; border.color: theme.line }
        contentItem: ListView { clip: true; implicitHeight: contentHeight; model: control.popup.visible ? control.delegateModel : null; currentIndex: control.highlightedIndex; ScrollBar.vertical: ScrollBar {} }
    }
}
