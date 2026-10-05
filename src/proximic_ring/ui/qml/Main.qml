import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: root
    width: 1440
    height: 940
    minimumWidth: 940
    minimumHeight: 700
    visible: true
    title: "Mythlink"
    color: uiTheme.background
    Material.theme: Material.Light
    Material.accent: uiTheme.primary
    Material.primary: uiTheme.primary
    Material.background: uiTheme.surface
    Material.foreground: uiTheme.text
    readonly property string uiFontFamily: Qt.platform.os === "osx"
                                           ? ".AppleSystemUIFont"
                                           : "Microsoft YaHei UI"
    font.family: uiFontFamily


    UiTheme { id: uiTheme }
    property int currentPage: 0
    readonly property var pageTitles: ["我的 Ring", "语音输入", "场景与手势", "触摸板"]
    readonly property int sidebarWidth: width < 1100 ? 180 : 218
    function showVoice(history) {
        currentPage = 1
        Qt.callLater(function() {
            if (history) mainPageScroll.scrollToHistory()
            else mainPageScroll.contentItem.contentY = 0
        })
    }
    function showSettings(page) {
        // Legacy service routes open the single advanced form.
        if (page === 4 || page === 5) {
            showAdvancedSettings(page)
            return
        }
        runtimeSettingsDialog.open()
        runtimeSettingsDialog.navigate(page)
    }
    function showAdvancedSettings(section) {
        runtimeSettingsDialog.saveCurrentEditor()
        advancedSettingsDialog.returnScrollPosition = runtimeSettingsScroll.contentItem.contentY
        advancedSettingsDialog.initialSection = section === 5 ? 5 : 4
        runtimeSettingsDialog.close()
        advancedSettingsDialog.open()
    }
    property color panel: uiTheme.surface
    property color panelAlt: uiTheme.subtle
    property color border: uiTheme.line
    property color primary: uiTheme.primary
    property color textMain: uiTheme.text
    property color textMuted: uiTheme.muted

    component GestureBindingSelector: ComboBox {
        id: gestureSelector
        required property string actionName
        required property int slotIndex
        objectName: actionName + "Gesture" + slotIndex
        property var options: {
            // Register the settings dependency even though options come from a slot.
            var bindings = appController.gestureBindings
            return appController.gestureOptionsForSlot(actionName, slotIndex)
        }
        readonly property string selectedGesture: appController.gestureBindings[actionName][slotIndex]
        readonly property int selectedIndex: {
            for (var i = 0; i < options.length; ++i)
                if (options[i].value === selectedGesture) return i
            return 0
        }
        Layout.fillWidth: true
        Layout.preferredHeight: 44
        model: options
        textRole: "label"
        valueRole: "value"
        currentIndex: selectedIndex
        Accessible.name: ({confirm: "确认", undo: "撤销", switch_mode: "类型转换"})[actionName] + "，手势 " + (slotIndex + 1)
        onActivated: {
            appController.setGestureBinding(actionName, slotIndex, options[currentIndex].value)
            // Restore the binding also when validation rejects a selection.
            currentIndex = Qt.binding(function() { return gestureSelector.selectedIndex })
        }
    }

    component GestureBindingRow: ColumnLayout {
        id: gestureRow
        required property string actionName
        required property string title
        Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
        spacing: 4
        Label { text: gestureRow.title; color: root.textMain; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            GestureBindingSelector { actionName: gestureRow.actionName; slotIndex: 0 }
            GestureBindingSelector { actionName: gestureRow.actionName; slotIndex: 1 }
        }
    }

    component SegmentedChoice: Rectangle {
        id: segmentedChoice
        property var options: []
        property int currentIndex: 0
        signal activated(int index)

        implicitHeight: 44
        radius: 12
        color: "#F3F5FB"
        border.width: 1
        border.color: "#DFE4F0"

        Row {
            anchors.fill: parent
            anchors.margins: 4
            spacing: 4

            Repeater {
                model: segmentedChoice.options

                Rectangle {
                    required property int index
                    required property string modelData
                    width: (parent.width - Math.max(0, segmentedChoice.options.length - 1) * parent.spacing)
                           / Math.max(1, segmentedChoice.options.length)
                    height: parent.height
                    radius: 9
                    color: index === segmentedChoice.currentIndex
                           ? "#E5EBFF" : "transparent"
                    border.width: index === segmentedChoice.currentIndex ? 1 : 0
                    border.color: "#C0CFFA"
                    opacity: segmentedChoice.enabled ? 1.0 : 0.45

                    Behavior on color { ColorAnimation { duration: 120 } }

                    Text {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        text: modelData
                        color: index === segmentedChoice.currentIndex
                               ? "#153FC4" : "#69748B"
                        font.family: root.uiFontFamily
                        font.pixelSize: 12
                        font.weight: index === segmentedChoice.currentIndex
                                     ? Font.DemiBold : Font.Normal
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }

                    MouseArea {
                        anchors.fill: parent
                        enabled: segmentedChoice.enabled
                        hoverEnabled: true
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: segmentedChoice.activated(index)
                    }
                }
            }
        }
    }

    component SettingsSectionHeader: RowLayout {
        id: sectionHeader
        property string title: ""
        property string badge: ""
        property color accent: root.primary

        spacing: 9

        Rectangle {
            Layout.preferredWidth: 4
            Layout.preferredHeight: 20
            radius: 2
            color: sectionHeader.accent
        }
        Label {
            text: sectionHeader.title
            color: root.textMain
            font.pixelSize: 15
            font.bold: true
        }
        Item { Layout.fillWidth: true }
        Rectangle {
            visible: sectionHeader.badge.length > 0
            Layout.preferredWidth: sectionBadge.implicitWidth + 16
            Layout.preferredHeight: 24
            radius: 12
            color: Qt.rgba(
                sectionHeader.accent.r,
                sectionHeader.accent.g,
                sectionHeader.accent.b,
                0.12
            )
            border.width: 1
            border.color: Qt.rgba(
                sectionHeader.accent.r,
                sectionHeader.accent.g,
                sectionHeader.accent.b,
                0.35
            )
            Label {
                id: sectionBadge
                anchors.centerIn: parent
                text: sectionHeader.badge
                color: sectionHeader.accent
                font.pixelSize: 10
                font.bold: true
            }
        }
    }


    onClosing: function(close) {
        close.accepted = false
        root.hide()
        appController.requestQuit()
    }

    Connections {
        target: appController
        function onDevicePickerRequested() {
            root.currentPage = 0
            devicePicker.open()
        }
    }

    Dialog {
        id: inputMethodSetupDialog
        objectName: "inputMethodSetupDialog"
        parent: Overlay.overlay
        popupType: Popup.Item
        title: "语音输入法设置"
        anchors.centerIn: parent
        width: Math.min(560, root.width - 48)
        modal: true
        Material.theme: Material.Light
        background: Rectangle { color: root.panel; radius: 18; border.color: root.border }
        Overlay.modal: Rectangle { color: "#99000000" }
        standardButtons: Dialog.Close
        closePolicy: appController.inlineInput.installing ? Popup.NoAutoClose : Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: appController.inlineInput.permissions.refresh()
        contentItem: ScrollView {
            id: inputMethodSetupScroll
            objectName: "inputMethodSetupScroll"
            clip: true
            contentWidth: availableWidth
            implicitHeight: Math.min(inputMethodSetupContent.implicitHeight, root.height - 200)
            ColumnLayout {
            id: inputMethodSetupContent
            width: inputMethodSetupScroll.availableWidth
            spacing: 14
            Label {
                Layout.fillWidth: true
                text: "1. 安装语音输入法"
                color: root.textMain
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: "首次使用点击安装即可。更新前请先切回拼音或 ABC。"
                color: root.textMuted
                wrapMode: Text.Wrap
            }
            RowLayout {
                Button {
                    objectName: "installInputMethodButton"
                    text: appController.inlineInput.installing ? "正在安装…" : "安装／更新输入法"
                    enabled: !appController.inlineInput.installing
                    onClicked: appController.inlineInput.installInputMethod()
                }
                BusyIndicator {
                    running: appController.inlineInput.installing
                    visible: running
                    Layout.preferredWidth: 28
                    Layout.preferredHeight: 28
                }
            }
            Label {
                objectName: "inputMethodInstallationMessage"
                Layout.fillWidth: true
                text: appController.inlineInput.installationMessage
                color: root.textMuted
                wrapMode: Text.Wrap
            }
            PermissionNotice {
                objectName: "inputMethodPermissionNotice"
                Layout.fillWidth: true
                message: appController.inlineInput.permissions.accessibilityWarningText
                onActivated: appController.inlineInput.openAccessibilitySettings()
            }
            Label {
                Layout.fillWidth: true
                text: "2. 启用语音输入法"
                color: root.textMain
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: "在键盘设置的输入法列表中启用语音输入法。连接 Ring 并开启识别后，点入文本框再 Tap 即可开始听写。"
                color: root.textMuted
                wrapMode: Text.Wrap
            }
            Button {
                text: "打开键盘设置"
                onClicked: appController.inlineInput.openInputSettings()
            }
            }
        }
    }

    Dialog {
        id: devicePicker
        objectName: "devicePicker"
        parent: Overlay.overlay
        x: Math.round((parent.width - width) / 2)
        y: Math.round((parent.height - height) / 2)
        width: Math.min(620, parent.width - 48)
        height: Math.min(520, parent.height - 48)
        modal: true
        popupType: Popup.Item
        title: "选择蓝牙设备"
        closePolicy: Popup.CloseOnEscape
        onClosed: appController.stopDeviceDiscovery()

        contentItem: ColumnLayout {
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                UiTextField {
                    id: deviceSearchField
                    objectName: "deviceSearchField"
                    Layout.fillWidth: true
                    text: appController.deviceSearch
                    hintText: "搜索设备名称或标识"
                    selectByMouse: true
                    onTextEdited: appController.deviceSearch = text
                }

                Button {
                    text: "清除"
                    enabled: deviceSearchField.text.length > 0
                    onClicked: {
                        deviceSearchField.clear()
                        appController.deviceSearch = ""
                        deviceSearchField.forceActiveFocus()
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: appController.scanMessage
                    color: root.textMuted
                    font.pixelSize: 13
                    wrapMode: Text.Wrap
                }
                BusyIndicator {
                    running: appController.scanBusy
                    visible: running
                    implicitWidth: 30
                    implicitHeight: 30
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 12
                color: root.panelAlt
                border.color: root.border
                clip: true

                ListView {
                    id: deviceList
                    objectName: "deviceList"
                    anchors.fill: parent
                    anchors.margins: 6
                    spacing: 6
                    clip: true
                    model: appController.availableDevices
                    ScrollBar.vertical: ScrollBar { }

                    delegate: Rectangle {
                        required property var modelData
                        width: deviceList.width
                        height: 70
                        radius: 9
                        color: connectButton.hovered ? "#EBEFFB" : "transparent"
                        border.color: connectButton.hovered ? root.primary : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 14
                            anchors.rightMargin: 10
                            spacing: 12

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3
                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.name
                                    color: root.textMain
                                    font.pixelSize: 14
                                    font.bold: true
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.identifier
                                          + (modelData.rssi === "" ? "" : "  ·  " + modelData.rssi + " dBm")
                                    color: root.textMuted
                                    font.pixelSize: 11
                                    elide: Text.ElideMiddle
                                }
                            }

                            Button {
                                id: connectButton
                                text: "连接"
                                enabled: !appController.busy
                                onClicked: {
                                    devicePicker.close()
                                    appController.connectToDevice(modelData.identifier, modelData.name)
                                }
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button {
                    text: appController.scanBusy ? "扫描中…" : "重新扫描"
                    enabled: !appController.scanBusy && !appController.busy
                    onClicked: appController.scanDevices()
                }
                Button {
                    text: "取消"
                    onClicked: devicePicker.close()
                }
            }
        }
    }

    Dialog {
        id: gpuInstallDialog
        objectName: "gpuInstallDialog"
        parent: Overlay.overlay
        x: Math.round((parent.width - width) / 2)
        y: Math.round((parent.height - height) / 2)
        width: Math.min(500, parent.width - 48)
        implicitHeight: 210
        modal: true
        popupType: Popup.Item
        title: "安装 NVIDIA GPU 加速"
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: appController.installGpuSupport()

        contentItem: Label {
            text: "安装需要下载数 GB 文件。应用将退出并打开独立安装窗口；安装验证成功后会自动重新启动。是否继续？"
            color: root.textMain
            font.pixelSize: 13
            wrapMode: Text.Wrap
        }
    }

    Dialog {
        id: runtimeLogDialog
        objectName: "runtimeLogDialog"
        parent: Overlay.overlay
        x: Math.round((parent.width - width) / 2)
        y: Math.round((parent.height - height) / 2)
        width: Math.min(820, parent.width - 48)
        height: Math.min(620, parent.height - 48)
        modal: true
        popupType: Popup.Item
        title: "运行诊断日志"
        closePolicy: Popup.CloseOnEscape
        onOpened: logArea.refreshLog()
        onClosed: logRefreshTimer.stop()

        // Rebuilding a hidden TextArea still performs text layout on the UI
        // thread. Keep telemetry off the voice path, and coalesce visible
        // bursts so each ASR partial does not lay out the whole history.
        Timer {
            id: logRefreshTimer
            interval: 150
            onTriggered: {
                if (runtimeLogDialog.visible)
                    logArea.refreshLog()
            }
        }

        contentItem: ColumnLayout {
            spacing: 12

            ScrollView {
                id: logScroll
                objectName: "logScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                TextArea {
                    id: logArea
                    objectName: "logArea"
                    width: logScroll.availableWidth
                    readOnly: true
                    textFormat: TextEdit.PlainText
                    text: "尚未启动"
                    color: root.textMuted
                    font.family: Qt.platform.os === "osx" ? "Menlo" : "Cascadia Mono"
                    font.pixelSize: 14
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    background: Rectangle { color: root.panelAlt; radius: 10 }

                    function refreshLog() {
                        var viewport = logScroll.contentItem
                        var previousY = viewport ? viewport.contentY : 0
                        var previousCursor = cursorPosition
                        var previousSelectionStart = selectionStart
                        var previousSelectionEnd = selectionEnd
                        var nextText = appController.logText
                        text = nextText.length > 0 ? nextText : "尚未启动"
                        cursorPosition = Math.min(previousCursor, length)
                        if (previousSelectionStart !== previousSelectionEnd) {
                            select(
                                Math.min(previousSelectionStart, length),
                                Math.min(previousSelectionEnd, length)
                            )
                        }
                        Qt.callLater(function() {
                            if (!viewport)
                                return
                            var maximumY = Math.max(0, viewport.contentHeight - viewport.height)
                            viewport.contentY = Math.max(0, Math.min(previousY, maximumY))
                        })
                    }

                    function jumpToLatest() {
                        cursorPosition = length
                        Qt.callLater(function() {
                            var viewport = logScroll.contentItem
                            if (viewport)
                                viewport.contentY = Math.max(
                                    0, viewport.contentHeight - viewport.height
                                )
                        })
                    }

                    Connections {
                        target: appController
                        function onLogChanged() {
                            if (runtimeLogDialog.visible && !logRefreshTimer.running)
                                logRefreshTimer.start()
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: "窗口保留最近 1000 行；完整诊断日志会持久保存并自动轮转"
                    color: root.textMuted
                    font.pixelSize: 11
                }
                Button {
                    objectName: "openDiagnosticLogDirectoryButton"
                    text: "打开日志目录"
                    onClicked: appController.openDiagnosticLogDirectory()
                }
                Button {
                    objectName: "jumpToLatestLogButton"
                    text: "跳到最新"
                    onClicked: logArea.jumpToLatest()
                }
                Button { text: "清空窗口"; onClicked: appController.clearLog() }
                Button { text: "关闭"; onClicked: runtimeLogDialog.close() }
            }
        }
    }

    Rectangle {
        id: sidebar
        objectName: "mainSidebar"
        width: root.sidebarWidth
        anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.left: parent.left
        color: uiTheme.sidebar
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 16; spacing: 10
            Image {
                objectName: "mythlinkLogo"
                Layout.topMargin: 22; Layout.leftMargin: 6; Layout.bottomMargin: 38
                Layout.preferredWidth: Math.min(162, sidebar.width - 44)
                Layout.preferredHeight: Layout.preferredWidth * 31.4473 / 162
                source: "../assets/figma/mythlink-logo.svg"
                sourceSize.width: Math.ceil(width * Screen.devicePixelRatio)
                fillMode: Image.PreserveAspectFit
                Accessible.name: "Mythlink"
            }
            Repeater {
                model: [{label: "首页", icon: "home", page: 0}, {label: "语音输入", icon: "voice", page: 1}, {label: "触摸板", icon: "pointer", page: 3}, {label: "场景与手势", icon: "gesture", page: 2}]
                delegate: AbstractButton {
                    required property int index
                    required property var modelData
                    objectName: "mainNav" + modelData.page
                    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: sidebar.width - 32; implicitHeight: 48
                    hoverEnabled: true
                    Accessible.name: modelData.label
                    checked: root.currentPage === modelData.page
                    onClicked: root.currentPage = modelData.page
                    background: Rectangle { radius: 10; color: parent.checked ? uiTheme.selection : parent.hovered ? "#E6E9F3" : "transparent"; border.color: parent.activeFocus ? uiTheme.primary : "transparent" }
                    contentItem: RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 12; spacing: 13
                        UiIcon { Layout.preferredWidth: 21; Layout.preferredHeight: 21; symbol: modelData.icon; ink: uiTheme.text }
                        Label { Layout.fillWidth: true; text: modelData.label; color: uiTheme.text; font.pixelSize: 16; font.weight: root.currentPage === modelData.page ? Font.DemiBold : Font.Normal }
                    }
                }
            }
            Item { Layout.fillHeight: true }
            Rectangle { Layout.fillWidth: true; height: 1; color: uiTheme.line }
            RowLayout {
                Layout.fillWidth: true; spacing: 4
                UiAction { objectName: "contactButton"; text: ""; symbol: "mail"; quiet: true; Accessible.name: "联系我们"; onClicked: contactDialog.open(); ToolTip.visible: hovered; ToolTip.text: "联系我们" }
                Item { Layout.fillWidth: true }
                UiAction { objectName: "runtimeSettingsButton"; text: ""; symbol: "settings"; quiet: true; Accessible.name: "设置"; onClicked: runtimeSettingsDialog.open(); ToolTip.visible: hovered; ToolTip.text: "设置" }
                UiAction { objectName: "helpButton"; text: ""; symbol: "help"; quiet: true; Accessible.name: "帮助"; onClicked: helpDialog.open(); ToolTip.visible: hovered; ToolTip.text: "帮助" }
            }
        }
    }
    Item {
        id: workspace
        anchors.left: sidebar.right; anchors.right: parent.right
        anchors.top: parent.top; anchors.bottom: parent.bottom
        anchors.leftMargin: root.width < 1100 ? 24 : 40
        anchors.rightMargin: root.width < 1100 ? 24 : 40
        Item {
            id: pageHeader
            anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
            height: 108
            Column {
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; spacing: 5
                Label { text: root.pageTitles[root.currentPage]; color: uiTheme.text; font.pixelSize: 32; font.weight: Font.Bold }
                Label { text: root.currentPage === 0 ? (appController.deviceName || "连接你的 Ring，开始使用") : root.currentPage === 1 ? "说话完成输入与修改，回顾每一次语音记录" : root.currentPage === 3 ? "让 Ring 成为你的触摸板，直接控制系统指针" : "查看手势与应用操作的对应关系"; color: uiTheme.muted; font.pixelSize: 12 }
            }
            RowLayout {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                Rectangle { width: 7; height: 7; radius: 4; color: appController.connected ? uiTheme.success : "#9AA3B6" }
                Label { text: appController.connected ? "Ring 已连接" : "未连接设备"; color: uiTheme.muted; font.pixelSize: 12 }
            }
        }
        Item {
            id: pageBody
            anchors.top: pageHeader.bottom; anchors.bottom: parent.bottom
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottomMargin: 24
            HomePage {
                anchors.fill: parent
                visible: root.currentPage === 0
                controller: appController
                onVoiceRequested: function(history) { root.showVoice(history) }
                onGesturesRequested: root.currentPage = 2
                onHelpRequested: helpDialog.open()
                onDevicePickerRequested: appController.requestDevicePicker()
                onConnectionRequested: {
                    if (appController.connected) appController.disconnectDevice()
                    else if (appController.canReconnect) appController.reconnectDevice()
                    else appController.requestDevicePicker()
                }
            }
            GesturesPage {
                anchors.fill: parent
                visible: root.currentPage === 2
                controller: appController
            }
        }
    }
    Dialog {
        id: contactDialog
        objectName: "contactDialog"
        parent: Overlay.overlay
        popupType: Popup.Item
        anchors.centerIn: parent
        width: Math.min(480, root.width - 48)
        modal: true
        title: "联系我们"
        property bool copied: false
        property bool emailCopied: false
        onOpened: { copied = false; emailCopied = false }
        footer: Item {
            implicitHeight: 58
            UiAction {
                anchors { right: parent.right; bottom: parent.bottom; margins: 16 }
                text: "关闭"; quiet: true
                onClicked: contactDialog.close()
            }
        }
        background: Rectangle { color: uiTheme.surface; radius: 16; border.color: uiTheme.line }
        contentItem: ColumnLayout {
            spacing: 16
            Label { Layout.fillWidth: true; text: "让 Mythlink 更好用"; font.pixelSize: 18; font.bold: true; color: uiTheme.text }
            Label { Layout.fillWidth: true; text: "欢迎向我们反馈使用中遇到的问题，也欢迎分享你的想法和建议。"; wrapMode: Text.Wrap; color: uiTheme.muted }
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: appController.inlineInput.permissions.contactEmail; color: uiTheme.primary; font.pixelSize: 16; wrapMode: Text.WrapAnywhere }
                UiAction {
                    objectName: "copyContactEmailButton"
                    text: contactDialog.emailCopied ? "已复制" : "复制邮箱"; quiet: true
                    onClicked: contactDialog.emailCopied = appController.inlineInput.permissions.copyContactEmail()
                }
            }
            Label { Layout.fillWidth: true; text: "反馈问题时，可以附上使用的应用、操作步骤和截图，方便我们了解具体情况。"; wrapMode: Text.Wrap; color: uiTheme.muted }
            UiAction {
                objectName: "copyContactAppInfoButton"
                text: contactDialog.copied ? "已复制应用信息" : "复制应用信息"
                onClicked: contactDialog.copied = appController.inlineInput.permissions.copyAppInfo()
            }
            Label { Layout.fillWidth: true; text: "应用信息包含版本号和系统版本，方便反馈时附上。"; wrapMode: Text.Wrap; color: uiTheme.muted; font.pixelSize: 12 }
        }
    }
    Dialog {
        id: helpDialog
        objectName: "helpDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, root.width - 48)
        modal: true
        title: "使用帮助"
        standardButtons: Dialog.Close
        background: Rectangle { color: uiTheme.surface; radius: 16; border.color: uiTheme.line }
        contentItem: ColumnLayout {
            spacing: 16
            Label { Layout.fillWidth: true; text: "Mythlink · 戒指交互平台"; font.pixelSize: 18; font.bold: true; color: uiTheme.text }
            Label { Layout.fillWidth: true; text: "融合语音输入、鼠标控制、手势识别与场景联动，在一个平台中管理戒指的交互功能。"; wrapMode: Text.Wrap; color: uiTheme.muted }
            Label { Layout.fillWidth: true; text: "语音输入"; font.pixelSize: 16; font.bold: true; color: uiTheme.text }
            Label { Layout.fillWidth: true; text: "连接 Ring 并开启语音识别。点入文本框后，使用当前配置的语音手势开始输入；语音输入页可查看记录。"; wrapMode: Text.Wrap; color: uiTheme.muted }
            Label { Layout.fillWidth: true; text: "鼠标控制"; font.pixelSize: 16; font.bold: true; color: uiTheme.text }
            Label { Layout.fillWidth: true; text: "在触摸板页开启鼠标控制，通过戒指触摸板移动指针和轻触点击，并调整指针速度。"; wrapMode: Text.Wrap; color: uiTheme.muted }
            Label { Layout.fillWidth: true; text: "场景与手势"; font.pixelSize: 16; font.bold: true; color: uiTheme.text }
            Label { Layout.fillWidth: true; text: "Tap 与四向滑动组成语音手势组。应用覆盖其中任意一个后，该应用常规状态下停用整组语音操作。放映、阅读和播放等场景独立配置；全局功能占用的手势会在应用内置灰。"; wrapMode: Text.Wrap; color: uiTheme.muted }
            UiAction { Layout.alignment: Qt.AlignLeft; text: "输入法与权限设置"; onClicked: { helpDialog.close(); inputMethodSetupDialog.open() } }
            UiAction { objectName: "runtimeLogButton"; Layout.alignment: Qt.AlignLeft; text: "故障排查 · 实时日志"; onClicked: { helpDialog.close(); runtimeLogDialog.open() } }
        }
    }

    TouchpadPage {
        parent: pageBody
        anchors.fill: parent
        visible: root.currentPage === 3
        controller: appController
        onHomeRequested: root.currentPage = 0
    }

    VoicePage {
        id: mainPageScroll
        parent: pageBody
        visible: root.currentPage === 1
        anchors.fill: parent
        controller: appController
        onHomeRequested: root.currentPage = 0
        onInputSetupRequested: inputMethodSetupDialog.open()
    }

        AdvancedSettingsDialog {
            id: advancedSettingsDialog
            controller: appController
            onGpuSetupRequested: gpuInstallDialog.open()
            onClosed: {
                if (root.visible) {
                    runtimeSettingsDialog.open()
                    Qt.callLater(function() {
                        runtimeSettingsScroll.contentItem.contentY = advancedSettingsDialog.returnScrollPosition
                    })
                }
            }
        }

        Dialog {
            id: runtimeSettingsDialog
            objectName: "runtimeSettingsDialog"
            parent: Overlay.overlay
            x: Math.round((parent.width - width) / 2)
            y: Math.round((parent.height - height) / 2)
            width: Math.min(820, parent.width - 48)
            height: Math.min(860, parent.height - 48)
            modal: true
            popupType: Popup.Item
            property int currentPage: 0
            readonly property var pageTitles: ["设置", "输入与控制", "手势说明", "麦克风与语音", "语音识别服务", "文本处理服务", "系统与权限", "应用快捷键", "微信快捷键设置", "离开锁屏"]
            title: pageTitles[currentPage]
            Overlay.modal: Rectangle { color: "#99000000" }
            header: Label {
                text: runtimeSettingsDialog.title
                leftPadding: 24; rightPadding: 24; topPadding: 22; bottomPadding: 14
                color: root.textMain; font.pixelSize: 28; font.bold: true
            }
            enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 100 } }
            exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 80 } }
            onClosed: {
                appController.appGestures.recording = false
                appController.inlineInput.permissions.cancelScreenPreview()
            }
            onAboutToShow: navigate(0)
            onOpened: appController.refreshMicrophones()
            closePolicy: Popup.CloseOnEscape
            readonly property bool deviceSettingsLocked:
                appController.connected || appController.busy
            function stage1Sensitivity(threshold) {
                var safe = Math.max(0.001, Math.min(0.05, Number(threshold)))
                return 1 + 9 * Math.log(0.05 / safe) / Math.log(50)
            }
            function thresholdForSensitivity(sensitivity) {
                return 0.05 * Math.pow(0.02, (Number(sensitivity) - 1) / 9)
            }
            background: Rectangle {
                radius: 18
                color: "#FFFFFF"
                border.width: 1
                border.color: "#E1E5F0"
            }

            function navigate(page) {
                // A ScrollView is a focus scope and can retain its editor's
                // focus. Focus the plain container so pending edits are saved.
                saveCurrentEditor()
                appController.appGestures.recording = false
                if (page !== 6)
                    appController.inlineInput.permissions.cancelScreenPreview()
                // Former audio/input detail routes now land on the grouped form.
                currentPage = page === 1 || page === 3 ? 0 : page
                if (page === 6 && Qt.platform.os === "osx")
                    appController.inlineInput.permissions.refreshScreenRecording()
                Qt.callLater(function() { runtimeSettingsScroll.contentItem.contentY = 0 })
            }
            function goBack() {
                if (currentPage === 0) close()
                else if (currentPage === 7) close()
                else navigate(currentPage === 8 ? 7 : 0)
            }

            function applyAndClose() {
                // Move focus away from the active editor first so its
                // onEditingFinished/onActiveFocusChanged handler persists the value.
                saveCurrentEditor()
                Qt.callLater(function() { runtimeSettingsDialog.close() })
            }
            function saveCurrentEditor() {
                settingsContent.forceActiveFocus(Qt.OtherFocusReason)
            }

            contentItem: ColumnLayout {
                id: settingsContent
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    visible: runtimeSettingsDialog.currentPage !== 0
                    Button {
                        objectName: "settingsBackButton"
                        text: "返回"
                        onClicked: runtimeSettingsDialog.goBack()
                    }
                    Label {
                        Layout.fillWidth: true
                        text: runtimeSettingsDialog.currentPage >= 7 && runtimeSettingsDialog.currentPage <= 8 ? "场景与手势 / 应用快捷键" : "设置"
                        color: root.textMuted; font.pixelSize: 12
                        elide: Text.ElideLeft
                    }
                }
                ScrollView {
                    id: runtimeSettingsScroll
                    objectName: "runtimeSettingsScroll"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ScrollBar.vertical.policy: ScrollBar.AsNeeded
                    ColumnLayout {
                        width: runtimeSettingsScroll.availableWidth
                        spacing: 14
                        SettingsOverview {
                            objectName: "settingsPage0"
                            Layout.fillWidth: true
                            Layout.leftMargin: 20; Layout.rightMargin: 20
                            Layout.topMargin: 8; Layout.bottomMargin: 20
                            visible: runtimeSettingsDialog.currentPage === 0
                            controller: appController
                            settingsDialog: runtimeSettingsDialog
                            onDetailRequested: function(page) { runtimeSettingsDialog.navigate(page) }
                            onAdvancedSettingsRequested: root.showAdvancedSettings(4)
                            onInputMethodSetupRequested: inputMethodSetupDialog.open()
                        }
                        ProximitySettings {
                            objectName: "settingsPage9"
                            Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                            Layout.topMargin: 8; Layout.bottomMargin: 20
                            visible: runtimeSettingsDialog.currentPage === 9
                            controller: appController.proximity
                            host: appController
                        }
                        ColumnLayout {
                            objectName: "settingsPage2"
                            Layout.fillWidth: true
                            visible: runtimeSettingsDialog.currentPage === 2
                            spacing: 14
                            Label {
                                objectName: "ringGestureModeLabel"
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                text: "Ring 全局菜单 · " + appController.ringGestures.modeLabel
                                      + "\n提示、模式切换与窗口选择的手势在“场景与手势 → 全局默认”中配置。"
                                      + "\n应用覆盖语音组后，整组语音操作在该应用常规状态下停用。"
                                      + "\n下滑进入输入框选择，四向滑动移动高亮，5 秒无操作退出。"
                                      + "\n普通框选中即聚焦，Tap 开始语音；地址栏需先 Tap 确认聚焦。"
                                color: root.textMuted; font.pixelSize: 12; wrapMode: Text.Wrap
                            }
                            SettingsFormRow {
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                visible: appController.appGestures.supported
                                title: "发送与切换对话"
                                description: "选择 Codex、WorkBuddy 或微信，自定义手势对应的快捷键"
                                UiAction {
                                    objectName: "openAppGestureSettingsButton"
                                    Layout.alignment: Qt.AlignRight
                                    text: "设置快捷键"
                                    onClicked: runtimeSettingsDialog.navigate(7)
                                }
                            }

                            SettingsSectionHeader {
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                objectName: "gestureSettingsSection"
                                title: "听写手势"
                                badge: "即时生效"
                                accent: "#16875C"
                            }
                            Label {
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                text: "每项最多设置两个手势，语音操作之间不能重复分配。应用常规配置覆盖语音组的任意成员后，整组在该应用内停用；移除全部覆盖后恢复。"
                                color: root.textMuted; font.pixelSize: 11; wrapMode: Text.Wrap
                            }
                            GestureBindingRow { actionName: "confirm"; title: "确认 · 开始／结束本句语音" }
                            GestureBindingRow { actionName: "undo"; title: "撤销 · 处理中用于取消" }
                            GestureBindingRow { actionName: "switch_mode"; title: "转换 · 下划线期间转为编辑" }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                visible: appController.appGestures.supported
                                Label { text: "切入 Mythlink"; color: root.textMain }
                                ComboBox {
                                    id: inputSourceGestureSelector
                                    objectName: "inputSourceGestureSelector"
                                    Layout.fillWidth: true
                                    model: appController.appGestures.inputSourceOptions
                                    textRole: "label"; valueRole: "value"
                                    readonly property int selectedIndex: {
                                        var options = appController.appGestures.inputSourceOptions
                                        for (var i = 0; i < options.length; ++i)
                                            if (options[i].value === appController.appGestures.inputSourceGesture) return i
                                        return 0
                                    }
                                    currentIndex: selectedIndex
                                    onActivated: {
                                        appController.appGestures.setInputSourceGesture(currentValue)
                                        currentIndex = Qt.binding(function() { return inputSourceGestureSelector.selectedIndex })
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "默认未绑定，可自行选择空闲手势。此操作只切换输入法，不开始听写；暂停语音识别时也可用。tap 自动切换并开始听写不受此设置影响。"
                                    color: root.textMuted; font.pixelSize: 11; wrapMode: Text.Wrap
                                }
                            }
                            Label {
                                objectName: "gestureSettingsErrorLabel"
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                visible: appController.gestureSettingsError.length > 0
                                text: appController.gestureSettingsError
                                color: "#986A15"; font.pixelSize: 12; wrapMode: Text.Wrap
                            }
                            RowLayout {
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                Label {
                                    Layout.fillWidth: true
                                    text: "确认至少保留一个手势。设置会自动保存。"
                                    color: root.textMuted; font.pixelSize: 11; wrapMode: Text.Wrap
                                }
                                Button {
                                    objectName: "resetGestureBindingsButton"
                                    text: "恢复默认手势"
                                    onClicked: appController.resetGestureBindings()
                                }
                            }


                        }
                        ColumnLayout {
                            objectName: "settingsPage6"
                            Layout.fillWidth: true
                            visible: runtimeSettingsDialog.currentPage === 6
                            spacing: 14
                            SettingsFormRow {
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                visible: appController.inlineInput.enabled
                                title: "语音输入法"
                                description: "安装或更新输入法，在系统中启用"
                                UiAction {
                                    objectName: "openInputMethodSetupButton"
                                    Layout.alignment: Qt.AlignRight
                                    text: "安装与管理"
                                    onClicked: inputMethodSetupDialog.open()
                                }
                            }
                            SettingsFormGroup {
                                objectName: "screenRecordingPermissionCard"
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                visible: Qt.platform.os === "osx"
                                title: "窗口实时预览"
                                description: "开启屏幕录制权限后，可在窗口总览中查看各个窗口的实时画面。"
                                SettingsFormRow {
                                    title: "屏幕录制"
                                    description: appController.inlineInput.permissions.screenRecordingGranted
                                        ? "已开启" : "在系统设置中管理此权限。"
                                    UiAction {
                                        objectName: "openScreenRecordingPermissionsButton"
                                        text: "去系统设置"
                                        enabled: !appController.inlineInput.permissions.screenRecordingRequesting
                                        onClicked: appController.inlineInput.permissions.openScreenRecordingSettings()
                                    }
                                }
                                PermissionNotice {
                                    objectName: "screenRecordingPermissionNotice"
                                    Layout.fillWidth: true; actionText: ""
                                    message: appController.inlineInput.permissions.screenRecordingWarning
                                        ? "未开启屏幕录制权限，暂时无法显示窗口预览。仍可选择和切换窗口。" : ""
                                }
                            }
                        }
                        ColumnLayout {
                            objectName: "settingsPage7"
                            Layout.fillWidth: true
                            visible: runtimeSettingsDialog.currentPage === 7
                            spacing: 14
                            AppGestureSettings {
                                id: appGestureSettings
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                service: appController.appGestures
                                onWechatSetupRequested: runtimeSettingsDialog.navigate(8)
                            }
                        }
                        ColumnLayout {
                            objectName: "settingsPage8"
                            Layout.fillWidth: true
                            visible: runtimeSettingsDialog.currentPage === 8
                            spacing: 14
                            WeChatShortcutGuide {
                                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                                service: appController.appGestures
                            }
                        }
                    }
                }
            }

            footer: Rectangle {
                implicitHeight: 64
                color: root.panel
                radius: 18

                Rectangle {
                    width: parent.width; height: parent.radius
                    color: parent.color
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    height: 1
                    color: root.border
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 20
                    anchors.rightMargin: 20
                    spacing: 10

                    Label {
                        Layout.fillWidth: true
                        text: "设置自动保存"
                        color: root.textMuted
                        font.pixelSize: 11
                    }

                    UiAction {
                        id: settingsApplyButton
                        objectName: "settingsApplyButton"
                        Layout.preferredWidth: 88
                        Layout.preferredHeight: 38
                        text: "完成"
                        primary: true
                        onClicked: runtimeSettingsDialog.applyAndClose()
                    }
                }
            }
        }

    Window {
        objectName: "appGestureNotice"
        transientParent: null
        flags: Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
        width: 380
        height: appGestureNoticeText.implicitHeight + 28
        x: Screen.virtualX + Screen.width - width - 24
        y: Screen.virtualY + 48
        color: "transparent"
        visible: appController.appGestures.notice.length > 0
        Rectangle {
            anchors.fill: parent; radius: 12; color: "#FFFFFF"; border.color: "#E1E5F0"
            Label {
                id: appGestureNoticeText
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 14; wrapMode: Text.Wrap
                text: appController.appGestures.notice; color: "#171C28"; font.pixelSize: 13
            }
        }
    }

    Window {
        id: ringDisconnectNotice
        objectName: "ringDisconnectNotice"
        transientParent: null
        width: 420
        height: 216
        x: Screen.virtualX + Math.round((Screen.width - width) / 2)
        y: Screen.virtualY + Math.round((Screen.height - height) / 3)
        visible: appController.ringDisconnectNoticeVisible
        title: appController.ringDisconnectNoticeTitle
        color: "transparent"
        Material.theme: Material.Light
        Material.accent: root.primary
        flags: Qt.Window | Qt.FramelessWindowHint
               | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
        onClosing: function(close) {
            close.accepted = false
            appController.dismissRingDisconnectNotice()
        }

        Rectangle {
            anchors.fill: parent
            radius: 16
            color: "#FFFFFF"
            border.color: "#96505D"
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 22
                spacing: 8
                Label {
                    Layout.fillWidth: true
                    text: appController.ringDisconnectNoticeTitle
                    color: "#A9384B"
                    font.pixelSize: 21
                    font.bold: true
                }
                Label {
                    Layout.fillWidth: true
                    text: appController.ringDisconnectNoticeDevice
                    color: root.textMuted
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
                Label {
                    Layout.fillWidth: true
                    text: "语音和手势交互已停止。\n请重新连接 Ring 后再继续。"
                    color: root.textMain
                    font.pixelSize: 14
                    wrapMode: Text.Wrap
                }
                Item { Layout.fillHeight: true }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Label {
                        Layout.fillWidth: true
                        text: appController.busy ? "正在释放设备资源…" : ""
                        color: root.textMuted
                        font.pixelSize: 11
                    }
                    Button {
                        objectName: "dismissRingDisconnectNoticeButton"
                        text: "知道了"
                        onClicked: appController.dismissRingDisconnectNotice()
                    }
                    Button {
                        objectName: "reconnectRingFromNoticeButton"
                        text: "重新连接"
                        highlighted: true
                        enabled: appController.canReconnect && !appController.busy
                                 && !appController.scanBusy
                        onClicked: {
                            root.currentPage = 0
                            root.showNormal()
                            root.raise()
                            root.requestActivate()
                            appController.reconnectDevice()
                        }
                    }
                }
            }
        }
    }

    Window {
        id: associationRecommendationOverlay
        objectName: "associationRecommendationOverlay"
        readonly property bool hasTargetBounds:
            appController.associationPopupTargetWidth > 0
            && appController.associationPopupTargetHeight > 0
        width: 430
        height: 194
        x: {
            var preferred = Screen.width - width - 32
            if (hasTargetBounds) {
                var right = appController.associationPopupTargetX
                          + appController.associationPopupTargetWidth + 12
                var left = appController.associationPopupTargetX - width - 12
                var rightFits = right + width <= Screen.width - 12
                var leftFits = left >= 12
                if (rightFits)
                    preferred = right
                else if (leftFits)
                    preferred = left
                else
                    preferred = appController.associationPopupTargetX
            }
            return Math.round(Math.max(12, Math.min(Screen.width - width - 12,
                                                     preferred)))
        }
        y: {
            var preferred = 72
            if (hasTargetBounds) {
                var right = appController.associationPopupTargetX
                          + appController.associationPopupTargetWidth + 12
                var left = appController.associationPopupTargetX - width - 12
                var sideFits = right + width <= Screen.width - 12 || left >= 12
                if (sideFits) {
                    preferred = appController.associationPopupTargetY
                } else {
                    var above = appController.associationPopupTargetY - height - 12
                    var appliedBelowFits = appController.associationPopupTargetY
                                           + appController.associationPopupTargetHeight
                                           + 12
                                           <= Screen.height - 20
                    preferred = appliedBelowFits
                                ? above
                                : appController.associationPopupTargetY
                                  + appController.associationPopupTargetHeight + 12
                }
            }
            var maximum = Screen.height - height - 20
            return Math.round(Math.max(20, Math.min(maximum, preferred)))
        }
        visible: appController.associationRecommendationVisible
                 && !appController.associationDetailVisible
                 && !appController.associationCenterVisible
        color: "transparent"
        flags: (Qt.platform.os === "osx" ? Qt.Window : Qt.Tool)
               | Qt.FramelessWindowHint
        onClosing: function(close) {
            close.accepted = false
            appController.performAssociationAction("recommendation.reject", "")
        }

        Rectangle {
            anchors.fill: parent
            radius: 16
            color: "#F2FFFFFF"
            border.color: "#8A4DD4AC"
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 9

                Label {
                    Layout.fillWidth: true
                    text: appController.associationRecommendationTitle
                    color: root.textMain
                    font.pixelSize: 15
                    font.bold: true
                }
                Label {
                    Layout.fillWidth: true
                    text: appController.associationRecommendationPositiveLabel
                          + "："
                          + appController.associationRecommendationPositiveText
                    color: "#16875C"
                    font.pixelSize: 13
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }
                Item { Layout.fillHeight: true }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Button {
                        objectName: "acceptAssociationRecommendationButton"
                        Layout.minimumWidth: 92
                        text: "关联"
                        onClicked: appController.performAssociationAction(
                            "recommendation.accept", ""
                        )
                    }
                    Button {
                        objectName: "showAssociationDetailsButton"
                        Layout.minimumWidth: 108
                        text: "查看详情"
                        onClicked: appController.performAssociationAction(
                            "recommendation.details.open", ""
                        )
                    }
                    Item { Layout.fillWidth: true }
                    Button {
                        objectName: "rejectAssociationRecommendationButton"
                        Layout.minimumWidth: 92
                        text: "不关联"
                        onClicked: appController.performAssociationAction(
                            "recommendation.reject", ""
                        )
                    }
                }
            }
        }
    }

    Window {
        id: associationDetailsWindow
        objectName: "associationDetailsWindow"
        width: 620
        height: 560
        minimumWidth: 540
        minimumHeight: 420
        x: Math.round((Screen.width - width) / 2)
        y: Math.round((Screen.height - height) / 2)
        visible: appController.associationDetailVisible
        title: "推荐关联详情"
        color: root.color
        flags: Qt.Window | Qt.WindowStaysOnTopHint
        onClosing: function(close) {
            close.accepted = false
            appController.performAssociationAction(
                "recommendation.details.close", ""
            )
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 12

            Label {
                text: "推荐关联详情"
                color: root.textMain
                font.pixelSize: 19
                font.bold: true
            }
            Label {
                text: appController.associationRecommendationTitle
                color: root.textMuted
                font.pixelSize: 12
            }
            ListView {
                id: associationDetailsList
                objectName: "associationDetailsList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 10
                clip: true
                model: appController.associationDetailEntries
                ScrollBar.vertical: ScrollBar { }
                delegate: Rectangle {
                    required property var modelData
                    width: associationDetailsList.width
                    height: detailColumn.implicitHeight + 24
                    radius: 12
                    color: modelData.role === "chosen" ? "#253E3540" : "#3A282D40"
                    border.width: 2
                    border.color: modelData.role === "chosen" ? "#16875C" : "#FF646F"
                    ColumnLayout {
                        id: detailColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 6
                        Label {
                            text: modelData.role === "chosen" ? "✓ 正例" : "× 反例"
                            color: modelData.role === "chosen" ? "#16875C" : "#BE4155"
                            font.bold: true
                        }
                        Label {
                            Layout.fillWidth: true
                            text: "ASR：" + modelData.asrText
                            color: root.textMain
                            wrapMode: Text.Wrap
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: Boolean(modelData.resultText)
                            text: "结果：" + modelData.resultText
                            color: root.textMain
                            wrapMode: Text.Wrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: modelData.status
                                color: root.textMuted
                                font.pixelSize: 11
                            }
                            Button {
                                Layout.minimumWidth: 92
                                visible: Boolean(modelData.audioPath)
                                text: "播放录音"
                                onClicked: appController.playVoiceHistory(
                                    modelData.audioPath
                                )
                            }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Button {
                    Layout.minimumWidth: 112
                    text: "关联"
                    onClicked: appController.performAssociationAction(
                        "recommendation.accept", ""
                    )
                }
                Item { Layout.fillWidth: true }
                Button {
                    Layout.minimumWidth: 112
                    text: "不关联"
                    onClicked: appController.performAssociationAction(
                        "recommendation.reject", ""
                    )
                }
            }
        }
    }

    Window {
        id: associationCenterWindow
        objectName: "associationCenterWindow"
        width: 760
        height: 680
        minimumWidth: 640
        minimumHeight: 520
        x: Math.round((Screen.width - width) / 2)
        y: Math.round((Screen.height - height) / 2)
        visible: appController.associationCenterVisible
        title: "数据关联中心"
        color: root.color
        onClosing: function(close) {
            close.accepted = false
            appController.performAssociationAction("center.close", "")
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "数据关联中心"
                    color: root.textMain
                    font.pixelSize: 20
                    font.bold: true
                }
                Item { Layout.fillWidth: true }
                Button {
                    visible: appController.associationCenterStage !== "home"
                    Layout.minimumWidth: 104
                    text: "上一步"
                    onClicked: appController.performAssociationAction(
                        "center.back", ""
                    )
                }
            }

            Label {
                Layout.fillWidth: true
                visible: appController.associationCenterStage !== "home"
                text: appController.associationCenterStage === "type"
                      ? "步骤 1/3 · 选择关联类型"
                      : appController.associationCenterStage === "select"
                        ? "步骤 2/3 · 选择一个正例和一个或多个反例"
                        : "步骤 3/3 · 确认并创建关联"
                color: root.textMuted
                font.pixelSize: 12
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: appController.associationCenterStage === "home"
                spacing: 16

                Item { Layout.fillHeight: true }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: appController.associationCenterLastCreatedId === ""
                          ? "每次操作只创建一个独立的 Association"
                          : "已创建关联："
                            + appController.associationCenterLastCreatedId
                    color: appController.associationCenterLastCreatedId === ""
                           ? root.textMuted : "#16875C"
                    font.pixelSize: 14
                }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 480
                    text: "选择类型、正例和反例后，最后确认才会写入关联。"
                    color: root.textMuted
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                }
                Button {
                    objectName: "createAssociationButton"
                    Layout.alignment: Qt.AlignHCenter
                    Layout.minimumWidth: 180
                    Layout.preferredHeight: 48
                    text: "创建一次关联"
                    onClicked: appController.performAssociationAction(
                        "center.create", ""
                    )
                }
                Item { Layout.fillHeight: true }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: appController.associationCenterStage === "type"
                spacing: 14
                Button {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 74
                    text: "ASR关联\n听写与编辑指令"
                    onClicked: appController.performAssociationAction(
                        "center.kind", "asr"
                    )
                }
                Button {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 74
                    text: "LLM关联\n正确结果与失败编辑"
                    onClicked: appController.performAssociationAction(
                        "center.kind", "llm"
                    )
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: appController.associationCenterStage === "select"
                         && appController.associationCenterKind === "asr"
                Button {
                    Layout.minimumWidth: 120
                    text: "听写记录"
                    highlighted: appController.associationCenterAsrSubtype
                                 === "dictation_retry"
                    onClicked: appController.performAssociationAction(
                        "center.asrSubtype", "dictation_retry"
                    )
                }
                Button {
                    Layout.minimumWidth: 120
                    text: "编辑指令记录"
                    highlighted: appController.associationCenterAsrSubtype
                                 === "instruction_retry"
                    onClicked: appController.performAssociationAction(
                        "center.asrSubtype", "instruction_retry"
                    )
                }
                Item { Layout.fillWidth: true }
            }

            ListView {
                id: associationCenterList
                objectName: "associationCenterList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: appController.associationCenterStage === "select"
                spacing: 9
                clip: true
                model: appController.associationCenterEntries
                ScrollBar.vertical: ScrollBar { }
                delegate: Rectangle {
                    required property var modelData
                    readonly property string selectedRole: {
                        appController.associationCenterSelectionSummary
                        return appController.associationCenterRole(
                            modelData.interactionId || ""
                        )
                    }
                    width: associationCenterList.width
                    height: centerCardColumn.implicitHeight + 22
                    radius: 11
                    color: selectedRole === "chosen"
                           ? "#253E3540"
                           : selectedRole === "rejected"
                             ? "#3A282D40" : root.panelAlt
                    border.width: selectedRole === "" ? 1 : 2
                    border.color: selectedRole === "chosen"
                                  ? "#16875C"
                                  : selectedRole === "rejected"
                                    ? "#FF646F" : root.border
                    ColumnLayout {
                        id: centerCardColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 11
                        spacing: 5
                        Label {
                            text: (selectedRole === "chosen" ? "✓ 正例 · "
                                  : selectedRole === "rejected" ? "× 反例 · " : "")
                                  + modelData.displayTime
                            color: selectedRole === "chosen"
                                   ? "#16875C"
                                   : selectedRole === "rejected"
                                     ? "#BE4155" : root.textMuted
                            font.bold: selectedRole !== ""
                        }
                        Label {
                            Layout.fillWidth: true
                            text: "ASR：" + (modelData.asrText || "（未识别出文本）")
                            color: root.textMain
                            wrapMode: Text.Wrap
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: Boolean(modelData.resultText)
                            text: "结果：" + modelData.resultText
                            color: root.textMain
                            wrapMode: Text.Wrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: modelData.statusLabel
                                color: root.textMuted
                                font.pixelSize: 11
                            }
                            Button {
                                Layout.minimumWidth: 74
                                enabled: Boolean(modelData.audioPath)
                                text: "播放"
                                onClicked: appController.playVoiceHistory(
                                    modelData.audioPath
                                )
                            }
                            Button {
                                Layout.minimumWidth: 108
                                text: selectedRole === "chosen"
                                      ? "✓ 已设为正例" : "设为正例"
                                onClicked: appController.performAssociationAction(
                                    "center.chosen", modelData.interactionId || ""
                                )
                            }
                            Button {
                                Layout.minimumWidth: 108
                                text: selectedRole === "rejected"
                                      ? "× 已设为反例" : "设为反例"
                                onClicked: appController.performAssociationAction(
                                    "center.rejected", modelData.interactionId || ""
                                )
                            }
                        }
                    }
                }
            }

            Label {
                Layout.alignment: Qt.AlignHCenter
                visible: appController.associationCenterStage === "select"
                         && associationCenterList.count === 0
                text: "没有尚未关联的可用记录"
                color: root.textMuted
            }

            RowLayout {
                Layout.fillWidth: true
                visible: appController.associationCenterStage === "select"
                Label {
                    Layout.fillWidth: true
                    text: appController.associationCenterSelectionSummary
                    color: appController.associationCenterCanSave
                           ? "#16875C" : root.textMuted
                }
                Button {
                    Layout.minimumWidth: 108
                    text: "加载更早记录"
                    onClicked: appController.performAssociationAction(
                        "center.loadMore", ""
                    )
                }
                Button {
                    Layout.minimumWidth: 96
                    text: "清空选择"
                    onClicked: appController.performAssociationAction(
                        "center.clear", ""
                    )
                }
                Button {
                    Layout.minimumWidth: 128
                    enabled: appController.associationCenterCanSave
                    text: "下一步：确认"
                    onClicked: appController.performAssociationAction(
                        "center.confirm", ""
                    )
                }
            }

            ListView {
                id: associationConfirmationList
                objectName: "associationConfirmationList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: appController.associationCenterStage === "confirm"
                spacing: 10
                clip: true
                model: appController.associationCenterConfirmationEntries
                ScrollBar.vertical: ScrollBar { }
                delegate: Rectangle {
                    required property var modelData
                    width: associationConfirmationList.width
                    height: confirmationColumn.implicitHeight + 22
                    radius: 11
                    color: modelData.role === "chosen" ? "#253E3540" : "#3A282D40"
                    border.width: 2
                    border.color: modelData.role === "chosen" ? "#16875C" : "#FF646F"

                    ColumnLayout {
                        id: confirmationColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 11
                        spacing: 6
                        Label {
                            text: modelData.role === "chosen" ? "✓ 正例" : "× 反例"
                            color: modelData.role === "chosen" ? "#16875C" : "#BE4155"
                            font.bold: true
                        }
                        Label {
                            Layout.fillWidth: true
                            text: "ASR：" + (modelData.asrText || "（未识别出文本）")
                            color: root.textMain
                            wrapMode: Text.Wrap
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: Boolean(modelData.resultText)
                            text: "结果：" + modelData.resultText
                            color: root.textMain
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: appController.associationCenterStage === "confirm"
                Label {
                    Layout.fillWidth: true
                    text: (appController.associationCenterKind === "llm"
                           ? "LLM / DPO 关联 · " : "ASR 关联 · ")
                          + appController.associationCenterSelectionSummary
                    color: root.textMuted
                }
                Button {
                    Layout.minimumWidth: 112
                    text: "返回修改"
                    onClicked: appController.performAssociationAction(
                        "center.back", ""
                    )
                }
                Button {
                    objectName: "commitAssociationButton"
                    Layout.minimumWidth: 136
                    text: "确定创建"
                    onClicked: appController.performAssociationAction(
                        "center.commit", ""
                    )
                }
            }
        }
    }
}
