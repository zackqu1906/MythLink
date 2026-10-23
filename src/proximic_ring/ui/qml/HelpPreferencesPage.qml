import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: dialog
    required property string appVersion
    signal tutorialRequested()
    signal helpCenterRequested()
    signal releaseNotesRequested()
    objectName: "helpPreferencesPage"
    Item {
        anchors.fill: parent
        ColumnLayout {
            anchors.left: parent.left; anchors.right: parent.right; anchors.rightMargin: 12
            spacing: 16
            PreferenceRow {
                Layout.fillWidth: true; title: "使用指南"; description: "探索 Ring 的使用教程。"
                FigmaAction { objectName: "helpTutorialButton"; Layout.fillWidth: true; text: "开始 / 继续"; font.pixelSize: 16; onClicked: dialog.tutorialRequested() }
            }
            PreferenceRow {
                Layout.fillWidth: true; title: "帮助中心"; description: "查找常见问题与操作帮助。"
                FigmaAction { objectName: "helpCenterButton"; Layout.fillWidth: true; text: "打开帮助中心"; font.pixelSize: 16; onClicked: dialog.helpCenterRequested() }
            }
            PreferenceRow {
                Layout.fillWidth: true; title: "版本说明"; description: "当前版本 V" + dialog.appVersion; metadata: true
                FigmaAction { objectName: "releaseNotesButton"; Layout.fillWidth: true; text: "查看版本说明"; font.pixelSize: 16; onClicked: dialog.releaseNotesRequested() }
            }
        }
    }
}
