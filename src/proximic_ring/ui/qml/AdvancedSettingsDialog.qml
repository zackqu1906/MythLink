import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "advancedSettingsDialog"
    required property var controller
    property int initialSection: 4
    property real returnScrollPosition: 0
    signal gpuSetupRequested()
    UiTheme { id: theme }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(820, parent.width - 48)
    height: Math.min(860, parent.height - 48)
    modal: true
    popupType: Popup.Item
    closePolicy: Popup.CloseOnEscape
    padding: 0
    title: "高级设置"
    Overlay.modal: Rectangle { color: "#99000000" }
    background: Rectangle { color: theme.surface; radius: 18; border.color: theme.line }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 100 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }
    onOpened: scrollToSection(initialSection)
    onAboutToHide: saveCurrentEditor()

    function saveCurrentEditor() {
        // Focus a plain item, outside the scroll view's focus scope, to commit edits.
        content.forceActiveFocus(Qt.OtherFocusReason)
    }
    function scrollToSection(section) {
        var flick = scroll.contentItem
        flick.contentY = section === 5
            ? Math.min(textServices.y, Math.max(0, flick.contentHeight - flick.height)) : 0
    }
    function finish() {
        saveCurrentEditor()
        close()
    }

    header: ColumnLayout {
        spacing: 6
        Label {
            Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.topMargin: 24
            text: dialog.title; color: theme.text; font.pixelSize: 28; font.bold: true
        }
        Label {
            Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.bottomMargin: 20
            text: "识别服务、文本模型与性能集中在此设置。"
            color: theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: theme.line }
    }
    contentItem: Item {
        id: content
        ScrollView {
            id: scroll
            objectName: "advancedSettingsScroll"
            anchors.fill: parent
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AsNeeded
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 36
                SpeechServiceSettings {
                    objectName: "settingsPage4"
                    Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.topMargin: 24
                    controller: dialog.controller
                    deviceSettingsLocked: controller.connected || controller.busy
                    onGpuSetupRequested: dialog.gpuSetupRequested()
                }
                TextServiceSettings {
                    id: textServices
                    objectName: "settingsPage5"
                    Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.bottomMargin: 28
                    controller: dialog.controller
                }
            }
        }
    }
    footer: Item {
        implicitHeight: 72
        Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: theme.line }
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 28; anchors.rightMargin: 28
            Label { Layout.fillWidth: true; text: "设置自动保存"; color: theme.muted; font.pixelSize: 12 }
            UiAction {
                objectName: "advancedSettingsDoneButton"
                text: "完成"; primary: true
                Layout.preferredWidth: 88
                onClicked: dialog.finish()
            }
        }
    }
}
