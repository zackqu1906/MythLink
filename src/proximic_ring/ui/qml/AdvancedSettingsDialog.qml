import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

PreferencesDialog {
    id: dialog
    objectName: "advancedSettingsDialog"
    required property var controller
    property int initialSection: 4
    property real returnScrollPosition: 0
    signal gpuSetupRequested()
    closePolicy: Popup.CloseOnEscape
    title: "高级设置"
    section: 1
    property int returnSection: 1
    showBack: true
    backObjectName: "advancedSettingsBackButton"
    onBackRequested: finish()
    closeObjectName: "advancedSettingsDoneButton"
    onCloseRequested: finish()
    onAccountRequested: { returnSection = 0; finish() }
    onSettingsRequested: { returnSection = 1; finish() }
    onHelpRequested: { returnSection = 2; finish() }
    onAboutToShow: returnSection = 1
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
                spacing: 24
                SpeechServiceSettings {
                    objectName: "settingsPage4"
                    Layout.fillWidth: true
                    controller: dialog.controller
                    deviceSettingsLocked: controller.connected || controller.busy
                    onGpuSetupRequested: dialog.gpuSetupRequested()
                }
                TextServiceSettings {
                    id: textServices
                    objectName: "settingsPage5"
                    Layout.fillWidth: true
                    controller: dialog.controller
                }
            }
        }
    }
}
