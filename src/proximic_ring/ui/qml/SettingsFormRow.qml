import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

GridLayout {
    id: row
    property string title: ""
    property string description: ""
    property string descriptionObjectName: ""
    property color descriptionColor: theme.muted
    property int controlWidth: 216
    default property alias controls: controlColumn.data
    columns: width < 540 ? 1 : 2
    columnSpacing: 24
    rowSpacing: 10
    Layout.fillWidth: true
    UiTheme { id: theme }
    ColumnLayout {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Layout.alignment: Qt.AlignVCenter
        spacing: 4
        Label {
            Layout.fillWidth: true
            text: row.title
            font.pixelSize: 16; font.bold: true
            color: theme.text; wrapMode: Text.Wrap
        }
        Label {
            objectName: row.descriptionObjectName
            Layout.fillWidth: true
            visible: text.length > 0
            text: row.description
            font.pixelSize: 13; lineHeight: 1.15
            color: row.descriptionColor; wrapMode: Text.Wrap
        }
    }
    ColumnLayout {
        id: controlColumn
        Layout.preferredWidth: row.columns === 1 ? row.width : row.controlWidth
        Layout.fillWidth: row.columns === 1
        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        spacing: 6
    }
}
