import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

Basic.Dialog {
    id: dialog
    FigmaTheme { id: theme }
    padding: 24
    topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
    font.family: theme.family
    font.pixelSize: 14
    header: Item {
        implicitHeight: 64
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 24; anchors.rightMargin: 20; anchors.topMargin: 24; anchors.bottomMargin: 8
            Label { Layout.fillWidth: true; text: dialog.title; font.family: theme.family; font.pixelSize: 22; font.weight: Font.Medium; color: theme.text }
            FigmaAction { symbol: "close"; quiet: true; implicitWidth: 32; implicitHeight: 32; Accessible.name: "关闭"; onClicked: dialog.close() }
        }
    }
    background: Rectangle {
        radius: 12; color: theme.surface; border.color: theme.line
        UiWindowDragArea { anchors.fill: parent }
    }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 120 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }
}
