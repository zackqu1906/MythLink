import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

GridLayout {
    id: row
    property string title: ""
    property string description: ""
    property string descriptionObjectName: ""
    property color descriptionColor: theme.muted
    property bool descriptionWarning: false
    property bool preferenceStyle: false
    property int controlWidth: preferenceStyle ? 220 : 216
    default property alias controls: controlColumn.data
    columns: width < 540 ? 1 : 2
    columnSpacing: 24
    rowSpacing: 10
    Layout.fillWidth: true
    Layout.leftMargin: preferenceStyle ? 16 : 0
    Layout.rightMargin: preferenceStyle ? 16 : 0
    FigmaTheme { id: theme }
    ColumnLayout {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Layout.alignment: Qt.AlignVCenter
        spacing: 4
        Label {
            Layout.fillWidth: true
            text: row.title
            font.pixelSize: row.preferenceStyle ? 18 : 16; font.weight: row.preferenceStyle ? Font.Medium : Font.Bold
            color: theme.text; wrapMode: Text.Wrap
        }
        UiNotice {
            objectName: row.descriptionObjectName
            warning: row.descriptionWarning
            Layout.fillWidth: true
            visible: text.length > 0
            text: row.description
            font.pixelSize: row.preferenceStyle ? 16 : 13; lineHeight: 1.15
            color: warning ? theme.warning : row.descriptionColor; wrapMode: Text.Wrap
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
