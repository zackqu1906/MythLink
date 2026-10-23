import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FigmaFormGroup {
        flat: true
    id: group
    required property var bridge
    objectName: "browserExtensionSettings"
    title: "浏览器媒体控制"; symbol: "operation"
    visible: Qt.platform.os === "osx"
    description: "按指引将 Mythlink 浏览器助手加入浏览器，即可使用「场景与手势」中的视频与音频配置。"
    FigmaFormRow {
            preferenceStyle: true
        title: "Google Chrome"
        controlWidth: 268
        description: group.bridge.chromeDevelopment ? "打开扩展页 → 加载扩展文件夹 → 允许网站访问。" : "从 Chrome 应用商店添加，再允许网站访问。"
        SettingsStatusAction {
                preferenceStyle: true
            statusObjectName: "chromeExtensionStatus"
            statusText: group.bridge.chromeSummary
            statusReady: group.bridge.chromeConnected
            statusHint: group.bridge.chromeStatus
            buttonObjectName: "connectChromeExtension"
            text: group.bridge.chromeConnected ? "管理扩展" : "安装指引"
            onClicked: { setup.browser = "chrome"; setup.open() }
        }
    }
    FigmaFormRow {
            preferenceStyle: true
        title: "Safari"
        controlWidth: 268
        description: "打开扩展页 → 勾选 Mythlink → 允许网站访问。"
        SettingsStatusAction {
                preferenceStyle: true
            statusObjectName: "safariExtensionStatus"
            statusText: group.bridge.safariSummary
            statusReady: group.bridge.safariConnected
            statusHint: group.bridge.safariStatus
            buttonObjectName: "connectSafariExtension"
            text: group.bridge.safariConnected ? "管理扩展" : "启用指引"
            onClicked: { setup.browser = "safari"; setup.open() }
        }
    }
    BrowserExtensionSetup { id: setup; bridge: group.bridge }
    Label {
        Layout.fillWidth: true
        text: "连接后自动识别网页媒体，进入场景时会显示提示。播放器状态只在本机传递。"
        wrapMode: Text.Wrap; font.pixelSize: 12; color: "#667085"
    }
}
