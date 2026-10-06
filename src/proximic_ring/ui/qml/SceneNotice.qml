import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    implicitWidth: 284; implicitHeight: 68
    width: implicitWidth; height: implicitHeight
    radius: 12
    color: Qt.rgba(theme.surface.r, theme.surface.g, theme.surface.b, 0.88)
    border.color: Qt.rgba(theme.line.r, theme.line.g, theme.line.b, 0.72)
    UiTheme { id: theme }
    RowLayout {
        anchors.fill: parent; anchors.margins: 12; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 10
        Rectangle {
            Layout.preferredWidth: 28; Layout.preferredHeight: 28
            radius: 8; color: theme.selection
            Label { anchors.centerIn: parent; text: "✓"; color: theme.primary; font.pixelSize: 18 }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 3
            Label {
                objectName: "sceneNoticeTitle"
                Layout.fillWidth: true; text: sceneNotice ? (sceneNotice.notice.title || "") : ""
                color: theme.text; font.pixelSize: 16; font.weight: Font.DemiBold; elide: Text.ElideRight
            }
            Label {
                objectName: "sceneNoticeApplication"
                Layout.fillWidth: true; text: sceneNotice ? (sceneNotice.notice.application || "") : ""
                color: theme.muted; font.pixelSize: 12; elide: Text.ElideRight
            }
        }
    }
}
