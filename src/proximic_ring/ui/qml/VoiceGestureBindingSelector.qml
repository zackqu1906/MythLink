import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

SettingsSelect {
    id: selector
    required property var controller
    required property string actionName
    required property int slotIndex
    property string namePrefix: ""
    objectName: namePrefix + actionName + "Gesture" + slotIndex
    readonly property var options: {
        var bindings = controller.gestureBindings
        return controller.gestureOptionsForSlot(actionName, slotIndex)
    }
    readonly property string selectedGesture: controller.gestureBindings[actionName][slotIndex]
    readonly property int selectedIndex: {
        for (var i = 0; i < options.length; ++i)
            if (options[i].value === selectedGesture) return i
        return 0
    }
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    model: options
    textRole: "label"
    valueRole: "value"
    currentIndex: selectedIndex
    function restoreSelection() {
        currentIndex = Qt.binding(function() { return selector.selectedIndex })
    }
    // A filtered model reset can make ComboBox select row zero even when the
    // saved gesture's index is unchanged. Restore it after that reset settles.
    onOptionsChanged: Qt.callLater(restoreSelection)
    Accessible.name: ({confirm: "开始或结束语音", undo: "取消或撤销", switch_mode: "转换为编辑"})[actionName]
                     + (slotIndex === 0 ? "，主手势" : "，备用手势")
    onActivated: {
        controller.setGestureBinding(actionName, slotIndex, options[currentIndex].value)
        restoreSelection()
    }
}
