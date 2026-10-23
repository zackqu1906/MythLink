import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Control {
    id: group
    property string title: ""
    property string description: ""
    property string symbol: ""
    property bool flat: false
    default property alias rows: body.data
    Layout.fillWidth: true
    padding: flat ? 0 : 20
    bottomPadding: 20
    implicitWidth: implicitContentWidth + leftPadding + rightPadding
    implicitHeight: implicitContentHeight + topPadding + bottomPadding
    FigmaTheme { id: theme }
    background: Rectangle {
        radius: 12
        color: group.flat ? "transparent" : theme.surface
        border.color: group.flat ? "transparent" : theme.line
        Rectangle { visible: group.flat; anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.line }
    }
    contentItem: ColumnLayout {
        spacing: 16
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                UiIcon {
                    visible: false
                    symbol: group.symbol; tint: true; ink: theme.primary
                    Layout.preferredWidth: 20; Layout.preferredHeight: 20
                }
                Label {
                    Layout.fillWidth: true
                    text: group.title
                    font.pixelSize: 22; font.weight: Font.Medium
                    color: theme.text; wrapMode: Text.Wrap
                }
            }
            Label {
                Layout.fillWidth: true
                visible: text.length > 0
                text: group.description
                font.pixelSize: group.flat ? 16 : 14; lineHeight: 1.2
                color: theme.muted; wrapMode: Text.Wrap
            }
        }

        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: 20
        }
    }
}
