import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

PreferenceSection {
    id: pane
    required property var controller
    readonly property var permissions: controller.inlineInput.permissions
    readonly property var input: controller.inlineInput
    readonly property bool mac: Qt.platform.os === "osx"
    objectName: "settingsPermissionsGroup"
    title: "系统与权限"
    PreferenceRow {
        visible: pane.mac
        Layout.fillWidth: true; title: "应用权限"; description: "蓝牙、麦克风、辅助功能与屏幕录制的授权状态。"
        SettingsStatusAction {
            preferenceStyle: true
            statusObjectName: "systemAuthorizationStatus"
            statusText: pane.permissions.authorizationStatusText
            statusReady: pane.permissions.allPermissionsGranted
            buttonObjectName: "permissionSetupButton"; text: "一键开启"
            buttonEnabled: !pane.controller.permissionSetup.busy && !pane.permissions.allPermissionsGranted
            onClicked: pane.controller.permissionSetup.open()
        }
    }
    PreferenceRow {
        visible: pane.mac
        Layout.fillWidth: true; title: "蓝牙"; description: "用于连接 Ring。"
        SettingsStatusAction {
            preferenceStyle: true
            statusObjectName: "settingsBluetoothStatus"
            statusText: pane.permissions.bluetoothStatusText; statusReady: pane.permissions.bluetoothGranted
            buttonObjectName: "settingsBluetoothButton"; text: "系统设置"
            onClicked: pane.permissions.openPermissionSettings("bluetooth")
        }
    }
    PreferenceRow {
        visible: pane.mac
        Layout.fillWidth: true; title: "麦克风"; description: "用于电脑麦克风收音与录音测试。"
        SettingsStatusAction {
            preferenceStyle: true
            statusObjectName: "settingsMicrophoneStatus"
            statusText: pane.permissions.microphoneStatusText; statusReady: pane.permissions.microphoneGranted
            buttonObjectName: "settingsMicrophoneButton"; text: "系统设置"
            onClicked: pane.permissions.openPermissionSettings("microphone")
        }
    }
    PreferenceRow {
        visible: pane.mac
        Layout.fillWidth: true; title: "辅助功能"
        descriptionObjectName: "settingsAccessibilityHint"
        description: pane.permissions.accessibilityWarningText || "用于语音编辑、手势与按键控制。"
        SettingsStatusAction {
            preferenceStyle: true
            statusObjectName: "settingsAccessibilityStatus"
            statusText: pane.permissions.accessibilityStatusText; statusReady: pane.permissions.accessibilityGranted
            buttonObjectName: "settingsAccessibilityButton"; text: "系统设置"
            buttonEnabled: !pane.permissions.systemPermissionRequesting
            onClicked: pane.permissions.openSettings()
        }
    }
    PreferenceRow {
        visible: pane.mac
        Layout.fillWidth: true; title: "屏幕录制"
        descriptionObjectName: "screenRecordingPermissionHint"
        description: pane.permissions.screenRecordingMessage || "用于显示窗口实时预览。"
        SettingsStatusAction {
            preferenceStyle: true
            statusObjectName: "screenRecordingPermissionStatus"
            statusText: pane.permissions.screenRecordingStatusText; statusReady: pane.permissions.screenRecordingGranted
            buttonObjectName: "openScreenRecordingPermissionsButton"; text: "系统设置"
            buttonEnabled: !pane.permissions.systemPermissionRequesting
            onClicked: pane.permissions.openScreenRecordingSettings()
        }
    }
    PreferenceRow {
        Layout.fillWidth: true; title: "Ring 输入法"; visible: pane.input.enabled || pane.input.windowsInputMethodSetup
        id: inputMethodRow
        readonly property string versionState: pane.input.inputMethodVersion.state
        readonly property bool needsInputSettings: versionState === "activation_pending" || versionState === "activation_unknown"
        descriptionObjectName: "inputMethodInstallationMessage"
        description: pane.input.inputMethodVersion.detail
            + (!needsInputSettings && pane.input.installationMessage.length ? "\n" + pane.input.installationMessage : "")
        SettingsStatusAction {
            preferenceStyle: true
            statusObjectName: "inputMethodVersionStatus"
            statusText: pane.input.installing ? "更新中…" : pane.input.inputMethodVersion.text
            statusReady: !pane.input.installing && pane.input.inputMethodVersion.current
            statusHint: pane.input.inputMethodVersion.detail
            buttonObjectName: "settingsInputMethodButton"
            text: pane.input.windowsInputMethodSetup ? (inputMethodRow.versionState === "not_installed" ? "安装输入法" : "修复输入法")
                : inputMethodRow.needsInputSettings ? "打开键盘设置" : pane.input.inputMethodVersion.current ? "输入设置"
                : inputMethodRow.versionState === "checking" ? "确认中…" : inputMethodRow.versionState === "not_installed" ? "安装输入法" : "更新输入法"
            buttonEnabled: !pane.input.installing && inputMethodRow.versionState !== "checking"
            onClicked: {
                if (pane.input.windowsInputMethodSetup) pane.input.installInputMethod()
                else if (pane.input.inputMethodVersion.current || inputMethodRow.needsInputSettings) pane.input.openInputSettings()
                else pane.input.installInputMethod()
            }
        }
    }
    FigmaAction {
        objectName: "copyInputMethodInstallDiagnostics"
        Layout.alignment: Qt.AlignRight; Layout.rightMargin: 16
        visible: pane.input.enabled && pane.input.hasInstallDiagnostics
        enabled: !pane.input.installing
        quiet: true; implicitHeight: 32
        text: copiedTimer.running ? "已复制安装诊断" : "复制输入法安装诊断"
        onClicked: if (pane.input.copyInstallDiagnostics()) copiedTimer.restart()
        Timer { id: copiedTimer; interval: 1800 }
    }
    PreferenceRow {
        visible: pane.mac
        Layout.fillWidth: true; title: "自动锁屏组件"
        description: "用于离开锁屏与手势解锁；重新授权会暂停自动锁屏。"
        SettingsStatusAction {
            preferenceStyle: true
            statusObjectName: "proximityPermissionStatus"
            statusText: pane.controller.proximity.permissionReady ? "已开启" : pane.controller.proximity.passwordStatusChecked ? "未开启" : "待确认"
            statusReady: pane.controller.proximity.permissionReady
            buttonObjectName: "proximityRequestPermissions"; text: "请求权限"
            buttonEnabled: !pane.controller.proximity.busy
            onClicked: pane.controller.proximity.requestPermissions()
        }
    }
}
