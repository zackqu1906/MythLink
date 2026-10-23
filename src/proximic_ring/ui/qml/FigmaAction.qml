import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

Basic.Button {
    id: control
    property string symbol: ""
    property bool primary: false
    property bool quiet: false
    property bool navigation: false
    property url trailingAsset: ""
    property color contentColor: primary ? "white" : navigation ? theme.muted : theme.text
    readonly property bool iconOnly: symbol.length > 0 && text.length === 0 && !navigation
    FigmaTheme { id: theme }
    implicitHeight: navigation ? 36 : 40
    implicitWidth: text.length ? Math.max(navigation ? 129 : 40, content.implicitWidth + leftPadding + rightPadding) : iconOnly ? 40 : 32
    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
    leftPadding: text.length ? (navigation ? 20 : 16) : 0; rightPadding: text.length ? (navigation ? 8 : 16) : 0
    topPadding: 0; bottomPadding: 0
    font.family: theme.family; font.pixelSize: navigation ? 16 : 14; font.weight: Font.Medium
    hoverEnabled: true
    opacity: enabled ? 1 : 0.45
    Accessible.name: text
    background: Rectangle {
        radius: 8
        color: control.primary ? (control.down ? theme.primaryPressed : control.hovered ? theme.primaryHover : theme.primary)
            : control.down ? theme.selection : control.hovered ? theme.subtle : control.quiet ? "transparent" : theme.background
        border.width: control.visualFocus ? 2 : control.primary || control.quiet ? 0 : 1
        border.color: control.visualFocus ? theme.primary : theme.line
        // Icon actions keep a single, immediate hover color for the entire stay.
        Behavior on color { enabled: !control.iconOnly; ColorAnimation { duration: 90 } }
    }
    contentItem: RowLayout {
        id: content
        spacing: control.navigation ? 4 : 8
        FigmaIcon {
            objectName: control.objectName + "Icon"
            visible: control.symbol.length > 0; symbol: control.symbol
            Layout.preferredWidth: 20; Layout.preferredHeight: 20
            Layout.fillWidth: control.iconOnly
            Layout.alignment: Qt.AlignHCenter | Qt.AlignVCenter
        }
        Label {
            visible: control.text.length > 0
            Layout.fillWidth: true
            text: control.text
            font: control.font
            color: control.contentColor
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        FigmaIcon { visible: control.navigation; symbol: "chevron-right"; asset: control.trailingAsset; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
    }
}
