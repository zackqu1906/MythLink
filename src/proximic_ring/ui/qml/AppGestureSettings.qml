import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: settings
    required property var service
    readonly property string selectedApp: appPicker.currentValue || "codex"
    function selectApp(value) {
        for (var i = 0; i < service.apps.length; ++i) {
            if (service.apps[i].value === value) {
                appPicker.currentIndex = i
                return
            }
        }
    }
    function actionLabel(action, fallback) {
        if (action === "previous") return selectedApp === "wechat" ? "上一个聊天" : "上一个任务"
        if (action === "next") return selectedApp === "wechat" ? "下一个聊天" : "下一个任务"
        return fallback
    }
    spacing: 10
    objectName: "appGestureSettingsSection"
    signal wechatSetupRequested()
    onSelectedAppChanged: service.recording = false
    onVisibleChanged: { if (!visible) service.recording = false }

    Label {
        Layout.fillWidth: true
        text: "输入框有焦点时：上滑固定按一次 Enter，适用于所有应用和浏览器的当前输入框。发送、换行或搜索由该输入框决定；听写中会先定稿。\n应用配置优先于全局配置；仅与输入手势重叠时，输入框内优先执行输入功能。"
        color: "#687286"; wrapMode: Text.Wrap; font.pixelSize: 12
    }
    SettingsSelect {
        id: appPicker
        objectName: "appGestureAppPicker"
        Layout.fillWidth: true
        model: settings.service.apps
        textRole: "label"; valueRole: "value"
        Accessible.name: "选择要控制的应用"
        onActivated: settings.service.recording = false
    }
    UiNotice {
        Layout.fillWidth: true; wrapMode: Text.Wrap
        text: settings.service.recordingError || (settings.service.recording ? "正在录制：按下组合键，Esc 取消" : "点击快捷键框后，直接按下组合键即可保存。")
        warning: settings.service.recordingError.length > 0; font.pixelSize: 12
    }
    Repeater {
        model: settings.service.profiles[settings.selectedApp] || []
        delegate: Rectangle {
            id: row
            required property var modelData
            readonly property bool voiceLocked: settings.service.catalog.voiceGestures.indexOf(modelData.gesture) >= 0
            Layout.fillWidth: true
            implicitHeight: fields.implicitHeight + 40
            color: "#FFFFFF"; radius: 14; border.color: "#E1E5F0"
            function save(gesture, shortcut, enabled) {
                settings.service.setBinding(settings.selectedApp, modelData.action, gesture, shortcut, enabled)
            }
            ColumnLayout {
                id: fields
                enabled: !row.voiceLocked
                anchors.fill: parent; anchors.margins: 20
                spacing: 5
                RowLayout {
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: settings.actionLabel(row.modelData.action, row.modelData.label) + (row.voiceLocked ? " · 语音手势已锁定" : ""); color: "#171C28" }
                    SettingsSwitch {
                        objectName: "appGestureEnabled_" + row.modelData.action
                        Layout.preferredHeight: 32
                        checked: row.modelData.enabled
                        enabled: row.modelData.gesture.length > 0
                        Accessible.name: "启用" + row.modelData.label
                        onClicked: row.save(row.modelData.gesture, row.modelData.shortcut, checked)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    SettingsSelect {
                        id: gesture
                        Layout.fillWidth: true; Layout.minimumWidth: 120
                        Layout.preferredHeight: 44
                        model: settings.service.gestures.filter(function(g) { return settings.service.catalog.voiceGestures.indexOf(g.value) < 0 })
                        textRole: "label"; valueRole: "value"
                        displayText: {
                            if (!row.voiceLocked) return currentText
                            for (var j = 0; j < settings.service.gestures.length; ++j)
                                if (settings.service.gestures[j].value === row.modelData.gesture)
                                    return settings.service.gestures[j].label
                            return row.modelData.gesture
                        }
                        currentIndex: {
                            for (var i = 0; i < model.length; i++)
                                if (model[i].value === row.modelData.gesture) return i
                            return 0
                        }
                        Accessible.name: row.modelData.label + "手势"
                        onActivated: row.save(currentValue, row.modelData.shortcut, row.modelData.enabled)
                    }
                    ShortcutField {
                        objectName: "appGestureShortcut_" + row.modelData.action
                        Layout.fillWidth: true; Layout.minimumWidth: 200
                        Layout.preferredHeight: 44
                        sequence: row.modelData.shortcut
                        service: settings.service
                        Accessible.name: row.modelData.label + "快捷键"
                        onRecorded: function(value) { row.save(row.modelData.gesture, value, row.modelData.enabled) }
                    }
                }
            }
        }
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap
        text: "切换对话：本句处理完成后生效。\n手势可以复用；有可撤销或转编辑的语音时，优先处理语音操作。"
        color: "#687286"; font.pixelSize: 11
    }
    UiAction {
        objectName: "openWeChatGuideButton"
        Layout.alignment: Qt.AlignLeft
        visible: settings.selectedApp === "wechat"
        text: "查看微信设置步骤"
        onClicked: settings.wechatSetupRequested()
    }
    UiNotice {
        Layout.fillWidth: true; wrapMode: Text.Wrap
        text: settings.service.error
        visible: text.length > 0; font.pixelSize: 12
    }
    UiAction {
        objectName: "resetAppGesturesButton"
        text: "恢复此应用默认手势"
        onClicked: settings.service.resetApp(settings.selectedApp)
    }
    Component.onDestruction: { if (settings.service) settings.service.recording = false }
}
