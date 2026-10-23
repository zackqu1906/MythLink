import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Control {
    id: group
    property string title: ""
    property string description: ""
    property string symbol: ""
    default property alias rows: body.data
    Layout.fillWidth: true
    padding: 20
    implicitWidth: implicitContentWidth + leftPadding + rightPadding
    implicitHeight: implicitContentHeight + topPadding + bottomPadding
    UiTheme { id: theme }
    background: Rectangle {
        radius: 14
        color: theme.surface
        border.color: theme.line
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
                    visible: group.symbol.length > 0
                    symbol: group.symbol; tint: true; ink: theme.primary
                    Layout.preferredWidth: 20; Layout.preferredHeight: 20
                }
                Label {
                    Layout.fillWidth: true
                    text: group.title
                    font.pixelSize: 17; font.weight: Font.DemiBold
                    color: theme.text; wrapMode: Text.Wrap
                }
            }
            Label {
                Layout.fillWidth: true
                visible: text.length > 0
                text: group.description
                font.pixelSize: 12; lineHeight: 1.2
                color: theme.muted; wrapMode: Text.Wrap
            }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.line }
        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: 20
        }
    }
}
