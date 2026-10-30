import QtQuick

FigmaSelect {
    id: control
    required property var controller
    Accessible.name: "语音输入设备"
    model: controller.audioInputs
    textRole: "label"
    valueRole: "value"
    enabled: !controller.busy
    currentIndex: controller.audioInputs.map(function(item) { return item.value }).indexOf(controller.audioInputSelection)
    displayText: currentIndex >= 0 ? currentText : controller.audioInputLabel
    onActivated: controller.selectAudioInput(currentValue)
    Connections {
        target: control.popup
        function onAboutToShow() { control.controller.refreshMicrophones() }
    }
}
