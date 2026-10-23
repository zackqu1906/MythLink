import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

SettingsDialog {
    id: dialog
    required property var bridge
    property string browser: "chrome"
    property bool showHelp: false
    readonly property bool isChrome: browser === "chrome"
    readonly property bool development: isChrome && bridge.chromeDevelopment
    readonly property bool connected: isChrome ? bridge.chromeConnected : bridge.safariConnected
    objectName: "browserExtensionSetup"
    title: (isChrome ? "Chrome" : "Safari") + " 扩展安装"
    subtitle: isChrome ? "将 Mythlink 浏览器助手添加到你常用的 Chrome 个人资料。"
                       : "Safari 扩展随 Mythlink 提供，在浏览器中启用即可。"
    symbol: "operation"
    width: Math.min(650, parent ? parent.width - 32 : 650)
    height: Math.min(720, parent ? parent.height - 32 : 720)
    doneObjectName: "browserSetupDone"
    doneText: connected ? "完成" : "稍后继续"
    footerHint: connected ? "已连接 · 使用「场景与手势」中的对应配置" : "启用后自动检测连接，无需配对码"
    onDoneRequested: close()
    onOpened: { showHelp = false; bridge.beginSetup(browser) }
    FigmaTheme { id: theme }

    contentItem: UiScrollView {
        objectName: "browserSetupScroll"
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 16
            Item { Layout.preferredHeight: 4 }
            Repeater {
                model: 3
                delegate: Rectangle {
                    id: step
                    required property int index
                    Layout.fillWidth: true
                    Layout.leftMargin: 24; Layout.rightMargin: 24
                    implicitHeight: fields.implicitHeight + 32
                    color: theme.surface; radius: 12; border.color: theme.line
                    RowLayout {
                        id: fields
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }
                        spacing: 12
                        Rectangle {
                            Layout.alignment: Qt.AlignTop
                            Layout.preferredWidth: 28; Layout.preferredHeight: 28
                            radius: 14; color: theme.selection
                            Label { anchors.centerIn: parent; text: step.index + 1; color: theme.primary; font.bold: true }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            spacing: 8
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.Wrap
                                text: step.index === 0 ? (dialog.isChrome && !dialog.development ? "打开 Chrome 应用商店" : "打开浏览器扩展页") : step.index === 1
                                      ? (dialog.development ? "加载我们的扩展文件夹" : dialog.isChrome ? "添加 Mythlink 浏览器助手" : "勾选 Mythlink 浏览器助手")
                                      : "允许网站访问，刷新媒体页面"
                                font.pixelSize: 15; font.weight: Font.DemiBold; color: theme.text
                            }
                            Label {
                                objectName: "browserSetupStep" + (step.index + 1)
                                Layout.fillWidth: true; wrapMode: Text.Wrap
                                font.pixelSize: 13; color: theme.muted; lineHeight: 1.2
                                text: step.index === 0
                                    ? (dialog.development ? "点击下方按钮，自动准备扩展、复制文件夹路径并打开 Chrome 扩展管理页。"
                                        : dialog.isChrome ? "打开扩展页面，在常用的 Chrome 个人资料中完成添加。"
                                        : "点击下方按钮，进入 Safari → 设置 → 扩展。")
                                    : step.index === 1
                                    ? (dialog.development ? "打开右上角「开发者模式」，点击「加载已解压的扩展程序」（Load unpacked）。\n在文件夹选择器按 ⌘⇧G，再按 ⌘V 粘贴路径，回车后点击「选择」。"
                                        : dialog.isChrome ? "点击「添加至 Chrome」并确认。若已安装，在扩展列表中启用即可。"
                                        : "在左侧列表勾选「Mythlink 浏览器助手」，按 Safari 提示确认。扩展已包含在应用里，无需选择文件夹。")
                                    : "允许你需要控制的网站访问，然后刷新视频或音频页面。保持 Mythlink 运行，连接状态会自动更新。"
                            }
                            FigmaAction {
                                objectName: step.index === 0 ? "browserSetupOpen" : ""
                                visible: step.index === 0
                                Layout.alignment: Qt.AlignLeft
                                primary: true
                                enabled: dialog.isChrome || !dialog.bridge.safariSettingsOpening
                                text: dialog.development ? "复制路径并打开扩展页" : dialog.isChrome ? "打开 Chrome 应用商店"
                                      : dialog.bridge.safariSettingsOpening ? "正在打开…" : "打开 Safari 扩展页"
                                onClicked: dialog.isChrome ? dialog.bridge.connectChrome() : dialog.bridge.connectSafari()
                            }
                            Label {
                                visible: step.index === 1 && dialog.development
                                Layout.fillWidth: true; wrapMode: Text.Wrap
                                text: "列表已有 Mythlink？直接启用；更新过扩展文件时，点击卡片上的重新加载按钮。"
                                font.pixelSize: 12; color: theme.muted
                            }
                            Flow {
                                visible: step.index === 1 && dialog.development && dialog.bridge.chromePath.length > 0
                                Layout.fillWidth: true
                                spacing: 8
                                FigmaAction { objectName: step.index === 1 ? "browserSetupCopyPath" : ""; text: "复制扩展路径"; onClicked: dialog.bridge.copyChromePath() }
                                FigmaAction { objectName: step.index === 1 ? "browserSetupShowFolder" : ""; text: "显示扩展文件夹"; quiet: true; onClicked: dialog.bridge.revealChrome() }
                            }
                            Label {
                                objectName: step.index === 2 ? "browserSetupConnection" : ""
                                visible: step.index === 2
                                Layout.fillWidth: true; wrapMode: Text.Wrap
                                text: dialog.isChrome ? dialog.bridge.chromeStatus : dialog.bridge.safariStatus
                                font.pixelSize: 13; color: dialog.connected ? theme.success : theme.warning
                            }
                        }
                    }
                }
            }
            FigmaAction {
                objectName: "browserSetupHelp"
                Layout.leftMargin: 24; Layout.alignment: Qt.AlignLeft
                text: dialog.showHelp ? "收起帮助" : dialog.isChrome ? "打开的页面不对？" : "扩展列表里没有 Mythlink？"
                quiet: true
                onClicked: dialog.showHelp = !dialog.showHelp
            }
            ColumnLayout {
                visible: dialog.showHelp
                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24
                spacing: 8
                Label {
                    objectName: "browserSetupHelpText"
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    text: dialog.isChrome ? "在你常用的 Chrome 窗口地址栏输入 chrome://extensions/。确认使用的是要安装扩展的个人资料，再按上面的步骤加载。"
                        : "当前测试版尚未公证。如果列表为空：\n1. Safari → 设置 → 高级，打开「显示网页开发者功能」。\n2. 在「开发者」中勾选 Allow unsigned extensions（允许未签名扩展），亲自完成系统验证。\n3. 返回「扩展」，勾选 Mythlink 并允许网站访问。\nSafari 重启后，这项测试许可可能需要重新开启。"
                    font.pixelSize: 12; color: theme.muted; lineHeight: 1.2
                }
                FigmaAction {
                    visible: dialog.isChrome
                    text: "复制扩展页地址"; onClicked: dialog.bridge.copyChromeExtensionsAddress()
                }
            }
            Label {
                objectName: "browserSetupMessage"
                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24
                visible: text.length > 0
                text: dialog.bridge.message
                wrapMode: Text.Wrap; font.pixelSize: 12; color: theme.muted
            }
            Item { Layout.preferredHeight: 8 }
        }
    }
}
