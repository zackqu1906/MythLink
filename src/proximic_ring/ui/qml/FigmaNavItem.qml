import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

AbstractButton {
    id: control
    property string symbol: ""
    property bool childRoute: false
    property bool disclosure: false
    property bool expanded: true
    implicitWidth: 180; implicitHeight: 44
    hoverEnabled: true
    opacity: enabled ? 1 : 0.4
    Accessible.name: text
    FigmaTheme { id: theme }
    background: Rectangle {
        radius: 8
        color: control.checked || control.down ? theme.selection : control.hovered ? theme.subtle : "transparent"
        border.width: control.visualFocus ? 2 : 0; border.color: theme.primary
    }
    contentItem: RowLayout {
        spacing: 12
        Item { Layout.preferredWidth: control.childRoute ? 32 : 0 }
        FigmaIcon { visible: !control.childRoute; symbol: control.symbol; selected: control.checked; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
        Label {
            Layout.fillWidth: true
            text: control.text; font.family: theme.family; font.pixelSize: 16
            font.weight: control.childRoute ? Font.Normal : Font.Medium
            color: control.checked ? theme.primary : theme.text
            elide: Text.ElideRight
        }
        FigmaIcon { visible: control.disclosure; symbol: "chevron-down"; rotation: control.expanded ? 0 : -90; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
    }
    leftPadding: 12; rightPadding: 12
}
