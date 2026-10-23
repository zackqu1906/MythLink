import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: section
    property string title: ""
    default property alias rows: body.data
    FigmaTheme { id: theme }
    spacing: 16
    Label { Layout.fillWidth: true; Layout.preferredHeight: 32; text: section.title; color: theme.text; font.pixelSize: 22; font.weight: Font.Medium; verticalAlignment: Text.AlignVCenter }
    ColumnLayout { id: body; Layout.fillWidth: true; spacing: 16 }
    Rectangle { Layout.fillWidth: true; Layout.topMargin: 4; implicitHeight: 1; color: theme.line }
}
