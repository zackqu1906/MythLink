import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: pane
    objectName: "advancedSettingsPage"
    required property var controller
    readonly property real textServicesPosition: textServices.y
    signal gpuSetupRequested()
    spacing: 24

    ClefConnectionSettings {
        Layout.fillWidth: true
        connection: pane.controller.clefConnection
    }

    SpeechServiceSettings {
        objectName: "settingsPage4"
        Layout.fillWidth: true
        controller: pane.controller
        deviceSettingsLocked: controller.connected || controller.busy
        onGpuSetupRequested: pane.gpuSetupRequested()
    }
    TextServiceSettings {
        id: textServices
        objectName: "settingsPage5"
        Layout.fillWidth: true
        controller: pane.controller
    }
}
