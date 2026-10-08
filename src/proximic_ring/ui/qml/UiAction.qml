import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: control
    property string symbol: ""
    property bool primary: false
    property bool quiet: false
    UiTheme { id: theme }
    implicitHeight: 42
    implicitWidth: text.length === 0 ? 42 : Math.max(42, content.implicitWidth + 28)
    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
    leftPadding: 14; rightPadding: 14
    topPadding: 0; bottomPadding: 0
    font.family: theme.family
    font.pixelSize: 13
    hoverEnabled: true
    Accessible.name: text
    background: Rectangle {
        radius: 10
        color: !control.enabled ? theme.sidebar : control.primary ? (control.down ? "#061F9D" : theme.primary)
             : control.down ? theme.selection : control.hovered ? "#EBEFFC" : control.quiet ? "transparent" : theme.surface
        border.width: control.primary || control.quiet ? 0 : 1
        border.color: theme.line
    }
    contentItem: RowLayout {
        id: content
        spacing: control.text.length > 0 ? 8 : 0
        UiIcon {
            visible: control.symbol.length > 0
            Layout.preferredWidth: control.text.length > 0 ? 18 : 20
            Layout.preferredHeight: Layout.preferredWidth
            Layout.alignment: Qt.AlignHCenter
            symbol: control.symbol
            tint: control.primary || !control.enabled
            ink: !control.enabled ? "#A0A8BA" : control.primary ? "white" : theme.primary
        }
        Label {
            objectName: "actionText"
            padding: 0
            visible: control.text.length > 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignVCenter
            text: control.text
            color: !control.enabled ? "#929AAD" : control.primary ? "white" : theme.text
            font: control.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }
}
