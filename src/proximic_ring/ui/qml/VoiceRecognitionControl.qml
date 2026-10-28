import QtQuick

FigmaEnableControl {
    required property var controller
    checked: controller.recognitionEnabled
    enabled: controller.connected && !controller.busy && !controller.scanBusy && !controller.touchpad.blocksVoice
    Accessible.name: checked ? "暂停语音识别" : "开启语音识别"
    onClicked: controller.toggleRecognition()
}
