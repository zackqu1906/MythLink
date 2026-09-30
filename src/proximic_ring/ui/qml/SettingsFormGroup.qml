import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: group
    property string title: ""
    property string description: ""
    property string symbol: ""
    default property alias rows: body.data
    Layout.fillWidth: true
    spacing: 10
    UiTheme { id: theme }
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4
        RowLayout {
            Layout.fillWidth: true
            spacing: 7
            UiIcon {
                visible: group.symbol.length > 0
                symbol: group.symbol
                Layout.preferredWidth: 20; Layout.preferredHeight: 20
            }
            Label {
                Layout.fillWidth: true
                text: group.title
                font.pixelSize: 19; font.bold: true
                color: theme.muted; wrapMode: Text.Wrap
            }
        }
        Label {
            Layout.fillWidth: true
            visible: text.length > 0
            text: group.description
            font.pixelSize: 12; color: theme.muted; wrapMode: Text.Wrap
        }
    }
    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.line }
    ColumnLayout {
        id: body
        Layout.fillWidth: true
        Layout.topMargin: 2
        spacing: 22
    }
}
