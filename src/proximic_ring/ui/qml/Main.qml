import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: root
    width: 1200
    height: 780
    minimumWidth: 940
    minimumHeight: 700
    readonly property bool unifiedTitlebar: Qt.platform.os === "osx"
    flags: unifiedTitlebar
           ? Qt.Window | Qt.ExpandedClientAreaHint | Qt.NoTitleBarBackgroundHint
           : Qt.Window
    visible: true
    title: "Mythlink"
    color: uiTheme.background
    Material.theme: Material.Light
    Material.accent: uiTheme.primary
    Material.primary: uiTheme.primary
    Material.background: uiTheme.surface
    Material.foreground: uiTheme.text
    readonly property string uiFontFamily: uiTheme.family
    font.family: uiFontFamily


    FigmaTheme { id: uiTheme }
    property int currentPage: 0
    // Keep association features implemented, but hide their UI surfaces.
    readonly property bool associationUiVisible: false
    readonly property var pageTitles: ["我的 Ring", "听写与编辑", "手势控制", "平面触控", "历史记录", "自动锁屏"]
    property bool voiceNavigationExpanded: true
    readonly property int sidebarWidth: 220
    // ApplicationWindow keeps content below the native traffic lights via its
    // safe-area padding; the two background surfaces extend behind them.
    background: Rectangle {
        color: uiTheme.background
        Rectangle {
            width: root.sidebarWidth
            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
            color: uiTheme.sidebar
        }
    }
    function showVoice(history) {
        voiceNavigationExpanded = true
        currentPage = history ? 4 : 1
        Qt.callLater(function() { mainPageScroll.contentItem.contentY = 0 })
    }
    function showPreferences(section) {
        runtimeSettingsDialog.showSection(section)
        runtimeSettingsDialog.open()
    }
    function showSettings(page) {
        if (page === 9) { currentPage = 5; return }
        if (page === 7) {
            runtimeSettingsDialog.close()
            currentPage = 2
            return
        }
        // Legacy service routes open the single advanced form.
        if (page === 4 || page === 5) {
            showAdvancedSettings(page)
            return
        }
        showPreferences(1)
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

    component GestureBindingSelector: VoiceGestureBindingSelector {
        controller: appController
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

    onVisibleChanged: if (!visible) appController.voicePractice.cancel()

    onActiveChanged: {
        if (!active) appController.voicePractice.cancel()
        if (active) {
            appController.inlineInput.refreshInputMethodVersion()
            if (runtimeSettingsDialog.visible) appController.agent.refreshInstallation()
        }
    }

    Connections {
        target: appController
        function onDevicePickerRequested() {
            root.currentPage = 0
            devicePicker.open()
        }
    }

    FigmaDialog {
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

                FigmaTextField {
                    id: deviceSearchField
                    objectName: "deviceSearchField"
                    Layout.fillWidth: true
                    text: appController.deviceSearch
                    hintText: "搜索设备名称或标识"
                    selectByMouse: true
                    onTextEdited: appController.deviceSearch = text
                }

                FigmaAction {
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
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }

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

                            FigmaAction {
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
                FigmaAction {
                    text: appController.scanBusy ? "扫描中…" : "重新扫描"
                    enabled: !appController.scanBusy && !appController.busy
                    onClicked: appController.scanDevices()
                }
                FigmaAction {
                    text: "取消"
                    onClicked: devicePicker.close()
                }
            }
        }
    }

    SettingsDialog {
        id: gpuInstallDialog
        objectName: "gpuInstallDialog"
        width: Math.min(560, parent.width - 48)
        height: Math.min(340, parent.height - 48)
        title: "安装 NVIDIA GPU 加速"
        subtitle: "为本地语音识别安装 GPU 运行环境。"
        symbol: "tool"
        onAccepted: appController.installGpuSupport()
        contentItem: Label {
            leftPadding: 28; rightPadding: 28; topPadding: 24; bottomPadding: 24
            text: "安装需要下载数 GB 文件。应用将退出并打开独立安装窗口；安装验证成功后会自动重新启动。是否继续？"
            color: root.textMain; font.pixelSize: 13; lineHeight: 1.4
            wrapMode: Text.Wrap
        }
        footer: DialogButtonBox {
            leftPadding: 28; rightPadding: 28; topPadding: 16; bottomPadding: 20; spacing: 12
            background: Rectangle {
                radius: 20; color: uiTheme.surface
                Rectangle { anchors.top: parent.top; width: parent.width; height: 20; color: parent.color }
                Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: uiTheme.line }
            }
            FigmaAction { text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            FigmaAction { text: "安装"; primary: true; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
        }
    }

    FigmaDialog {
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
        background: Rectangle { color: root.panel; radius: 18; border.color: root.border }
        closePolicy: Popup.CloseOnEscape
        onOpened: { logArea.refreshLog(); logArea.jumpToLatest() }
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

        Timer {
            interval: 500
            running: runtimeLogDialog.visible
            repeat: true
            onTriggered: logArea.refreshLog()
        }

        contentItem: ColumnLayout {
            spacing: 12

            UiScrollView {
                ScrollBar.vertical.policy: ScrollBar.AlwaysOff
                id: logScroll
                objectName: "logScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                background: Rectangle { color: root.panelAlt; radius: 10 }

                TextArea {
                    id: logArea
                    objectName: "logArea"
                    width: logScroll.availableWidth
                    readOnly: true
                    textFormat: TextEdit.PlainText
                    text: "暂无日志"
                    color: root.textMuted
                    font.family: Qt.platform.os === "osx" ? "Menlo" : "Cascadia Mono"
                    font.pixelSize: 14
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    padding: 12
                    background: null

                    function refreshLog() {
                        var viewport = logScroll.contentItem
                        var previousY = viewport ? viewport.contentY : 0
                        var previousCursor = cursorPosition
                        var previousSelectionStart = selectionStart
                        var previousSelectionEnd = selectionEnd
                        var nextText = appController.readDiagnosticLog()
                        if (text === (nextText.length > 0 ? nextText : "暂无日志"))
                            return
                        text = nextText.length > 0 ? nextText : "暂无日志"
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

            Label {
                Layout.fillWidth: true
                text: "diagnostic.log · 窗口显示最近 1000 行；导出包含已保留的全部历史"
                color: root.textMuted
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                FigmaAction {
                    objectName: "exportDiagnosticLogButton"
                    text: "导出完整日志"
                    onClicked: appController.exportDiagnosticLog()
                }
                FigmaAction {
                    objectName: "openDiagnosticLogDirectoryButton"
                    text: "打开日志目录"
                    onClicked: appController.openDiagnosticLogDirectory()
                }
                Item { Layout.fillWidth: true }
                FigmaAction {
                    objectName: "jumpToLatestLogButton"
                    text: "跳到最新"
                    onClicked: logArea.jumpToLatest()
                }
                FigmaAction { text: "清空窗口"; onClicked: appController.clearLog() }
                FigmaAction { text: "关闭"; onClicked: runtimeLogDialog.close() }
            }
        }
    }

    UiWindowDragArea {
        objectName: "mainWindowDragArea"
        parent: root.background
        anchors.fill: parent
        z: -1
    }

    Rectangle {
        id: sidebar
        objectName: "mainSidebar"
        width: root.sidebarWidth
        anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.left: parent.left
        color: uiTheme.sidebar
        ColumnLayout {
            anchors.fill: parent; anchors.leftMargin: 20; anchors.rightMargin: 20
            anchors.topMargin: 24; anchors.bottomMargin: 16; spacing: 8
            Image {
                objectName: "mythlinkLogo"
                Layout.topMargin: 8; Layout.bottomMargin: 16
                Layout.preferredWidth: 168; Layout.preferredHeight: 32.6123
                source: "../assets/figma/redesign-383-9247-brand.svg"
                Accessible.name: "Mythlink"
            }
            FigmaNavItem { objectName: "mainNav0"; Layout.fillWidth: true; text: "首页"; symbol: "home"; checked: root.currentPage === 0; onClicked: root.currentPage = 0 }
            FigmaNavItem { objectName: "mainNav2"; Layout.fillWidth: true; text: "手势控制"; symbol: "gesture"; checked: root.currentPage === 2; onClicked: root.currentPage = 2 }
            FigmaNavItem { objectName: "voiceNavigationGroup"; Layout.fillWidth: true; text: "语音输入"; symbol: "mic"; disclosure: true; expanded: root.voiceNavigationExpanded; onClicked: root.voiceNavigationExpanded = !root.voiceNavigationExpanded }
            Item {
                visible: root.voiceNavigationExpanded
                Layout.fillWidth: true; Layout.preferredHeight: 92
                Column {
                    anchors.fill: parent; spacing: 4
                    FigmaNavItem { objectName: "mainNav1"; width: parent.width; text: "听写与编辑"; childRoute: true; checked: root.currentPage === 1; onClicked: root.showVoice(false) }
                    FigmaNavItem { objectName: "mainNav4"; width: parent.width; text: "历史记录"; childRoute: true; checked: root.currentPage === 4; onClicked: root.showVoice(true) }
                }
                Image { x: 0; y: 0; source: "../assets/figma/redesign-383-9247-tree-ends-at-last-voice-child.svg" }
            }
            FigmaNavItem { objectName: "mainNav3"; Layout.fillWidth: true; text: "平面触控"; symbol: "pointer"; checked: root.currentPage === 3; onClicked: root.currentPage = 3 }
            FigmaNavItem { objectName: "mainNav5"; Layout.fillWidth: true; text: "自动锁屏"; symbol: "lock"; checked: root.currentPage === 5; onClicked: root.currentPage = 5 }
            Item { Layout.fillHeight: true }
            Item {
                objectName: "sidebarUtilityBar"
                Layout.fillWidth: true; Layout.preferredHeight: 44
                Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: uiTheme.line }
                FigmaAction {
                    objectName: "sidebarAccountButton"; symbol: "account"; quiet: true
                    anchors.left: parent.left; anchors.leftMargin: 6
                    width: 32; height: 44
                    Accessible.name: "账户"; onClicked: root.showPreferences(0)
                }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: 6; height: 44; spacing: 0
                    FigmaAction { objectName: "runtimeSettingsButton"; symbol: "settings"; quiet: true; width: 32; height: 44; Accessible.name: "设置"; onClicked: root.showPreferences(1) }
                    FigmaAction { objectName: "helpButton"; symbol: "help"; quiet: true; width: 32; height: 44; Accessible.name: "帮助"; onClicked: root.showPreferences(2) }
                }
            }
        }
    }

    Item {
        id: workspace
        anchors.left: sidebar.right; anchors.right: parent.right
        anchors.top: parent.top; anchors.bottom: parent.bottom
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        Item {
            id: pageHeader
            objectName: "mainPageHeader"
            anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
            height: (root.currentPage === 1 || root.currentPage === 2) ? 104 - root.topPadding : 76
            Column {
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                anchors.verticalCenterOffset: (root.currentPage === 1 || root.currentPage === 2) ? 2 - root.topPadding / 2 : 0
                width: Math.max(200, parent.width - 220); spacing: 0
                Label { height: (root.currentPage === 1 || root.currentPage === 2) ? 36 : implicitHeight; verticalAlignment: Text.AlignVCenter; text: root.pageTitles[root.currentPage]; color: uiTheme.text; font.pixelSize: 28; font.weight: Font.Bold }
                Label {
                    visible: root.currentPage !== 0
                    height: (root.currentPage === 1 || root.currentPage === 2) ? 24 : implicitHeight
                    verticalAlignment: Text.AlignVCenter
                    width: parent.width; elide: Text.ElideRight
                    text: root.currentPage === 2 ? "为不同场景配置戒指手势" : root.currentPage === 1 ? "语音手势、热词与输入测试" : root.currentPage === 3 ? "光标偏好调节，笔画输入测试" : root.currentPage === 4 ? "查找听写与修改的结果" : root.currentPage === 5 ? "离开后锁屏，靠近后响指解锁" : "查看手势与应用操作的对应关系"
                    color: uiTheme.muted; font.pixelSize: (root.currentPage === 1 || root.currentPage === 2) ? 16 : 14
                }
            }
            FigmaAction {
                objectName: "accountEntryButton"
                visible: root.currentPage === 0
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                width: 212; height: 48; quiet: true; leftPadding: 0; rightPadding: 0
                Accessible.name: appController.localProfile.signedIn ? "查看账户" : "登录"
                onClicked: root.showPreferences(0)
                contentItem: RowLayout {
                    spacing: 8
                    Column {
                        Layout.preferredWidth: 164; Layout.alignment: Qt.AlignVCenter; spacing: 0
                        Label {
                            objectName: "accountHeaderName"
                            width: parent.width; height: 24
                            text: appController.localProfile.signedIn ? (appController.localProfile.displayName || "我的账户") : "登录"
                            font.family: uiTheme.family; font.pixelSize: 16; font.weight: Font.Medium
                            color: uiTheme.text; horizontalAlignment: Text.AlignRight; verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight; textFormat: Text.PlainText
                        }
                        Label {
                            objectName: "accountHeaderEmail"
                            width: parent.width; height: 22
                            text: appController.localProfile.signedIn ? (appController.localProfile.email || "未填写邮箱") : "填写用户名和邮箱"
                            font.family: uiTheme.family; font.pixelSize: 14; font.weight: Font.Normal
                            color: uiTheme.muted; horizontalAlignment: Text.AlignRight; verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight; textFormat: Text.PlainText
                        }
                    }
                    Image { Layout.preferredWidth: 40; Layout.preferredHeight: 40; source: "../assets/figma/redesign-383-9247-generic-avatar.svg" }
                }
            }
            FigmaAction {
                id: deviceEntry
                objectName: "headerDeviceEntry"
                visible: root.currentPage !== 0
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                anchors.verticalCenterOffset: (root.currentPage === 1 || root.currentPage === 2) ? 2 - root.topPadding / 2 : 0
                implicitWidth: (root.currentPage === 1 || root.currentPage === 2) ? 208 : Math.max(129, implicitContentWidth + leftPadding + rightPadding)
                implicitHeight: (root.currentPage === 1 || root.currentPage === 2) ? 48 : 36
                font.pixelSize: (root.currentPage === 1 || root.currentPage === 2) ? 14 : 16
                font.weight: (root.currentPage === 1 || root.currentPage === 2) ? Font.Normal : Font.Medium
                leftPadding: (root.currentPage === 1 || root.currentPage === 2) ? 12 : 20
                symbol: "ring"; navigation: true; quiet: true
                text: (appController.deviceName || "Ring") + "  " + (appController.connected ? "已连接" : appController.busy ? "连接中" : "未连接")
                onClicked: root.currentPage = 0
                contentItem: RowLayout {
                    spacing: (root.currentPage === 1 || root.currentPage === 2) ? 12 : 4
                    FigmaIcon { symbol: "ring"; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                    RowLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                        Label {
                            Layout.fillWidth: root.currentPage !== 1 && root.currentPage !== 2; Layout.minimumWidth: 0
                            text: (root.currentPage === 1 || root.currentPage === 2) ? (appController.deviceName || "Ring") : deviceEntry.text
                            font.family: deviceEntry.font.family; font.pixelSize: deviceEntry.font.pixelSize; font.weight: Font.Medium
                            color: (root.currentPage === 1 || root.currentPage === 2) ? uiTheme.text : uiTheme.muted
                            horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                        }
                        Label {
                            visible: (root.currentPage === 1 || root.currentPage === 2); Layout.fillWidth: true
                            text: appController.connected ? "已连接" : appController.busy ? "连接中" : "未连接"
                            font.family: uiTheme.family; font.pixelSize: 14; color: uiTheme.muted
                        }
                    }
                    FigmaIcon { symbol: "chevron-right"; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                }
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
                onDeleteHistoryRequested: function(record) { mainPageScroll.requestHistoryDelete(record) }
                onGesturesRequested: root.currentPage = 2
                onTouchpadRequested: root.currentPage = 3
                onHelpRequested: root.showPreferences(2)
                onTutorialRequested: tutorialPreview.open()
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
                onVoiceSettingsRequested: mainPageScroll.scrollToGestures()
            }
        }
    }
    FigmaDialog {
        id: helpCenterDialog
        objectName: "helpCenterDialog"
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
            Label { Layout.fillWidth: true; text: "手势控制"; font.pixelSize: 16; font.bold: true; color: uiTheme.text }
            Label { Layout.fillWidth: true; text: "默认双击 Tap 开始语音、单击 Tap 结束语音。仅与输入手势重叠时，输入框内优先执行输入功能；其他手势按应用优先、全局兜底执行。语音处理期间仍可取消本次操作。"; wrapMode: Text.Wrap; color: uiTheme.muted }
            Label { Layout.fillWidth: true; text: "输入法安装与系统授权统一在“设置 → 系统与权限”中管理。"; wrapMode: Text.Wrap; color: uiTheme.muted }
        }
    }

    ReleaseNotesDialog { id: releaseNotesDialog; objectName: "releaseNotesDialog"; appVersion: appController.appVersion }
    TutorialPreviewDialog {
        id: tutorialPreview
        controller: appController
        onHomeRequested: { root.currentPage = 0; close() }
        onSettingsRequested: { close(); root.showSettings(6) }
        onVoiceRequested: { root.showVoice(false); close() }
        onGesturesRequested: { root.currentPage = 2; close() }
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
        associationUiVisible: root.associationUiVisible
        parent: pageBody
        visible: root.currentPage === 1 || root.currentPage === 4
        historyOnly: root.currentPage === 4
        anchors.fill: parent
        controller: appController
        onHomeRequested: root.currentPage = 0
        onHistoryRequested: root.showVoice(true)
        onTutorialRequested: tutorialPreview.open()
    }

    LockPage {
        parent: pageBody; anchors.fill: parent
        visible: root.currentPage === 5
        controller: appController
        onSettingsRequested: root.showSettings(6)
    }

        AdvancedSettingsDialog {
            id: advancedSettingsDialog
            controller: appController
            onGpuSetupRequested: gpuInstallDialog.open()
            onClosed: {
                if (root.visible) {
                    root.showPreferences(1)
                    Qt.callLater(function() {
                        runtimeSettingsScroll.contentItem.contentY = advancedSettingsDialog.returnScrollPosition
                    })
                }
            }
        }

        PreferencesDialog {
            id: runtimeSettingsDialog
            objectName: "runtimeSettingsDialog"
            property int currentPage: 0
            readonly property var pageTitles: ["设置", "输入与控制", "手势说明", "麦克风与语音", "语音识别服务", "文本处理服务", "系统与权限", "应用快捷键", "微信快捷键设置", "离开锁屏"]
            title: section === 0 ? accountPage.title : section === 2 ? "帮助" : pageTitles[currentPage]
            section: 1
            showBack: section === 1 && currentPage !== 0
            backObjectName: "settingsBackButton"
            closeObjectName: "settingsApplyButton"
            onBackRequested: goBack()
            onCloseRequested: applyAndClose()
            onAccountRequested: showSection(0)
            onHelpRequested: showSection(2)
            onSettingsRequested: {
                showSection(1)
                settingsOverview.expandedSection = ""
                navigate(0)
            }
            onClosed: {
                appController.appGestures.recording = false
                appController.inlineInput.permissions.cancelScreenPreview()
            }

            onOpened: {
                if (section === 0) accountPage.focusEditor()
                refreshStatus()
            }
            function refreshStatus() {
                appController.agent.refreshInstallation()
                appController.startup.refresh()
                appController.refreshMicrophones()
                appController.inlineInput.refreshInputMethodVersion()
                appController.inlineInput.permissions.refresh()
                appController.inlineInput.permissions.refreshScreenRecording()
            }
            function showSection(nextSection) {
                // Keep the popup and dimmer mounted while changing pages.
                if (opened && section === nextSection) return
                saveCurrentEditor()
                appController.appGestures.recording = false
                appController.inlineInput.permissions.cancelScreenPreview()
                section = nextSection
                if (section === 0) {
                    accountPage.resetDraft()
                    if (visible) accountPage.focusEditor()
                } else if (section === 1) {
                    settingsOverview.expandedSection = ""
                    navigate(0)
                    if (visible) refreshStatus()
                }
            }
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
            function navigate(page) {
                // A ScrollView is a focus scope and can retain its editor's
                // focus. Focus the plain container so pending edits are saved.
                saveCurrentEditor()
                appController.appGestures.recording = false
                if (page !== 6)
                    appController.inlineInput.permissions.cancelScreenPreview()
                // Former audio/input detail routes now land on the grouped form.
                currentPage = page === 1 || page === 3 || page === 6 ? 0 : page
                if (page === 6) settingsOverview.expandedSection = ""
                if (page === 6 && Qt.platform.os === "osx")
                    appController.inlineInput.permissions.refreshScreenRecording()
                Qt.callLater(function() {
                    var flick = runtimeSettingsScroll.contentItem
                    flick.contentY = page === 6
                        ? Math.min(settingsOverview.y + settingsOverview.permissionsPosition,
                                   Math.max(0, flick.contentHeight - flick.height)) : 0
                })
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

            contentItem: Item {
                id: settingsContent
                AccountPreferencesPage {
                    id: accountPage
                    anchors.fill: parent
                    visible: runtimeSettingsDialog.section === 0
                    profile: appController.localProfile
                    onCloseRequested: runtimeSettingsDialog.close()
                }
                HelpPreferencesPage {
                    anchors.fill: parent
                    visible: runtimeSettingsDialog.section === 2
                    appVersion: appController.appVersion
                    onTutorialRequested: { runtimeSettingsDialog.close(); tutorialPreview.open() }
                    onHelpCenterRequested: { runtimeSettingsDialog.close(); helpCenterDialog.open() }
                    onReleaseNotesRequested: releaseNotesDialog.open()
                }
                UiScrollView {
                    id: runtimeSettingsScroll
                    objectName: "runtimeSettingsScroll"
                    anchors.fill: parent
                    visible: runtimeSettingsDialog.section === 1
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ScrollBar.vertical: PreferenceScrollBar {
                        parent: runtimeSettingsDialog.background
                        visible: runtimeSettingsDialog.section === 1 && size < 1
                        x: runtimeSettingsDialog.width - 13; y: 107
                        height: runtimeSettingsDialog.height - 143
                    }
                    ColumnLayout {
                        width: runtimeSettingsScroll.availableWidth
                        spacing: 14
                        SettingsOverview {
                            id: settingsOverview
                            onResetScrollRequested: Qt.callLater(function() { runtimeSettingsScroll.contentItem.contentY = 0 })
                            objectName: "settingsPage0"
                            Layout.fillWidth: true
                            Layout.rightMargin: 12
                            visible: runtimeSettingsDialog.currentPage === 0
                            controller: appController
                            settingsDialog: runtimeSettingsDialog
                            onDetailRequested: function(page) {
                                if (page === 12) { runtimeSettingsDialog.close(); root.currentPage = 2 }
                                else if (page === 9) { runtimeSettingsDialog.close(); root.currentPage = 5 }
                                else if (page === 10 || page === 11) { runtimeSettingsDialog.close(); root.showVoice(page === 10); if (page === 11) Qt.callLater(function() { mainPageScroll.toggleHotwords() }) }
                                else runtimeSettingsDialog.navigate(page)
                            }
                            onAdvancedSettingsRequested: root.showAdvancedSettings(4)
                            onLogRequested: { runtimeSettingsDialog.saveCurrentEditor(); runtimeSettingsDialog.close(); runtimeLogDialog.open() }
                        }
                        ProximitySettings {
                            objectName: "settingsPage9"
                            Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                            Layout.topMargin: 24; Layout.bottomMargin: 28
                            visible: runtimeSettingsDialog.currentPage === 9
                            controller: appController.proximity
                            host: appController
                        }
                        ColumnLayout {
                            objectName: "settingsPage2"
                            Layout.fillWidth: true
                            Layout.topMargin: 24; Layout.bottomMargin: 28
                            visible: runtimeSettingsDialog.currentPage === 2
                            spacing: 18
                            Label {
                                objectName: "ringGestureModeLabel"
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                text: "按输入焦点自动分配手势"
                                      + "\n提示、窗口选择与页面滚动的手势在“手势控制 → 全局默认”中配置。"
                                      + "\n输入焦点仅影响与输入功能冲突的手势；其余手势按应用优先、全局兜底执行。"
                                      + "\n双击 Tap 沿用现有输入法启动入口；输入时下滑润色本句，不发送。"
                                      + "\n录音中下滑：定稿后润色，左滑撤销整句；听写后下滑：左滑先还原听写。"
                                color: root.textMuted; font.pixelSize: 12; wrapMode: Text.Wrap
                            }
                            SettingsFormRow {
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                visible: appController.appGestures.supported
                                title: "应用手势"
                                description: "添加应用后配置专属手势；未配置的应用使用全局配置"
                                FigmaAction {
                                    objectName: "openAppGestureSettingsButton"
                                    Layout.alignment: Qt.AlignRight
                                    text: "设置快捷键"
                                    onClicked: root.showSettings(7)
                                }
                            }

                            SettingsSectionHeader {
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                objectName: "gestureSettingsSection"
                                title: "听写手势"
                                badge: "即时生效"
                                accent: "#16875C"
                            }
                            Label {
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                text: "每项最多设置两个手势；开始与结束可共用同一手势，其他语音操作不能重复分配。语音手势与应用、全局手势可复用，按输入焦点自动分配。"
                                color: root.textMuted; font.pixelSize: 11; wrapMode: Text.Wrap
                            }
                            GestureBindingRow { actionName: "confirm"; title: "开始本句语音" }
                            GestureBindingRow { actionName: "end"; title: "结束本句语音" }
                            GestureBindingRow { actionName: "undo"; title: "撤销 · 处理中用于取消" }
                            GestureBindingRow { actionName: "switch_mode"; title: "转换 · 下划线期间转为编辑" }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                visible: appController.appGestures.supported
                                Label { text: "切入 Mythlink"; color: root.textMain }
                                SettingsSelect {
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
                                    text: "默认未绑定，可自行选择空闲手势。此操作只切换输入法，不开始听写；暂停语音识别时也可用。双击 tap 自动切换并开始听写不受此设置影响。"
                                    color: root.textMuted; font.pixelSize: 11; wrapMode: Text.Wrap
                                }
                            }
                            UiNotice {
                                objectName: "gestureSettingsErrorLabel"
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                visible: appController.gestureSettingsError.length > 0
                                text: appController.gestureSettingsError
                                font.pixelSize: 12; wrapMode: Text.Wrap
                            }
                            RowLayout {
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                Label {
                                    Layout.fillWidth: true
                                    text: "确认至少保留一个手势。设置会自动保存。"
                                    color: root.textMuted; font.pixelSize: 11; wrapMode: Text.Wrap
                                }
                                FigmaAction {
                                    objectName: "resetGestureBindingsButton"
                                    text: "恢复默认手势"
                                    onClicked: appController.resetGestureBindings()
                                }
                            }


                        }
                        ColumnLayout {
                            objectName: "settingsPage7"
                            Layout.fillWidth: true
                            Layout.topMargin: 24; Layout.bottomMargin: 28
                            visible: runtimeSettingsDialog.currentPage === 7
                            spacing: 18
                            AppGestureSettings {
                                id: appGestureSettings
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                service: appController.appGestures
                                onWechatSetupRequested: runtimeSettingsDialog.navigate(8)
                            }
                        }
                        ColumnLayout {
                            objectName: "settingsPage8"
                            Layout.fillWidth: true
                            Layout.topMargin: 24; Layout.bottomMargin: 28
                            visible: runtimeSettingsDialog.currentPage === 8
                            spacing: 18
                            WeChatShortcutGuide {
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                service: appController.appGestures
                            }
                        }
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
        visible: root.associationUiVisible && appController.associationRecommendationVisible
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
        visible: root.associationUiVisible && appController.associationDetailVisible
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
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }
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
        visible: root.associationUiVisible && appController.associationCenterVisible
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
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }
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
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }
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
