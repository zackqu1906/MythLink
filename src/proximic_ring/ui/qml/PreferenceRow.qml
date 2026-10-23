import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: row
    property string title: ""
    property string description: ""
    property string descriptionObjectName: ""
    property bool metadata: false
    property bool linked: false
    default property alias controls: controlSlot.data
    FigmaTheme { id: theme }
    implicitWidth: 780
    implicitHeight: Math.max(copy.implicitHeight, controlSlot.implicitHeight)
    Column {
        id: copy
        anchors.left: parent.left; anchors.leftMargin: 16
        anchors.right: controlSlot.left; anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        spacing: 4
        Label {
            width: parent.width; height: row.linked ? 24 : 28
            text: row.title; color: theme.text; font.pixelSize: row.linked ? 16 : 18; font.weight: Font.Medium
            textFormat: Text.PlainText; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
        }
        Label {
            objectName: row.descriptionObjectName
            width: parent.width; height: Math.max(implicitHeight, row.metadata ? 40 : 24)
            text: row.description; color: theme.muted; font.pixelSize: row.metadata ? 14 : 16
            lineHeightMode: Text.FixedHeight; lineHeight: row.metadata ? 22 : 24
            wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
    }
    RowLayout {
        id: controlSlot
        anchors.right: parent.right; anchors.rightMargin: row.linked ? 0 : 16
        anchors.verticalCenter: parent.verticalCenter
        width: row.linked ? implicitWidth : Math.min(220, row.width * 0.43)
        spacing: 0
    }
}
