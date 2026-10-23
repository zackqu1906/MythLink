import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

SettingsDialog {
    id: dialog
    objectName: "advancedSettingsDialog"
    required property var controller
    property int initialSection: 4
    property real returnScrollPosition: 0
    signal gpuSetupRequested()
    closePolicy: Popup.CloseOnEscape
    title: "高级设置"
    subtitle: "识别服务、文本模型与性能集中在此设置。"
    symbol: "tool"
    showBack: true
    backObjectName: "advancedSettingsBackButton"
    onBackRequested: finish()
    doneObjectName: "advancedSettingsDoneButton"
    onDoneRequested: finish()
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

    contentItem: Item {
        id: content
        UiScrollView {
            id: scroll
            objectName: "advancedSettingsScroll"
            anchors.fill: parent
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 18
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
}
